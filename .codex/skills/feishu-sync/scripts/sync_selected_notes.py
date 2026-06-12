#!/usr/bin/env python3
"""Sync selected my-mind Markdown notes to Feishu/Lark without duplicates."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[4]
TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_RECORD_FILE = ROOT / "85_运行记录" / "飞书知识库同步记录.jsonl"
DEFAULT_DRAFT_DIR = ROOT / "85_运行记录" / "飞书知识库同步页"
DEFAULT_SCAN_DIRS = [
    ROOT / "20_资料库",
    ROOT / "10_项目",
    ROOT / "75_提示词库",
    ROOT / "design",
]
DEFAULT_AUTO_SYNC_DIRS = [
    ROOT / "20_资料库",
]
DEFAULT_STRATEGIES = {"精选同步", "目录页同步"}
SKIP_STRATEGIES = {"不同步", "禁止同步"}
BLOCKED_SENSITIVE = {"敏感", "私密", "禁止同步", "不可同步"}

DEFAULT_CREATE_COMMAND = (
    'OPENCLAW_HOME="$HOME/.openclaw" lark-cli docs +create '
    "--api-version v2 --wiki-space my_library --title {title} --content @{markdown_file_rel}"
)
DEFAULT_UPDATE_COMMAND = (
    'OPENCLAW_HOME="$HOME/.openclaw" lark-cli docs +update '
    "--api-version v2 --doc {page_token} --mode overwrite --new-title {title} --markdown @{markdown_file_rel}"
)
DEFAULT_SEARCH_COMMAND = (
    'OPENCLAW_HOME="$HOME/.openclaw" lark-cli docs +search --as user --query {search_title}'
)


@dataclass
class Note:
    path: Path
    rel_path: str
    text: str
    front_matter: str
    body: str
    metadata: dict[str, Any]
    sync: dict[str, str]
    title: str
    strategy: str
    status: str
    digest: str


@dataclass
class RemotePage:
    feishu_url: str = ""
    page_token: str = ""
    wiki_node_token: str = ""
    content_hash: str = ""
    source: str = ""

    def exists(self) -> bool:
        return bool(self.feishu_url or self.page_token or self.wiki_node_token)


def now_datetime() -> str:
    return dt.datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %z")


def now_filename() -> str:
    return dt.datetime.now(TZ).strftime("%Y-%m-%d-%H%M%S")


def repo_relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def is_under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def default_strategy_for(path: Path, auto_sync_dirs: list[Path]) -> str:
    if not any(is_under(path, parent) for parent in auto_sync_dirs):
        return ""
    if path.name == "目录说明.md" or path.name.endswith("索引.md"):
        return "目录页同步"
    return "精选同步"


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def split_front_matter(text: str) -> tuple[str, str]:
    if not text.startswith("---\n"):
        return "", text
    match = re.search(r"\n---\s*\n", text[4:])
    if not match:
        return "", text
    end = 4 + match.start()
    body_start = 4 + match.end()
    return text[4:end], text[body_start:]


def clean_scalar(value: str) -> str:
    value = value.strip()
    if value in {'""', "''"}:
        return ""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        value = value[1:-1]
    return value.replace('\\"', '"').strip()


def parse_front_matter(front_matter: str) -> dict[str, Any]:
    data: dict[str, Any] = {}
    current_map = ""
    for raw_line in front_matter.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        if not raw_line.startswith((" ", "\t")):
            match = re.match(r"^([^:]+):\s*(.*)$", raw_line)
            if not match:
                current_map = ""
                continue
            key = match.group(1).strip()
            value = match.group(2).strip()
            if value:
                data[key] = clean_scalar(value)
                current_map = ""
            else:
                data[key] = {}
                current_map = key
            continue
        if current_map and isinstance(data.get(current_map), dict):
            match = re.match(r"^\s+([^:]+):\s*(.*)$", raw_line)
            if match:
                data[current_map][match.group(1).strip()] = clean_scalar(match.group(2))
    return data


def first_heading(body: str) -> str:
    match = re.search(r"^#\s+(.+?)\s*$", body, flags=re.M)
    return match.group(1).strip() if match else ""


def normalized_body_for_hash(body: str) -> str:
    body = body.replace("\r\n", "\n").replace("\r", "\n").strip()
    return re.sub(r"\n{3,}", "\n\n", body)


def content_hash(title: str, body: str) -> str:
    payload = f"{title.strip()}\n\n{normalized_body_for_hash(body)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_note(path: Path, auto_sync_dirs: list[Path]) -> Note | None:
    text = read_text(path)
    front_matter, body = split_front_matter(text)
    default_strategy = default_strategy_for(path, auto_sync_dirs)
    metadata = parse_front_matter(front_matter) if front_matter else {}
    raw_sync = metadata.get("飞书同步")
    if not isinstance(raw_sync, dict) and not default_strategy:
        return None
    sync = {str(key): str(value) for key, value in raw_sync.items()} if isinstance(raw_sync, dict) else {}
    title = str(metadata.get("标题") or first_heading(body) or path.stem).strip()
    strategy = sync.get("策略", "").strip() or default_strategy
    status = sync.get("状态", "").strip()
    front_matter = front_matter or f"标题: {title}"
    return Note(
        path=path,
        rel_path=repo_relative(path),
        text=text,
        front_matter=front_matter,
        body=body,
        metadata=metadata,
        sync=sync,
        title=title,
        strategy=strategy,
        status=status,
        digest=content_hash(title, body),
    )


def iter_markdown_files(scan_dirs: list[Path], explicit_files: list[Path]) -> list[Path]:
    paths: list[Path] = []
    if explicit_files:
        paths.extend(explicit_files)
    else:
        for scan_dir in scan_dirs:
            if scan_dir.exists():
                paths.extend(scan_dir.rglob("*.md"))
    return sorted({path.resolve() for path in paths if path.exists() and path.is_file()})


def discover_notes(
    *,
    scan_dirs: list[Path],
    explicit_files: list[Path],
    auto_sync_dirs: list[Path],
    strategies: set[str],
    allow_sensitive: bool,
) -> list[Note]:
    notes: list[Note] = []
    for path in iter_markdown_files(scan_dirs, explicit_files):
        note = load_note(path, auto_sync_dirs)
        if not note:
            continue
        if note.strategy in SKIP_STRATEGIES:
            continue
        if note.strategy not in strategies:
            continue
        if note.status == "暂停":
            continue
        sensitive = str(note.metadata.get("敏感状态", "")).strip()
        if sensitive in BLOCKED_SENSITIVE and not allow_sensitive:
            continue
        notes.append(note)
    return notes


def parse_json_payload(text: str) -> Any:
    text = text.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"(\{.*\})", text, flags=re.S)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            return None
    return None


def walk_json(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        found.append(value)
        for child in value.values():
            found.extend(walk_json(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(walk_json(child))
    return found


def extract_remote_page_from_obj(obj: dict[str, Any]) -> RemotePage:
    url = str(
        obj.get("url")
        or obj.get("docs_url")
        or obj.get("doc_url")
        or obj.get("link")
        or obj.get("web_url")
        or ""
    )
    token = str(
        obj.get("document_id")
        or obj.get("doc_token")
        or obj.get("obj_token")
        or obj.get("token")
        or obj.get("page_token")
        or ""
    )
    node_token = str(obj.get("node_token") or obj.get("wiki_token") or "")
    nested_doc = obj.get("document")
    if isinstance(nested_doc, dict):
        url = url or str(nested_doc.get("url") or "")
        token = token or str(nested_doc.get("document_id") or nested_doc.get("token") or "")
    return RemotePage(feishu_url=url, page_token=token, wiki_node_token=node_token)


def parse_publish_output(output: str, fallback: RemotePage | None = None) -> RemotePage:
    data = parse_json_payload(output)
    if isinstance(data, dict):
        if data.get("ok") is False:
            error = data.get("error") if isinstance(data.get("error"), dict) else {}
            message = str(error.get("message") or "命令返回 ok=false")
            hint = str(error.get("hint") or "").strip()
            raise RuntimeError(f"{message}{'；' + hint if hint else ''}")
        for obj in walk_json(data):
            page = extract_remote_page_from_obj(obj)
            if page.exists():
                if fallback:
                    page.feishu_url = page.feishu_url or fallback.feishu_url
                    page.page_token = page.page_token or fallback.page_token
                    page.wiki_node_token = page.wiki_node_token or fallback.wiki_node_token
                return page
    url_match = re.search(r"https?://\S+", output)
    token_match = re.search(r"\b([A-Za-z0-9]{16,})\b", output)
    page = RemotePage(
        feishu_url=url_match.group(0).rstrip("\"'。,;；)") if url_match else "",
        page_token=token_match.group(1) if token_match else "",
    )
    if fallback:
        page.feishu_url = page.feishu_url or fallback.feishu_url
        page.page_token = page.page_token or fallback.page_token
        page.wiki_node_token = page.wiki_node_token or fallback.wiki_node_token
    return page


def normalize_title(title: str) -> str:
    return re.sub(r"\s+", " ", title).strip().lower()


def short_search_title(title: str) -> str:
    return title.strip()[:30]


def search_existing_page(note: Note, search_command: str, delay_seconds: float, retries: int) -> RemotePage | None:
    replacements = command_replacements(note, Path(""), RemotePage())
    command = search_command.format(**replacements)
    if delay_seconds > 0:
        time.sleep(delay_seconds)
    completed = None
    for attempt in range(max(1, retries + 1)):
        completed = subprocess.run(command, shell=True, text=True, capture_output=True, check=False)
        combined = "\n".join(part for part in [completed.stdout.strip(), completed.stderr.strip()] if part)
        if completed.returncode == 0 or "too many request" not in combined:
            break
        if attempt < retries:
            time.sleep(max(delay_seconds, 1.0) * (attempt + 1))
    assert completed is not None
    combined_output = "\n".join(part for part in [completed.stdout.strip(), completed.stderr.strip()] if part)
    if completed.returncode != 0:
        raise RuntimeError(f"飞书搜索失败，退出码 {completed.returncode}：{combined_output}")
    data = parse_json_payload(completed.stdout)
    if isinstance(data, dict) and data.get("ok") is False:
        raise RuntimeError(f"飞书搜索失败：{combined_output}")

    expected = normalize_title(note.title)
    exact_matches: list[RemotePage] = []
    for obj in walk_json(data):
        title = str(obj.get("title") or obj.get("name") or obj.get("docs_title") or "")
        if normalize_title(title) != expected:
            continue
        page = extract_remote_page_from_obj(obj)
        if page.exists():
            exact_matches.append(page)

    unique = []
    seen = set()
    for page in exact_matches:
        key = page.feishu_url or page.page_token or page.wiki_node_token
        if key and key not in seen:
            unique.append(page)
            seen.add(key)

    if len(unique) > 1:
        raise RuntimeError(f"搜索到多个同名飞书文档，拒绝自动创建重复文档：{note.title}")
    return unique[0] if unique else None


def load_latest_records(record_file: Path) -> dict[str, RemotePage]:
    latest: dict[str, RemotePage] = {}
    if not record_file.exists():
        return latest
    for raw_line in record_file.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not raw_line.strip():
            continue
        try:
            record = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        source_file = str(record.get("source_file") or "")
        if not source_file:
            continue
        page = RemotePage(
            feishu_url=str(record.get("feishu_url") or ""),
            page_token=str(record.get("page_token") or ""),
            wiki_node_token=str(record.get("wiki_node_token") or ""),
            content_hash=str(record.get("content_hash") or ""),
            source="record",
        )
        if page.exists():
            latest[source_file] = page
    return latest


def remote_from_note(note: Note, records: dict[str, RemotePage]) -> RemotePage:
    frontmatter_page = RemotePage(
        feishu_url=note.sync.get("飞书页面", ""),
        page_token=note.sync.get("页面Token", ""),
        wiki_node_token=note.sync.get("Wiki节点", ""),
        content_hash=note.sync.get("内容哈希", ""),
        source="front_matter",
    )
    record_page = records.get(note.rel_path, RemotePage())
    if frontmatter_page.exists():
        if record_page.exists():
            frontmatter_page.feishu_url = frontmatter_page.feishu_url or record_page.feishu_url
            frontmatter_page.page_token = frontmatter_page.page_token or record_page.page_token
            frontmatter_page.wiki_node_token = frontmatter_page.wiki_node_token or record_page.wiki_node_token
            frontmatter_page.content_hash = frontmatter_page.content_hash or record_page.content_hash
        return frontmatter_page
    if record_page.exists():
        record_page.source = "record"
        return record_page
    return RemotePage()


def strip_duplicate_h1(title: str, body: str) -> str:
    body = body.strip()
    pattern = rf"^#\s+{re.escape(title)}\s*\n+"
    return re.sub(pattern, "", body, count=1)


def render_sync_markdown(note: Note) -> str:
    body = strip_duplicate_h1(note.title, note.body)
    lines = [
        f"# {note.title}",
        "",
        "> 本页由本地 my-mind 知识库精选同步生成；本地 Markdown 是唯一可信源。",
        "",
        "## 同步信息",
        "",
        f"- 本地路径：`{note.rel_path}`",
        f"- 同步策略：{note.strategy}",
        f"- 处理状态：{note.metadata.get('处理状态', '')}",
        f"- 可信状态：{note.metadata.get('可信状态', '')}",
        f"- 同步时间：{now_datetime()}",
        "",
        "---",
        "",
        body,
        "",
    ]
    return "\n".join(lines)


def safe_filename(name: str) -> str:
    name = re.sub(r"[\\/:*?\"<>|]+", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name[:80] or "untitled"


def write_draft(note: Note, draft_dir: Path) -> Path:
    draft_dir.mkdir(parents=True, exist_ok=True)
    path = draft_dir / f"{now_filename()} {safe_filename(note.title)}.md"
    path.write_text(render_sync_markdown(note), encoding="utf-8")
    return path


def command_replacements(note: Note, markdown_file: Path, remote: RemotePage) -> dict[str, str]:
    markdown_file_abs = str(markdown_file.resolve()) if str(markdown_file) else ""
    markdown_file_rel = repo_relative(markdown_file) if str(markdown_file) else ""
    return {
        "title": shlex.quote(note.title),
        "source_file": shlex.quote(str(note.path.resolve())),
        "source_file_rel": shlex.quote(note.rel_path),
        "markdown_file": shlex.quote(markdown_file_abs),
        "markdown_file_rel": shlex.quote(markdown_file_rel),
        "content_hash": shlex.quote(note.digest),
        "search_title": shlex.quote(short_search_title(note.title)),
        "feishu_url": shlex.quote(remote.feishu_url),
        "page_token": shlex.quote(remote.page_token),
        "wiki_node_token": shlex.quote(remote.wiki_node_token),
    }


def run_template_command(command_template: str, note: Note, markdown_file: Path, remote: RemotePage) -> tuple[RemotePage, str]:
    command = command_template.format(**command_replacements(note, markdown_file, remote))
    completed = subprocess.run(command, shell=True, text=True, capture_output=True, check=False)
    combined_output = "\n".join(part for part in [completed.stdout.strip(), completed.stderr.strip()] if part)
    if completed.returncode != 0:
        raise RuntimeError(f"命令失败，退出码 {completed.returncode}：{combined_output}")
    page = parse_publish_output(completed.stdout, fallback=remote)
    if not page.exists():
        page = RemotePage(
            feishu_url=remote.feishu_url,
            page_token=remote.page_token,
            wiki_node_token=remote.wiki_node_token,
        )
    return page, combined_output


def load_parent_map(path: Path | None) -> dict[str, str]:
    if not path or not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"目录映射必须是 JSON object：{path}")
    mapping: dict[str, str] = {}
    for key, value in data.items():
        if isinstance(value, str):
            mapping[str(key)] = value
        elif isinstance(value, dict):
            token = value.get("parent_node_token") or value.get("node_token") or value.get("token")
            if token:
                mapping[str(key)] = str(token)
    return mapping


def choose_parent_token(note: Note, parent_map: dict[str, str], fallback: str) -> str:
    best_prefix = ""
    best_token = ""
    for prefix, token in parent_map.items():
        normalized = prefix.strip().strip("/")
        if note.rel_path == normalized or note.rel_path.startswith(normalized + "/"):
            if len(normalized) > len(best_prefix):
                best_prefix = normalized
                best_token = token
    return best_token or fallback


def move_to_wiki(
    *,
    page: RemotePage,
    note: Note,
    wiki_space_id: str,
    parent_token: str,
    wiki_move_command: str,
) -> tuple[RemotePage, str]:
    if not wiki_space_id or not page.page_token:
        return page, ""

    if wiki_move_command:
        replacements = command_replacements(note, Path(""), page)
        replacements.update(
            {
                "wiki_space_id": shlex.quote(wiki_space_id),
                "wiki_parent_node_token": shlex.quote(parent_token),
            }
        )
        completed = subprocess.run(wiki_move_command.format(**replacements), shell=True, text=True, capture_output=True, check=False)
    else:
        env = os.environ.copy()
        env.setdefault("OPENCLAW_HOME", str(Path.home() / ".openclaw"))
        command = [
            "lark-cli",
            "wiki",
            "+move",
            "--obj-token",
            page.page_token,
            "--obj-type",
            "docx",
            "--target-space-id",
            wiki_space_id,
        ]
        if parent_token:
            command.extend(["--target-parent-token", parent_token])
        completed = subprocess.run(command, text=True, capture_output=True, check=False, env=env)

    combined_output = "\n".join(part for part in [completed.stdout.strip(), completed.stderr.strip()] if part)
    if completed.returncode != 0:
        raise RuntimeError(f"知识库移动失败，退出码 {completed.returncode}：{combined_output}")
    moved = parse_publish_output(completed.stdout, fallback=page)
    page.wiki_node_token = moved.wiki_node_token or page.wiki_node_token
    return page, combined_output


def append_record(record_file: Path, record: dict[str, Any]) -> None:
    record_file.parent.mkdir(parents=True, exist_ok=True)
    with record_file.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def yaml_value(value: str) -> str:
    return json.dumps(value or "", ensure_ascii=False)


def sync_block(note: Note, status: str, page: RemotePage, error: str = "") -> list[str]:
    return [
        "飞书同步:",
        f"  策略: {yaml_value(note.strategy)}",
        f"  状态: {yaml_value(status)}",
        f"  飞书页面: {yaml_value(page.feishu_url)}",
        f"  页面Token: {yaml_value(page.page_token)}",
        f"  Wiki节点: {yaml_value(page.wiki_node_token)}",
        f"  最近同步: {yaml_value(now_datetime())}",
        f"  内容哈希: {yaml_value(note.digest if status == '已同步' else page.content_hash)}",
        f"  最近错误: {yaml_value(error)}",
    ]


def update_note_front_matter(note: Note, status: str, page: RemotePage, error: str = "") -> None:
    front_lines = note.front_matter.splitlines()
    start = None
    end = None
    for idx, line in enumerate(front_lines):
        if re.match(r"^飞书同步:\s*$", line):
            start = idx
            end = idx + 1
            while end < len(front_lines) and (front_lines[end].startswith((" ", "\t")) or not front_lines[end].strip()):
                end += 1
            break
    new_block = sync_block(note, status, page, error)
    if start is None:
        front_lines.extend(new_block)
    else:
        front_lines[start:end] = new_block
    new_text = "---\n" + "\n".join(front_lines).rstrip() + "\n---\n" + note.body.lstrip("\n")
    note.path.write_text(new_text, encoding="utf-8")


def action_for(note: Note, remote: RemotePage, force: bool) -> str:
    if remote.exists() and remote.content_hash == note.digest and not force:
        return "跳过"
    if remote.exists():
        return "更新"
    return "创建"


def build_record(
    *,
    note: Note,
    action: str,
    status: str,
    remote: RemotePage,
    draft_path: Path | None = None,
    error: str = "",
    output: str = "",
) -> dict[str, Any]:
    return {
        "created_at": now_datetime(),
        "source_file": note.rel_path,
        "title": note.title,
        "strategy": note.strategy,
        "action": action,
        "status": status,
        "content_hash": note.digest,
        "feishu_url": remote.feishu_url,
        "page_token": remote.page_token,
        "wiki_node_token": remote.wiki_node_token,
        "draft_path": repo_relative(draft_path) if draft_path else "",
        "error": error,
        "command_output": output,
    }


def print_plan(notes: list[Note], records: dict[str, RemotePage], force: bool) -> None:
    if not notes:
        print("没有找到需要同步的候选文档。")
        return
    rows = []
    for note in notes:
        remote = remote_from_note(note, records)
        rows.append((action_for(note, remote, force), note.strategy, note.rel_path, note.title))
    width = max(len(row[0]) for row in rows)
    for action, strategy, rel_path, title in rows:
        print(f"{action:<{width}}  {strategy}  {rel_path}  | {title}")


def process_note(
    *,
    note: Note,
    records: dict[str, RemotePage],
    mode: str,
    args: argparse.Namespace,
    parent_map: dict[str, str],
) -> dict[str, Any]:
    remote = remote_from_note(note, records)
    action = action_for(note, remote, args.force)

    if mode == "dry-run":
        return build_record(note=note, action=action, status="预览", remote=remote)

    if action == "跳过":
        if args.write_back:
            update_note_front_matter(note, "已同步", remote)
        return build_record(note=note, action=action, status="已跳过", remote=remote)

    draft_path = write_draft(note, args.draft_dir)
    output_parts: list[str] = []

    if action == "创建":
        if args.search_before_create:
            found = search_existing_page(note, args.search_command, args.search_delay_seconds, args.search_retries)
            if found:
                remote = found
                action = "认领更新"
            elif not args.allow_create_without_search:
                # Search ran successfully and found no exact match, so creation is safe.
                pass
        elif not args.allow_create_without_search:
            raise RuntimeError("未启用创建前搜索；为避免重复文档，请保留搜索或显式加 --allow-create-without-search。")

    if action == "创建":
        remote, output = run_template_command(args.create_command, note, draft_path, remote)
        output_parts.append(output)
        parent_token = choose_parent_token(note, parent_map, args.wiki_move_parent_node_token)
        moved, move_output = move_to_wiki(
            page=remote,
            note=note,
            wiki_space_id=args.wiki_move_space_id,
            parent_token=parent_token,
            wiki_move_command=args.wiki_move_command,
        )
        remote = moved
        if move_output:
            output_parts.append(move_output)
    else:
        if not args.update_command:
            if args.write_back:
                update_note_front_matter(note, "需更新", remote, "缺少更新命令，未创建新文档以避免重复。")
            return build_record(
                note=note,
                action=action,
                status="需更新",
                remote=remote,
                draft_path=draft_path,
                error="缺少更新命令，未创建新文档以避免重复。",
            )
        remote, output = run_template_command(args.update_command, note, draft_path, remote)
        output_parts.append(output)

    remote.content_hash = note.digest
    if args.write_back:
        update_note_front_matter(note, "已同步", remote)
    return build_record(
        note=note,
        action=action,
        status="已同步",
        remote=remote,
        draft_path=draft_path,
        output="\n".join(part for part in output_parts if part),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync selected my-mind Markdown notes into Feishu/Lark.")
    parser.add_argument("--scan-dir", action="append", default=[], help="Directory to scan. Can be repeated. Defaults to curated my-mind dirs.")
    parser.add_argument("--file", action="append", default=[], help="Explicit Markdown file to consider. Can be repeated.")
    parser.add_argument("--strategy", action="append", default=[], help="Allowed 飞书同步.策略 value. Defaults to 精选同步 and 目录页同步.")
    parser.add_argument("--no-default-library-sync", dest="default_library_sync", action="store_false", default=True, help="Do not treat unmarked 20_资料库 Markdown files as sync candidates.")
    parser.add_argument("--record-file", type=Path, default=DEFAULT_RECORD_FILE)
    parser.add_argument("--draft-dir", type=Path, default=DEFAULT_DRAFT_DIR)
    parser.add_argument("--create-command", default=os.environ.get("MY_MIND_FEISHU_SYNC_CREATE_COMMAND", DEFAULT_CREATE_COMMAND))
    parser.add_argument("--update-command", default=os.environ.get("MY_MIND_FEISHU_SYNC_UPDATE_COMMAND", DEFAULT_UPDATE_COMMAND))
    parser.add_argument("--search-command", default=os.environ.get("MY_MIND_FEISHU_SYNC_SEARCH_COMMAND", DEFAULT_SEARCH_COMMAND))
    parser.add_argument("--parent-map-file", type=Path, default=Path(os.environ["MY_MIND_FEISHU_SYNC_PARENT_MAP"]) if os.environ.get("MY_MIND_FEISHU_SYNC_PARENT_MAP") else None)
    parser.add_argument("--wiki-move-space-id", default=os.environ.get("MY_MIND_FEISHU_SYNC_WIKI_SPACE_ID", ""))
    parser.add_argument("--wiki-move-parent-node-token", default=os.environ.get("MY_MIND_FEISHU_SYNC_PARENT_NODE_TOKEN", ""))
    parser.add_argument("--wiki-move-command", default=os.environ.get("MY_MIND_FEISHU_SYNC_WIKI_MOVE_COMMAND", ""))
    parser.add_argument("--limit", type=int, default=0, help="Maximum notes to process. 0 means all.")
    parser.add_argument("--force", action="store_true", help="Update even when content hash is unchanged.")
    parser.add_argument("--allow-sensitive", action="store_true", help="Allow notes marked with sensitive statuses.")
    parser.add_argument("--no-search-before-create", dest="search_before_create", action="store_false", default=True)
    parser.add_argument("--search-delay-seconds", type=float, default=float(os.environ.get("MY_MIND_FEISHU_SYNC_SEARCH_DELAY_SECONDS", "0.8")), help="Delay before each Feishu search to reduce rate limiting.")
    parser.add_argument("--search-retries", type=int, default=int(os.environ.get("MY_MIND_FEISHU_SYNC_SEARCH_RETRIES", "3")), help="Retry count for Feishu search rate limits.")
    parser.add_argument("--allow-create-without-search", action="store_true")
    parser.add_argument("--no-write-back", dest="write_back", action="store_false", default=True)
    parser.add_argument("--record-skips", action="store_true", help="Append JSONL records for unchanged skipped notes.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", help="Preview only. Default mode.")
    group.add_argument("--publish", action="store_true", help="Create or update Feishu pages.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    mode = "publish" if args.publish else "dry-run"
    scan_dirs = [Path(path) for path in args.scan_dir] if args.scan_dir else DEFAULT_SCAN_DIRS
    scan_dirs = [path if path.is_absolute() else ROOT / path for path in scan_dirs]
    explicit_files = [Path(path) if Path(path).is_absolute() else ROOT / path for path in args.file]
    auto_sync_dirs = DEFAULT_AUTO_SYNC_DIRS if args.default_library_sync else []
    strategies = set(args.strategy) if args.strategy else DEFAULT_STRATEGIES

    notes = discover_notes(
        scan_dirs=scan_dirs,
        explicit_files=explicit_files,
        auto_sync_dirs=auto_sync_dirs,
        strategies=strategies,
        allow_sensitive=args.allow_sensitive,
    )
    if args.limit > 0:
        notes = notes[: args.limit]

    records = load_latest_records(args.record_file)
    if mode == "dry-run":
        print_plan(notes, records, args.force)
        return 0

    parent_map = load_parent_map(args.parent_map_file)
    failures = 0
    for note in notes:
        try:
            record = process_note(note=note, records=records, mode=mode, args=args, parent_map=parent_map)
            if record["action"] != "跳过" or args.record_skips:
                append_record(args.record_file, record)
            if record.get("feishu_url") or record.get("page_token"):
                records[note.rel_path] = RemotePage(
                    feishu_url=str(record.get("feishu_url") or ""),
                    page_token=str(record.get("page_token") or ""),
                    wiki_node_token=str(record.get("wiki_node_token") or ""),
                    content_hash=str(record.get("content_hash") or ""),
                    source="record",
                )
            print(f"{record['status']} {record['action']} {note.rel_path}")
        except Exception as exc:  # noqa: BLE001 - CLI should record per-note failures.
            failures += 1
            remote = remote_from_note(note, records)
            error = str(exc)
            if args.write_back:
                update_note_front_matter(note, "失败", remote, error)
            append_record(
                args.record_file,
                build_record(note=note, action="失败", status="失败", remote=remote, error=error),
            )
            print(f"失败 {note.rel_path}: {error}", file=sys.stderr)

    if not notes:
        print("没有找到需要同步的候选文档。")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
