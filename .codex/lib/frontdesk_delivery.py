#!/usr/bin/env python3
"""Small, explicit delivery-state contract for my-mind frontdesk items."""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Iterable


STAGES = ("prepared", "published", "sent", "feedback_received", "applied")


def empty_state() -> dict[str, Any]:
    return {"version": 2, "items": {}}


def normalize_state(state: dict[str, Any]) -> dict[str, Any]:
    items = state.get("items")
    if not isinstance(items, dict):
        items = {}
        state["items"] = items
    for item in items.values():
        if not isinstance(item, dict):
            continue
        raw_stage = item.get("delivery_stage")
        stage = str(raw_stage or "")
        if stage and stage not in STAGES:
            raise ValueError(f"Invalid delivery stage: {stage}")
        if not stage:
            item["delivery_stage"] = ""
            if any(key in item for key in ("last_pushed_at", "feedback_status", "push_count")):
                item["legacy_delivery"] = "unknown"
    state["version"] = max(int(state.get("version") or 1), 2)
    return state


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return empty_state()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid delivery state JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Delivery state must be a JSON object: {path}")
    return normalize_state(value)


def save_state(path: Path, state: dict[str, Any]) -> bool:
    content = json.dumps(normalize_state(state), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if path.exists() and path.read_text(encoding="utf-8", errors="ignore") == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(content)
            temp_path = Path(handle.name)
        os.replace(temp_path, path)
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink()
    return True


def _items(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    normalize_state(state)
    return state["items"]


def _stage(item: dict[str, Any]) -> str:
    return str(item.get("delivery_stage") or "")


def _touch(state: dict[str, Any], at: str) -> None:
    state["updated_at"] = at


def mark_prepared(
    state: dict[str, Any],
    entries: Iterable[dict[str, Any]],
    *,
    push_file: str,
    at: str,
) -> bool:
    items = _items(state)
    changed = False
    for entry in entries:
        source = str(entry.get("source_file") or "").strip()
        if not source:
            continue
        item = items.get(source)
        if not isinstance(item, dict):
            item = {}
            items[source] = item
        stage = _stage(item)
        if stage not in {"", "prepared"}:
            continue
        values = {
            "delivery_stage": "prepared",
            "prepared_at": at,
            "prepared_push_file": push_file,
            "source_file": source,
        }
        for key in ("title", "content_hash"):
            if entry.get(key):
                values[key] = entry[key]
        before = dict(item)
        item.update(values)
        item["preparation_count"] = int(before.get("preparation_count") or 0) + (before != item)
        changed = changed or before != item
    if changed:
        _touch(state, at)
    return changed


def mark_published(
    state: dict[str, Any],
    source_files: Iterable[str],
    *,
    push_file: str,
    at: str,
) -> bool:
    items = _items(state)
    sources = [str(source).strip() for source in source_files if str(source).strip()]
    for source in sources:
        item = items.get(source)
        if not isinstance(item, dict) or _stage(item) not in {"prepared", "published", "sent", "feedback_received", "applied"}:
            raise ValueError(f"Cannot publish item without prepared state: {source}")
    changed = False
    for source in sources:
        item = items[source]
        if _stage(item) != "prepared":
            continue
        item.update(
            {
                "delivery_stage": "published",
                "published_at": at,
                "published_push_file": push_file,
            }
        )
        changed = True
    if changed:
        _touch(state, at)
    return changed


def ack_sent(
    state: dict[str, Any],
    source_files: Iterable[str],
    *,
    channel: str,
    at: str,
    message_id: str = "",
    idempotency_key: str = "",
) -> bool:
    channel = channel.strip()
    message_id = message_id.strip()
    ack_key = idempotency_key.strip() or message_id
    if not channel:
        raise ValueError("A send acknowledgement requires a channel")
    if not ack_key:
        raise ValueError("A send acknowledgement requires a message ID or idempotency key")
    try:
        sent_at = dt.datetime.fromisoformat(at)
    except ValueError as exc:
        raise ValueError("A send acknowledgement requires an ISO-8601 timestamp") from exc
    if sent_at.tzinfo is None:
        raise ValueError("A send acknowledgement timestamp requires a timezone")
    sources = [str(source).strip() for source in source_files if str(source).strip()]
    if not sources:
        raise ValueError("A send acknowledgement requires at least one source item")

    items = _items(state)
    pending: list[dict[str, Any]] = []
    for source in sources:
        item = items.get(source)
        if not isinstance(item, dict):
            raise ValueError(f"Unknown delivery item: {source}")
        stage = _stage(item)
        if stage in {"sent", "feedback_received", "applied"}:
            if str(item.get("send_idempotency_key") or "") == ack_key:
                if str(item.get("sent_channel") or "") != channel:
                    raise ValueError(f"Conflicting send channel for {source}")
                if message_id and item.get("message_id") and str(item["message_id"]) != message_id:
                    raise ValueError(f"Conflicting message ID for {source}")
                continue
            raise ValueError(f"Conflicting send acknowledgement for {source}")
        if stage != "published":
            raise ValueError(f"Cannot acknowledge send from {stage or 'unknown'}: {source}")
        pending.append(item)

    for item in pending:
        item.update(
            {
                "delivery_stage": "sent",
                "sent_at": at,
                "sent_channel": channel,
                "send_idempotency_key": ack_key,
            }
        )
        if message_id:
            item["message_id"] = message_id
    if pending:
        _touch(state, at)
    return bool(pending)


def mark_feedback_received(
    state: dict[str, Any],
    source_file: str,
    *,
    at: str,
    action: str,
    content: str,
) -> bool:
    item = _items(state).get(source_file)
    if not isinstance(item, dict):
        return False
    stage = _stage(item)
    if stage == "applied":
        return False
    if not stage and str(item.get("legacy_delivery") or "") == "unknown":
        before = dict(item)
        item.update(
            {
                "legacy_feedback_received_at": at,
                "last_feedback_action": action,
                "last_feedback_content": content,
            }
        )
        changed = before != item
        if changed:
            _touch(state, at)
        return changed
    if stage not in {"sent", "feedback_received"}:
        raise ValueError(f"Cannot receive feedback from {stage or 'unknown'}: {source_file}")
    before = dict(item)
    item.update(
        {
            "delivery_stage": "feedback_received",
            "feedback_received_at": at,
            "last_feedback_action": action,
            "last_feedback_content": content,
        }
    )
    changed = before != item
    if changed:
        _touch(state, at)
    return changed


def mark_applied(state: dict[str, Any], source_file: str, *, at: str, result: str) -> bool:
    item = _items(state).get(source_file)
    if not isinstance(item, dict):
        return False
    stage = _stage(item)
    if stage == "applied":
        return False
    if stage != "feedback_received":
        raise ValueError(f"Cannot apply feedback from {stage or 'unknown'}: {source_file}")
    item.update({"delivery_stage": "applied", "applied_at": at, "applied_result": result})
    _touch(state, at)
    return True


def waiting_for_feedback_count(state: dict[str, Any]) -> int:
    return sum(1 for item in _items(state).values() if isinstance(item, dict) and _stage(item) == "sent")


def source_files_from_push(path: Path) -> list[str]:
    if not path.exists():
        return []
    sources: list[str] = []
    for match in re.finditer(r"^- 来源文件：`([^`]+)`", path.read_text(encoding="utf-8", errors="ignore"), flags=re.M):
        source = match.group(1).strip()
        if source and source not in sources:
            sources.append(source)
    return sources
