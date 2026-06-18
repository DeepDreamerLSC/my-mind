#!/usr/bin/env python3
"""Review managed project decisions and deviation risk for my-mind."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[4]
RUN_DIR = ROOT / "85_运行记录"
DASHBOARD_DIR = RUN_DIR / "后台总览"
TZ = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class ProjectConfig:
    key: str
    name: str
    project_dir: Path
    repo_dir: Path
    stage: str
    target: str


PROJECTS = {
    "my-mind": ProjectConfig(
        key="my-mind",
        name="个人数据资产系统",
        project_dir=ROOT / "10_项目/个人数据资产系统",
        repo_dir=ROOT,
        stage="试运行和闭环打磨阶段",
        target="让本地知识库、OpenClaw 前台和 Codex 后台形成稳定的输入、分拣、沉淀、项目反馈闭环。",
    ),
    "edu-agent": ProjectConfig(
        key="edu-agent",
        name="edu-agent",
        project_dir=ROOT / "10_项目/edu-agent",
        repo_dir=ROOT.parent / "edu-agent",
        stage="PDF 题目入库与智能排版质量门禁阶段",
        target="把 PDF 题目入库、裁题/OCR、存储、OpenAPI、前端对接和质量验证收敛成可复核交付。",
    ),
}


@dataclass(frozen=True)
class Commit:
    sha: str
    date: str
    subject: str


@dataclass(frozen=True)
class StatusItem:
    code: str
    path: str


@dataclass
class DecisionEvidence:
    project: ProjectConfig
    commits: list[Commit] = field(default_factory=list)
    status_items: list[StatusItem] = field(default_factory=list)
    project_files: dict[str, str] = field(default_factory=dict)
    latest_reports: dict[str, Path] = field(default_factory=dict)


@dataclass
class DecisionReview:
    risk_level: str
    conclusion: str
    assumptions: list[str]
    counter_view: list[str]
    deviation_risks: list[str]
    opportunity_costs: list[str]
    smallest_actions: list[str]
    stop_doing: list[str]
    openclaw_questions: list[str]


def now() -> dt.datetime:
    return dt.datetime.now(TZ)


def now_text() -> str:
    return now().strftime("%Y-%m-%d %H:%M:%S %z")


def now_filename() -> str:
    return now().strftime("%Y-%m-%d-%H%M")


def repo_rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def run_git(args: list[str], *, cwd: Path) -> str:
    if not cwd.exists():
        return ""
    result = subprocess.run(["git", "-c", "core.quotePath=false", *args], cwd=cwd, text=True, capture_output=True, check=False)
    return result.stdout.rstrip("\n") if result.returncode == 0 else ""


def collect_commits(project: ProjectConfig, since_hours: int, limit: int) -> list[Commit]:
    output = run_git(
        ["log", f"--since={since_hours} hours ago", f"--max-count={limit}", "--date=iso-strict", "--pretty=format:%h%x09%ad%x09%s"],
        cwd=project.repo_dir,
    )
    if not output:
        output = run_git(["log", f"--max-count={min(limit, 5)}", "--date=iso-strict", "--pretty=format:%h%x09%ad%x09%s"], cwd=project.repo_dir)
    commits: list[Commit] = []
    for line in output.splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3:
            commits.append(Commit(sha=parts[0], date=parts[1], subject=parts[2]))
    return commits


def collect_status(project: ProjectConfig) -> list[StatusItem]:
    output = run_git(["status", "--short"], cwd=project.repo_dir)
    items: list[StatusItem] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        items.append(StatusItem(code=(line[:2].strip() or "??"), path=line[3:].strip() if len(line) > 3 else line.strip()))
    return items


def collect_project_files(project: ProjectConfig) -> dict[str, str]:
    files: dict[str, str] = {}
    for name in ["项目总览.md", "项目上下文.md", "任务清单.md", "风险清单.md", "问题清单.md", "决策记录.md", "项目进展.md"]:
        path = project.project_dir / name
        if path.exists():
            files[name] = read_text(path)
    return files


def latest_file(pattern: str) -> Path | None:
    files = sorted(RUN_DIR.glob(pattern), key=lambda path: path.stat().st_mtime, reverse=True)
    return files[0] if files else None


def latest_project_report(project: ProjectConfig) -> Path | None:
    return latest_file(f"项目进展巡检-{project.key}-*.md")


def collect_reports(project: ProjectConfig) -> dict[str, Path]:
    reports: dict[str, Path] = {}
    progress = latest_project_report(project)
    advice = latest_file("建议分析-*.md")
    if progress:
        reports["项目进展巡检"] = progress
    if advice:
        reports["建议分析"] = advice
    return reports


def classify_path(path: str, project: ProjectConfig) -> str:
    if project.key == "edu-agent":
        if path.startswith("edu_agent/"):
            return "后端"
        if path.startswith("frontend/"):
            return "前端"
        if path.startswith("tests/") or path.startswith("evals/"):
            return "测试"
        if path.startswith("design/") or path == "DESIGN.md":
            return "设计"
        if path.startswith("artifacts/") or path.startswith("logs/") or path.startswith("data/"):
            return "运行产物"
        if path.startswith(".env") or path.startswith("configs/") or path.startswith("infra/"):
            return "配置"
        return "其他"
    if path.startswith(".codex/skills/"):
        return "技能自动化"
    if path.startswith("00_收件箱/") or path.startswith("05_流转区/"):
        return "输入流转"
    if path.startswith("85_运行记录/"):
        return "运行记录"
    if path.startswith("10_项目/"):
        return "项目管理"
    if path.startswith("20_资料库/") or path.startswith("65_洞察/") or path.startswith("75_提示词库/"):
        return "长期知识"
    if path.startswith("design/") or path == "README.md":
        return "规则文档"
    return "其他"


def count_by_category(items: list[StatusItem], project: ProjectConfig) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        category = classify_path(item.path.strip('"'), project)
        counts[category] = counts.get(category, 0) + 1
    return counts


def has_project_file(files: dict[str, str], name: str) -> bool:
    return bool(files.get(name, "").strip())


def incomplete_tasks(text: str) -> int:
    return len(re.findall(r"(?m)^-\s+\[\s\]\s+", text))


def extract_bullets(text: str, heading: str) -> list[str]:
    marker = f"## {heading}"
    lines = text.splitlines()
    start: int | None = None
    for index, line in enumerate(lines):
        if line.strip() == marker:
            start = index + 1
            break
    if start is None:
        return []
    bullets: list[str] = []
    for line in lines[start:]:
        if line.startswith("## "):
            break
        stripped = line.strip()
        if stripped.startswith("- "):
            bullets.append(stripped[2:].strip())
    return bullets


def report_snippet(path: Path, heading: str) -> list[str]:
    try:
        return extract_bullets(read_text(path), heading)[:3]
    except OSError:
        return []


def choose_risk_level(risks: list[str], status_count: int) -> str:
    if any(keyword in " ".join(risks) for keyword in ["偏航", "没有决策记录", "配置", "生产", "密钥", "过度自动化"]):
        return "高"
    if status_count >= 30 or len(risks) >= 3:
        return "高"
    if status_count >= 10 or risks:
        return "中"
    return "低"


def build_review(evidence: DecisionEvidence) -> DecisionReview:
    project = evidence.project
    categories = count_by_category(evidence.status_items, project)
    paths = [item.path.strip('"') for item in evidence.status_items]
    project_text = "\n".join(evidence.project_files.values())
    latest_progress = evidence.latest_reports.get("项目进展巡检")

    assumptions = [
        f"当前阶段仍然是「{project.stage}」，继续投入应服务于：{project.target}",
        "近期 commit 和工作区变化能够代表真实进展，而不是只代表自动化产物增加。",
    ]
    counter_view: list[str] = []
    risks: list[str] = []
    costs: list[str] = []
    actions: list[str] = []
    stop_doing: list[str] = []
    questions: list[str] = []

    if not has_project_file(evidence.project_files, "决策记录.md"):
        risks.append("没有可用的 `决策记录.md`，项目方向变化可能只停留在聊天和临时报告里。")
        actions.append("补一条本阶段决策记录：当前目标、暂不做什么、下一次复盘标准。")
    if not has_project_file(evidence.project_files, "风险清单.md"):
        risks.append("没有可用的 `风险清单.md`，外部视角难以及时阻止惯性推进。")
        actions.append("把本轮最高风险写成一条可检查的项目风险。")
    if incomplete_tasks(evidence.project_files.get("任务清单.md", "")) >= 8:
        risks.append("任务清单未完成项较多，容易把忙碌误认为推进。")
        actions.append("把任务清单压缩成 3 个以内的本周最小动作。")

    if project.key == "my-mind":
        operational = categories.get("运行记录", 0) + categories.get("输入流转", 0)
        system_work = categories.get("技能自动化", 0) + categories.get("规则文档", 0)
        if operational >= 10 and system_work == 0:
            risks.append("运行状态变化远多于系统规则变化，可能是在维护流水线而不是改善用户决策质量。")
        if categories.get("技能自动化", 0) >= 4:
            assumptions.append("继续新增 skill 会降低而不是增加系统摩擦。")
            counter_view.append("反方意见：当前瓶颈也许不是 skill 数量，而是哪些输出真正能改变你的项目决策。")
            costs.append("继续扩展后台能力会占用项目执行注意力，推迟真正使用和复盘。")
            stop_doing.append("暂停新增低频自动化，先观察现有驾驶舱和前台提醒是否真的触达你。")
        if categories.get("长期知识", 0) and categories.get("输入流转", 0):
            risks.append("输入流转和长期知识同时变化，需要确认哪些是候选，哪些已经真的化为己有。")
    else:
        if categories.get("后端", 0) and not categories.get("测试", 0):
            risks.append("后端代码有变化但测试证据不足，可能提前进入功能扩张。")
        if categories.get("前端", 0) and not categories.get("设计", 0):
            counter_view.append("反方意见：前端变化如果没有设计或验收标准，可能只是局部体验调整。")
        if categories.get("运行产物", 0):
            risks.append("运行产物进入工作区，容易把样例输出误认为交付质量。")
            stop_doing.append("不要把 artifact 数量当作质量门禁；只保留可复核样例和验证结论。")

    if len(evidence.status_items) >= 20:
        risks.append(f"工作区已有 {len(evidence.status_items)} 个未提交改动，当前决策横跨多个批次。")
        actions.append("先按代码、文档、运行状态分批收敛，再继续新增能力。")
    if evidence.commits and evidence.status_items:
        risks.append("近期已有 commit 但工作区仍未闭合，项目结论可能跨越多个未复核上下文。")
    if not evidence.commits:
        assumptions.append("在缺少近期 commit 的情况下，当前审视主要依赖工作区和运行记录。")

    if latest_progress:
        for item in report_snippet(latest_progress, "项目判断"):
            assumptions.append(f"项目巡检判断：{item}")
        for item in report_snippet(latest_progress, "风险与阻塞"):
            risks.append(f"项目巡检风险：{item}")

    if "用户" not in project_text and project.key == "my-mind":
        counter_view.append("反方意见：系统可能越来越精巧，但用户实际阅读、反馈、确认的证据仍不足。")
    counter_view.append("反方意见：如果今天只允许做一件事，最可能产生价值的是验证一个真实使用闭环，而不是继续完善框架。")

    if not costs:
        costs.append("继续沿当前路径投入，会推迟一次更小范围的验收和复盘。")
    if not actions:
        actions.append("保留本轮审视报告，下一步只做一个可验证的小动作。")
    actions.append("用一次真实工作场景验证：这个项目输出是否帮你更快、更稳地做决策。")
    if not stop_doing:
        stop_doing.append("暂时不要把新增报告数量当作项目质量。")

    if risks:
        questions.append(f"{project.name} 当前最高风险是否符合你的直觉，还是需要 Codex 重新审视证据？")
    if any("决策记录" in risk for risk in risks):
        questions.append("是否允许 Codex 起草一条候选决策记录，等你确认后写入项目文件？")
    if len(evidence.status_items) >= 20:
        questions.append("是否先暂停新增功能，优先整理当前工作区并分批提交？")
    if not questions:
        questions.append("当前无需打扰用户；下次项目巡检后再复查。")

    risk_level = choose_risk_level(risks, len(evidence.status_items))
    if risk_level == "高":
        conclusion = "需要先收敛方向和工作区，再继续扩大实现。"
    elif risk_level == "中":
        conclusion = "可以继续推进，但下一步必须保持小而可逆。"
    else:
        conclusion = "方向风险较低，继续用小步验证即可。"

    return DecisionReview(
        risk_level=risk_level,
        conclusion=conclusion,
        assumptions=list(dict.fromkeys(assumptions))[:8],
        counter_view=list(dict.fromkeys(counter_view))[:6],
        deviation_risks=list(dict.fromkeys(risks))[:8] or ["暂无明显偏航风险；仍需用真实使用场景验证。"],
        opportunity_costs=list(dict.fromkeys(costs))[:5],
        smallest_actions=list(dict.fromkeys(actions))[:5],
        stop_doing=list(dict.fromkeys(stop_doing))[:4],
        openclaw_questions=list(dict.fromkeys(questions))[:4],
    )


def add_bullets(lines: list[str], values: list[str]) -> None:
    lines.extend(f"- {value}" for value in values)


def render_project(evidence: DecisionEvidence) -> str:
    review = build_review(evidence)
    project = evidence.project
    categories = count_by_category(evidence.status_items, project)
    lines = [
        "---",
        "类别: 运行记录",
        "记录类型: 决策审视",
        f"项目: {project.name}",
        f"项目键: {project.key}",
        f"生成时间: {now_text()}",
        "审视模型建议: gpt-5.4 / xhigh",
        f"最高风险: {review.risk_level}",
        f"建议动作: {review.smallest_actions[0] if review.smallest_actions else '继续观察'}",
        "---",
        "",
        f"# {project.name} 决策审视",
        "",
        "## 总览",
        "",
        f"- 项目阶段：{project.stage}",
        f"- 目标：{project.target}",
        f"- 风险等级：{review.risk_level}",
        f"- 结论：{review.conclusion}",
        f"- 近期 commit：{len(evidence.commits)}",
        f"- 工作区改动：{len(evidence.status_items)}",
        f"- 项目文件：{len(evidence.project_files)}",
        "- 自动修改项目文件：否",
        "",
        "## 当前隐含假设",
        "",
    ]
    add_bullets(lines, review.assumptions)
    lines.extend(["", "## 反方视角", ""])
    add_bullets(lines, review.counter_view)
    lines.extend(["", "## 偏航风险", ""])
    add_bullets(lines, review.deviation_risks)
    lines.extend(["", "## 机会成本", ""])
    add_bullets(lines, review.opportunity_costs)
    lines.extend(["", "## 下个最小可逆动作", ""])
    add_bullets(lines, review.smallest_actions)
    lines.extend(["", "## 现在先别做", ""])
    add_bullets(lines, review.stop_doing)
    lines.extend(["", "## OpenClaw 可提醒问题", ""])
    add_bullets(lines, review.openclaw_questions)
    lines.extend(["", "## 证据概览", ""])
    if categories:
        for category, count in sorted(categories.items(), key=lambda item: (-item[1], item[0])):
            lines.append(f"- {category}：{count} 项")
    else:
        lines.append("- 工作区干净。")
    lines.extend(["", "## Commit 证据", ""])
    if evidence.commits:
        for commit in evidence.commits[:10]:
            lines.append(f"- `{commit.sha}` {commit.date}：{commit.subject}")
    else:
        lines.append("- 时间窗口内没有 commit。")
    lines.extend(["", "## 工作区样例", ""])
    if evidence.status_items:
        for item in evidence.status_items[:20]:
            lines.append(f"- `{item.code}` `{item.path}`")
        if len(evidence.status_items) > 20:
            lines.append(f"- 另有 {len(evidence.status_items) - 20} 项未展开。")
    else:
        lines.append("- 工作区干净。")
    lines.extend(["", "## 证据来源", ""])
    for name, path in evidence.latest_reports.items():
        lines.append(f"- {name}: `{repo_rel(path)}`")
    for name in sorted(evidence.project_files):
        lines.append(f"- 项目文件: `{repo_rel(project.project_dir / name)}`")
    return "\n".join(lines).rstrip() + "\n"


def gather(project: ProjectConfig, args: argparse.Namespace) -> DecisionEvidence:
    return DecisionEvidence(
        project=project,
        commits=collect_commits(project, args.since_hours, args.commit_limit),
        status_items=collect_status(project),
        project_files=collect_project_files(project),
        latest_reports=collect_reports(project),
    )


def write_report(project: ProjectConfig, report: str) -> Path:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    path = RUN_DIR / f"决策审视-{project.key}-{now_filename()}.md"
    if path.exists():
        for index in range(2, 1000):
            candidate = RUN_DIR / f"决策审视-{project.key}-{now_filename()}-{index}.md"
            if not candidate.exists():
                path = candidate
                break
    path.write_text(report, encoding="utf-8")
    (DASHBOARD_DIR / f"决策审视-{project.key}.md").write_text(report, encoding="utf-8")
    return path


def parse_frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---\n"):
        return {}
    lines = text.splitlines()
    data: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip().strip('"')
    return data


def summary_from_reports(paths: list[Path]) -> str:
    lines = ["# 决策审视", "", f"- 更新时间：{now_text()}", ""]
    for path in paths:
        meta = parse_frontmatter(read_text(path))
        lines.append(f"## {meta.get('项目', path.stem)}")
        lines.append("")
        lines.append(f"- 最高风险：{meta.get('最高风险', '未知')}")
        lines.append(f"- 建议动作：{meta.get('建议动作', '继续观察')}")
        lines.append(f"- 报告：`{repo_rel(path)}`")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def resolve_projects(value: str) -> list[ProjectConfig]:
    if value == "all":
        return [PROJECTS["my-mind"], PROJECTS["edu-agent"]]
    if value in PROJECTS:
        return [PROJECTS[value]]
    choices = "、".join(["all", *PROJECTS.keys()])
    raise SystemExit(f"未知项目：{value}。可选：{choices}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Review project decisions and deviation risk.")
    parser.add_argument("--project", default="my-mind", help="Project key: my-mind, edu-agent, or all.")
    parser.add_argument("--since-hours", type=int, default=24)
    parser.add_argument("--commit-limit", type=int, default=20)
    parser.add_argument("--format", choices=["markdown", "json"], default="markdown")
    parser.add_argument("--write", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    reports: list[tuple[ProjectConfig, DecisionEvidence, str, Path | None]] = []
    for project in resolve_projects(args.project):
        evidence = gather(project, args)
        report = render_project(evidence)
        path = write_report(project, report) if args.write else None
        reports.append((project, evidence, report, path))

    if args.format == "json":
        payload: dict[str, Any] = {"generated_at": now_text(), "projects": []}
        for project, evidence, report, path in reports:
            meta = parse_frontmatter(report)
            payload["projects"].append(
                {
                    "project": project.name,
                    "project_key": project.key,
                    "risk_level": meta.get("最高风险", ""),
                    "suggested_action": meta.get("建议动作", ""),
                    "commits": len(evidence.commits),
                    "status_items": len(evidence.status_items),
                    "report_path": repo_rel(path) if path else "",
                }
            )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for _, _, report, path in reports:
            print(report, end="")
            if path:
                print(f"\n已写入决策审视报告：{repo_rel(path)}\n")

    if args.write:
        written = [path for _, _, _, path in reports if path]
        if written:
            (DASHBOARD_DIR / "决策审视.md").write_text(summary_from_reports(written), encoding="utf-8")
            print(f"已刷新固定决策审视：{repo_rel(DASHBOARD_DIR / '决策审视.md')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
