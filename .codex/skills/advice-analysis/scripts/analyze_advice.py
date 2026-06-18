#!/usr/bin/env python3
"""Generate prioritized action advice from my-mind operating evidence."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[4]
TZ = ZoneInfo("Asia/Shanghai")
RUN_DIR = ROOT / "85_运行记录"
PROJECT_DIR = ROOT / "10_项目"
DASHBOARD_DIR = RUN_DIR / "后台总览"
DASHBOARD_DATA = DASHBOARD_DIR / "飞书仪表盘数据.json"
DASHBOARD_CSV_DIR = DASHBOARD_DIR / "飞书仪表盘数据"
LATEST_ADVICE = DASHBOARD_DIR / "当前行动建议.md"
ADVICE_STATE = RUN_DIR / "建议分析状态.json"


@dataclass
class EvidenceSource:
    kind: str
    path: str
    title: str = ""


@dataclass
class Suggestion:
    score: int
    owner: str
    domain: str
    action: str
    reason: str
    next_step: str
    evidence: list[str] = field(default_factory=list)
    source: str = ""
    record_key: str = ""
    status: str = ""
    first_seen: str = ""
    last_seen: str = ""
    reminded_count: int = 0

    @property
    def priority(self) -> str:
        if self.score >= 90:
            return "P0"
        if self.score >= 75:
            return "P1"
        if self.score >= 55:
            return "P2"
        return "P3"


@dataclass
class AdviceContext:
    generated_at: str
    project_filter: str
    dashboard_loaded: bool
    dashboard_generated_at: str = ""
    project_reports: dict[str, Path] = field(default_factory=dict)
    decision_reports: dict[str, Path] = field(default_factory=dict)
    code_quality_reports: dict[str, Path] = field(default_factory=dict)
    sources: list[EvidenceSource] = field(default_factory=list)


def now() -> dt.datetime:
    return dt.datetime.now(TZ)


def now_filename() -> str:
    return now().strftime("%Y-%m-%d-%H%M")


def repo_rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def parse_frontmatter(text: str) -> dict[str, Any]:
    if not text.startswith("---\n"):
        return {}
    lines = text.splitlines()
    end_index: int | None = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            end_index = index
            break
    if end_index is None:
        return {}
    result: dict[str, Any] = {}
    current_key: str | None = None
    for raw_line in lines[1:end_index]:
        line = raw_line.rstrip()
        if not line.strip():
            continue
        if line.startswith("  - ") and current_key:
            result.setdefault(current_key, [])
            if isinstance(result[current_key], list):
                result[current_key].append(line[4:].strip().strip('"'))
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        current_key = key.strip()
        raw_value = value.strip().strip('"')
        result[current_key] = raw_value if raw_value else []
    return result


def load_dashboard() -> dict[str, Any]:
    if not DASHBOARD_DATA.exists():
        return {}
    try:
        value = json.loads(read_text(DASHBOARD_DATA))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def load_state(path: Path = ADVICE_STATE) -> dict[str, Any]:
    if not path.exists():
        return {"records": {}}
    try:
        value = json.loads(read_text(path))
    except json.JSONDecodeError:
        return {"records": {}}
    if not isinstance(value, dict):
        return {"records": {}}
    records = value.get("records")
    if not isinstance(records, dict):
        value["records"] = {}
    return value


def save_state(state: dict[str, Any], path: Path = ADVICE_STATE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = now().strftime("%Y-%m-%d %H:%M:%S %z")
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    try:
        return json.loads(read_text(DASHBOARD_DATA))
    except json.JSONDecodeError:
        return {}


def table_rows(dashboard: dict[str, Any], table_name: str) -> list[dict[str, Any]]:
    table = dashboard.get("tables", {}).get(table_name, {})
    rows = table.get("rows", []) if isinstance(table, dict) else []
    return rows if isinstance(rows, list) else []


def extract_heading_section(text: str, heading: str, level: int = 3) -> list[str]:
    marker = "#" * level + " " + heading
    lines = text.splitlines()
    start: int | None = None
    collected: list[str] = []
    for index, line in enumerate(lines):
        if line.strip() == marker:
            start = index + 1
            break
    if start is None:
        return []
    stop_pattern = re.compile(rf"^#{{1,{level}}}\s+")
    for line in lines[start:]:
        if stop_pattern.match(line):
            break
        collected.append(line)
    return collected


def extract_bullets(lines: list[str]) -> list[str]:
    bullets: list[str] = []
    current: str | None = None
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("- "):
            if current:
                bullets.append(current)
            current = stripped[2:].strip()
        elif re.match(r"^\d+\.\s+", stripped):
            if current:
                bullets.append(current)
            current = re.sub(r"^\d+\.\s+", "", stripped).strip()
        elif current and stripped.startswith("  - "):
            current += "；" + stripped[4:].strip()
        elif current and stripped and not stripped.startswith("#"):
            current += " " + stripped
    if current:
        bullets.append(current)
    return [bullet for bullet in bullets if bullet]


def latest_project_reports(project_filter: str) -> dict[str, Path]:
    reports: dict[str, Path] = {}
    files = sorted(RUN_DIR.glob("项目进展巡检-*.md"), key=lambda path: path.stat().st_mtime, reverse=True)
    for path in files:
        text = read_text(path)
        meta = parse_frontmatter(text)
        project = str(meta.get("项目") or infer_project_from_filename(path.name))
        if not project:
            continue
        if project_filter != "all" and project_filter not in {project, normalize_project_key(project)}:
            continue
        if project not in reports:
            reports[project] = path
    return reports


def latest_review_reports(prefix: str, project_filter: str) -> dict[str, Path]:
    reports: dict[str, Path] = {}
    files = sorted(RUN_DIR.glob(f"{prefix}-*.md"), key=lambda path: path.stat().st_mtime, reverse=True)
    for path in files:
        text = read_text(path)
        meta = parse_frontmatter(text)
        project = str(meta.get("项目") or "")
        project_key = str(meta.get("项目键") or normalize_project_key(project))
        if not project:
            continue
        if project_filter != "all" and project_filter not in {project, project_key, normalize_project_key(project)}:
            continue
        if project not in reports:
            reports[project] = path
    return reports


def infer_project_from_filename(name: str) -> str:
    match = re.match(r"项目进展巡检-([a-zA-Z0-9_-]+)-\d{4}-", name)
    if not match:
        return "个人数据资产系统"
    key = match.group(1)
    if key == "my-mind":
        return "个人数据资产系统"
    return key


def normalize_project_key(project: str) -> str:
    if project in {"个人数据资产系统", "my-mind"}:
        return "my-mind"
    return project


def score_from_priority(value: str, default: int = 55) -> int:
    mapping = {"紧急": 95, "高": 82, "中": 62, "低": 42}
    return mapping.get(value, default)


def classify_domain(text: str) -> str:
    if any(word in text for word in ["决策审视", "隐含假设", "偏航", "机会成本", "最小可逆"]):
        return "决策审视"
    if any(word in text for word in ["代码质量", "测试", "lint", "typecheck", "code review", "AI 代码"]):
        return "代码质量"
    if any(word in text for word in ["解析", "转写", "OCR", "内容质量", "待核验", "门禁"]):
        return "质量门禁"
    if any(word in text for word in ["待确认", "转正", "候选"]):
        return "候选确认"
    if any(word in text for word in ["飞书", "前台", "推送", "反馈", "OpenClaw"]):
        return "前台协作"
    if any(word in text for word in ["commit", "提交", "工作区", "环境", "配置", "artifact", "日志", "项目"]):
        return "项目推进"
    return "后台治理"


def next_step_for(owner: str, action: str) -> str:
    if "待确认" in action or "确认转正" in action or "候选" in action:
        if owner.startswith("Codex"):
            return "由 Codex 先核对候选状态、来源证据和门禁结果；需要用户判断时再交给 OpenClaw 短消息确认。"
        return "由 OpenClaw 只推送标题、候选文件和可回复动作，用户用短回复确认。"
    if "已推送" in action and "未反馈" in action:
        return "由 OpenClaw 拆成多条短消息，优先给飞书知识库链接和三个动作选项。"
    if "artifact" in action or "日志" in action or "运行产物" in action:
        return "由 Codex 只保留可复核证据索引；大体积样例、临时日志和重复产物不要进入长期知识。"
    if "解析" in action or "核验" in action:
        return "由 Codex 运行解析质量修复或逐条补抓，再重新进入分拣/推送门禁。"
    if "工作区" in action or "提交" in action:
        return "由 Codex 先做只读整理，区分功能变更、配置风险、运行产物和文档，再分批提交。"
    if "决策审视" in action or "偏航" in action:
        return "由 Codex 给出反方观点、证据和最小可逆动作；需要用户取舍时交给 OpenClaw 短消息确认。"
    if "代码质量" in action or "质量门禁" in action:
        return "由 Codex 先补验证、拆分提交边界或进入正式 code review；不要继续堆功能。"
    if "待沉淀" in action:
        if "已有候选" in action:
            return "由 OpenClaw 展示待确认候选，Codex 不重复生成候选草稿。"
        if "待补判断" in action:
            return "由 Codex 先补解析或补用户判断，再决定是否进入候选沉淀。"
        return "由 Codex 消费待沉淀可消费队列，生成候选草稿并进入待确认，不直接转正。"
    if owner.startswith("OpenClaw"):
        return "由 OpenClaw 拆成短消息触达，避免把完整日志推给用户。"
    return "由对应角色按证据路径继续处理，完成后再跑一次建议分析。"


def add_dashboard_suggestions(dashboard: dict[str, Any], suggestions: list[Suggestion]) -> None:
    has_confirmation_rows = bool(table_rows(dashboard, "confirmations"))
    for row in table_rows(dashboard, "actions"):
        action = str(row.get("事项") or "").strip()
        if not action:
            continue
        if has_confirmation_rows and "候选知识等待确认" in action:
            continue
        owner = str(row.get("责任方") or "Codex")
        score = score_from_priority(str(row.get("优先级") or ""), 58)
        if owner == "用户" and "已推送" in action and "未反馈" in action:
            owner = "OpenClaw/用户"
            score = min(score, 76)
        elif owner == "用户":
            score += 6
        suggestions.append(
            Suggestion(
                score=min(score, 95),
                owner=owner,
                domain=classify_domain(action),
                action=action,
                reason=f"后台总控已将该事项标为{row.get('优先级') or '中'}优先级。",
                next_step=next_step_for(owner, action),
                evidence=["85_运行记录/后台总览/飞书仪表盘数据.json#actions"],
                source="backend-control",
            )
        )

    quality_rows = table_rows(dashboard, "quality_items")
    if quality_rows:
        titles = "、".join(str(row.get("标题") or "未命名") for row in quality_rows[:3])
        suggestions.append(
            Suggestion(
                score=78,
                owner="Codex",
                domain="质量门禁",
                action=f"优先处理 {len(quality_rows)} 条低质量或需核验解析。",
                reason=f"低质量解析不应进入前台推送或长期知识转正；样例：{titles}。",
                next_step="运行 parse-quality-repair；对仍需人工核验的条目生成最小补充问题。",
                evidence=[str(row.get("来源文件") or "") for row in quality_rows[:5] if row.get("来源文件")],
                source="backend-control",
            )
        )

    confirmation_rows = table_rows(dashboard, "confirmations")
    if confirmation_rows:
        titles = "、".join(str(row.get("标题") or "未命名") for row in confirmation_rows[:3])
        suggestions.append(
            Suggestion(
                score=86,
                owner="OpenClaw/用户",
                domain="候选确认",
                action=f"分批呈现 {len(confirmation_rows)} 条待确认候选，先处理高优先级项。",
                reason=f"候选已经生成但尚未决定确认转正、继续核验、调整分类或跳过；样例：{titles}。",
                next_step="OpenClaw 读取待确认队列，发送候选标题、类型、候选文件和可回复动作。",
                evidence=[str(row.get("候选文件") or "") for row in confirmation_rows[:5] if row.get("候选文件")],
                source="backend-control",
            )
        )

    flow_rows = table_rows(dashboard, "flow")
    for row in flow_rows:
        queue = str(row.get("队列") or "")
        count = int(float(row.get("数量") or 0))
        if count <= 0:
            continue
        if queue in {"待沉淀待消费", "待沉淀可消费"} and count > 0:
            suggestions.append(
                Suggestion(
                    score=70,
                    owner="Codex",
                    domain="沉淀流水线",
                    action=f"消费 {count} 条待沉淀可消费内容，生成候选草稿。",
                    reason="这些条目尚未生成候选，继续堆积会让前台阅读反馈无法进入长期知识候选。",
                    next_step=next_step_for("Codex", "待沉淀可消费"),
                    evidence=["05_流转区/30_待沉淀/收件箱待沉淀队列.md"],
                    source="backend-control",
                )
            )
        if queue == "已有候选待确认" and count >= 3:
            suggestions.append(
                Suggestion(
                    score=72,
                    owner="OpenClaw/用户",
                    domain="候选确认",
                    action=f"待沉淀队列中 {count} 条已有候选，优先提醒确认而不是重复沉淀。",
                    reason="这些内容已经生成候选，下一步是确认转正、继续核验、调整分类或跳过。",
                    next_step=next_step_for("OpenClaw/用户", "待沉淀已有候选"),
                    evidence=["05_流转区/30_待沉淀/收件箱待沉淀队列.md"],
                    source="backend-control",
                )
            )
        if queue == "待补判断" and count > 0:
            suggestions.append(
                Suggestion(
                    score=68,
                    owner="Codex",
                    domain="质量门禁",
                    action=f"待沉淀队列中 {count} 条待补判断，先补解析或补用户判断。",
                    reason="这些条目尚不具备自动生成候选的条件，直接消费会制造低质量候选。",
                    next_step=next_step_for("Codex", "待沉淀待补判断"),
                    evidence=["05_流转区/30_待沉淀/收件箱待沉淀队列.md"],
                    source="backend-control",
                )
            )
        if queue == "待核验" and count >= 5:
            suggestions.append(
                Suggestion(
                    score=74,
                    owner="Codex",
                    domain="质量门禁",
                    action=f"收敛 {count} 条待核验内容，阻止低质量材料进入精选。",
                    reason="待核验内容过多会污染前台推送和候选转正。",
                    next_step=next_step_for("Codex", "待核验"),
                    evidence=["05_流转区/40_待核验/收件箱待核验队列.md"],
                    source="backend-control",
                )
            )


def report_action_from_risk(project: str, risk: str) -> Suggestion:
    score = 68
    owner = "Codex"
    action = f"处理 {project} 风险：{risk}"
    if any(word in risk for word in ["环境", "密钥", "生产", "配置"]):
        score = 84
        action = f"先审查 {project} 的环境和配置变更，再进入提交。"
    elif any(word in risk for word in ["日志", "artifact", "运行产物"]):
        score = 76
        action = f"筛选 {project} 的 artifact 和日志，只保留必要证据索引。"
    elif any(word in risk for word in ["工作区", "未提交", "分批", "commit"]):
        score = 80
        action = f"整理 {project} 工作区，按主题分批提交或暂存。"
    return Suggestion(
        score=score,
        owner=owner,
        domain=classify_domain(action + risk),
        action=action,
        reason=f"最新项目巡检将其列为风险：{risk}",
        next_step=next_step_for(owner, action),
        evidence=[],
        source="project-progress",
    )


def add_project_report_suggestions(reports: dict[str, Path], suggestions: list[Suggestion]) -> None:
    for project, path in reports.items():
        text = read_text(path)
        evidence_path = repo_rel(path)
        risks = extract_bullets(extract_heading_section(text, "风险与阻塞"))
        next_actions = extract_bullets(extract_heading_section(text, "下一步建议"))
        questions = extract_bullets(extract_heading_section(text, "需要确认"))
        for risk in risks[:4]:
            item = report_action_from_risk(project, risk)
            item.evidence.append(evidence_path)
            suggestions.append(item)
        for action in next_actions[:4]:
            suggestions.append(
                Suggestion(
                    score=64,
                    owner="Codex",
                    domain=classify_domain(action),
                    action=f"{project}：{action}",
                    reason="最新项目巡检给出了下一步建议。",
                    next_step=next_step_for("Codex", action),
                    evidence=[evidence_path],
                    source="project-progress",
                )
            )
        for question in questions[:3]:
            suggestions.append(
                Suggestion(
                    score=58,
                    owner="Codex/用户",
                    domain="项目判断",
                    action=f"确认 {project} 的项目判断：{question}",
                    reason="项目巡检无法替用户确认哪些候选应写入正式项目进展。",
                    next_step="Codex 先给出推荐答案和证据；必要时再让用户确认。",
                    evidence=[evidence_path],
                    source="project-progress",
                )
            )


def score_decision_level(level: str) -> int:
    return {"高": 88, "中": 72, "低": 48}.get(level, 58)


def score_quality_level(level: str) -> int:
    return {"红色": 92, "黄色": 78, "绿色": 42}.get(level, 58)


def add_decision_review_suggestions(reports: dict[str, Path], suggestions: list[Suggestion]) -> None:
    for project, path in reports.items():
        text = read_text(path)
        meta = parse_frontmatter(text)
        level = str(meta.get("最高风险") or "未知")
        if level == "低":
            continue
        action = str(meta.get("建议动作") or "复核当前方向和下一步。")
        risks = extract_bullets(extract_heading_section(text, "偏航风险"))
        risk_text = "；".join(risks[:2]) if risks else f"{project} 决策审视风险等级为 {level}。"
        suggestions.append(
            Suggestion(
                score=score_decision_level(level),
                owner="Codex/用户" if level == "高" else "Codex",
                domain="决策审视",
                action=f"复核 {project} 决策审视：{action}",
                reason=risk_text,
                next_step=next_step_for("Codex", "决策审视"),
                evidence=[repo_rel(path)],
                source="decision-review",
            )
        )


def add_code_quality_suggestions(reports: dict[str, Path], suggestions: list[Suggestion]) -> None:
    for project, path in reports.items():
        text = read_text(path)
        meta = parse_frontmatter(text)
        level = str(meta.get("质量等级") or "未知")
        if level == "绿色":
            continue
        action = str(meta.get("建议动作") or "补充验证并复核提交边界。")
        risks = extract_bullets(extract_heading_section(text, "主要风险"))
        risk_text = "；".join(risks[:2]) if risks else f"{project} 代码质量等级为 {level}。"
        suggestions.append(
            Suggestion(
                score=score_quality_level(level),
                owner="Codex",
                domain="代码质量",
                action=f"先处理 {project} 代码质量审视：{action}",
                reason=risk_text,
                next_step=next_step_for("Codex", "代码质量"),
                evidence=[repo_rel(path)],
                source="code-quality-review",
            )
        )


def add_project_file_suggestions(project_filter: str, suggestions: list[Suggestion]) -> None:
    if not PROJECT_DIR.exists():
        return
    for overview in sorted(PROJECT_DIR.glob("*/项目总览.md")):
        project_dir = overview.parent
        project = project_dir.name
        if project_filter != "all" and project_filter not in {project, normalize_project_key(project)}:
            continue
        task_file = project_dir / "任务清单.md"
        risk_file = project_dir / "风险清单.md"
        if task_file.exists():
            text = read_text(task_file)
            pending = re.findall(r"^- \[ \] (.+)", text, flags=re.M)
            if len(pending) >= 5:
                suggestions.append(
                    Suggestion(
                        score=52,
                        owner="Codex",
                        domain="项目推进",
                        action=f"压缩 {project} 的任务清单，把未完成项整理成前三个下一步。",
                        reason=f"{project} 当前有 {len(pending)} 个未完成任务，容易让行动面板变得噪声过高。",
                        next_step="Codex 先按依赖、风险和近期目标排序，不直接删除任务。",
                        evidence=[repo_rel(task_file)],
                        source="project-files",
                    )
                )
        if risk_file.exists():
            risks = re.findall(r"^- (.+)", read_text(risk_file), flags=re.M)
            if len(risks) >= 5:
                suggestions.append(
                    Suggestion(
                        score=56,
                        owner="Codex",
                        domain="项目推进",
                        action=f"把 {project} 的风险清单转成可验证门禁或检查项。",
                        reason="风险如果只停留在描述层，会反复出现在巡检报告里。",
                        next_step="Codex 为每条高风险补一个证据入口、触发条件和解除标准。",
                        evidence=[repo_rel(risk_file)],
                        source="project-files",
                    )
                )


def suggestion_key(suggestion: Suggestion) -> str:
    raw = f"{suggestion.owner}|{suggestion.domain}|{suggestion.action}"
    return f"advice:{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:12]}"


def default_status(suggestion: Suggestion) -> str:
    if "OpenClaw" in suggestion.owner or suggestion.owner == "用户" or suggestion.domain in {"候选确认", "前台协作"}:
        return "待提醒"
    return "待处理"


def apply_state(
    suggestions: list[Suggestion],
    *,
    write: bool = False,
    mark_reminded: set[str] | None = None,
    state_path: Path = ADVICE_STATE,
) -> dict[str, Any]:
    state = load_state(state_path)
    records: dict[str, Any] = state.setdefault("records", {})
    seen_at = now().strftime("%Y-%m-%d %H:%M:%S %z")
    mark_reminded = mark_reminded or set()
    current_keys: set[str] = set()
    for suggestion in suggestions:
        record_key = suggestion_key(suggestion)
        current_keys.add(record_key)
        record = records.get(record_key)
        if not isinstance(record, dict):
            record = {
                "记录键": record_key,
                "状态": default_status(suggestion),
                "首次出现": seen_at,
                "提醒次数": 0,
            }
        if record_key in mark_reminded:
            record["状态"] = "已提醒"
            record["提醒次数"] = int(record.get("提醒次数") or 0) + 1
            record["最近提醒"] = seen_at
        record["最近出现"] = seen_at
        record["当前出现"] = True
        record["责任方"] = suggestion.owner
        record["领域"] = suggestion.domain
        record["事项"] = suggestion.action
        record["优先级"] = suggestion.priority
        record["分数"] = suggestion.score
        records[record_key] = record
        suggestion.record_key = record_key
        suggestion.status = str(record.get("状态") or default_status(suggestion))
        suggestion.first_seen = str(record.get("首次出现") or seen_at)
        suggestion.last_seen = str(record.get("最近出现") or seen_at)
        suggestion.reminded_count = int(record.get("提醒次数") or 0)
    for key, record in records.items():
        if isinstance(record, dict) and key not in current_keys:
            record["当前出现"] = False
    if write:
        save_state(state, state_path)
    return state


def dedupe_and_sort(suggestions: list[Suggestion], limit: int) -> list[Suggestion]:
    by_key: dict[str, Suggestion] = {}
    for suggestion in suggestions:
        key = suggestion_key(suggestion)
        existing = by_key.get(key)
        if not existing or suggestion.score > existing.score:
            by_key[key] = suggestion
        elif existing:
            for evidence in suggestion.evidence:
                if evidence and evidence not in existing.evidence:
                    existing.evidence.append(evidence)
    return sorted(by_key.values(), key=lambda item: (-item.score, item.owner, item.action))[:limit]


def build_advice(project_filter: str, limit: int) -> tuple[AdviceContext, list[Suggestion]]:
    dashboard = load_dashboard()
    reports = latest_project_reports(project_filter)
    decision_reports = latest_review_reports("决策审视", project_filter)
    code_quality_reports = latest_review_reports("代码质量审视", project_filter)
    context = AdviceContext(
        generated_at=now().strftime("%Y-%m-%d %H:%M:%S %z"),
        project_filter=project_filter,
        dashboard_loaded=bool(dashboard),
        dashboard_generated_at=str(dashboard.get("generated_at") or ""),
        project_reports=reports,
        decision_reports=decision_reports,
        code_quality_reports=code_quality_reports,
    )
    if dashboard:
        context.sources.append(EvidenceSource(kind="dashboard", path=repo_rel(DASHBOARD_DATA), title="飞书仪表盘数据"))
    for project, path in reports.items():
        context.sources.append(EvidenceSource(kind="project_report", path=repo_rel(path), title=project))
    for project, path in decision_reports.items():
        context.sources.append(EvidenceSource(kind="decision_review", path=repo_rel(path), title=project))
    for project, path in code_quality_reports.items():
        context.sources.append(EvidenceSource(kind="code_quality_review", path=repo_rel(path), title=project))
    suggestions: list[Suggestion] = []
    if dashboard:
        add_dashboard_suggestions(dashboard, suggestions)
    add_project_report_suggestions(reports, suggestions)
    add_decision_review_suggestions(decision_reports, suggestions)
    add_code_quality_suggestions(code_quality_reports, suggestions)
    add_project_file_suggestions(project_filter, suggestions)
    suggestions = dedupe_and_sort(suggestions, limit)
    return context, suggestions


def group_by_owner(suggestions: list[Suggestion]) -> dict[str, list[Suggestion]]:
    grouped: dict[str, list[Suggestion]] = {}
    for suggestion in suggestions:
        grouped.setdefault(suggestion.owner, []).append(suggestion)
    return grouped


def render_markdown(context: AdviceContext, suggestions: list[Suggestion]) -> str:
    counts_by_priority: dict[str, int] = {}
    for suggestion in suggestions:
        counts_by_priority[suggestion.priority] = counts_by_priority.get(suggestion.priority, 0) + 1
    lines = [
        "---",
        "类别: 运行记录",
        "记录类型: 建议分析",
        f"生成时间: {context.generated_at}",
        f"项目过滤: {context.project_filter}",
        "---",
        "",
        "# 建议分析",
        "",
        "## 总览",
        "",
        f"- 建议数量：{len(suggestions)}",
        f"- 项目过滤：{context.project_filter}",
        f"- 后台仪表盘：{'已读取' if context.dashboard_loaded else '未找到'}",
        f"- 仪表盘生成时间：{context.dashboard_generated_at or '未知'}",
        f"- 项目巡检报告：{len(context.project_reports)} 份",
        f"- 优先级分布：{', '.join(f'{key}={value}' for key, value in sorted(counts_by_priority.items())) or '无'}",
    ]
    if suggestions:
        top = suggestions[0]
        lines.append(f"- 最高优先级：[{top.priority}] {top.action}")
    lines.extend(["", "## 优先建议", ""])
    if not suggestions:
        lines.append("- 暂无可生成的建议。")
    for index, suggestion in enumerate(suggestions, 1):
        lines.extend(
            [
                f"### {index}. [{suggestion.priority}] {suggestion.action}",
                "",
                f"- 责任方：{suggestion.owner}",
                f"- 领域：{suggestion.domain}",
                f"- 状态：{suggestion.status or default_status(suggestion)}",
                f"- 记录键：`{suggestion.record_key or suggestion_key(suggestion)}`",
                f"- 分数：{suggestion.score}",
                f"- 理由：{suggestion.reason}",
                f"- 下一步：{suggestion.next_step}",
                f"- 来源：{suggestion.source}",
            ]
        )
        if suggestion.evidence:
            lines.append("- 证据：")
            for evidence in suggestion.evidence[:6]:
                lines.append(f"  - `{evidence}`")
        lines.append("")
    lines.extend(["## 按角色拆分", ""])
    grouped = group_by_owner(suggestions)
    for owner, items in grouped.items():
        lines.extend([f"### {owner}", ""])
        for item in items[:6]:
            lines.append(f"- [{item.priority}][{item.status or default_status(item)}] {item.action}")
        lines.append("")
    lines.extend(["## OpenClaw 可转发摘要", ""])
    openclaw_items = [
        item
        for item in suggestions
        if "OpenClaw" in item.owner or item.owner == "用户" or item.domain in {"候选确认", "前台协作"}
    ]
    if not openclaw_items:
        lines.append("- 暂无需要 OpenClaw 主动触达的事项。")
    else:
        for item in openclaw_items[:5]:
            lines.append(f"- [{item.priority}][{item.status or default_status(item)}] {item.action}（{item.next_step}）")
    lines.extend(["", "## 证据来源", ""])
    if context.sources:
        for source in context.sources:
            title = f" - {source.title}" if source.title else ""
            lines.append(f"- {source.kind}{title}: `{source.path}`")
    else:
        lines.append("- 未读取到证据来源。")
    return "\n".join(lines).rstrip() + "\n"


def render_json(context: AdviceContext, suggestions: list[Suggestion]) -> str:
    payload = {
        "context": {
            **asdict(context),
            "project_reports": {key: repo_rel(value) for key, value in context.project_reports.items()},
            "decision_reports": {key: repo_rel(value) for key, value in context.decision_reports.items()},
            "code_quality_reports": {key: repo_rel(value) for key, value in context.code_quality_reports.items()},
        },
        "suggestions": [{**asdict(item), "priority": item.priority} for item in suggestions],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def dashboard_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def csv_fieldnames(rows: list[dict[str, Any]]) -> list[str]:
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    return fieldnames


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = csv_fieldnames(rows)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: dashboard_value(row.get(key, "")) for key in fieldnames})


def advice_rows(context: AdviceContext, suggestions: list[Suggestion]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for suggestion in suggestions:
        rows.append(
            {
                "记录键": suggestion.record_key or suggestion_key(suggestion),
                "生成时间": context.generated_at,
                "优先级": suggestion.priority,
                "分数": suggestion.score,
                "状态": suggestion.status or default_status(suggestion),
                "责任方": suggestion.owner,
                "领域": suggestion.domain,
                "事项": suggestion.action,
                "下一步": suggestion.next_step,
                "理由": suggestion.reason,
                "来源": suggestion.source,
                "证据": "\n".join(suggestion.evidence),
                "首次出现": suggestion.first_seen,
                "最近出现": suggestion.last_seen,
                "提醒次数": suggestion.reminded_count,
                "项目过滤": context.project_filter,
            }
        )
    return rows


def update_dashboard_data(context: AdviceContext, suggestions: list[Suggestion]) -> None:
    if DASHBOARD_DATA.exists():
        payload = load_dashboard()
    else:
        payload = {
            "generated_at": context.generated_at,
            "snapshot_key": now().strftime("%Y%m%d%H%M"),
            "source": "advice-analysis",
            "tables": {},
        }
    tables = payload.setdefault("tables", {})
    tables["advice"] = {
        "display_name": "当前行动建议",
        "primary_key": "记录键",
        "rows": advice_rows(context, suggestions),
    }
    payload["advice_generated_at"] = context.generated_at
    DASHBOARD_DATA.parent.mkdir(parents=True, exist_ok=True)
    DASHBOARD_DATA.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(DASHBOARD_CSV_DIR / "当前行动建议.csv", list(tables["advice"]["rows"]))


def write_outputs(markdown: str, context: AdviceContext, suggestions: list[Suggestion], *, update_dashboard: bool = True) -> Path:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    path = RUN_DIR / f"建议分析-{now_filename()}.md"
    if path.exists():
        for index in range(2, 1000):
            candidate = RUN_DIR / f"建议分析-{now_filename()}-{index}.md"
            if not candidate.exists():
                path = candidate
                break
    path.write_text(markdown, encoding="utf-8")
    LATEST_ADVICE.write_text(markdown, encoding="utf-8")
    if update_dashboard:
        update_dashboard_data(context, suggestions)
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate my-mind prioritized action advice from collected evidence.")
    parser.add_argument("--project", default="all", help="Project filter: all, my-mind, 个人数据资产系统, edu-agent.")
    parser.add_argument("--limit", type=int, default=12, help="Maximum suggestions to render.")
    parser.add_argument("--format", choices=["markdown", "json"], default="markdown", help="Output format.")
    parser.add_argument("--write", action="store_true", help="Write markdown report and latest advice panel.")
    parser.add_argument("--no-dashboard-update", action="store_true", help="Do not inject advice rows into Feishu dashboard data when writing.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    context, suggestions = build_advice(args.project, args.limit)
    apply_state(suggestions, write=args.write)
    if args.format == "json":
        print(render_json(context, suggestions), end="")
        return 0
    markdown = render_markdown(context, suggestions)
    print(markdown, end="")
    if args.write:
        path = write_outputs(markdown, context, suggestions, update_dashboard=not args.no_dashboard_update)
        print(f"\n已写入建议分析报告：{repo_rel(path)}")
        print(f"已刷新当前行动建议：{repo_rel(LATEST_ADVICE)}")
        if not args.no_dashboard_update:
            print(f"已更新飞书仪表盘建议表：{repo_rel(DASHBOARD_DATA)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
