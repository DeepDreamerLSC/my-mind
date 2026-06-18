#!/usr/bin/env python3
"""Sync my-mind backend dashboard rows into Feishu/Lark Base tables."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[4]
TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_DATA_FILE = ROOT / "85_运行记录" / "后台总览" / "飞书仪表盘数据.json"
DEFAULT_CONFIG_FILE = ROOT / "85_运行记录" / "飞书仪表盘配置.local.json"
DEFAULT_RECORD_FILE = ROOT / "85_运行记录" / "飞书仪表盘同步记录.jsonl"
DEFAULT_LARK_PREFIX = 'OPENCLAW_HOME="$HOME/.openclaw" lark-cli'
DEFAULT_TABLE_MAP = {
    "cockpit": "后台驾驶舱",
    "metrics": "后台指标",
    "metric_history": "指标历史",
    "actions": "行动队列",
    "automations": "自动化状态",
    "run_records": "运行记录",
    "quality_items": "解析质量",
    "confirmations": "待确认候选",
    "push_items": "前台推送",
    "flow": "流转队列",
    "advice": "当前行动建议",
    "decision_reviews": "决策审视",
    "code_quality_reviews": "代码质量审视",
}
NUMBER_FIELDS = {"数值", "距现在小时", "数量", "分数", "提醒次数"}


def now_datetime() -> str:
    return dt.datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %z")


def repo_relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def redact_value(value: Any, secret: str) -> Any:
    if not secret:
        return value
    if isinstance(value, str):
        return value.replace(secret, "[REDACTED_BASE_TOKEN]")
    if isinstance(value, list):
        return [redact_value(item, secret) for item in value]
    if isinstance(value, dict):
        return {key: redact_value(item, secret) for key, item in value.items()}
    return value


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return read_json(path)


def merge_table_map(config: dict[str, Any], raw_override: str) -> dict[str, str]:
    table_map = dict(DEFAULT_TABLE_MAP)
    if isinstance(config.get("table_map"), dict):
        table_map.update({str(key): str(value) for key, value in config["table_map"].items()})
    if raw_override:
        table_map.update({str(key): str(value) for key, value in json.loads(raw_override).items()})
    return table_map


def normalize_cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def coerce_row(row: dict[str, Any]) -> dict[str, Any]:
    coerced: dict[str, Any] = {}
    for key, value in row.items():
        if key in NUMBER_FIELDS and value != "":
            try:
                coerced[key] = float(value)
                continue
            except (TypeError, ValueError):
                pass
        coerced[key] = normalize_cell(value)
    return coerced


def field_plan(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    names: list[str] = []
    for row in rows:
        for key in row:
            if key not in names:
                names.append(key)
    return [{"name": name, "type": "number" if name in NUMBER_FIELDS else "text"} for name in names]


def shell_json_arg(value: Any) -> str:
    return shlex.quote(json.dumps(value, ensure_ascii=False))


def run_command(command: str, *, dry_run: bool) -> dict[str, Any]:
    if dry_run:
        return {"ok": True, "dry_run": True, "command": command}
    result = subprocess.run(command, cwd=ROOT, shell=True, text=True, capture_output=True, check=False)
    payload: dict[str, Any] = {
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "command": command,
    }
    if result.stdout.strip().startswith("{"):
        try:
            payload["json"] = json.loads(result.stdout)
        except json.JSONDecodeError:
            pass
    return payload


def lark_command(prefix: str, subcommand: str, *, identity: str, base_token: str, args: list[str]) -> str:
    parts = [prefix, "base", subcommand, "--as", identity, "--base-token", base_token]
    parts.extend(args)
    return " ".join(shlex.quote(part) if index >= 3 else part for index, part in enumerate(parts))


def find_lists(value: Any, key: str) -> list[list[Any]]:
    found: list[list[Any]] = []
    if isinstance(value, dict):
        for current_key, current_value in value.items():
            if current_key == key and isinstance(current_value, list):
                found.append(current_value)
            found.extend(find_lists(current_value, key))
    elif isinstance(value, list):
        for item in value:
            found.extend(find_lists(item, key))
    return found


def cell_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        return "".join(cell_text(item) for item in value)
    if isinstance(value, dict):
        for key in ("text", "value", "name"):
            if key in value:
                return cell_text(value[key])
        return json.dumps(value, ensure_ascii=False)
    return ""


def extract_record_id(search_payload: dict[str, Any], record_key: str) -> str:
    json_payload = search_payload.get("json") if isinstance(search_payload, dict) else None
    if not isinstance(json_payload, dict):
        return ""
    table_data = json_payload.get("data")
    if isinstance(table_data, dict) and isinstance(table_data.get("data"), list):
        fields = table_data.get("fields") if isinstance(table_data.get("fields"), list) else []
        record_ids = table_data.get("record_id_list") if isinstance(table_data.get("record_id_list"), list) else []
        try:
            key_index = fields.index("记录键")
        except ValueError:
            key_index = 0
        for index, row in enumerate(table_data["data"]):
            if isinstance(row, list) and len(row) > key_index and cell_text(row[key_index]) == record_key:
                if index < len(record_ids):
                    return str(record_ids[index] or "")
    for items in find_lists(json_payload, "items") + find_lists(json_payload, "records"):
        for item in items:
            if not isinstance(item, dict):
                continue
            fields = item.get("fields") if isinstance(item.get("fields"), dict) else {}
            if cell_text(fields.get("记录键")) == record_key:
                return str(item.get("record_id") or item.get("id") or "")
    return ""


def extract_record_key_rows(list_payload: dict[str, Any]) -> list[tuple[str, str]]:
    json_payload = list_payload.get("json") if isinstance(list_payload, dict) else None
    if not isinstance(json_payload, dict):
        return []
    table_data = json_payload.get("data")
    if not isinstance(table_data, dict) or not isinstance(table_data.get("data"), list):
        return []
    fields = table_data.get("fields") if isinstance(table_data.get("fields"), list) else []
    record_ids = table_data.get("record_id_list") if isinstance(table_data.get("record_id_list"), list) else []
    try:
        key_index = fields.index("记录键")
    except ValueError:
        key_index = 0
    rows: list[tuple[str, str]] = []
    for index, row in enumerate(table_data["data"]):
        if not isinstance(row, list) or len(row) <= key_index or index >= len(record_ids):
            continue
        record_key = cell_text(row[key_index])
        record_id = str(record_ids[index] or "")
        if record_key and record_id:
            rows.append((record_key, record_id))
    return rows


def refresh_dashboard_data(data_file: Path) -> None:
    command = [
        sys.executable,
        str(ROOT / ".codex" / "skills" / "backend-control" / "scripts" / "backend_health_check.py"),
        "--export-dashboard-data",
        "--dashboard-data-file",
        str(data_file),
    ]
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"刷新本地仪表盘数据失败：{result.stderr.strip() or result.stdout.strip()}")


def refresh_advice_data() -> None:
    command = [
        sys.executable,
        str(ROOT / ".codex" / "skills" / "advice-analysis" / "scripts" / "analyze_advice.py"),
        "--write",
    ]
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"刷新建议分析失败：{result.stderr.strip() or result.stdout.strip()}")


def init_tables(
    *,
    tables: dict[str, Any],
    table_map: dict[str, str],
    prefix: str,
    identity: str,
    base_token: str,
    selected: set[str],
    dry_run: bool,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for table_key, table in tables.items():
        if selected and table_key not in selected:
            continue
        table_id = table_map.get(table_key) or table.get("display_name") or table_key
        rows = list(table.get("rows") or [])
        fields = field_plan(rows)
        create_cmd = lark_command(
            prefix,
            "+table-create",
            identity=identity,
            base_token=base_token,
            args=["--name", str(table_id), "--fields", json.dumps(fields, ensure_ascii=False)],
        )
        results.append({"table": table_key, "operation": "table-create", "result": run_command(create_cmd, dry_run=dry_run)})
    return results


def dedupe_table(
    *,
    table_key: str,
    table_id: str,
    prefix: str,
    identity: str,
    base_token: str,
    dry_run: bool,
    delete_long_keys: bool,
    delete_stale: bool,
    current_keys: set[str],
) -> dict[str, Any]:
    list_cmd = lark_command(
        prefix,
        "+record-list",
        identity=identity,
        base_token=base_token,
        args=["--table-id", table_id, "--field-id", "记录键", "--limit", "200", "--format", "json"],
    )
    list_result = run_command(list_cmd, dry_run=dry_run)
    if dry_run:
        return {"table_key": table_key, "table_id": table_id, "deleted": 0, "duplicates": 0, "errors": [], "commands": [list_cmd]}
    if not list_result.get("ok"):
        return {"table_key": table_key, "table_id": table_id, "deleted": 0, "duplicates": 0, "errors": ["record-list 失败"], "commands": [list_cmd]}
    grouped: dict[str, list[str]] = {}
    for record_key, record_id in extract_record_key_rows(list_result):
        grouped.setdefault(record_key, []).append(record_id)
    delete_ids: list[str] = []
    for record_key, ids in grouped.items():
        if delete_stale and record_key not in current_keys:
            delete_ids.extend(ids)
            continue
        if delete_long_keys and len(record_key) > 50:
            delete_ids.extend(ids)
            continue
        if len(ids) > 1:
            delete_ids.extend(ids[1:])
    if not delete_ids:
        return {"table_key": table_key, "table_id": table_id, "deleted": 0, "duplicates": 0, "errors": [], "commands": [list_cmd]}
    delete_cmd = lark_command(
        prefix,
        "+record-delete",
        identity=identity,
        base_token=base_token,
        args=["--table-id", table_id, "--json", json.dumps({"record_id_list": delete_ids}, ensure_ascii=False), "--yes"],
    )
    delete_result = run_command(delete_cmd, dry_run=dry_run)
    errors = [] if delete_result.get("ok") else ["record-delete 失败"]
    return {
        "table_key": table_key,
        "table_id": table_id,
        "deleted": len(delete_ids) if delete_result.get("ok") else 0,
        "duplicates": len(delete_ids),
        "errors": errors,
        "commands": [list_cmd, delete_cmd],
    }


def sync_table(
    *,
    table_key: str,
    table: dict[str, Any],
    table_id: str,
    prefix: str,
    identity: str,
    base_token: str,
    dry_run: bool,
    max_rows: int,
) -> dict[str, Any]:
    rows = list(table.get("rows") or [])
    if max_rows > 0:
        rows = rows[:max_rows]
    created = 0
    updated = 0
    skipped = 0
    errors: list[str] = []
    commands: list[str] = []

    for row in rows:
        record_key = str(row.get(table.get("primary_key") or "记录键") or row.get("记录键") or "")
        if not record_key:
            skipped += 1
            continue
        search_body = {"keyword": record_key, "search_fields": ["记录键"], "select_fields": ["记录键"], "limit": 10}
        search_cmd = lark_command(
            prefix,
            "+record-search",
            identity=identity,
            base_token=base_token,
            args=["--table-id", table_id, "--json", json.dumps(search_body, ensure_ascii=False), "--format", "json"],
        )
        search_result = run_command(search_cmd, dry_run=dry_run)
        record_id = "" if dry_run else extract_record_id(search_result, record_key)
        if not dry_run and not search_result.get("ok"):
            errors.append(f"{table_key}/{record_key} 搜索失败")
            continue
        upsert_args = ["--table-id", table_id, "--json", json.dumps(coerce_row(row), ensure_ascii=False)]
        if record_id:
            upsert_args.extend(["--record-id", record_id])
        upsert_cmd = lark_command(
            prefix,
            "+record-upsert",
            identity=identity,
            base_token=base_token,
            args=upsert_args,
        )
        commands.extend([search_cmd, upsert_cmd])
        upsert_result = run_command(upsert_cmd, dry_run=dry_run)
        if not upsert_result.get("ok"):
            errors.append(f"{table_key}/{record_key} 写入失败")
            continue
        if record_id:
            updated += 1
        else:
            created += 1

    return {
        "table_key": table_key,
        "table_id": table_id,
        "display_name": table.get("display_name") or table_id,
        "rows": len(rows),
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
        "commands": commands[:8],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync my-mind backend dashboard rows into Feishu Base.")
    parser.add_argument("--data-file", type=Path, default=DEFAULT_DATA_FILE)
    parser.add_argument("--config", type=Path, default=Path(os.environ.get("MY_MIND_FEISHU_DASHBOARD_CONFIG", str(DEFAULT_CONFIG_FILE))))
    parser.add_argument("--record-file", type=Path, default=DEFAULT_RECORD_FILE)
    parser.add_argument("--base-token", default=os.environ.get("MY_MIND_FEISHU_DASHBOARD_BASE_TOKEN", ""))
    parser.add_argument("--table-map", default=os.environ.get("MY_MIND_FEISHU_DASHBOARD_TABLE_MAP", ""), help="JSON object mapping local table keys to Feishu table names or IDs.")
    parser.add_argument("--lark-prefix", default=os.environ.get("MY_MIND_FEISHU_DASHBOARD_LARK_PREFIX", DEFAULT_LARK_PREFIX))
    parser.add_argument("--identity", default=os.environ.get("MY_MIND_FEISHU_DASHBOARD_IDENTITY", "user"))
    parser.add_argument("--tables", default="", help="Comma-separated local table keys. Defaults to all.")
    parser.add_argument("--max-rows", type=int, default=0, help="Limit rows per table. 0 means all rows.")
    parser.add_argument("--refresh-data", action="store_true", help="Run backend-control export before syncing.")
    parser.add_argument("--refresh-advice", action="store_true", help="Run advice-analysis after refreshing dashboard data so the advice table is included.")
    parser.add_argument("--init-tables", action="store_true", help="Create Feishu Base tables and fields. Use only for a new Base.")
    parser.add_argument("--init-only", action="store_true", help="Only initialize tables/fields; do not sync rows.")
    parser.add_argument("--dedupe", action="store_true", help="Delete duplicate Feishu rows with the same 记录键 after syncing.")
    parser.add_argument("--dedupe-only", action="store_true", help="Only delete duplicate Feishu rows; do not sync rows.")
    parser.add_argument("--delete-long-keys", action="store_true", help="During dedupe, also delete legacy rows whose 记录键 is longer than 50 characters.")
    parser.add_argument("--delete-stale", action="store_true", help="During dedupe, delete current-state rows whose 记录键 is no longer present locally. Keeps metric_history.")
    parser.add_argument("--write", action="store_true", help="Execute Feishu writes. Default is dry-run.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    data_file = args.data_file if args.data_file.is_absolute() else ROOT / args.data_file
    config_file = args.config if args.config.is_absolute() else ROOT / args.config
    record_file = args.record_file if args.record_file.is_absolute() else ROOT / args.record_file

    if args.refresh_data or not data_file.exists():
        refresh_dashboard_data(data_file)
    if args.refresh_advice:
        refresh_advice_data()
    if not data_file.exists():
        print(f"错误：缺少仪表盘数据文件 {repo_relative(data_file)}", file=sys.stderr)
        return 2

    payload = read_json(data_file)
    tables = payload.get("tables")
    if not isinstance(tables, dict):
        print("错误：仪表盘数据缺少 tables。", file=sys.stderr)
        return 2

    config = load_config(config_file)
    base_token = args.base_token or str(config.get("base_token") or "")
    table_map = merge_table_map(config, args.table_map)
    selected = {value.strip() for value in args.tables.split(",") if value.strip()}
    dry_run = not args.write

    if args.write and not base_token:
        print("错误：缺少 --base-token 或 MY_MIND_FEISHU_DASHBOARD_BASE_TOKEN。", file=sys.stderr)
        return 2

    init_results: list[dict[str, Any]] = []
    if args.init_tables:
        if not base_token and args.write:
            print("错误：初始化表格需要 base token。", file=sys.stderr)
            return 2
        init_results = init_tables(
            tables=tables,
            table_map=table_map,
            prefix=args.lark_prefix,
            identity=args.identity,
            base_token=base_token or "BASE_TOKEN",
            selected=selected,
            dry_run=dry_run,
        )

    sync_results: list[dict[str, Any]] = []
    if not args.init_only and not args.dedupe_only:
        for table_key, table in tables.items():
            if selected and table_key not in selected:
                continue
            table_id = table_map.get(table_key) or str(table.get("display_name") or table_key)
            sync_results.append(
                sync_table(
                    table_key=table_key,
                    table=table,
                    table_id=table_id,
                    prefix=args.lark_prefix,
                    identity=args.identity,
                    base_token=base_token or "BASE_TOKEN",
                    dry_run=dry_run,
                    max_rows=max(args.max_rows, 0),
                )
            )

    dedupe_results: list[dict[str, Any]] = []
    if args.dedupe or args.dedupe_only:
        if args.write and not base_token:
            print("错误：去重需要 base token。", file=sys.stderr)
            return 2
        for table_key, table in tables.items():
            if selected and table_key not in selected:
                continue
            table_id = table_map.get(table_key) or str(table.get("display_name") or table_key)
            current_keys = {str(row.get("记录键") or "") for row in list(table.get("rows") or []) if row.get("记录键")}
            delete_stale = bool(args.delete_stale and table_key != "metric_history")
            dedupe_results.append(
                dedupe_table(
                    table_key=table_key,
                    table_id=table_id,
                    prefix=args.lark_prefix,
                    identity=args.identity,
                    base_token=base_token or "BASE_TOKEN",
                    dry_run=dry_run,
                    delete_long_keys=args.delete_long_keys,
                    delete_stale=delete_stale,
                    current_keys=current_keys,
                )
            )

    summary = {
        "created_at": now_datetime(),
        "mode": "write" if args.write else "dry-run",
        "data_file": repo_relative(data_file),
        "config_file": repo_relative(config_file) if config_file.exists() else "",
        "base_token_configured": bool(base_token),
        "selected_tables": sorted(selected) if selected else "all",
        "init_results": init_results,
        "sync_results": sync_results,
        "dedupe_results": dedupe_results,
    }
    safe_summary = redact_value(summary, base_token)
    if args.write:
        append_jsonl(record_file, safe_summary)
        safe_summary["record_file"] = repo_relative(record_file)
    print(json.dumps(safe_summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
