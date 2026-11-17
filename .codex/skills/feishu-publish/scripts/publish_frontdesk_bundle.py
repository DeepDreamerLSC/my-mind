#!/usr/bin/env python3
"""Publish a frontdesk push as a Feishu index page plus per-item pages."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import publish_feishu_reading as base
import reading_version as reading
import visual_evidence as visuals


ROOT = base.ROOT
TZ = base.TZ
DEFAULT_RUN_DIR = base.DEFAULT_RUN_DIR
DEFAULT_DRAFT_DIR = DEFAULT_RUN_DIR / "飞书精选页"
DEFAULT_RECORD_FILE = base.DEFAULT_RECORD_FILE
DEFAULT_ITEM_PARENT_MAP = DEFAULT_RUN_DIR / "飞书知识库目录映射.local.json"
DEFAULT_GATE_QUEUE = ROOT / "05_流转区/40_待核验/飞书发布待补全队列.md"
DEFAULT_GATE_REPORT_DIR = DEFAULT_RUN_DIR
DEFAULT_VISUAL_CACHE_DIR = DEFAULT_DRAFT_DIR / "媒体"
DEFAULT_TRANSCRIPT_DIR = DEFAULT_DRAFT_DIR / "完整转写"
DEFAULT_VISUAL_WIDTH = 960
DEFAULT_CREATE_COMMAND = (
    'OPENCLAW_HOME="$HOME/.openclaw" lark-cli docs +create '
    "--api-version v2 --wiki-space my_library --title {title} --doc-format markdown --content @{markdown_file_rel}"
)
DEFAULT_UPDATE_COMMAND = (
    'OPENCLAW_HOME="$HOME/.openclaw" lark-cli docs +update '
    "--api-version v2 --doc {page_token} --command overwrite --doc-format markdown --new-title {title} --content @{markdown_file_rel}"
)

INTERNAL_SOURCE_HEADINGS = (
    "为什么保存",
    "初步想法",
    "阅读思考",
    "后续处理建议",
    "分拣记录",
    "沉淀记录",
    "入库记录",
    "解析修复记录",
    "原始链接",
)
INCOMPLETE_QUALITY_VALUES = {"需继续解析", "需核验", "解析失败", "部分解析"}
INCOMPLETE_PARSE_STATUSES = {"解析失败", "部分解析"}
SECTION_STOP_PATTERN = re.compile(r"^#{1,6}\s+", flags=re.M)


def clean_title(title: str, max_chars: int = 90) -> str:
    value = re.sub(r"\s+", " ", title).strip()
    value = value.replace("/", "／").replace(":", "：")
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 1].rstrip() + "…"


def now_filename() -> str:
    return dt.datetime.now(TZ).strftime("%Y-%m-%d-%H%M")


def stable_item_draft_path(directory: Path, item: base.PushItem) -> Path:
    return directory / f"{item.index:02d}-{clean_title(item.title, 48)}.md"


def stable_index_draft_path(directory: Path) -> Path:
    return directory / f"精选索引-{dt.datetime.now(TZ).strftime('%Y-%m-%d')}.md"


def stable_transcript_draft_path(directory: Path, item: base.PushItem) -> Path:
    return directory / f"{item.index:02d}-{clean_title(item.title, 44)}-完整转写.md"


def stable_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def resolve_path(path: str | Path, default: Path | None = None) -> Path:
    value = Path(path) if path else default
    if value is None:
        raise ValueError("path is empty")
    return value if value.is_absolute() else ROOT / value


def markdown_link(title: str, url: str) -> str:
    if not url:
        return title
    safe_title = title.replace("[", "【").replace("]", "】")
    return f"[{safe_title}]({url})"


def wiki_url(record: dict[str, object]) -> str:
    token = str(record.get("wiki_node_token") or "").strip()
    if token:
        return f"https://my.feishu.cn/wiki/{token}"
    return str(record.get("feishu_url") or "").strip()


def item_identity(item: base.PushItem) -> str:
    return item.source_file or item.original_url or f"frontdesk-item-{item.index}-{item.title}"


def run_template_command(
    command_template: str,
    *,
    markdown_file: Path,
    title: str,
    push_path: Path,
    record_file: Path,
    digest: str,
    page_token: str = "",
) -> tuple[str, str, str]:
    try:
        markdown_file_rel = markdown_file.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        markdown_file_rel = str(markdown_file)
    replacements = {
        "markdown_file": shlex.quote(str(markdown_file)),
        "markdown_file_rel": shlex.quote(markdown_file_rel),
        "title": shlex.quote(title),
        "source_push": shlex.quote(str(push_path)),
        "record_file": shlex.quote(str(record_file)),
        "content_hash": shlex.quote(digest),
        "page_token": shlex.quote(page_token),
    }
    completed = subprocess.run(command_template.format(**replacements), shell=True, text=True, capture_output=True, check=False)
    combined_output = "\n".join(part for part in [completed.stdout.strip(), completed.stderr.strip()] if part)
    if completed.returncode != 0:
        raise RuntimeError(f"飞书命令失败，退出码 {completed.returncode}：{combined_output}")
    data = base.parse_json_payload(completed.stdout)
    if isinstance(data, dict) and data.get("ok") is False:
        error = data.get("error") if isinstance(data.get("error"), dict) else {}
        message = str(error.get("message") or "飞书命令返回 ok=false")
        hint = str(error.get("hint") or "").strip()
        raise RuntimeError(f"{message}{'；' + hint if hint else ''}")
    url, token = base.parse_publish_output(completed.stdout)
    return url, token, combined_output


def run_wiki_node_move_command(
    *,
    wiki_node_token: str,
    wiki_space_id: str,
    wiki_parent_node_token: str,
) -> str:
    if not wiki_node_token or not wiki_parent_node_token:
        return ""
    env = os.environ.copy()
    env.setdefault("OPENCLAW_HOME", str(Path.home() / ".openclaw"))
    command = [
        "lark-cli",
        "wiki",
        "+move",
        "--node-token",
        wiki_node_token,
        "--target-parent-token",
        wiki_parent_node_token,
    ]
    if wiki_space_id:
        command.extend(["--target-space-id", wiki_space_id])
    completed = subprocess.run(command, text=True, capture_output=True, check=False, env=env)
    combined_output = "\n".join(part for part in [completed.stdout.strip(), completed.stderr.strip()] if part)
    if completed.returncode != 0:
        raise RuntimeError(f"知识库节点移动失败，退出码 {completed.returncode}：{combined_output}")
    data = base.parse_json_payload(completed.stdout)
    if isinstance(data, dict) and data.get("ok") is False:
        error = data.get("error") if isinstance(data.get("error"), dict) else {}
        message = str(error.get("message") or "知识库节点移动返回 ok=false")
        hint = str(error.get("hint") or "").strip()
        raise RuntimeError(f"{message}{'；' + hint if hint else ''}")
    return combined_output


def run_media_insert_command(
    *,
    page_token: str,
    asset_path: Path,
    evidence: visuals.VisualEvidence,
    width: int,
    selection: str = "",
) -> tuple[bool, str]:
    env = os.environ.copy()
    env.setdefault("OPENCLAW_HOME", str(Path.home() / ".openclaw"))
    command = [
        "lark-cli",
        "docs",
        "+media-insert",
        "--as",
        "user",
        "--doc",
        page_token,
        "--file",
        str(asset_path),
        "--type",
        "image",
        "--width",
        str(max(320, width)),
        "--caption",
        evidence.caption,
    ]
    if selection:
        command.extend(["--selection-with-ellipsis", selection])
    completed = subprocess.run(command, text=True, capture_output=True, check=False, env=env)
    combined_output = "\n".join(part for part in [completed.stdout.strip(), completed.stderr.strip()] if part)
    if completed.returncode != 0:
        return False, combined_output or f"lark-cli 返回码 {completed.returncode}"
    data = base.parse_json_payload(completed.stdout)
    if isinstance(data, dict) and data.get("ok") is False:
        error = data.get("error") if isinstance(data.get("error"), dict) else {}
        message = str(error.get("message") or "飞书媒体插入失败")
        hint = str(error.get("hint") or "").strip()
        return False, f"{message}{'；' + hint if hint else ''}"
    return True, combined_output


def selection_anchor_missing(output: str) -> bool:
    """Return whether media insertion failed only because the text anchor is absent."""

    normalized = str(output or "").lower()
    markers = (
        "anchor not found",
        "selection not found",
        "selection-with-ellipsis",
        "matching text",
        "no matching text",
        "no match",
        "未找到匹配文本",
        "未找到匹配的文本",
        "未找到选区",
        "未找到锚点",
        "无法定位文本",
    )
    return any(marker in normalized for marker in markers)


def embed_visual_evidence(
    *,
    page_token: str,
    materialized: list[visuals.MaterializedVisual],
    existing: dict[str, object] | None,
    width: int,
) -> tuple[list[str], list[str], str]:
    """Insert new source images while leaving an already-published page usable on media failures."""

    previous_ids: set[str] = set()
    if existing and str(existing.get("page_token") or "") == page_token:
        embedded = existing.get("visual_embedded_ids")
        if isinstance(embedded, list):
            previous_ids = {str(value) for value in embedded if str(value)}

    inserted: list[str] = []
    errors: list[str] = []
    outputs: list[str] = []
    for item in materialized:
        evidence = item.evidence
        if item.error:
            errors.append(f"{evidence.caption}：{item.error}")
            continue
        if not item.asset_path:
            errors.append(f"{evidence.caption}：未生成可上传图片。")
            continue
        if evidence.evidence_id in previous_ids:
            continue
        success, output = run_media_insert_command(
            page_token=page_token,
            asset_path=item.asset_path,
            evidence=evidence,
            width=width,
            selection=evidence.anchor,
        )
        if not success and selection_anchor_missing(output):
            # Existing pages do not have the new Markdown anchor. Append in that case.
            success, fallback_output = run_media_insert_command(
                page_token=page_token,
                asset_path=item.asset_path,
                evidence=evidence,
                width=width,
            )
            output = "\n".join(part for part in [output, fallback_output] if part)
        if success:
            inserted.append(evidence.evidence_id)
            if output:
                outputs.append(output)
        else:
            errors.append(f"{evidence.caption}：{output or '飞书图片插入失败。'}")
    return inserted, errors, "\n\n".join(outputs)


def latest_record_for(records: list[dict[str, object]], *, record_type: str, key: str, value: str) -> dict[str, object] | None:
    for record in reversed(records):
        if record.get("record_type") == record_type and str(record.get(key) or "") == value and record.get("status") == "已发布":
            return record
    return None


def latest_wiki_context(records: list[dict[str, object]]) -> tuple[str, str]:
    for record in reversed(records):
        if record.get("record_type") != "frontdesk_bundle_index" or record.get("status") != "已发布":
            continue
        space_id = str(record.get("wiki_space_id") or "").strip()
        parent_token = str(record.get("wiki_parent_node_token") or "").strip()
        if space_id or parent_token:
            return space_id, parent_token
    return "", ""


def load_item_parent_map(path: str | Path) -> dict[str, str]:
    map_path = resolve_path(path)
    if not map_path.exists():
        return {}
    data = json.loads(map_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"目录映射必须是 JSON object：{base.repo_relative(map_path)}")
    return {str(key): str(value).strip() for key, value in data.items() if str(value).strip()}


def item_classification_text(item: base.PushItem) -> str:
    return "\n".join(
        [
            item.title,
            item.source,
            item.source_file,
            item.summary,
            item.excerpt,
            item.original_url,
            item.share_url,
        ]
    )


ITEM_PARENT_RULES: list[tuple[str, str, str]] = [
    (
        "20_资料库/教育与学习",
        r"教育|教育ai|教育 AI|教育技术|教育科技|edtech|education|educational|teacher|教师|学生|课堂|课程|教学|学习科学|pedagogy|curriculum|测评|assessment|考试|错题|题库|组卷|智能辅导|tutoring|自适应学习|学术诚信",
        "命中教育、教学、学习或测评语义",
    ),
    (
        "20_资料库/人工智能产业",
        r"人工智能产业|ai usage|economics|saas|a16z|benedict|基础设施|usage and what|software economics|模型经济|产业|商业模式|nvidia|jensen|huang|黄仁勋|cuda|算力|芯片|数据中心|ai revolution",
        "命中 AI 产业、SaaS 或商业经济语义",
    ),
    (
        "20_资料库/工作流与自动化",
        r"codex.*(prompt|提示词|skill|工作流|workflow|agent)|codex agent|subagent|工作流优化|越用越聪明|神级prompt",
        "命中 Codex、Skill 或 Agent 工作流语义",
    ),
    (
        "20_资料库/设计与视觉",
        r"设计|视觉|ui|ux|image|图像|排版|配色|高级感|presentation|ppt",
        "命中设计或视觉语义",
    ),
    (
        "20_资料库/写作与表达",
        r"写作|表达|writing|essay|newsletter|内容创作|演讲|叙事",
        "命中写作或表达语义",
    ),
    (
        "20_资料库/管理与组织",
        r"团队管理|管理者|领导力|领导|leader|leadership|manager|management|激活团队|下属|经营者|一把手|职场管理|人才管理|管理决策|组织架构|驭人术",
        "命中管理、组织或领导力语义",
    ),
    (
        "20_资料库/工作流与自动化",
        r"工作流|自动化|workflow|automation|第二大脑|second brain|code challenge|capture|organize|distill|express|tiago|月末|报告|dashboard|dashboards|finance|财务|知识工作|productivity|生产力",
        "命中知识工作流、自动化或第二大脑语义",
    ),
    (
        "20_资料库/AI产品与工具",
        r"codex|claude code|anthropic|openai|chatgpt|ai工具|agentic ai|agent|智能体|prompt|提示词|大模型|基础模型",
        "命中 AI 产品、工具或提示词语义",
    ),
]


def infer_item_parent(item: base.PushItem, parent_map: dict[str, str]) -> tuple[str, str, str]:
    text = item_classification_text(item).casefold()
    for directory, pattern, reason in ITEM_PARENT_RULES:
        if directory in parent_map and re.search(pattern, text, flags=re.I):
            return parent_map[directory], directory, reason
    if "20_资料库" in parent_map:
        return parent_map["20_资料库"], "20_资料库", "未配置已审批目录，回落到资料库根目录（不创建飞书目录）"
    return "", "", "未配置目录映射"


def strip_front_matter(text: str) -> str:
    if not text.startswith("---"):
        return text
    match = re.match(r"^---\s*\n.*?\n---\s*\n?", text, flags=re.S)
    return text[match.end() :] if match else text


def parse_simple_frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        return {}
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n?", text, flags=re.S)
    if not match:
        return {}
    meta: dict[str, str] = {}
    for raw_line in match.group(1).splitlines():
        if not raw_line.strip() or raw_line.startswith(" "):
            continue
        if ":" not in raw_line:
            continue
        key, value = raw_line.split(":", 1)
        meta[key.strip()] = value.strip().strip('"')
    return meta


def strip_leading_h1(text: str) -> str:
    return re.sub(r"^# .+?\n+", "", text.lstrip(), count=1)


def cut_before_internal_sections(text: str) -> str:
    earliest = len(text)
    for heading in INTERNAL_SOURCE_HEADINGS:
        match = re.search(rf"^## {re.escape(heading)}\s*$", text, flags=re.M)
        if match:
            earliest = min(earliest, match.start())
    return text[:earliest].strip()


def demote_markdown_headings(text: str) -> str:
    return re.sub(r"^(#{1,5})(\s+)", r"#\1\2", text, flags=re.M)


def section_text(body: str, heading: str) -> str:
    match = re.search(rf"^## {re.escape(heading)}\s*$", body, flags=re.M)
    if not match:
        return ""
    start = match.end()
    next_match = SECTION_STOP_PATTERN.search(body, start)
    end = next_match.start() if next_match else len(body)
    return body[start:end].strip()


def int_meta(meta: dict[str, str], key: str) -> int:
    match = re.search(r"\d+", str(meta.get(key) or ""))
    return int(match.group(0)) if match else 0


def has_truncation_marker(text: str) -> bool:
    return bool(re.search(r"摘录已截断|仍包含截断|全文稿|补全文稿|\[…\]|…|\.\.\.", text))


def source_note_path(item: base.PushItem) -> Path | None:
    source_file = item.source_file.strip()
    if not source_file or source_file == "未知":
        return None
    path = resolve_path(source_file)
    return path if path.exists() else None


def assess_source_material(meta: dict[str, str], body: str, char_count: int) -> dict[str, object]:
    blockers: list[str] = []
    quality = str(meta.get("内容质量") or "")
    parse_status = str(meta.get("解析状态") or "")
    platform = str(meta.get("来源平台") or "")
    source_kind = str(meta.get("内容摘录来源") or "")
    excerpt_chars = int_meta(meta, "内容摘录字数")
    ocr_chars = int_meta(meta, "图片OCR字数")

    transcript = reading.transcript_text(body)
    ocr_section = section_text(body, "图片文字 OCR")
    copy_section = section_text(body, "文案摘录")
    original_section = section_text(body, "原文摘录")
    article_section = section_text(body, "正文") or section_text(body, "文章正文")

    transcript_chars = len(reading.remove_markdown(transcript))
    has_video_transcript = bool(transcript_chars >= 120 and not has_truncation_marker(transcript))
    has_ocr = bool(ocr_section and ocr_chars >= 80)
    has_platform_copy = bool(copy_section and len(copy_section) >= 180)
    has_article_body = bool(article_section and len(article_section) >= 500)
    has_original_excerpt = bool(original_section and len(original_section) >= 500 and not has_truncation_marker(original_section))
    has_official_reference = bool(section_text(body, "官方转录来源") and section_text(body, "章节目录"))
    ready_signal = (
        has_video_transcript
        or has_ocr
        or has_platform_copy
        or has_article_body
        or has_original_excerpt
    )

    if not body.strip() or char_count < 180:
        blockers.append("来源笔记可发布正文不足 180 字。")
    if quality in INCOMPLETE_QUALITY_VALUES:
        blockers.append(f"内容质量为「{quality}」。")
    if parse_status in INCOMPLETE_PARSE_STATUSES:
        blockers.append(f"解析状态为「{parse_status}」。")
    if not ready_signal:
        blockers.append("未发现完整正文、平台文案、OCR、视频转写或官方转录来源。")
    if has_truncation_marker(original_section or body) and not (has_video_transcript or has_ocr or has_platform_copy or has_article_body):
        blockers.append("来源仍包含截断摘录，需要继续解析后再进入飞书精选。")
    if "RSS/Atom" in source_kind and not (has_video_transcript or has_ocr or has_article_body or has_official_reference):
        blockers.append("当前只有 RSS/Atom 摘要，不足以作为手机阅读正本。")
    if has_official_reference and not has_video_transcript and not has_article_body:
        blockers.append("当前只有转录来源或章节目录，尚未保存正文级完整转写。")

    blockers = sorted(set(blockers))
    evidence: list[str] = []
    if has_video_transcript:
        evidence.append("视频转写")
    if has_ocr:
        evidence.append("图片 OCR")
    if has_platform_copy:
        evidence.append("平台文案")
    if has_article_body:
        evidence.append("文章正文")
    if has_original_excerpt:
        evidence.append("完整原文摘录")
    if has_official_reference:
        evidence.append("官方转录来源")
    return {
        "ready": not blockers,
        "status": "可发布正本" if not blockers else "需继续解析",
        "blockers": blockers,
        "evidence": evidence,
        "platform": platform,
        "quality": quality,
        "parse_status": parse_status,
    }


def source_material_warning(assessment: dict[str, object]) -> str:
    blockers = assessment.get("blockers")
    if isinstance(blockers, list) and blockers:
        return "；".join(str(blocker) for blocker in blockers)
    return ""


def apply_reading_quality_gate(
    assessment: dict[str, object],
    reading_view: reading.ReadingVersion | None,
) -> dict[str, object]:
    if not reading_view or not reading_view.source_language.casefold().startswith("en"):
        return assessment
    if reading_view.claims and all(reading.contains_cjk(claim.statement) for claim in reading_view.claims):
        return assessment
    blockers = list(assessment.get("blockers") or [])
    blockers.append("英文内容尚缺中文核心观点，需要先完成中文结构化阅读版。")
    return {
        **assessment,
        "ready": False,
        "status": "需继续解析",
        "blockers": sorted(set(str(blocker) for blocker in blockers)),
    }


def load_source_material(item: base.PushItem) -> dict[str, object]:
    path = source_note_path(item)
    if not path:
        return {
            "status": "来源文件不存在",
            "publish_status": "需继续解析",
            "publish_ready": False,
            "publish_blockers": ["来源文件不存在。"],
            "publish_evidence": [],
            "path": item.source_file,
            "source_path": None,
            "metadata": {},
            "visual_source_body": "",
            "reading_version": None,
            "body": "",
            "body_hash": "",
            "char_count": 0,
            "warning": "来源文件不存在。",
        }
    raw = base.read_text(path)
    meta = parse_simple_frontmatter(raw)
    source_body = strip_front_matter(raw)
    body = strip_leading_h1(source_body)
    body = cut_before_internal_sections(body)
    assessment_body = body.strip()
    body = demote_markdown_headings(assessment_body).strip()
    assessment = assess_source_material(meta, assessment_body, len(body))
    reading_view = reading.build_reading_version(
        metadata=meta,
        body=source_body,
        item_summary=item.summary,
        item_excerpt=item.excerpt,
    )
    assessment = apply_reading_quality_gate(assessment, reading_view)
    return {
        "status": "已接入来源正本" if body else "来源正本为空",
        "publish_status": assessment["status"],
        "publish_ready": bool(assessment["ready"]),
        "publish_blockers": assessment["blockers"],
        "publish_evidence": assessment["evidence"],
        "path": base.repo_relative(path),
        "source_path": path,
        "metadata": meta,
        "visual_source_body": source_body,
        "reading_version": reading_view,
        "body": body,
        "body_hash": base.content_hash(body) if body else "",
        "char_count": len(body),
        "warning": source_material_warning(assessment),
    }


def build_item_visual_evidence(
    item: base.PushItem,
    source_material: dict[str, object],
    *,
    max_visuals: int,
) -> list[visuals.VisualEvidence]:
    metadata = source_material.get("metadata")
    source_path = source_material.get("source_path")
    reading_view = source_material.get("reading_version")
    return visuals.build_visual_evidence(
        metadata=metadata if isinstance(metadata, dict) else {},
        body=str(source_material.get("visual_source_body") or ""),
        source_path=source_path if isinstance(source_path, Path) else None,
        original_url=item.original_url,
        max_visuals=max_visuals,
        reading_version=reading_view if isinstance(reading_view, reading.ReadingVersion) else None,
    )


def visual_cache_path(root: Path, item: base.PushItem) -> Path:
    return root / visuals.evidence_id("item", item_identity(item))


def render_visual_evidence_section(evidence: list[visuals.VisualEvidence]) -> list[str]:
    if not evidence:
        return []
    labels = {
        "cover": "来源封面",
        "source_image": "来源原始图片",
        "local_image": "来源本地图片",
        "video_frame": "视频关键画面",
    }
    lines = [
        "## 视觉证据",
        "",
        "以下图片仅来自原始资料的封面、原始图片或公开视频关键画面，不使用生成配图。",
        "",
    ]
    for item in evidence:
        lines.extend(
            [
                f"### {item.anchor}",
                "",
                f"- 类型：{labels.get(item.kind, '来源图片')}",
                f"- 说明：{item.caption}",
            ]
        )
        if item.timecode:
            time_range = f"{item.timecode}-{item.end_timecode}" if item.end_timecode else item.timecode
            lines.append(f"- 时间范围：{time_range}")
        if item.source_url:
            lines.append(f"- 原始来源：{item.source_url}")
        lines.append("")
    return lines


def render_gate_queue(blocked: list[tuple[base.PushItem, dict[str, object]]], push_path: Path) -> str:
    lines = [
        "# 飞书发布待补全队列",
        "",
        "这里记录飞书精选发布前发现的正文不完整条目。它们不应进入手机精选索引，需先补正文、OCR、字幕或转写。",
        "",
        "## 总览",
        "",
        f"- 更新时间：{base.now_datetime()}",
        f"- 来源推送：`{base.repo_relative(push_path)}`",
        f"- 待补全数量：{len(blocked)}",
        "",
        "## 队列",
        "",
    ]
    if not blocked:
        lines.append("当前没有飞书发布待补全条目。")
        return "\n".join(lines).rstrip() + "\n"
    for index, (item, material) in enumerate(blocked, start=1):
        blockers = material.get("publish_blockers") if isinstance(material.get("publish_blockers"), list) else []
        lines.extend(
            [
                f"### {index}. {item.title}",
                "",
                f"- 来源文件：`{material.get('path') or item.source_file or '未知'}`",
                f"- 飞书发布状态：{material.get('publish_status') or '未知'}",
                f"- 已接入字数：{material.get('char_count') or 0}",
                "- 待补全：",
            ]
        )
        lines.extend(f"  - {blocker}" for blocker in blockers)
        lines.extend(["", "- 建议：先执行继续解析或补充正文证据，再重新生成前台推送和飞书精选。", ""])
    return "\n".join(lines).rstrip() + "\n"


def write_gate_outputs(
    blocked: list[tuple[base.PushItem, dict[str, object]]],
    *,
    push_path: Path,
    queue_path: Path,
    report_dir: Path,
) -> Path:
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    rendered = render_gate_queue(blocked, push_path)
    queue_path.write_text(rendered, encoding="utf-8")
    report_path = report_dir / f"飞书精选发布门禁-{now_filename()}.md"
    report_path.write_text(rendered, encoding="utf-8")
    return report_path


def render_inline_transcript(reading_view: reading.ReadingVersion) -> list[str]:
    lines = ["## 完整转写", "", "以下内容保留为核验证据，已经按自然段合并字幕碎片。", ""]
    units = reading.sentence_units(reading_view.transcript)
    for index in range(0, len(units), 4):
        lines.extend(["".join(units[index : index + 4]), ""])
    return lines


def render_source_and_quality(
    item: base.PushItem,
    material: dict[str, object],
    push_path: Path,
    generated_at: str,
) -> list[str]:
    lines = [
        "## 来源与质量",
        "",
        f"- 来源：{item.source or '未知'}",
        f"- 来源笔记：`{material.get('path') or item.source_file or '未知'}`",
        f"- 来源推送：`{base.repo_relative(push_path)}`",
        f"- 前台推送时间：{generated_at or '未知'}",
        f"- 接入状态：{material.get('status') or '未知'}",
        f"- 来源正文：{material.get('char_count') or 0} 字",
    ]
    if item.original_url:
        lines.append(f"- 原文链接：{item.original_url}")
    if item.share_url and item.share_url != item.original_url:
        lines.append(f"- 分享链接：{item.share_url}")
    if item.transcript_url and item.transcript_url not in {item.original_url, item.share_url}:
        lines.append(f"- 外部转录链接：{item.transcript_url}")
    if material.get("warning"):
        lines.append(f"- 完整性提醒：{material.get('warning')}")
    if item.quality:
        lines.append(f"- 质量说明：{reading.remove_markdown(item.quality)}")
    return lines


def render_reading_item_page(
    item: base.PushItem,
    push_path: Path,
    generated_at: str,
    material: dict[str, object],
    reading_view: reading.ReadingVersion,
    evidence: list[visuals.VisualEvidence],
    transcript_href: str,
) -> str:
    lines = [f"# {item.title}", ""]
    if not material.get("publish_ready"):
        lines.extend(
            [
                "> **待核验阅读页**：来源尚未通过完整性门禁，本页不能视为正式精选或长期知识。",
                "",
            ]
        )
    lines.extend(["## 30 秒结论", "", reading_view.conclusion or "当前转写尚不足以形成稳定结论。", "", "## 核心观点", ""])
    for index, claim in enumerate(reading_view.claims, start=1):
        lines.extend(
            [
                f"### 观点 {index}",
                "",
                claim.statement,
                "",
                f"- 时间范围：{reading.format_timecode(claim.start_seconds)}-{reading.format_timecode(claim.end_seconds)}",
                f"- 来源证据：{claim.evidence}",
                "",
            ]
        )
    lines.extend(["## 分章节阅读", ""])
    for chapter in reading_view.chapters:
        lines.extend(
            [
                f"### {chapter.title}",
                "",
                f"- 时间范围：{reading.format_timecode(chapter.start_seconds)}-{reading.format_timecode(chapter.end_seconds)}",
                "",
                chapter.summary,
                "",
                f"> 证据上下文：{chapter.evidence}",
                "",
            ]
        )
    if evidence:
        lines.extend(render_visual_evidence_section(evidence))
    lines.extend(["## 对我的价值", ""])
    if item.value:
        lines.append(f"- 适用场景：{reading.remove_markdown(item.value)}")
    if item.action:
        lines.append(f"- 下一步动作：{reading.remove_markdown(item.action)}")
    if item.distill_direction:
        lines.append(f"- 可沉淀方向：{reading.remove_markdown(item.distill_direction)}")
    lines.append(f"- 使用边界：{'来源仍需补全或核验。' if not material.get('publish_ready') else '先结合原始语境核验，再决定是否进入长期知识。'}")
    lines.append("")
    if reading_view.transcript_mode == "inline":
        lines.extend(render_inline_transcript(reading_view))
    elif reading_view.transcript_mode == "separate":
        lines.extend(["## 完整转写", "", f"[查看按语义章节整理的完整转写]({transcript_href})" if transcript_href else "完整转写已单独生成，发布后将在此提供链接。", ""])
    lines.extend(
        [
            "## 回复给 OpenClaw",
            "",
            f"- `{item.index} 已读：你的想法`",
            f"- `{item.index} 沉淀成提示词`",
            f"- `{item.index} 跳过`",
            f"- `{item.index} 继续解析`",
            "",
            *render_source_and_quality(item, material, push_path, generated_at),
            "",
            "## 处理边界",
            "",
            "- 本页是本地 `my-mind` 的手机阅读镜像，不是长期知识源。",
            "- 完整转写只作为搜索和核验证据，不代表观点已经转正。",
            "- 未经用户确认，不自动晋升资料库、原子笔记、洞察或提示词库。",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def render_incomplete_item_page(
    item: base.PushItem,
    push_path: Path,
    generated_at: str,
    material: dict[str, object],
) -> str:
    blockers = material.get("publish_blockers") if isinstance(material.get("publish_blockers"), list) else []
    lines = [
        f"# 待核验｜{item.title}",
        "",
        "> **来源不完整**：该页面仅用于继续解析，不能作为正式精选、稳定观点或长期知识。",
        "",
        "## 当前可确认的信息",
        "",
        reading.bounded_complete_text(item.summary or item.excerpt, 60, 240) or "当前只有来源简介或截断片段，尚不能形成稳定观点。",
        "",
        "## 仍需补全",
        "",
    ]
    lines.extend(f"- {blocker}" for blocker in blockers)
    lines.extend(
        [
            "",
            "## 建议动作",
            "",
            f"- `{item.index} 继续解析`",
            f"- `{item.index} 跳过`",
            "",
            *render_source_and_quality(item, material, push_path, generated_at),
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def render_item_page(
    item: base.PushItem,
    push_path: Path,
    generated_at: str,
    source_material: dict[str, object] | None = None,
    visual_evidence: list[visuals.VisualEvidence] | None = None,
    transcript_href: str = "",
) -> str:
    material = source_material or load_source_material(item)
    reading_view = material.get("reading_version")
    if isinstance(reading_view, reading.ReadingVersion):
        return render_reading_item_page(
            item,
            push_path,
            generated_at,
            material,
            reading_view,
            visual_evidence or [],
            transcript_href,
        )
    if not material.get("publish_ready"):
        return render_incomplete_item_page(item, push_path, generated_at, material)

    lines = [f"# {item.title}", "", "## 30 秒结论", "", reading.bounded_complete_text(item.summary or item.excerpt, 100, 200)]
    if item.excerpt:
        lines.extend(["", "## 内容摘录", "", item.excerpt])
    if item.questions:
        lines.extend(["", "## 阅读时重点", "", item.questions])
    if visual_evidence:
        lines.extend(["", *render_visual_evidence_section(visual_evidence)])
    lines.extend(["", *render_source_and_quality(item, material, push_path, generated_at)])
    return "\n".join(lines).rstrip() + "\n"


def render_index_page(
    *,
    title: str,
    push_path: Path,
    generated_at: str,
    item_records: list[dict[str, object]],
    project_progress: str,
) -> str:
    lines = [
        f"# {title}",
        "",
        "- 页面类型：my-mind 前台精选索引",
        f"- 生成时间：{base.now_datetime()}",
        "- 生成来源：Codex / feishu-publish bundle",
        f"- 来源推送：`{base.repo_relative(push_path)}`",
        f"- 前台推送时间：{generated_at or '未知'}",
        f"- 精选条目：{len(item_records)}",
        "",
        "## 阅读方式",
        "",
        "- 本页只做索引，每一节标题都链接到对应的飞书知识库单篇文章。",
        "- 原文链接、分享链接、来源笔记正本、OCR、视频转写和建议动作都放在单篇文章内。",
        "- 读完后回复 OpenClaw：`序号 已读：你的想法`、`序号 沉淀成提示词`、`序号 跳过` 或 `序号 继续解析`。",
        "",
        "## 今日精选",
        "",
    ]
    if not item_records:
        lines.append("暂无需要发布的精选条目。")
    for record in item_records:
        index = record.get("item_index") or "?"
        item_title = str(record.get("title") or "未命名条目")
        url = wiki_url(record)
        brief = record.get("reading_brief") or record.get("summary") or "暂无"
        lines.extend(
            [
                f"### {index}. {markdown_link(item_title, url)}",
                "",
                f"- 简述：{brief}",
                f"- 来源：{record.get('source') or '未知'}",
                f"- 来源文件：`{record.get('source_file') or '未知'}`",
                f"- 飞书目录：{record.get('wiki_parent_directory') or '未记录'}",
                f"- 飞书文章：{url or '未发布'}",
                "",
            ]
        )
    if project_progress:
        lines.extend(["## 项目进度", "", project_progress, ""])
    lines.extend(
        [
            "## 统一回复格式",
            "",
            "```text",
            "序号 已读：你的想法",
            "序号 沉淀成提示词",
            "序号 跳过",
            "序号 继续解析",
            "```",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def item_hash(item: base.PushItem, source_material: dict[str, object] | None = None) -> str:
    material = source_material or load_source_material(item)
    reading_view = material.get("reading_version")
    return base.content_hash(
        stable_json(
            {
                "type": "frontdesk_item",
                "identity": item_identity(item),
                "title": item.title,
                "source": item.source,
                "status": item.status,
                "summary": item.summary,
                "excerpt": item.excerpt,
                "questions": item.questions,
                "quality": item.quality,
                "links": [item.original_url, item.share_url, item.transcript_url],
                "value": item.value,
                "action": item.action,
                "distill_direction": item.distill_direction,
                "source_material": {
                    "path": material.get("path"),
                    "status": material.get("status"),
                    "publish_status": material.get("publish_status"),
                    "publish_ready": material.get("publish_ready"),
                    "body_hash": material.get("body_hash"),
                    "char_count": material.get("char_count"),
                    "warning": material.get("warning"),
                    "reading_version_hash": reading_view.digest() if isinstance(reading_view, reading.ReadingVersion) else "",
                },
            }
        )
    )


def transcript_hash(item: base.PushItem, reading_view: reading.ReadingVersion) -> str:
    return base.content_hash(
        stable_json(
            {
                "type": "frontdesk_transcript",
                "identity": item_identity(item),
                "title": item.title,
                "reading_version_hash": reading_view.digest(),
            }
        )
    )


def build_transcript_record(
    *,
    status: str,
    operation: str,
    item: base.PushItem,
    push_path: Path,
    draft_path: Path,
    digest: str,
    title: str,
    feishu_url: str = "",
    page_token: str = "",
    wiki_space_id: str = "",
    wiki_node_token: str = "",
    wiki_parent_node_token: str = "",
    command_output: str = "",
    wiki_move_output: str = "",
    error: str = "",
) -> dict[str, object]:
    return {
        "created_at": base.now_datetime(),
        "record_type": "frontdesk_transcript",
        "status": status,
        "operation": operation,
        "title": title,
        "source_push": base.repo_relative(push_path),
        "source_file": item.source_file,
        "item_identity": item_identity(item),
        "item_index": item.index,
        "draft_path": base.repo_relative(draft_path),
        "feishu_url": feishu_url,
        "page_token": page_token,
        "wiki_space_id": wiki_space_id,
        "wiki_node_token": wiki_node_token,
        "wiki_parent_node_token": wiki_parent_node_token,
        "content_hash": digest,
        "error": error,
        "command_output": command_output,
        "wiki_move_output": wiki_move_output,
    }


def index_hash(title: str, item_records: list[dict[str, object]], project_progress: str) -> str:
    return base.content_hash(
        stable_json(
            {
                "type": "frontdesk_bundle_index",
                "title": title,
                "items": [
                    {
                        "item_index": record.get("item_index"),
                        "title": record.get("title"),
                        "source_file": record.get("source_file"),
                        "wiki_node_token": record.get("wiki_node_token"),
                        "wiki_parent_directory": record.get("wiki_parent_directory"),
                        "feishu_url": record.get("feishu_url"),
                        "summary": record.get("summary"),
                        "reading_brief": record.get("reading_brief"),
                    }
                    for record in item_records
                ],
                "project_progress": project_progress,
            }
        )
    )


def build_item_record(
    *,
    status: str,
    operation: str,
    item: base.PushItem,
    push_path: Path,
    draft_path: Path,
    digest: str,
    title: str,
    feishu_url: str = "",
    page_token: str = "",
    wiki_space_id: str = "",
    wiki_node_token: str = "",
    wiki_parent_node_token: str = "",
    wiki_parent_directory: str = "",
    wiki_parent_reason: str = "",
    source_material: dict[str, object] | None = None,
    transcript_record: dict[str, object] | None = None,
    visual_evidence: list[visuals.VisualEvidence] | None = None,
    materialized_visuals: list[visuals.MaterializedVisual] | None = None,
    visual_embedded_ids: list[str] | None = None,
    visual_errors: list[str] | None = None,
    visual_output: str = "",
    command_output: str = "",
    wiki_move_output: str = "",
    error: str = "",
) -> dict[str, object]:
    material = source_material or {}
    transcript = transcript_record or {}
    reading_view = material.get("reading_version")
    evidence = visual_evidence or []
    materialized = materialized_visuals or []
    return {
        "created_at": base.now_datetime(),
        "record_type": "frontdesk_item",
        "status": status,
        "operation": operation,
        "title": title,
        "source_push": base.repo_relative(push_path),
        "source_file": item.source_file,
        "item_identity": item_identity(item),
        "item_index": item.index,
        "draft_path": base.repo_relative(draft_path),
        "feishu_url": feishu_url,
        "page_token": page_token,
        "wiki_space_id": wiki_space_id,
        "wiki_node_token": wiki_node_token,
        "wiki_parent_node_token": wiki_parent_node_token,
        "wiki_parent_directory": wiki_parent_directory,
        "wiki_parent_reason": wiki_parent_reason,
        "content_hash": digest,
        "source": item.source,
        "summary": item.summary,
        "reading_brief": reading_view.conclusion if isinstance(reading_view, reading.ReadingVersion) else item.summary,
        "original_url": item.original_url,
        "share_url": item.share_url,
        "source_material_status": material.get("status", ""),
        "source_material_path": material.get("path", ""),
        "source_material_chars": material.get("char_count", 0),
        "source_material_hash": material.get("body_hash", ""),
        "source_material_warning": material.get("warning", ""),
        "source_material_publish_status": material.get("publish_status", ""),
        "source_material_publish_ready": material.get("publish_ready", False),
        "source_material_publish_blockers": material.get("publish_blockers", []),
        "source_material_publish_evidence": material.get("publish_evidence", []),
        "reading_version_hash": reading_view.digest() if isinstance(reading_view, reading.ReadingVersion) else "",
        "transcript_mode": reading_view.transcript_mode if isinstance(reading_view, reading.ReadingVersion) else "none",
        "transcript_draft_path": transcript.get("draft_path", ""),
        "transcript_feishu_url": transcript.get("feishu_url", ""),
        "transcript_page_token": transcript.get("page_token", ""),
        "transcript_content_hash": transcript.get("content_hash", ""),
        "visual_evidence_hash": visuals.manifest_hash(evidence),
        "visual_evidence": [item.record() for item in evidence],
        "visual_materialization": [item.record() for item in materialized],
        "visual_embedded_ids": visual_embedded_ids or [],
        "visual_errors": visual_errors or [],
        "visual_output": visual_output,
        "error": error,
        "command_output": command_output,
        "wiki_move_output": wiki_move_output,
    }


def build_index_record(
    *,
    status: str,
    operation: str,
    title: str,
    push_path: Path,
    draft_path: Path,
    digest: str,
    item_records: list[dict[str, object]],
    feishu_url: str = "",
    page_token: str = "",
    wiki_space_id: str = "",
    wiki_node_token: str = "",
    wiki_parent_node_token: str = "",
    command_output: str = "",
    wiki_move_output: str = "",
    error: str = "",
) -> dict[str, object]:
    return {
        "created_at": base.now_datetime(),
        "record_type": "frontdesk_bundle_index",
        "status": status,
        "operation": operation,
        "title": title,
        "source_push": base.repo_relative(push_path),
        "draft_path": base.repo_relative(draft_path),
        "feishu_url": feishu_url,
        "page_token": page_token,
        "wiki_space_id": wiki_space_id,
        "wiki_node_token": wiki_node_token,
        "wiki_parent_node_token": wiki_parent_node_token,
        "content_hash": digest,
        "item_count": len(item_records),
        "items": [
            {
                "index": record.get("item_index"),
                "title": record.get("title"),
                "source_file": record.get("source_file"),
                "summary": record.get("summary"),
                "reading_brief": record.get("reading_brief") or record.get("summary"),
                "feishu_url": record.get("feishu_url"),
                "wiki_node_token": record.get("wiki_node_token"),
                "wiki_url": wiki_url(record),
            }
            for record in item_records
        ],
        "error": error,
        "command_output": command_output,
        "wiki_move_output": wiki_move_output,
    }


def publish_one(
    *,
    existing: dict[str, object] | None,
    title: str,
    draft_path: Path,
    push_path: Path,
    record_file: Path,
    digest: str,
    create_command: str,
    update_command: str,
    wiki_space_id: str,
    wiki_parent_node_token: str,
    wiki_move_command: str,
    force: bool,
) -> tuple[str, str, str, str, str, str]:
    if existing and existing.get("content_hash") == digest and existing.get("feishu_url") and not force:
        existing_parent = str(existing.get("wiki_parent_node_token") or "")
        wiki_node_token = str(existing.get("wiki_node_token") or "")
        wiki_move_output = ""
        operation = "reused"
        if wiki_parent_node_token:
            if wiki_node_token and existing_parent != wiki_parent_node_token:
                wiki_move_output = run_wiki_node_move_command(
                    wiki_node_token=wiki_node_token,
                    wiki_space_id=wiki_space_id,
                    wiki_parent_node_token=wiki_parent_node_token,
                )
                operation = "reused-moved"
            elif not wiki_node_token and existing.get("page_token"):
                wiki_node_token, wiki_move_output = base.run_wiki_move_command(
                    page_token=str(existing.get("page_token") or ""),
                    title=title,
                    feishu_url=str(existing.get("feishu_url") or ""),
                    wiki_space_id=wiki_space_id,
                    wiki_parent_node_token=wiki_parent_node_token,
                    wiki_move_command=wiki_move_command,
                )
                operation = "reused-moved"
        return (
            operation,
            str(existing.get("feishu_url") or ""),
            str(existing.get("page_token") or ""),
            wiki_node_token,
            "",
            wiki_move_output,
        )

    if existing and existing.get("page_token") and update_command and not force:
        feishu_url, page_token, output = run_template_command(
            update_command,
            markdown_file=draft_path,
            title=title,
            push_path=push_path,
            record_file=record_file,
            digest=digest,
            page_token=str(existing.get("page_token") or ""),
        )
        return (
            "updated",
            feishu_url or str(existing.get("feishu_url") or ""),
            page_token or str(existing.get("page_token") or ""),
            str(existing.get("wiki_node_token") or ""),
            output,
            run_wiki_node_move_command(
                wiki_node_token=str(existing.get("wiki_node_token") or ""),
                wiki_space_id=wiki_space_id,
                wiki_parent_node_token=wiki_parent_node_token,
            )
            if wiki_parent_node_token
            and str(existing.get("wiki_node_token") or "")
            and str(existing.get("wiki_parent_node_token") or "") != wiki_parent_node_token
            else "",
        )

    feishu_url, page_token, output = run_template_command(
        create_command,
        markdown_file=draft_path,
        title=title,
        push_path=push_path,
        record_file=record_file,
        digest=digest,
    )
    wiki_node_token = ""
    wiki_move_output = ""
    if wiki_space_id or wiki_move_command:
        wiki_node_token, wiki_move_output = base.run_wiki_move_command(
            page_token=page_token,
            title=title,
            feishu_url=feishu_url,
            wiki_space_id=wiki_space_id,
            wiki_parent_node_token=wiki_parent_node_token,
            wiki_move_command=wiki_move_command,
        )
    return "created", feishu_url, page_token, wiki_node_token, output, wiki_move_output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Publish frontdesk push as Feishu item pages plus an index page.")
    parser.add_argument("--push-file", default="", help="Frontdesk push markdown path. Defaults to latest 85_运行记录/前台推送-*.md.")
    parser.add_argument("--run-dir", default=str(DEFAULT_RUN_DIR))
    parser.add_argument("--draft-dir", default=str(DEFAULT_DRAFT_DIR))
    parser.add_argument("--transcript-dir", default=str(DEFAULT_TRANSCRIPT_DIR), help="Local drafts for separate full-transcript pages.")
    parser.add_argument("--record-file", default=str(DEFAULT_RECORD_FILE))
    parser.add_argument("--title", default="", help="Index page title. Defaults to my-mind 今日精选 YYYY-MM-DD.")
    parser.add_argument("--max-items", type=int, default=0, help="Maximum items to publish. 0 means all.")
    parser.add_argument("--source-file", default="", help="Only process one exact repository-relative source file.")
    parser.add_argument("--no-index", action="store_true", help="Update item/transcript pages without rewriting the daily index; intended for bounded backfill.")
    parser.add_argument("--publish-command", default=os.environ.get("MY_MIND_FEISHU_PUBLISH_COMMAND", DEFAULT_CREATE_COMMAND))
    parser.add_argument("--update-command", default=os.environ.get("MY_MIND_FEISHU_UPDATE_COMMAND", DEFAULT_UPDATE_COMMAND))
    parser.add_argument("--wiki-move-space-id", default=os.environ.get("MY_MIND_FEISHU_WIKI_SPACE_ID", ""))
    parser.add_argument("--wiki-move-parent-node-token", default=os.environ.get("MY_MIND_FEISHU_WIKI_PARENT_NODE_TOKEN", ""))
    parser.add_argument("--item-wiki-parent-node-token", default=os.environ.get("MY_MIND_FEISHU_ITEM_WIKI_PARENT_NODE_TOKEN", ""))
    parser.add_argument("--item-parent-map", default=os.environ.get("MY_MIND_FEISHU_ITEM_PARENT_MAP", str(DEFAULT_ITEM_PARENT_MAP)))
    parser.add_argument("--gate-queue", default=str(DEFAULT_GATE_QUEUE), help="Markdown queue for Feishu publish items blocked by completeness gate.")
    parser.add_argument("--gate-report-dir", default=str(DEFAULT_GATE_REPORT_DIR), help="Directory for timestamped Feishu publish gate reports.")
    parser.add_argument("--visual-cache-dir", default=str(DEFAULT_VISUAL_CACHE_DIR), help="Local cache for source images and video frames uploaded to Feishu.")
    parser.add_argument("--max-visuals", type=int, default=visuals.MAX_VISUALS, help="Maximum source visuals per item. Default: 4.")
    parser.add_argument("--visual-timeout-seconds", type=int, default=visuals.DEFAULT_TIMEOUT_SECONDS, help="Per-source media download or frame-extraction timeout.")
    parser.add_argument("--visual-width", type=int, default=DEFAULT_VISUAL_WIDTH, help="Feishu display width for inserted images.")
    parser.add_argument("--no-auto-item-parent-map", action="store_true", help="Disable per-item Feishu directory inference.")
    parser.add_argument("--no-visual-evidence", dest="visual_evidence", action="store_false", default=True, help="Do not fetch or insert source visuals during publish.")
    parser.add_argument("--include-incomplete", action="store_true", help="Publish items even when source material is incomplete. Default blocks them.")
    parser.add_argument("--no-gate-queue", action="store_true", help="Do not write the Feishu publish completeness queue/report.")
    parser.add_argument("--wiki-move-command", default=os.environ.get("MY_MIND_FEISHU_WIKI_MOVE_COMMAND", ""))
    parser.add_argument("--force", action="store_true", help="Create instead of reusing/updating existing records.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Preview only. Default mode.")
    mode.add_argument("--write-local", action="store_true", help="Write local drafts and append draft records.")
    mode.add_argument("--publish", action="store_true", help="Publish item pages and index page to Feishu.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    run_dir = resolve_path(args.run_dir)
    draft_dir = resolve_path(args.draft_dir)
    transcript_dir = resolve_path(args.transcript_dir)
    record_file = resolve_path(args.record_file)
    gate_queue = resolve_path(args.gate_queue)
    gate_report_dir = resolve_path(args.gate_report_dir)
    visual_cache_dir = resolve_path(args.visual_cache_dir)
    push_path = resolve_path(args.push_file, default=base.latest_push(run_dir)) if args.push_file else base.latest_push(run_dir)

    title = args.title or f"my-mind 今日精选 {dt.datetime.now(TZ).strftime('%Y-%m-%d')}"
    push_text = base.read_text(push_path)
    generated_at = base.parse_generated_at(push_text)
    project_progress = base.section_between(push_text, "项目进度")
    items = base.parse_items(push_text)
    if args.source_file:
        items = [item for item in items if item.source_file == args.source_file]
    if args.max_items > 0:
        items = items[: args.max_items]
    if not items:
        print("错误：没有找到符合条件的前台推送条目。", file=sys.stderr)
        return 2
    item_materials = [(item, load_source_material(item)) for item in items]
    visual_plans = {
        item_identity(item): build_item_visual_evidence(item, material, max_visuals=args.max_visuals)
        for item, material in item_materials
    }
    blocked_items = [(item, material) for item, material in item_materials if not material.get("publish_ready")]
    publishable_items = item_materials if args.include_incomplete else [
        (item, material) for item, material in item_materials if material.get("publish_ready")
    ]

    records = base.load_records(record_file)
    inferred_space_id, inferred_index_parent = latest_wiki_context(records)
    wiki_space_id = args.wiki_move_space_id or inferred_space_id
    index_parent_token = args.wiki_move_parent_node_token or inferred_index_parent
    item_parent_map = {} if args.no_auto_item_parent_map else load_item_parent_map(args.item_parent_map)
    item_draft_dir = draft_dir / "单篇"
    index_draft_dir = draft_dir / "索引"

    if not args.write_local and not args.publish:
        print(f"# {title}")
        print()
        print(f"- 来源推送：`{base.repo_relative(push_path)}`")
        print(f"- 将生成单篇文章：{len(publishable_items)}")
        print(f"- 门禁拦截：{0 if args.include_incomplete else len(blocked_items)}")
        print(f"- 将生成索引页：{0 if args.no_index else 1}")
        print()
        for item, source_material in item_materials:
            evidence = visual_plans[item_identity(item)]
            existing = latest_record_for(records, record_type="frontdesk_item", key="item_identity", value=item_identity(item))
            operation = "reuse" if existing and existing.get("content_hash") == item_hash(item, source_material) else "update" if existing else "create"
            if not args.include_incomplete and not source_material.get("publish_ready"):
                operation = "blocked"
            if args.item_wiki_parent_node_token:
                parent_directory = "显式指定目录"
                parent_reason = "使用 --item-wiki-parent-node-token"
            else:
                _, parent_directory, parent_reason = infer_item_parent(item, item_parent_map)
                if not parent_directory:
                    parent_directory = "未配置，回落到索引目录" if index_parent_token else "未配置"
            reading_view = source_material.get("reading_version")
            transcript_plan = reading_view.transcript_mode if isinstance(reading_view, reading.ReadingVersion) else "none"
            print(
                f"- {item.index}. {item.title}：{operation}；单篇目录：{parent_directory}（{parent_reason}）；"
                f"来源正本：{source_material.get('publish_status')} / {source_material.get('char_count')} 字；"
                f"视觉候选：{len(evidence)}；完整转写：{transcript_plan}"
            )
        return 0

    if blocked_items and not args.include_incomplete and not args.no_gate_queue:
        write_gate_outputs(blocked_items, push_path=push_path, queue_path=gate_queue, report_dir=gate_report_dir)
    if not publishable_items and blocked_items and not args.include_incomplete:
        print(
            f"错误：飞书精选发布门禁拦截了全部 {len(blocked_items)} 条内容。"
            f"请先处理 `{base.repo_relative(gate_queue)}` 后重试，或显式使用 --include-incomplete。",
            file=sys.stderr,
        )
        return 2

    item_draft_dir.mkdir(parents=True, exist_ok=True)
    index_draft_dir.mkdir(parents=True, exist_ok=True)

    item_records: list[dict[str, object]] = []
    for item, source_material in publishable_items:
        item_title = clean_title(item.title)
        evidence = visual_plans[item_identity(item)] if args.visual_evidence else []
        if args.item_wiki_parent_node_token:
            item_parent = args.item_wiki_parent_node_token
            item_parent_directory = "显式指定目录"
            item_parent_reason = "使用 --item-wiki-parent-node-token"
        else:
            inferred_parent, item_parent_directory, item_parent_reason = infer_item_parent(item, item_parent_map)
            item_parent = inferred_parent or index_parent_token
            if not inferred_parent and index_parent_token:
                item_parent_directory = item_parent_directory or "索引页目录"
                item_parent_reason = f"{item_parent_reason}，回落到 --wiki-move-parent-node-token"

        reading_view = source_material.get("reading_version")
        transcript_record: dict[str, object] | None = None
        transcript_href = ""
        if isinstance(reading_view, reading.ReadingVersion) and reading_view.transcript_mode == "separate":
            transcript_dir.mkdir(parents=True, exist_ok=True)
            transcript_title = clean_title(f"完整转写｜{item.title}")
            transcript_draft_path = stable_transcript_draft_path(transcript_dir, item)
            transcript_draft_path.write_text(reading.transcript_markdown(item.title, reading_view), encoding="utf-8")
            transcript_digest = transcript_hash(item, reading_view)
            existing_transcript = latest_record_for(
                records,
                record_type="frontdesk_transcript",
                key="item_identity",
                value=item_identity(item),
            )
            if args.write_local:
                transcript_record = build_transcript_record(
                    status="草稿已生成",
                    operation="draft",
                    item=item,
                    push_path=push_path,
                    draft_path=transcript_draft_path,
                    digest=transcript_digest,
                    title=transcript_title,
                    wiki_parent_node_token=item_parent,
                )
                transcript_href = Path("..") / transcript_draft_path.parent.name / transcript_draft_path.name
                transcript_href = transcript_href.as_posix()
            else:
                try:
                    (
                        transcript_operation,
                        transcript_url,
                        transcript_page_token,
                        transcript_wiki_node_token,
                        transcript_output,
                        transcript_move_output,
                    ) = publish_one(
                        existing=existing_transcript,
                        title=transcript_title,
                        draft_path=transcript_draft_path,
                        push_path=push_path,
                        record_file=record_file,
                        digest=transcript_digest,
                        create_command=args.publish_command,
                        update_command=args.update_command,
                        wiki_space_id=wiki_space_id,
                        wiki_parent_node_token=item_parent,
                        wiki_move_command=args.wiki_move_command,
                        force=args.force,
                    )
                    transcript_record = build_transcript_record(
                        status="已发布",
                        operation=transcript_operation,
                        item=item,
                        push_path=push_path,
                        draft_path=transcript_draft_path,
                        digest=transcript_digest,
                        title=transcript_title,
                        feishu_url=transcript_url,
                        page_token=transcript_page_token,
                        wiki_space_id=wiki_space_id,
                        wiki_node_token=transcript_wiki_node_token,
                        wiki_parent_node_token=item_parent,
                        command_output=transcript_output,
                        wiki_move_output=transcript_move_output,
                    )
                    transcript_href = wiki_url(transcript_record) or transcript_url
                except Exception as exc:  # noqa: BLE001
                    transcript_record = build_transcript_record(
                        status="发布失败",
                        operation="failed",
                        item=item,
                        push_path=push_path,
                        draft_path=transcript_draft_path,
                        digest=transcript_digest,
                        title=transcript_title,
                        wiki_parent_node_token=item_parent,
                        error=str(exc),
                    )
                    base.append_record(record_file, transcript_record)
                    print(f"错误：完整转写发布失败：{item.index}. {item.title}：{exc}", file=sys.stderr)
                    return 1
            base.append_record(record_file, transcript_record)
            records.append(transcript_record)

        rendered = render_item_page(item, push_path, generated_at, source_material, evidence, transcript_href)
        digest = item_hash(item, source_material)
        draft_path = stable_item_draft_path(item_draft_dir, item)
        draft_path.write_text(rendered, encoding="utf-8")
        existing = latest_record_for(records, record_type="frontdesk_item", key="item_identity", value=item_identity(item))
        if args.write_local:
            record = build_item_record(
                status="草稿已生成",
                operation="draft",
                item=item,
                push_path=push_path,
                draft_path=draft_path,
                digest=digest,
                title=item_title,
                wiki_parent_node_token=item_parent,
                wiki_parent_directory=item_parent_directory,
                wiki_parent_reason=item_parent_reason,
                source_material=source_material,
                transcript_record=transcript_record,
                visual_evidence=evidence,
            )
        else:
            try:
                operation, feishu_url, page_token, wiki_node_token, command_output, wiki_move_output = publish_one(
                    existing=existing,
                    title=item_title,
                    draft_path=draft_path,
                    push_path=push_path,
                    record_file=record_file,
                    digest=digest,
                    create_command=args.publish_command,
                    update_command=args.update_command,
                    wiki_space_id=wiki_space_id,
                    wiki_parent_node_token=item_parent,
                    wiki_move_command=args.wiki_move_command,
                    force=args.force,
                )
                materialized_visuals: list[visuals.MaterializedVisual] = []
                visual_embedded_ids: list[str] = []
                visual_errors: list[str] = []
                visual_output = ""
                if evidence:
                    try:
                        materialized_visuals = visuals.materialize_visual_evidence(
                            evidence,
                            output_dir=visual_cache_path(visual_cache_dir, item),
                            timeout_seconds=max(1, args.visual_timeout_seconds),
                        )
                        document_target = page_token or feishu_url
                        if document_target:
                            visual_embedded_ids, visual_errors, visual_output = embed_visual_evidence(
                                page_token=document_target,
                                materialized=materialized_visuals,
                                existing=existing,
                                width=args.visual_width,
                            )
                        else:
                            visual_errors = ["飞书页面未返回页面标识，未插入图片。"]
                    except Exception as exc:  # noqa: BLE001 - visual failures should not invalidate a published reading page.
                        visual_errors = [f"视觉证据处理失败：{exc!r}"]
                record = build_item_record(
                    status="已发布",
                    operation=operation,
                    item=item,
                    push_path=push_path,
                    draft_path=draft_path,
                    digest=digest,
                    title=item_title,
                    feishu_url=feishu_url,
                    page_token=page_token,
                    wiki_space_id=wiki_space_id,
                    wiki_node_token=wiki_node_token,
                    wiki_parent_node_token=item_parent,
                    wiki_parent_directory=item_parent_directory,
                    wiki_parent_reason=item_parent_reason,
                    source_material=source_material,
                    transcript_record=transcript_record,
                    visual_evidence=evidence,
                    materialized_visuals=materialized_visuals,
                    visual_embedded_ids=visual_embedded_ids,
                    visual_errors=visual_errors,
                    visual_output=visual_output,
                    command_output=command_output,
                    wiki_move_output=wiki_move_output,
                )
            except Exception as exc:  # noqa: BLE001
                record = build_item_record(
                    status="发布失败",
                    operation="failed",
                    item=item,
                    push_path=push_path,
                    draft_path=draft_path,
                    digest=digest,
                    title=item_title,
                    wiki_parent_node_token=item_parent,
                    wiki_parent_directory=item_parent_directory,
                    wiki_parent_reason=item_parent_reason,
                    source_material=source_material,
                    transcript_record=transcript_record,
                    visual_evidence=evidence,
                    error=str(exc),
                )
                base.append_record(record_file, record)
                print(f"错误：单篇发布失败：{item.index}. {item.title}：{exc}", file=sys.stderr)
                return 1
        base.append_record(record_file, record)
        records.append(record)
        item_records.append(record)

    if args.no_index:
        print(str(item_records[-1].get("draft_path") or "") if args.write_local else wiki_url(item_records[-1]))
        return 0

    index_rendered = render_index_page(
        title=title,
        push_path=push_path,
        generated_at=generated_at,
        item_records=item_records,
        project_progress=project_progress,
    )
    digest = index_hash(title, item_records, project_progress)
    index_draft_path = stable_index_draft_path(index_draft_dir)
    index_draft_path.write_text(index_rendered, encoding="utf-8")

    existing_index = latest_record_for(records, record_type="frontdesk_bundle_index", key="title", value=title)
    if args.write_local:
        index_record = build_index_record(
            status="草稿已生成",
            operation="draft",
            title=title,
            push_path=push_path,
            draft_path=index_draft_path,
            digest=digest,
            item_records=item_records,
        )
        base.append_record(record_file, index_record)
        print(base.repo_relative(index_draft_path))
        return 0

    try:
        operation, feishu_url, page_token, wiki_node_token, command_output, wiki_move_output = publish_one(
            existing=existing_index,
            title=title,
            draft_path=index_draft_path,
            push_path=push_path,
            record_file=record_file,
            digest=digest,
            create_command=args.publish_command,
            update_command=args.update_command,
            wiki_space_id=wiki_space_id,
            wiki_parent_node_token=index_parent_token,
            wiki_move_command=args.wiki_move_command,
            force=args.force,
        )
        index_record = build_index_record(
            status="已发布",
            operation=operation,
            title=title,
            push_path=push_path,
            draft_path=index_draft_path,
            digest=digest,
            item_records=item_records,
            feishu_url=feishu_url,
            page_token=page_token,
            wiki_space_id=wiki_space_id,
            wiki_node_token=wiki_node_token,
            wiki_parent_node_token=index_parent_token,
            command_output=command_output,
            wiki_move_output=wiki_move_output,
        )
        base.append_record(record_file, index_record)
        print(wiki_url(index_record) or feishu_url or base.repo_relative(index_draft_path))
        return 0
    except Exception as exc:  # noqa: BLE001
        index_record = build_index_record(
            status="发布失败",
            operation="failed",
            title=title,
            push_path=push_path,
            draft_path=index_draft_path,
            digest=digest,
            item_records=item_records,
            error=str(exc),
        )
        base.append_record(record_file, index_record)
        print(f"错误：索引发布失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
