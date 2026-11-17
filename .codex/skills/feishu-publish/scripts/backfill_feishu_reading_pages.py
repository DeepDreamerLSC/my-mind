#!/usr/bin/env python3
"""Audit or rebuild existing Feishu media pages with the layered reading layout."""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import publish_feishu_reading as base
import publish_frontdesk_bundle as bundle
import reading_version as reading


ROOT = base.ROOT
DEFAULT_RECORD_FILE = base.DEFAULT_RECORD_FILE
PUBLISHER = Path(__file__).with_name("publish_frontdesk_bundle.py")


@dataclass(frozen=True)
class RebuildCandidate:
    record: dict[str, object]
    item: base.PushItem
    material: dict[str, object]
    reading_view: reading.ReadingVersion | None

    @property
    def source_file(self) -> str:
        return str(self.record.get("source_file") or "")

    @property
    def source_push(self) -> str:
        return str(self.record.get("source_push") or "")

    @property
    def eligible(self) -> bool:
        return bool(self.material.get("publish_ready") and self.reading_view and self.source_push)

    @property
    def media_like(self) -> bool:
        haystack = " ".join(
            (
                self.item.title,
                self.item.source,
                self.item.source_file,
                self.item.original_url,
                str(self.material.get("source_type") or ""),
            )
        ).casefold()
        markers = ("抖音", "douyin", "youtube", "youtu.be", "视频", "音频", "播客", "podcast")
        return any(marker in haystack for marker in markers)

    @property
    def duration_class(self) -> str:
        if not self.reading_view:
            return "非音视频"
        if self.reading_view.duration_seconds <= reading.SHORT_TRANSCRIPT_SECONDS:
            return "短"
        if self.reading_view.duration_seconds <= 20 * 60:
            return "中"
        return "长"

    @property
    def needs_rebuild(self) -> bool:
        if not self.eligible or not self.reading_view:
            return False
        current_hash = str(self.record.get("reading_version_hash") or "")
        transcript_hash = str(self.record.get("transcript_content_hash") or "")
        expected_transcript_hash = (
            bundle.transcript_hash(self.item, self.reading_view)
            if self.reading_view.transcript_mode == "separate"
            else ""
        )
        return current_hash != self.reading_view.digest() or transcript_hash != expected_transcript_hash


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def latest_item_records(records: list[dict[str, object]]) -> list[dict[str, object]]:
    latest: dict[str, dict[str, object]] = {}
    for record in records:
        if record.get("record_type") != "frontdesk_item" or record.get("status") != "已发布":
            continue
        identity = str(record.get("item_identity") or record.get("source_file") or "")
        if identity:
            latest[identity] = record
    return sorted(latest.values(), key=lambda record: (str(record.get("source_file") or ""), str(record.get("title") or "")))


def item_from_record(record: dict[str, object]) -> base.PushItem:
    try:
        index = int(record.get("item_index") or 0)
    except (TypeError, ValueError):
        index = 0
    return base.PushItem(
        index=index,
        title=str(record.get("title") or "未命名条目"),
        source=str(record.get("source") or ""),
        source_file=str(record.get("source_file") or ""),
        original_url=str(record.get("original_url") or ""),
        share_url=str(record.get("share_url") or ""),
        transcript_url=str(record.get("transcript_url") or ""),
        summary=str(record.get("summary") or ""),
    )


def load_candidates(record_file: Path, source_file: str = "") -> list[RebuildCandidate]:
    candidates: list[RebuildCandidate] = []
    for record in latest_item_records(base.load_records(record_file)):
        if source_file and str(record.get("source_file") or "") != source_file:
            continue
        item = item_from_record(record)
        material = bundle.load_source_material(item)
        reading_view = material.get("reading_version")
        candidates.append(
            RebuildCandidate(
                record=record,
                item=item,
                material=material,
                reading_view=reading_view if isinstance(reading_view, reading.ReadingVersion) else None,
            )
        )
    return candidates


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit or rebuild existing Feishu media reading pages.")
    parser.add_argument("--record-file", default=str(DEFAULT_RECORD_FILE))
    parser.add_argument("--source-file", default="", help="Only inspect one exact repository-relative source file.")
    parser.add_argument("--max-items", type=int, default=0)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Preview only. Default mode; no files or Feishu pages are written.")
    mode.add_argument("--write-local", action="store_true", help="Regenerate local reading and transcript drafts only.")
    mode.add_argument("--publish", action="store_true", help="Explicitly update eligible existing Feishu pages.")
    return parser.parse_args()


def publisher_command(candidate: RebuildCandidate, args: argparse.Namespace) -> list[str]:
    command = [
        sys.executable,
        str(PUBLISHER),
        "--push-file",
        candidate.source_push,
        "--source-file",
        candidate.source_file,
        "--record-file",
        str(resolve_path(args.record_file)),
        "--no-index",
    ]
    command.append("--publish" if args.publish else "--write-local")
    return command


def main() -> int:
    args = parse_args()
    candidates = load_candidates(resolve_path(args.record_file), args.source_file)
    if args.max_items > 0:
        candidates = candidates[: args.max_items]
    eligible = [candidate for candidate in candidates if candidate.eligible]
    actionable = [candidate for candidate in eligible if candidate.needs_rebuild]
    blocked = [candidate for candidate in candidates if candidate.media_like and not candidate.eligible]
    non_media = [candidate for candidate in candidates if not candidate.media_like]
    classes = Counter(candidate.duration_class for candidate in eligible)

    if not args.write_local and not args.publish:
        print("# 飞书音视频阅读页重建预演")
        print()
        print(f"- 已发布页面：{len(candidates)}")
        print(f"- 音视频且质量通过：{len(eligible)}（短 {classes['短']}，中 {classes['中']}，长 {classes['长']}）")
        print(f"- 需要重建：{len(actionable)}；已经是最新版：{len(eligible) - len(actionable)}")
        print(f"- 音视频质量门禁拦截：{len(blocked)}")
        print(f"- 非音视频或无法识别类型：{len(non_media)}")
        print("- 外部写入：无")
        print()
        for candidate in actionable:
            assert candidate.reading_view is not None
            print(
                f"- [{candidate.duration_class}] {candidate.item.title}："
                f"{reading.format_timecode(candidate.reading_view.duration_seconds)}；"
                f"观点 {len(candidate.reading_view.claims)}；章节 {len(candidate.reading_view.chapters)}；"
                f"转写 {candidate.reading_view.transcript_mode}"
            )
        return 0

    failures = 0
    for candidate in actionable:
        completed = subprocess.run(
            publisher_command(candidate, args),
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode:
            failures += 1
            print(f"重建失败：{candidate.item.title}：{completed.stderr.strip()}", file=sys.stderr)
        else:
            print(f"已生成：{candidate.item.title}：{completed.stdout.strip()}")
    print(f"完成：{len(actionable) - failures} 页成功，{failures} 页失败。")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
