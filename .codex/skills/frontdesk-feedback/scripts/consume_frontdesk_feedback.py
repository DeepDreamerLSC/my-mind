#!/usr/bin/env python3
"""Consume OpenClaw frontdesk feedback and write it back to source inbox notes."""

from __future__ import annotations

import argparse
import datetime as dt
import difflib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[4]
TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_RUN_DIR = ROOT / "85_运行记录"
DEFAULT_QUEUE = DEFAULT_RUN_DIR / "前台反馈队列.jsonl"
DEFAULT_PUSH_STATE = DEFAULT_RUN_DIR / "前台推送状态.json"
DEFAULT_CONFIRM_QUEUE = DEFAULT_RUN_DIR / "待确认事项.jsonl"
CONFIRM_QUEUE_MD = ROOT / "05_流转区/50_待确认/待确认队列.md"
DISTILL_SCRIPT = ROOT / ".codex" / "skills" / "inbox-distill" / "scripts" / "distill_inbox_note.py"
FEISHU_SYNC_SCRIPT = ROOT / ".codex" / "skills" / "feishu-sync" / "scripts" / "sync_selected_notes.py"
TERMINAL_STATUSES = {"已处理", "已晋升", "可丢弃", "已归档"}
CONFIRM_ACTIONS = {"确认转正", "继续核验", "调整分类"}
PROMOTION_ROOTS = [
    ROOT / "20_资料库",
    ROOT / "30_原子笔记",
    ROOT / "65_洞察",
    ROOT / "75_提示词库",
]
INBOX_ROOT = ROOT / "00_收件箱"


def now_datetime() -> str:
    return dt.datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %z")


def now_date() -> str:
    return dt.datetime.now(TZ).strftime("%Y-%m-%d")


def now_filename() -> str:
    return dt.datetime.now(TZ).strftime("%Y-%m-%d-%H%M")


def repo_rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for index, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            records.append({"处理状态": "解析失败", "处理结果": f"第 {index} 行不是合法 JSON", "原始行": line})
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(record, ensure_ascii=False, sort_keys=True) for record in records) + "\n", encoding="utf-8")


def parse_frontmatter(text: str) -> dict[str, Any]:
    if not text.startswith("---\n"):
        return {}
    lines = text.splitlines()
    end_index = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            end_index = index
            break
    if end_index is None:
        return {}
    data: dict[str, Any] = {}
    for raw_line in lines[1:end_index]:
        if ":" not in raw_line or raw_line.startswith("  "):
            continue
        key, value = raw_line.split(":", 1)
        data[key.strip()] = value.strip().strip('"')
    return data


