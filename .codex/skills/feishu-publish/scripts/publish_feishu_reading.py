#!/usr/bin/env python3
"""Prepare or publish my-mind frontdesk pushes as Feishu/Lark reading pages."""

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
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[4]
TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_RUN_DIR = ROOT / "85_运行记录"
DEFAULT_DRAFT_DIR = DEFAULT_RUN_DIR / "飞书阅读页"
DEFAULT_RECORD_FILE = DEFAULT_RUN_DIR / "飞书发布记录.jsonl"


@dataclass
class PushItem:
    index: int
    title: str
    source: str = ""
    status: str = ""
    value: str = ""
    action: str = ""
    distill_direction: str = ""
    source_file: str = ""
    original_url: str = ""
    share_url: str = ""
    transcript_url: str = ""
    summary: str = ""
    excerpt: str = ""
    questions: str = ""
    quality: str = ""


def now_datetime() -> str:
    return dt.datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %z")


def now_filename() -> str:
    return dt.datetime.now(TZ).strftime("%Y-%m-%d-%H%M")


def repo_relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def latest_push(run_dir: Path) -> Path:
    candidates = sorted(run_dir.glob("前台推送-*.md"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not candidates:
        raise FileNotFoundError(f"没有找到前台推送文件：{run_dir}/前台推送-*.md")
    return candidates[0]


def markdown_link_url(text: str) -> str:
    match = re.search(r"\[[^\]]+\]\((https?://[^)]+)\)", text)
    if match:
        return match.group(1).strip()
    match = re.search(r"https?://\S+", text)
    return match.group(0).rstrip("。，,；;)") if match else ""


def strip_backticks(text: str) -> str:
    return text.strip().strip("`").strip()


def section_between(text: str, heading: str, next_heading_level: int = 2) -> str:
    pattern = rf"^{'#' * next_heading_level} {re.escape(heading)}\s*$"
    match = re.search(pattern, text, flags=re.M)
    if not match:
        return ""
    start = match.end()
    next_match = re.search(rf"^{'#' * next_heading_level} ", text[start:], flags=re.M)
    end = start + next_match.start() if next_match else len(text)
    return text[start:end].strip()


def subsection_between(text: str, heading: str) -> str:
    pattern = rf"^#### {re.escape(heading)}\s*$"
    match = re.search(pattern, text, flags=re.M)
    if not match:
        return ""
    start = match.end()
    next_match = re.search(r"^#### ", text[start:], flags=re.M)
    end = start + next_match.start() if next_match else len(text)
    return text[start:end].strip()


def parse_bullets(block: str) -> dict[str, str]:
    bullets: dict[str, str] = {}
    for raw_line in block.splitlines():
        line = raw_line.strip()
        if not line.startswith("- "):
            continue
        match = re.match(r"^- ([^：:]+)[：:]\s*(.*)$", line)
        if match:
            bullets[match.group(1).strip()] = match.group(2).strip()
    return bullets


def parse_items(push_text: str) -> list[PushItem]:
    reading_section = section_between(push_text, "今天最值得读")
    headings = list(re.finditer(r"^###\s+(\d+)\.\s+(.+?)\s*$", reading_section, flags=re.M))
    items: list[PushItem] = []
    for idx, heading in enumerate(headings):
        start = heading.end()
        end = headings[idx + 1].start() if idx + 1 < len(headings) else len(reading_section)
        block = reading_section[start:end].strip()
        bullets = parse_bullets(block)
        item = PushItem(
            index=int(heading.group(1)),
            title=heading.group(2).strip(),
            source=bullets.get("来源", ""),
            status=bullets.get("状态", ""),
            value=bullets.get("为什么值得读", ""),
            action=bullets.get("建议动作", ""),
            distill_direction=bullets.get("建议沉淀方向", ""),
            source_file=strip_backticks(bullets.get("来源文件", "")),
            original_url=markdown_link_url(bullets.get("原文链接", "")),
            share_url=markdown_link_url(bullets.get("分享链接", "")),
            transcript_url=markdown_link_url(bullets.get("转录链接", "")),
            summary=bullets.get("一句话摘要", ""),
            excerpt=subsection_between(block, "内容摘录"),
            questions=subsection_between(block, "阅读时重点"),
            quality=subsection_between(block, "质量提醒"),
        )
        items.append(item)
    return items


def parse_generated_at(push_text: str) -> str:
    match = re.search(r"^- 生成时间：(.+)$", push_text, flags=re.M)
    return match.group(1).strip() if match else ""


def render_feishu_markdown(push_path: Path, push_text: str, title: str, max_items: int = 0) -> tuple[str, list[PushItem]]:
    items = parse_items(push_text)
    if max_items > 0:
        items = items[:max_items]
    generated_at = parse_generated_at(push_text)
    project_progress = section_between(push_text, "项目进度")

    lines = [
        f"# {title}",
        "",
        f"- 生成时间：{now_datetime()}",
        "- 生成来源：Codex / feishu-publish",
        f"- 来源推送：`{repo_relative(push_path)}`",
        f"- 前台推送时间：{generated_at or '未知'}",
        f"- 阅读条目：{len(items)}",
        "",
        "## 阅读方式",
        "",
        "- 在飞书里阅读正文和原文链接。",
        "- 读完后回复 OpenClaw：`序号 已读：你的想法`、`序号 沉淀成提示词`、`序号 跳过` 或 `序号 继续解析`。",
        "- OpenClaw 只记录反馈；Codex 后台再回写和沉淀。",
        "",
        "## 今日待读",
        "",
    ]

    if not items:
        lines.append("暂无需要发布的待读条目。")
    for item in items:
        lines.extend(
            [
                f"### {item.index}. {item.title}",
                "",
                f"- 来源：{item.source or '未知'}",
                f"- 状态：{item.status or '未知'}",
                f"- 来源文件：`{item.source_file or '未知'}`",
            ]
        )
        if item.original_url:
            lines.append(f"- 原文链接：{item.original_url}")
        if item.share_url and item.share_url != item.original_url:
            lines.append(f"- 分享链接：{item.share_url}")
        if item.transcript_url and item.transcript_url not in {item.original_url, item.share_url}:
            lines.append(f"- 转录链接：{item.transcript_url}")
        if item.value:
            lines.append(f"- 为什么值得读：{item.value}")
        if item.action:
            lines.append(f"- 建议动作：{item.action}")
        if item.distill_direction:
            lines.append(f"- 建议沉淀方向：{item.distill_direction}")
        if item.summary:
            lines.extend(["", "#### 一句话摘要", "", item.summary])
        if item.excerpt:
            lines.extend(["", "#### 内容摘录", "", item.excerpt])
        if item.questions:
            lines.extend(["", "#### 阅读时重点", "", item.questions])
        if item.quality:
            lines.extend(["", "#### 质量提醒", "", item.quality])
        lines.extend(
            [
                "",
                "#### 回复给 OpenClaw",
                "",
                f"- `{item.index} 已读：你的想法`",
                f"- `{item.index} 沉淀成提示词`",
                f"- `{item.index} 跳过`",
                f"- `{item.index} 继续解析`",
                "",
            ]
        )

    if project_progress:
        lines.extend(["## 项目进度", "", project_progress, ""])
    lines.extend(
        [
            "## 处理边界",
            "",
            "- 本页是本地 `my-mind` 的手机阅读镜像，不是长期知识源。",
            "- 原始状态、回链和沉淀记录仍以本地 Markdown 为准。",
            "- 未经用户确认，不自动晋升资料库、原子笔记、洞察或提示词库。",
        ]
    )
    return "\n".join(lines).rstrip() + "\n", items


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_records(record_file: Path) -> list[dict[str, object]]:
    if not record_file.exists():
        return []
    records: list[dict[str, object]] = []
    for line in record_file.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def find_existing_publish(records: list[dict[str, object]], digest: str) -> dict[str, object] | None:
    for record in reversed(records):
        if record.get("content_hash") == digest and record.get("status") == "已发布" and record.get("feishu_url"):
            return record
    return None


def unique_path(directory: Path, filename: str) -> Path:
    path = directory / filename
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    index = 2
    while True:
        candidate = directory / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def parse_publish_output(output: str) -> tuple[str, str]:
    output = output.strip()
    if not output:
        return "", ""
    try:
        data = json.loads(output)
        if isinstance(data, dict):
            url = str(data.get("feishu_url") or data.get("url") or data.get("link") or "")
            token = str(data.get("page_token") or data.get("document_id") or data.get("node_token") or data.get("token") or "")
            document = data.get("data", {}).get("document", {}) if isinstance(data.get("data"), dict) else {}
            if isinstance(document, dict):
                url = url or str(document.get("url") or "")
                token = token or str(document.get("document_id") or "")
            return url, token
    except json.JSONDecodeError:
        pass
    match = re.search(r"https?://\S+", output)
    return (match.group(0).rstrip("。，,；;)") if match else "", "")


def parse_json_payload(output: str) -> dict[str, object] | None:
    payload = output.strip()
    if not payload:
        return None
    for candidate in [payload, payload[payload.find("{") :] if "{" in payload else ""]:
        if not candidate:
            continue
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        return data if isinstance(data, dict) else None
    return None


def run_publish_command(command_template: str, markdown_file: Path, title: str, push_file: Path, record_file: Path, digest: str) -> tuple[str, str, str]:
    try:
        markdown_file_rel = markdown_file.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        markdown_file_rel = str(markdown_file)
    replacements = {
        "markdown_file": shlex.quote(str(markdown_file)),
        "markdown_file_rel": shlex.quote(markdown_file_rel),
        "title": shlex.quote(title),
        "source_push": shlex.quote(str(push_file)),
        "record_file": shlex.quote(str(record_file)),
        "content_hash": shlex.quote(digest),
    }
    command = command_template.format(**replacements)
    completed = subprocess.run(command, shell=True, text=True, capture_output=True, check=False)
    combined_output = "\n".join(part for part in [completed.stdout.strip(), completed.stderr.strip()] if part)
    if completed.returncode != 0:
        raise RuntimeError(f"发布命令失败，退出码 {completed.returncode}：{combined_output}")
    data = parse_json_payload(completed.stdout)
    if isinstance(data, dict) and data.get("ok") is False:
        error = data.get("error") if isinstance(data.get("error"), dict) else {}
        message = str(error.get("message") or "发布命令返回 ok=false")
        hint = str(error.get("hint") or "").strip()
        raise RuntimeError(f"{message}{'；' + hint if hint else ''}")
    url, token = parse_publish_output(completed.stdout)
    return url, token, combined_output


def run_wiki_move_command(
    *,
    page_token: str,
    title: str,
    feishu_url: str,
    wiki_space_id: str,
    wiki_parent_node_token: str,
    wiki_move_command: str,
) -> tuple[str, str]:
    if not page_token:
        raise RuntimeError("缺少 page_token，无法移动到飞书知识库。")

    if wiki_move_command:
        replacements = {
            "page_token": shlex.quote(page_token),
            "title": shlex.quote(title),
            "feishu_url": shlex.quote(feishu_url),
            "wiki_space_id": shlex.quote(wiki_space_id),
            "wiki_parent_node_token": shlex.quote(wiki_parent_node_token),
        }
        completed = subprocess.run(wiki_move_command.format(**replacements), shell=True, text=True, capture_output=True, check=False)
    else:
        if not wiki_space_id:
            raise RuntimeError("缺少 --wiki-move-space-id 或 MY_MIND_FEISHU_WIKI_SPACE_ID。")
        env = os.environ.copy()
        env.setdefault("OPENCLAW_HOME", str(Path.home() / ".openclaw"))
        command = [
            "lark-cli",
            "wiki",
            "+move",
            "--obj-token",
            page_token,
            "--obj-type",
            "docx",
            "--target-space-id",
            wiki_space_id,
        ]
        if wiki_parent_node_token:
            command.extend(["--target-parent-token", wiki_parent_node_token])
        completed = subprocess.run(command, text=True, capture_output=True, check=False, env=env)

    combined_output = "\n".join(part for part in [completed.stdout.strip(), completed.stderr.strip()] if part)
    if completed.returncode != 0:
        raise RuntimeError(f"知识库移动失败，退出码 {completed.returncode}：{combined_output}")

    data = parse_json_payload(completed.stdout)
    if isinstance(data, dict) and data.get("ok") is False:
        error = data.get("error") if isinstance(data.get("error"), dict) else {}
        message = str(error.get("message") or "知识库移动返回 ok=false")
        hint = str(error.get("hint") or "").strip()
        raise RuntimeError(f"{message}{'；' + hint if hint else ''}")

    result = data.get("data", {}) if isinstance(data, dict) else {}
    node_token = str(result.get("node_token") or result.get("wiki_token") or "") if isinstance(result, dict) else ""
    return node_token, combined_output


def append_record(record_file: Path, record: dict[str, object]) -> None:
    record_file.parent.mkdir(parents=True, exist_ok=True)
    with record_file.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def compact_items(items: list[PushItem]) -> list[dict[str, object]]:
    return [
        {
            "index": item.index,
            "title": item.title,
            "source_file": item.source_file,
            "original_url": item.original_url,
            "share_url": item.share_url,
            "summary": item.summary,
        }
        for item in items
    ]


def build_record(
    *,
    status: str,
    title: str,
    push_path: Path,
    draft_path: Path,
    digest: str,
    items: list[PushItem],
    mode: str,
    feishu_url: str = "",
    page_token: str = "",
    wiki_space_id: str = "",
    wiki_node_token: str = "",
    wiki_parent_node_token: str = "",
    wiki_move_output: str = "",
    wiki_move_error: str = "",
    error: str = "",
    command_output: str = "",
) -> dict[str, object]:
    return {
        "created_at": now_datetime(),
        "status": status,
        "publish_mode": mode,
        "title": title,
        "source_push": repo_relative(push_path),
        "draft_path": repo_relative(draft_path),
        "feishu_url": feishu_url,
        "page_token": page_token,
        "wiki_space_id": wiki_space_id,
        "wiki_node_token": wiki_node_token,
        "wiki_parent_node_token": wiki_parent_node_token,
        "content_hash": digest,
        "item_count": len(items),
        "items": compact_items(items),
        "error": error,
        "command_output": command_output,
        "wiki_move_output": wiki_move_output,
        "wiki_move_error": wiki_move_error,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare or publish my-mind frontdesk push notes as Feishu/Lark reading pages.")
    parser.add_argument("--push-file", default="", help="Frontdesk push markdown path. Defaults to latest 85_运行记录/前台推送-*.md.")
    parser.add_argument("--run-dir", default=str(DEFAULT_RUN_DIR), help="Run-record directory.")
    parser.add_argument("--draft-dir", default=str(DEFAULT_DRAFT_DIR), help="Directory for local Feishu reading-page drafts.")
    parser.add_argument("--record-file", default=str(DEFAULT_RECORD_FILE), help="JSONL publish record path.")
    parser.add_argument("--title", default="", help="Feishu page title. Defaults to my-mind 今日待读 YYYY-MM-DD.")
    parser.add_argument("--max-items", type=int, default=0, help="Maximum items to publish from the push. Use 0 for all.")
    parser.add_argument(
        "--publish-command",
        default=os.environ.get("MY_MIND_FEISHU_PUBLISH_COMMAND", ""),
        help="Shell command template used with --publish. Use {markdown_file}, {markdown_file_rel}, {title}, {source_push}, {record_file}, and {content_hash}.",
    )
    parser.add_argument("--wiki-move-space-id", default=os.environ.get("MY_MIND_FEISHU_WIKI_SPACE_ID", ""), help="Move the published docx into this Feishu wiki space after publishing.")
    parser.add_argument("--wiki-move-parent-node-token", default=os.environ.get("MY_MIND_FEISHU_WIKI_PARENT_NODE_TOKEN", ""), help="Optional parent wiki node token for --wiki-move-space-id.")
    parser.add_argument(
        "--wiki-move-command",
        default=os.environ.get("MY_MIND_FEISHU_WIKI_MOVE_COMMAND", ""),
        help="Optional shell command template for moving a published docx into wiki. Use {page_token}, {title}, {feishu_url}, {wiki_space_id}, and {wiki_parent_node_token}.",
    )
    parser.add_argument("--force", action="store_true", help="Publish even when an identical content hash was already published.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Preview without writing. Default mode.")
    mode.add_argument("--write-local", action="store_true", help="Write local reading-page draft and append a draft publish record.")
    mode.add_argument("--publish", action="store_true", help="Write draft, run publish command, and append publish record.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    run_dir = Path(args.run_dir)
    draft_dir = Path(args.draft_dir)
    record_file = Path(args.record_file)
    if not run_dir.is_absolute():
        run_dir = ROOT / run_dir
    if not draft_dir.is_absolute():
        draft_dir = ROOT / draft_dir
    if not record_file.is_absolute():
        record_file = ROOT / record_file
    push_path = Path(args.push_file) if args.push_file else latest_push(run_dir)
    if not push_path.is_absolute():
        push_path = ROOT / push_path

    title = args.title or f"my-mind 今日待读 {dt.datetime.now(TZ).strftime('%Y-%m-%d')}"
    push_text = read_text(push_path)
    rendered, items = render_feishu_markdown(push_path, push_text, title, max(args.max_items, 0))
    digest = content_hash(rendered)
    draft_path = unique_path(draft_dir, f"飞书阅读页-{now_filename()}.md")

    if args.publish:
        existing = find_existing_publish(load_records(record_file), digest)
        if existing and not args.force:
            print(json.dumps(existing, ensure_ascii=False, indent=2))
            return 0

    if not args.write_local and not args.publish:
        print(rendered, end="")
        return 0

    draft_dir.mkdir(parents=True, exist_ok=True)
    draft_path.write_text(rendered, encoding="utf-8")

    if args.write_local:
        record = build_record(status="草稿已生成", title=title, push_path=push_path, draft_path=draft_path, digest=digest, items=items, mode="local")
        append_record(record_file, record)
        print(repo_relative(draft_path))
        print(repo_relative(record_file))
        return 0

    if not args.publish_command:
        record = build_record(
            status="发布失败",
            title=title,
            push_path=push_path,
            draft_path=draft_path,
            digest=digest,
            items=items,
            mode="command",
            error="缺少 --publish-command 或 MY_MIND_FEISHU_PUBLISH_COMMAND。",
        )
        append_record(record_file, record)
        print("错误：缺少 --publish-command 或 MY_MIND_FEISHU_PUBLISH_COMMAND。", file=sys.stderr)
        return 2

    try:
        feishu_url, page_token, command_output = run_publish_command(args.publish_command, draft_path, title, push_path, record_file, digest)
        wiki_node_token = ""
        wiki_move_output = ""
        wiki_move_error = ""
        if args.wiki_move_space_id or args.wiki_move_command:
            try:
                wiki_node_token, wiki_move_output = run_wiki_move_command(
                    page_token=page_token,
                    title=title,
                    feishu_url=feishu_url,
                    wiki_space_id=args.wiki_move_space_id,
                    wiki_parent_node_token=args.wiki_move_parent_node_token,
                    wiki_move_command=args.wiki_move_command,
                )
            except Exception as exc:  # noqa: BLE001
                wiki_move_error = str(exc)
        status = "已发布" if feishu_url else "已发布待补链接"
        if wiki_move_error:
            status = "已发布待移动知识库"
        record = build_record(
            status=status,
            title=title,
            push_path=push_path,
            draft_path=draft_path,
            digest=digest,
            items=items,
            mode="command",
            feishu_url=feishu_url,
            page_token=page_token,
            wiki_space_id=args.wiki_move_space_id,
            wiki_node_token=wiki_node_token,
            wiki_parent_node_token=args.wiki_move_parent_node_token,
            wiki_move_output=wiki_move_output,
            wiki_move_error=wiki_move_error,
            command_output=command_output,
        )
        append_record(record_file, record)
        print(feishu_url or repo_relative(draft_path))
        return 0
    except Exception as exc:  # noqa: BLE001
        record = build_record(
            status="发布失败",
            title=title,
            push_path=push_path,
            draft_path=draft_path,
            digest=digest,
            items=items,
            mode="command",
            error=str(exc),
        )
        append_record(record_file, record)
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
