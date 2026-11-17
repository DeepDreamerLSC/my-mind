#!/usr/bin/env python3
"""Audit and backfill source-backed visuals into existing Feishu knowledge pages."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
import sys

import publish_feishu_reading as base
import publish_frontdesk_bundle as bundle
import visual_evidence as visuals


ROOT = base.ROOT
DEFAULT_FRONTDESK_RECORD_FILE = base.DEFAULT_RECORD_FILE
DEFAULT_LIBRARY_RECORD_FILE = ROOT / "85_运行记录" / "飞书知识库同步记录.jsonl"
DEFAULT_BACKFILL_RECORD_FILE = ROOT / "85_运行记录" / "飞书视觉补全记录.jsonl"
DEFAULT_VISUAL_CACHE_DIR = bundle.DEFAULT_VISUAL_CACHE_DIR / "存量"


@dataclass(frozen=True)
class ExistingPage:
    lane: str
    source_file: str
    title: str
    original_url: str
    page_token: str
    feishu_url: str
    record: dict[str, object]

    @property
    def document_target(self) -> str:
        return self.page_token or self.feishu_url

    @property
    def identity(self) -> str:
        return f"{self.lane}:{self.source_file}:{self.document_target}"


def as_text(value: object) -> str:
    return str(value or "").strip()


def is_frontdesk_record(record: dict[str, object]) -> bool:
    return record.get("record_type") == "frontdesk_item" and as_text(record.get("status")).startswith("已发布")


def is_library_record(record: dict[str, object]) -> bool:
    return as_text(record.get("status")) == "已同步" and bool(as_text(record.get("source_file")))


def page_from_record(lane: str, record: dict[str, object]) -> ExistingPage | None:
    source_file = as_text(record.get("source_file"))
    page_token = as_text(record.get("page_token"))
    feishu_url = as_text(record.get("feishu_url"))
    if not source_file or not (page_token or feishu_url):
        return None
    return ExistingPage(
        lane=lane,
        source_file=source_file,
        title=as_text(record.get("title")) or Path(source_file).stem,
        original_url=as_text(record.get("original_url")),
        page_token=page_token,
        feishu_url=feishu_url,
        record=record,
    )


def latest_frontdesk_pages(records: list[dict[str, object]]) -> list[ExistingPage]:
    latest: dict[str, ExistingPage] = {}
    for record in records:
        if not is_frontdesk_record(record):
            continue
        page = page_from_record("前台精选", record)
        if page:
            key = as_text(record.get("item_identity")) or page.source_file
            latest[key] = page
    return sorted(latest.values(), key=lambda page: (page.source_file, page.title))


def latest_library_pages(records: list[dict[str, object]]) -> list[ExistingPage]:
    latest: dict[str, ExistingPage] = {}
    for record in records:
        if not is_library_record(record):
            continue
        page = page_from_record("长期知识", record)
        if page:
            latest[page.source_file] = page
    return sorted(latest.values(), key=lambda page: (page.source_file, page.title))


def load_pages(
    *,
    frontdesk_records: list[dict[str, object]],
    library_records: list[dict[str, object]],
    lane: str,
    source_file: str = "",
) -> list[ExistingPage]:
    pages: list[ExistingPage] = []
    if lane in {"all", "frontdesk"}:
        pages.extend(latest_frontdesk_pages(frontdesk_records))
    if lane in {"all", "library"}:
        pages.extend(latest_library_pages(library_records))
    if source_file:
        pages = [page for page in pages if page.source_file == source_file]
    return sorted(pages, key=lambda page: (page.lane, page.source_file, page.title))


def prior_visual_ids(records: list[dict[str, object]], page: ExistingPage) -> set[str]:
    result: set[str] = set()
    direct = page.record.get("visual_embedded_ids")
    if isinstance(direct, list):
        result.update(as_text(value) for value in direct if as_text(value))
    for record in records:
        if record.get("record_type") != "feishu_visual_backfill":
            continue
        if as_text(record.get("page_identity")) != page.identity:
            continue
        embedded = record.get("visual_embedded_ids")
        if isinstance(embedded, list):
            result.update(as_text(value) for value in embedded if as_text(value))
    return result


def item_from_page(page: ExistingPage) -> base.PushItem:
    try:
        index = int(page.record.get("item_index") or 0)
    except (TypeError, ValueError):
        index = 0
    return base.PushItem(
        index=index,
        title=page.title,
        source=as_text(page.record.get("source")),
        source_file=page.source_file,
        original_url=page.original_url,
        share_url=as_text(page.record.get("share_url")),
        transcript_url=as_text(page.record.get("transcript_url")),
        summary=as_text(page.record.get("summary")),
    )


def page_visual_plan(page: ExistingPage, max_visuals: int) -> tuple[base.PushItem, dict[str, object], list[visuals.VisualEvidence]]:
    item = item_from_page(page)
    source_material = bundle.load_source_material(item)
    evidence = bundle.build_item_visual_evidence(item, source_material, max_visuals=max_visuals)
    return item, source_material, evidence


def visual_summary(evidence: list[visuals.VisualEvidence]) -> str:
    counts = Counter(item.kind for item in evidence)
    labels = {
        "cover": "封面",
        "source_image": "来源图",
        "local_image": "本地图",
        "video_frame": "视频帧",
    }
    return "、".join(f"{labels.get(kind, kind)} {count}" for kind, count in sorted(counts.items())) or "无"


def cache_path(root: Path, page: ExistingPage) -> Path:
    return root / visuals.evidence_id("backfill", page.identity)


def build_backfill_record(
    *,
    page: ExistingPage,
    source_material: dict[str, object],
    evidence: list[visuals.VisualEvidence],
    materialized: list[visuals.MaterializedVisual],
    embedded_ids: list[str],
    errors: list[str],
    output: str,
) -> dict[str, object]:
    return {
        "created_at": base.now_datetime(),
        "record_type": "feishu_visual_backfill",
        "status": "已补全" if not errors else "部分补全" if embedded_ids else "补全失败",
        "lane": page.lane,
        "page_identity": page.identity,
        "source_file": page.source_file,
        "title": page.title,
        "feishu_url": page.feishu_url,
        "page_token": page.page_token,
        "source_material_path": source_material.get("path", ""),
        "source_material_status": source_material.get("publish_status", ""),
        "visual_evidence_hash": visuals.manifest_hash(evidence),
        "visual_evidence": [item.record() for item in evidence],
        "visual_materialization": [item.record() for item in materialized],
        "visual_embedded_ids": embedded_ids,
        "visual_errors": errors,
        "visual_output": output,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit or backfill source-backed visuals for existing Feishu pages.")
    parser.add_argument("--frontdesk-record-file", default=str(DEFAULT_FRONTDESK_RECORD_FILE))
    parser.add_argument("--library-record-file", default=str(DEFAULT_LIBRARY_RECORD_FILE))
    parser.add_argument("--backfill-record-file", default=str(DEFAULT_BACKFILL_RECORD_FILE))
    parser.add_argument("--visual-cache-dir", default=str(DEFAULT_VISUAL_CACHE_DIR))
    parser.add_argument("--lane", choices=("all", "frontdesk", "library"), default="all")
    parser.add_argument("--source-file", default="", help="Backfill one exact repository-relative source path.")
    parser.add_argument("--max-items", type=int, default=0, help="Maximum existing pages to inspect. 0 means all.")
    parser.add_argument("--max-visuals", type=int, default=visuals.MAX_VISUALS)
    parser.add_argument("--visual-timeout-seconds", type=int, default=visuals.DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--visual-width", type=int, default=bundle.DEFAULT_VISUAL_WIDTH)
    parser.add_argument("--include-incomplete", action="store_true", help="Also backfill pages whose current source material does not pass the reading-quality gate.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Preview visual candidates only. Default mode.")
    mode.add_argument("--publish", action="store_true", help="Materialize and insert source visuals into existing Feishu pages.")
    return parser.parse_args()


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def main() -> int:
    args = parse_args()
    frontdesk_records = base.load_records(resolve_path(args.frontdesk_record_file))
    library_records = base.load_records(resolve_path(args.library_record_file))
    backfill_record_file = resolve_path(args.backfill_record_file)
    backfill_records = base.load_records(backfill_record_file)
    pages = load_pages(
        frontdesk_records=frontdesk_records,
        library_records=library_records,
        lane=args.lane,
        source_file=args.source_file,
    )
    if args.max_items > 0:
        pages = pages[: args.max_items]

    planned: list[tuple[ExistingPage, base.PushItem, dict[str, object], list[visuals.VisualEvidence], set[str]]] = []
    for page in pages:
        item, source_material, evidence = page_visual_plan(page, args.max_visuals)
        known_ids = prior_visual_ids(backfill_records, page)
        planned.append((page, item, source_material, evidence, known_ids))

    eligible = planned if args.include_incomplete else [
        entry for entry in planned if bool(entry[2].get("publish_ready"))
    ]
    blocked = [entry for entry in planned if entry not in eligible]
    actionable = [entry for entry in eligible if any(item.evidence_id not in entry[4] for item in entry[3])]
    if not args.publish:
        lane_counts = Counter(page.lane for page, *_ in planned)
        candidate_count = sum(len(evidence) for _, _, _, evidence, _ in eligible)
        pending_count = sum(sum(item.evidence_id not in known_ids for item in evidence) for _, _, _, evidence, known_ids in eligible)
        print("# 飞书知识库视觉补全预演")
        print()
        print(f"- 已发布页面：{len(planned)}（前台精选 {lane_counts['前台精选']}，长期知识 {lane_counts['长期知识']}）")
        print(f"- 来源质量通过：{len(eligible)}；质量门禁跳过：{len(blocked)}")
        print(f"- 含来源视觉页面：{sum(bool(evidence) for _, _, _, evidence, _ in eligible)}")
        print(f"- 视觉候选：{candidate_count}；待插入：{pending_count}")
        print(f"- 已完成或无候选：{len(planned) - len(actionable)}")
        print()
        for page, _, source_material, evidence, known_ids in actionable:
            pending = [item for item in evidence if item.evidence_id not in known_ids]
            print(
                f"- [{page.lane}] {page.title}：待补 {len(pending)} 张（{visual_summary(pending)}）；"
                f"来源正本：{source_material.get('publish_status') or '未知'}"
            )
        return 0

    visual_cache_dir = resolve_path(args.visual_cache_dir)
    completed = 0
    partial = 0
    for page, _, source_material, evidence, known_ids in actionable:
        pending = [item for item in evidence if item.evidence_id not in known_ids]
        materialized: list[visuals.MaterializedVisual] = []
        inserted_ids: list[str] = []
        errors: list[str] = []
        output = ""
        try:
            materialized = visuals.materialize_visual_evidence(
                pending,
                output_dir=cache_path(visual_cache_dir, page),
                timeout_seconds=max(1, args.visual_timeout_seconds),
            )
            inserted_ids, errors, output = bundle.embed_visual_evidence(
                page_token=page.document_target,
                materialized=materialized,
                existing={"page_token": page.document_target, "visual_embedded_ids": sorted(known_ids)},
                width=args.visual_width,
            )
        except Exception as exc:  # noqa: BLE001 - one source must not stop a bounded backfill batch.
            errors = [f"视觉补全失败：{exc!r}"]
        record = build_backfill_record(
            page=page,
            source_material=source_material,
            evidence=pending,
            materialized=materialized,
            embedded_ids=inserted_ids,
            errors=errors,
            output=output,
        )
        base.append_record(backfill_record_file, record)
        if errors:
            partial += 1
            print(f"部分补全：[{page.lane}] {page.title}：{'；'.join(errors)}", file=sys.stderr)
        else:
            completed += 1
            print(f"已补全：[{page.lane}] {page.title}：{len(inserted_ids)} 张")
    print(f"完成：{completed} 页已补全，{partial} 页存在素材错误。")
    return 0 if not partial else 1


if __name__ == "__main__":
    raise SystemExit(main())