def split_frontmatter(text: str) -> tuple[list[str], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return [], text
    end_index = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            end_index = index
            break
    if end_index is None:
        return [], text
    return lines[1:end_index], "\n".join(lines[end_index + 1 :]).lstrip("\n")


def yaml_string(value: str) -> str:
    return json.dumps(value or "", ensure_ascii=False)


def replace_frontmatter_entry(text: str, key: str, replacement_lines: list[str]) -> str:
    front_lines, body = split_frontmatter(text)
    if not front_lines:
        return text
    start = None
    end = None
    for index, line in enumerate(front_lines):
        if re.match(rf"^{re.escape(key)}:\s*", line):
            start = index
            end = index + 1
            while end < len(front_lines) and (front_lines[end].startswith((" ", "\t")) or not front_lines[end].strip()):
                end += 1
            break
    if start is None:
        insert_at = len(front_lines)
        front_lines[insert_at:insert_at] = replacement_lines
    else:
        front_lines[start:end] = replacement_lines
    return "---\n" + "\n".join(front_lines).rstrip() + "\n---\n" + body.lstrip("\n")


def set_frontmatter_scalar(text: str, key: str, value: str) -> str:
    return replace_frontmatter_entry(text, key, [f"{key}: {value}"])


def set_feishu_sync_pending(text: str) -> str:
    block = [
        "飞书同步:",
        f"  策略: {yaml_string('精选同步')}",
        f"  状态: {yaml_string('待同步')}",
        f"  飞书页面: {yaml_string('')}",
        f"  页面Token: {yaml_string('')}",
        f"  Wiki节点: {yaml_string('')}",
        f"  最近同步: {yaml_string('')}",
        f"  内容哈希: {yaml_string('')}",
        f"  最近错误: {yaml_string('')}",
    ]
    return replace_frontmatter_entry(text, "飞书同步", block)


def normalize_match_text(value: str) -> str:
    return re.sub(r"[\W_]+", "", str(value or "").casefold())


def markdown_source_file(line: str) -> str:
    match = re.search(r"来源文件：`([^`]+)`", line)
    if match:
        return match.group(1).strip()
    match = re.search(r"来源文件：\[[^\]]+\]\(([^)]+)\)", line)
    if not match:
        return ""
    raw = match.group(1).strip()
    try:
        from urllib.parse import unquote

        raw = unquote(raw)
    except Exception:  # noqa: BLE001
        pass
    return raw.lstrip("./")


def push_file_candidates(record: dict[str, Any]) -> list[dict[str, str]]:
    push_file = str(record.get("推送文件") or "").strip()
    if not push_file:
        return []
    path = Path(push_file)
    if not path.is_absolute():
        path = ROOT / path
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8", errors="ignore")
    headings = list(re.finditer(r"^###\s+\d+\.\s+(.+?)\s*$", text, flags=re.M))
    candidates: list[dict[str, str]] = []
    for index, heading in enumerate(headings):
        start = heading.end()
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        block = text[start:end]
        source = ""
        for line in block.splitlines():
            source = markdown_source_file(line)
            if source:
                break
        if source:
            candidates.append({"title": heading.group(1).strip(), "source": source, "evidence": repo_rel(path), "boost": "120"})
    return candidates


def publish_record_candidates(record: dict[str, Any]) -> list[dict[str, str]]:
    path = DEFAULT_RUN_DIR / "飞书发布记录.jsonl"
    records = read_jsonl(path)
    source_push = str(record.get("推送文件") or "").strip()
    candidates: list[dict[str, str]] = []
    for publish_record in records:
        if source_push and str(publish_record.get("source_push") or "") != source_push:
            continue
        source = str(publish_record.get("source_file") or "").strip()
        title = str(publish_record.get("title") or "").strip()
        if source:
            candidates.append({"title": title, "source": source, "evidence": repo_rel(path), "boost": "160"})
        items = publish_record.get("items")
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                item_source = str(item.get("source_file") or "").strip()
                item_title = str(item.get("title") or "").strip()
                if item_source:
                    candidates.append({"title": item_title, "source": item_source, "evidence": repo_rel(path), "boost": "180"})
    return candidates


def note_candidates() -> list[dict[str, str]]:
    roots = [INBOX_ROOT, *PROMOTION_ROOTS]
    candidates: list[dict[str, str]] = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*.md"):
            if path.name == "目录说明.md":
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            meta = parse_frontmatter(text)
            title = str(meta.get("标题") or path.stem).strip()
            source = str(meta.get("来源文件") or "").strip()
            boost = 40
            if root in PROMOTION_ROOTS:
                boost = 260
                if (
                    str(meta.get("处理状态") or "") in {"正式", "已处理"}
                    and str(meta.get("吸收状态") or "") in {"已吸收", "已应用"}
                    and str(meta.get("转正门禁") or "") == "通过"
                ):
                    boost = 520
            candidates.append(
                {
                    "title": title,
                    "source": source or repo_rel(path),
                    "evidence": repo_rel(path),
                    "boost": str(boost),
                }
            )
    return candidates


def infer_source_file(record: dict[str, Any]) -> tuple[str, str]:
    query = "\n".join(
        str(record.get(key) or "")
        for key in ["目标标题", "内容", "原始回复", "目标类型"]
    )
    normalized_query = normalize_match_text(query)
    if not normalized_query:
        return "", ""
    seen: set[tuple[str, str]] = set()
    scored: list[tuple[int, str, str]] = []
    for candidate in [*push_file_candidates(record), *publish_record_candidates(record), *note_candidates()]:
        source = candidate.get("source", "").strip()
        title = candidate.get("title", "").strip()
        if not source or not title:
            continue
        key = (source, title)
        if key in seen:
            continue
        seen.add(key)
        normalized_title = normalize_match_text(title)
        if not normalized_title:
            continue
        score = int(candidate.get("boost") or "0")
        if normalized_title in normalized_query:
            score += 1200 + min(len(normalized_title), 160)
        elif normalized_query in normalized_title:
            score += 800 + min(len(normalized_query), 120)
        score += int(difflib.SequenceMatcher(None, normalized_query, normalized_title).ratio() * 320)
        if score >= 520:
            evidence = candidate.get("evidence", "")
            scored.append((score, source, f"由 `{evidence}` 反查，匹配标题：{title}"))
    if not scored:
        return "", ""
    scored.sort(reverse=True)
    _, source, reason = scored[0]
    return source, reason


def is_promotion_feedback(record: dict[str, Any]) -> bool:
    text = str(record.get("内容") or record.get("原始回复") or "")
    return any(keyword in text for keyword in ["晋升完成", "确认晋升", "转为长期知识", "转正", "正式提示词", "library 知识"])


def set_scalar_field(text: str, key: str, value: str) -> str:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return text
    prefix = f"{key}:"
    for index, line in enumerate(lines):
        if line.startswith(prefix):
            lines[index] = f"{key}: {value}"
            return "\n".join(lines).rstrip() + "\n"
    insert_at = 1
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            insert_at = index
            break
    lines.insert(insert_at, f"{key}: {value}")
    return "\n".join(lines).rstrip() + "\n"


def append_to_section(text: str, heading: str, entry: str) -> str:
    if entry in text:
        return text
    pattern = re.compile(rf"(^##\s+{re.escape(heading)}\s*\n)", flags=re.M)
    match = pattern.search(text)
    if match:
        insert_at = match.end()
        return text[:insert_at] + "\n" + entry.rstrip() + "\n" + text[insert_at:].lstrip("\n")
    insert_before = re.search(r"^## 原始链接\s*$", text, flags=re.M)
    block = f"\n## {heading}\n\n{entry.rstrip()}\n"
    if insert_before:
        return text[: insert_before.start()].rstrip() + "\n" + block + "\n" + text[insert_before.start() :]
    return text.rstrip() + block


def confirmation_records() -> list[dict[str, Any]]:
    return read_jsonl(DEFAULT_CONFIRM_QUEUE)


def first_candidate_from_record(record: dict[str, Any]) -> str:
    direct = str(record.get("候选文件") or "").strip()
    if direct:
        return direct
    values = record.get("候选文件列表")
    if isinstance(values, list):
        for value in values:
            if str(value).strip():
                return str(value).strip()
    values = record.get("已有候选")
    if isinstance(values, list):
        for value in values:
            if str(value).strip():
                return str(value).strip()
    return ""


def find_confirmation_target(record: dict[str, Any]) -> dict[str, Any]:
    target_no = str(record.get("目标序号") or "").strip()
    source_file = str(record.get("来源文件") or "").strip()
    candidate_file = first_candidate_from_record(record)
    title = normalize_match_text(str(record.get("目标标题") or ""))
    for candidate in confirmation_records():
        if target_no and str(candidate.get("序号") or "") == target_no:
            return candidate
        if candidate_file and candidate_file in {
            str(candidate.get("候选文件") or ""),
            *[str(item) for item in candidate.get("已有候选", []) if isinstance(candidate.get("已有候选"), list)],
        }:
            return candidate
        if source_file and source_file == str(candidate.get("来源文件") or ""):
            return candidate
        if title and title == normalize_match_text(str(candidate.get("标题") or "")):
            return candidate
    return {}


def candidate_path_for_confirmation(record: dict[str, Any], confirm: dict[str, Any]) -> Path | None:
    candidate_file = first_candidate_from_record(record) or first_candidate_from_record(confirm)
    if not candidate_file:
        return None
    path = Path(candidate_file)
    return path if path.is_absolute() else ROOT / path


def source_path_for_confirmation(record: dict[str, Any], confirm: dict[str, Any]) -> Path | None:
    source_file = str(record.get("来源文件") or confirm.get("来源文件") or "").strip()
    if not source_file:
        return None
    path = Path(source_file)
    return path if path.is_absolute() else ROOT / path


def promotion_gate(candidate_path: Path, source_path: Path | None) -> tuple[bool, list[str]]:
    problems: list[str] = []
    if not candidate_path.exists():
        return False, [f"候选文件不存在：{repo_rel(candidate_path)}"]
    text = candidate_path.read_text(encoding="utf-8", errors="ignore")
    meta = parse_frontmatter(text)
    body = re.sub(r"^---.*?---", "", text, flags=re.S).strip() if text.startswith("---\n") else text
    if len(re.sub(r"\s+", "", body)) < 260:
        problems.append("候选正文太短，尚不足以作为长期知识。")
    if not str(meta.get("来源文件") or source_path or "").strip():
        problems.append("候选缺少可追溯来源文件。")
    if "待补充" in body or "暂无" in body:
        problems.append("候选正文仍包含待补充或暂无占位。")
    if re.search(r"扣袋子|扣得克斯|口袋子|克劳德代码", body, flags=re.I):
        problems.append("候选正文仍有明显术语误识别。")
    if source_path and source_path.exists():
        source_meta = parse_frontmatter(source_path.read_text(encoding="utf-8", errors="ignore"))
        if str(source_meta.get("内容质量") or "") == "需继续解析":
            problems.append("来源内容质量仍为需继续解析。")
        if str(source_meta.get("解析状态") or "") == "解析失败":
            problems.append("来源解析状态仍为解析失败。")
    return not problems, problems


def promote_candidate(candidate_path: Path, source_path: Path | None, record: dict[str, Any]) -> None:
    text = candidate_path.read_text(encoding="utf-8", errors="ignore")
    text = set_frontmatter_scalar(text, "处理状态", "已处理")
    text = set_frontmatter_scalar(text, "吸收状态", "已吸收")
    text = set_frontmatter_scalar(text, "可信状态", "已核验")
    text = set_frontmatter_scalar(text, "转正门禁", "通过")
    text = set_frontmatter_scalar(text, "需要人工确认", "否")
    text = set_feishu_sync_pending(text)
    entry = f"- {now_datetime()}：用户通过 OpenClaw/Codex 确认转正，`frontdesk-feedback` 门禁通过。原始回复：{record.get('原始回复') or ''}"
    text = append_to_section(text, "转正记录", entry)
    candidate_path.write_text(text, encoding="utf-8")

    if source_path and source_path.exists():
        source_text = source_path.read_text(encoding="utf-8", errors="ignore")
        source_text = set_scalar_field(source_text, "处理状态", "已处理")
        source_text = set_scalar_field(source_text, "入库状态", "已晋升")
        source_text = set_scalar_field(source_text, "阅读状态", "已读")
        source_entry = (
            f"- {now_datetime()}：用户确认候选转正，已晋升长期知识："
            f"[{candidate_path.stem}]({repo_rel(candidate_path).replace(' ', '%20')})。"
        )
        source_text = append_to_section(source_text, "沉淀记录", source_entry)
        source_path.write_text(source_text, encoding="utf-8")


def mark_candidate_needs_verification(candidate_path: Path, problems: list[str], record: dict[str, Any], *, write: bool) -> None:
    if not candidate_path.exists():
        return
    text = candidate_path.read_text(encoding="utf-8", errors="ignore")
    text = set_frontmatter_scalar(text, "吸收状态", "待确认")
    text = set_frontmatter_scalar(text, "可信状态", "待核验")
    entry = f"- {now_datetime()}：转正门禁未通过或用户要求继续核验。问题：{'；'.join(problems)}。原始回复：{record.get('原始回复') or ''}"
    text = append_to_section(text, "转正门禁记录", entry)
    if write:
        candidate_path.write_text(text, encoding="utf-8")


def update_confirmation_status(
    *,
    feedback_record: dict[str, Any],
    confirm: dict[str, Any],
    status: str,
    result: str,
    candidate_path: Path | None,
) -> None:
    records = confirmation_records()
    target_no = str(feedback_record.get("目标序号") or confirm.get("序号") or "")
    candidate_rel = repo_rel(candidate_path) if candidate_path else first_candidate_from_record(feedback_record) or first_candidate_from_record(confirm)
    source_file = str(feedback_record.get("来源文件") or confirm.get("来源文件") or "")
    changed = False
    for item in records:
        match = False
        if target_no and str(item.get("序号") or "") == target_no:
            match = True
        elif candidate_rel and candidate_rel in {str(item.get("候选文件") or ""), *[str(v) for v in item.get("已有候选", []) if isinstance(item.get("已有候选"), list)]}:
            match = True
        elif source_file and source_file == str(item.get("来源文件") or ""):
            match = True
        if not match:
            continue
        item["处理状态"] = status
        item["确认处理时间"] = now_datetime()
        item["确认处理结果"] = result
        if candidate_rel:
            item["候选文件"] = candidate_rel
        changed = True
        break
    if changed:
        write_jsonl(DEFAULT_CONFIRM_QUEUE, records)
        refresh_confirm_queue_markdown(records)


def refresh_confirm_queue_markdown(records: list[dict[str, Any]]) -> None:
    pending = [record for record in records if str(record.get("处理状态") or "") == "待确认"]
    lines = [
        "# 待确认队列",
        "",
        "这里承接 `pending-distill` 自动消费后仍需要用户判断的事项。",
        "",
        "## 使用边界",
        "",
        "- 这是确认视图，不是长期知识正文。",
        "- OpenClaw 可以读取本页生成“今日待确认”前台消息。",
        "- 用户确认后仍由 Codex 后台执行转正门禁、回写状态和飞书同步。",
        "",
        "## 总览",
        "",
        f"- 更新时间：{now_datetime()}",
        f"- 待确认数量：{len(pending)}",
        "",
        "## 队列",
        "",
    ]
    if not pending:
        lines.append("当前没有待确认事项。")
    for index, item in enumerate(pending, start=1):
        candidate = str(item.get("候选文件") or "")
        existing = item.get("已有候选")
        if not candidate and isinstance(existing, list) and existing:
            candidate = "、".join(f"`{value}`" for value in existing)
        elif candidate:
            candidate = f"`{candidate}`"
        else:
            candidate = "暂无"
        questions = item.get("确认问题") if isinstance(item.get("确认问题"), list) else []
        options = item.get("可回复") if isinstance(item.get("可回复"), list) else []
        lines.extend(
            [
                f"### {index}. {item.get('标题') or '未命名'}",
                "",
                f"- 状态：{item.get('状态') or '待确认'}",
                f"- 优先级：{item.get('优先级') or '未知'}",
                f"- 类型：{item.get('类型') or item.get('类型代码') or '待判断'}",
                f"- 来源文件：`{item.get('来源文件') or ''}`",
                f"- 候选文件：{candidate}",
                f"- 原因：{item.get('原因') or ''}",
                f"- 可回复：{' / '.join(str(option) for option in options) if options else '确认转正 / 继续核验 / 调整分类 / 跳过'}",
                "- 需要确认：",
            ]
        )
        lines.extend(f"  - {question}" for question in questions or [f"请确认《{item.get('标题') or '本条'}》下一步。"])
        lines.append("")
    CONFIRM_QUEUE_MD.parent.mkdir(parents=True, exist_ok=True)
    CONFIRM_QUEUE_MD.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def maybe_sync_feishu(candidate_path: Path, *, write: bool, sync_feishu: bool) -> str:
    if not sync_feishu:
        return "已开启飞书精选同步标记，等待后续 feishu-sync。"
    command = [sys.executable, str(FEISHU_SYNC_SCRIPT), "--publish", "--file", repo_rel(candidate_path)]
    if not write:
        return "dry-run：将调用 feishu-sync 发布候选文档。"
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
    output = (result.stdout + result.stderr).strip()
    if result.returncode != 0:
        return "飞书同步失败：" + output[-800:]
    return "飞书同步完成：" + output[-800:]


def process_confirmation_record(
    record: dict[str, Any],
    *,
    write: bool,
    sync_feishu: bool,
) -> tuple[dict[str, Any], str]:
    action = str(record.get("动作") or "")
    confirm = find_confirmation_target(record)
    candidate_path = candidate_path_for_confirmation(record, confirm)
    source_path = source_path_for_confirmation(record, confirm)
    if source_path and not record.get("来源文件"):
        record["来源文件"] = repo_rel(source_path)
    if candidate_path and not record.get("候选文件"):
        record["候选文件"] = repo_rel(candidate_path)
    if not candidate_path:
        record["处理状态"] = "处理失败"
        record["处理结果"] = "缺少候选文件，无法处理待确认反馈"
        record["处理时间"] = now_datetime()
        return record, "失败：缺少候选文件"

    if action == "确认转正":
        ok, problems = promotion_gate(candidate_path, source_path)
        if not ok:
            mark_candidate_needs_verification(candidate_path, problems, record, write=write)
            record["处理状态"] = "待确认"
            record["处理结果"] = "转正门禁未通过：" + "；".join(problems)
            record["处理时间"] = now_datetime()
            if write:
                update_confirmation_status(
                    feedback_record=record,
                    confirm=confirm,
                    status="待确认",
                    result=record["处理结果"],
                    candidate_path=candidate_path,
                )
            return record, "待确认：转正门禁未通过"
        if write:
            promote_candidate(candidate_path, source_path, record)
        sync_result = maybe_sync_feishu(candidate_path, write=write, sync_feishu=sync_feishu)
        record["处理状态"] = "已晋升"
        record["处理结果"] = f"候选已通过转正门禁。{sync_result}"
        record["处理时间"] = now_datetime()
        if write:
            update_confirmation_status(
                feedback_record=record,
                confirm=confirm,
                status="已晋升",
                result=record["处理结果"],
                candidate_path=candidate_path,
            )
        return record, f"完成：确认转正 -> {repo_rel(candidate_path)}"

    if action == "继续核验":
        problems = [str(record.get("内容") or "用户要求继续核验。")]
        mark_candidate_needs_verification(candidate_path, problems, record, write=write)
        record["处理状态"] = "已处理"
        record["处理结果"] = "已记录继续核验请求"
        record["处理时间"] = now_datetime()
        if write:
            update_confirmation_status(
                feedback_record=record,
                confirm=confirm,
                status="需继续核验",
                result=record["处理结果"],
                candidate_path=candidate_path,
            )
        return record, f"完成：继续核验 -> {repo_rel(candidate_path)}"

    if action == "调整分类":
        if write and candidate_path.exists():
            text = candidate_path.read_text(encoding="utf-8", errors="ignore")
            target_type = str(record.get("目标类型") or record.get("内容") or "待判断")
            text = set_frontmatter_scalar(text, "建议分类", target_type)
            text = append_to_section(
                text,
                "分类调整记录",
                f"- {now_datetime()}：用户要求调整分类为「{target_type}」。原始回复：{record.get('原始回复') or ''}",
            )
            candidate_path.write_text(text, encoding="utf-8")
        record["处理状态"] = "已处理"
        record["处理结果"] = "已记录分类调整，候选仍待确认"
        record["处理时间"] = now_datetime()
        if write:
            update_confirmation_status(
                feedback_record=record,
                confirm=confirm,
                status="待确认",
                result=record["处理结果"],
                candidate_path=candidate_path,
            )
        return record, f"完成：调整分类 -> {repo_rel(candidate_path)}"

    if action == "跳过":
        if write and candidate_path.exists():
            text = candidate_path.read_text(encoding="utf-8", errors="ignore")
            text = set_frontmatter_scalar(text, "吸收状态", "已跳过")
            text = append_to_section(
                text,
                "转正门禁记录",
                f"- {now_datetime()}：用户跳过本次候选确认。原始回复：{record.get('原始回复') or ''}",
            )
            candidate_path.write_text(text, encoding="utf-8")
        record["处理状态"] = "已处理"
        record["处理结果"] = "已跳过本次候选确认"
        record["处理时间"] = now_datetime()
        if write:
            update_confirmation_status(
                feedback_record=record,
                confirm=confirm,
                status="已跳过",
                result=record["处理结果"],
                candidate_path=candidate_path,
            )
        return record, f"完成：跳过待确认 -> {repo_rel(candidate_path)}"

    return record, "跳过：不是待确认动作"


def feedback_entry(record: dict[str, Any]) -> str:
    action = str(record.get("动作") or "反馈")
    source = str(record.get("来源") or "OpenClaw")
    channel = str(record.get("渠道") or "")
    content = str(record.get("内容") or record.get("原始回复") or "").strip()
    if not content:
        if action == "沉淀":
            content = f"用户要求继续沉淀为{record.get('目标类型') or '候选内容'}。"
        elif action == "继续解析":
            content = "用户要求继续解析。"
        elif action == "跳过":
            content = "用户选择跳过本条。"
        else:
            content = "用户已读。"
    channel_text = f"/{channel}" if channel else ""
    return f"- {now_date()}（{source}{channel_text}，{action}）：{content}"


def process_record(record: dict[str, Any], *, write: bool, distill: bool, force_distill: bool, sync_feishu: bool) -> tuple[dict[str, Any], str]:
    if str(record.get("处理状态") or "") != "待处理":
        return record, "跳过：不是待处理反馈"
    action = str(record.get("动作") or "")
    confirm_context = bool(record.get("候选文件")) or "待确认队列" in str(record.get("推送文件") or "")
    if (action in CONFIRM_ACTIONS or (action == "跳过" and confirm_context)) and (confirm_context or find_confirmation_target(record)):
        return process_confirmation_record(record, write=write, sync_feishu=sync_feishu)
    source = str(record.get("来源文件") or "").strip()
    if not source:
        inferred_source, reason = infer_source_file(record)
        if inferred_source:
            source = inferred_source
            record["来源文件"] = inferred_source
            record["来源文件推断"] = reason
        else:
            record["处理状态"] = "处理失败"
            record["处理结果"] = "缺少来源文件，且无法从回执内容反查"
            record["处理时间"] = now_datetime()
            return record, "失败：缺少来源文件"
    source_path = Path(source)
    if not source_path.is_absolute():
        source_path = ROOT / source_path
    if not source_path.exists():
        record["处理状态"] = "处理失败"
        record["处理结果"] = f"来源文件不存在：{source}"
        record["处理时间"] = now_datetime()
        return record, f"失败：来源文件不存在 {source}"

    raw = source_path.read_text(encoding="utf-8", errors="ignore")
    meta = parse_frontmatter(raw)
    promotion_feedback = is_promotion_feedback(record)
    updated = raw
    entry = feedback_entry(record)

    if action in {"已读", "补充想法", "沉淀"}:
        updated = append_to_section(updated, "阅读思考", entry)
    if promotion_feedback:
        updated = append_to_section(updated, "前台反馈处理记录", entry)
        updated = set_scalar_field(updated, "阅读状态", "已读")
        updated = set_scalar_field(updated, "处理状态", "已晋升")
        updated = set_scalar_field(updated, "入库状态", "已晋升")
    elif action == "跳过":
        updated = append_to_section(updated, "前台反馈处理记录", entry)
        updated = set_scalar_field(updated, "处理状态", "已归档")
    elif action == "继续解析":
        updated = append_to_section(updated, "前台反馈处理记录", entry)
        if str(meta.get("处理状态") or "") not in TERMINAL_STATUSES:
            updated = set_scalar_field(updated, "处理状态", "待分拣")
    elif str(meta.get("处理状态") or "") not in TERMINAL_STATUSES:
        updated = set_scalar_field(updated, "处理状态", "已分拣")

    if write:
        source_path.write_text(updated, encoding="utf-8")

    distill_result = ""
    target_type = str(record.get("目标类型") or "")
    if action == "沉淀" and distill and ("提示词" in target_type or "提示词" in str(record.get("原始回复") or "")):
        command = [sys.executable, str(DISTILL_SCRIPT), "--source", repo_rel(source_path), "--target", "prompt", "--write"]
        if force_distill:
            command.append("--force")
        if write:
            result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
            distill_result = (result.stdout + result.stderr).strip()
            if result.returncode != 0:
                record["处理状态"] = "处理失败"
                record["处理结果"] = "阅读思考已准备回写，但候选提示词生成失败：" + distill_result[-500:]
                record["处理时间"] = now_datetime()
                return record, f"失败：候选提示词生成失败 {source}"
        else:
            distill_result = "dry-run：将调用 inbox-distill 生成候选提示词"

    record["处理状态"] = "已晋升" if promotion_feedback else "已处理"
    record["处理时间"] = now_datetime()
    if promotion_feedback:
        record["处理结果"] = "已反查来源、回写反馈，并标记来源为已晋升"
    elif action == "沉淀" and distill_result:
        record["处理结果"] = "已回写阅读思考并生成候选提示词"
    elif action == "沉淀":
        record["处理结果"] = "已回写阅读思考并记录沉淀请求"
    else:
        record["处理结果"] = "已回写阅读思考" if action in {"已读", "补充想法"} else "已记录前台处理动作"
    if distill_result:
        record["沉淀结果"] = distill_result[-1000:]
    return record, f"完成：{action} -> {source}"


def update_push_state(path: Path, records: list[dict[str, Any]], *, write: bool) -> None:
    if not path.exists():
        return
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    items = state.get("items") if isinstance(state, dict) else None
    if not isinstance(items, dict):
        return
    changed = False
    for record in records:
        source = str(record.get("来源文件") or "")
        if not source or source not in items:
            continue
        item = items[source]
        if not isinstance(item, dict):
            continue
        item["feedback_status"] = "已晋升" if str(record.get("处理状态") or "") == "已晋升" else str(record.get("动作") or "已反馈")
        item["last_feedback_at"] = str(record.get("时间") or now_datetime())
        item["last_feedback_content"] = str(record.get("内容") or record.get("原始回复") or "")
        changed = True
    if changed:
        state["updated_at"] = dt.datetime.now(TZ).isoformat(timespec="seconds")
        if write:
            path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_report(results: list[str], processed: list[dict[str, Any]], dry_run: bool) -> str:
    lines = [
        "# 前台反馈消费报告",
        "",
        f"- 时间：{now_datetime()}",
        f"- 模式：{'dry-run' if dry_run else '写入'}",
        f"- 本轮处理：{len(processed)}",
        "",
        "## 结果",
        "",
    ]
    lines.extend(f"- {result}" for result in results)
    if processed:
        lines.extend(["", "## 已处理记录", ""])
        for record in processed:
            lines.append(
                f"- {record.get('动作', '未知动作')}：`{record.get('来源文件', '未知来源')}`，状态：{record.get('处理状态')}"
            )
    return "\n".join(lines).rstrip() + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Consume my-mind frontdesk feedback queue.")
    parser.add_argument("--queue", default=str(DEFAULT_QUEUE), help="Feedback JSONL queue.")
    parser.add_argument("--run-dir", default=str(DEFAULT_RUN_DIR), help="Run record directory.")
    parser.add_argument("--push-state", default=str(DEFAULT_PUSH_STATE), help="Frontdesk push state JSON.")
    parser.add_argument("--write", action="store_true", help="Write source updates and queue status. Default is dry-run.")
    parser.add_argument("--no-distill", action="store_true", help="Do not call inbox-distill for 沉淀成提示词 feedback.")
    parser.add_argument("--force-distill", action="store_true", help="Pass --force to inbox-distill when writing prompt candidates.")
    parser.add_argument("--sync-feishu", action="store_true", help="After confirmed promotion, immediately run feishu-sync for the promoted candidate.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    queue = Path(args.queue)
    run_dir = Path(args.run_dir)
    push_state = Path(args.push_state)
    if not queue.is_absolute():
        queue = ROOT / queue
    if not run_dir.is_absolute():
        run_dir = ROOT / run_dir
    if not push_state.is_absolute():
        push_state = ROOT / push_state

    records = read_jsonl(queue)
    updated_records: list[dict[str, Any]] = []
    processed_records: list[dict[str, Any]] = []
    results: list[str] = []
    for record in records:
        updated, result = process_record(
            record,
            write=args.write,
            distill=not args.no_distill,
            force_distill=args.force_distill,
            sync_feishu=args.sync_feishu,
        )
        updated_records.append(updated)
        results.append(result)
        if result.startswith(("完成", "失败")):
            processed_records.append(updated)

    report = build_report(results, processed_records, dry_run=not args.write)
    if args.write:
        write_jsonl(queue, updated_records)
        update_push_state(push_state, processed_records, write=True)
        run_dir.mkdir(parents=True, exist_ok=True)
        report_path = run_dir / f"反馈消费-{now_filename()}.md"
        report_path.write_text(report, encoding="utf-8")
        print(repo_rel(report_path))
    else:
        print(report, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
