#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[4]
TZ = ZoneInfo("Asia/Shanghai")
PROJECT_DIR = ROOT / "10_项目/个人数据资产系统"
RUN_DIR = ROOT / "85_运行记录"


@dataclass(frozen=True)
class ProjectConfig:
    key: str
    name: str
    project_dir: Path
    repo_dir: Path
    stage_judgement: str


PROJECTS = {
    "my-mind": ProjectConfig(
        key="my-mind",
        name="个人数据资产系统",
        project_dir=ROOT / "10_项目/个人数据资产系统",
        repo_dir=ROOT,
        stage_judgement="阶段三仍处于试运行和打通闭环阶段：当前重点不是新增更多资料，而是验证入库、Obsidian 视图、项目影响和进度巡检能否形成稳定闭环。",
    ),
    "个人数据资产系统": ProjectConfig(
        key="my-mind",
        name="个人数据资产系统",
        project_dir=ROOT / "10_项目/个人数据资产系统",
        repo_dir=ROOT,
        stage_judgement="阶段三仍处于试运行和打通闭环阶段：当前重点不是新增更多资料，而是验证入库、Obsidian 视图、项目影响和进度巡检能否形成稳定闭环。",
    ),
    "edu-agent": ProjectConfig(
        key="edu-agent",
        name="edu-agent",
        project_dir=ROOT / "10_项目/edu-agent",
        repo_dir=ROOT.parent / "edu-agent",
        stage_judgement="edu-agent 处于 PDF 题目入库与智能排版质量门禁阶段：当前重点是把裁题、OCR、文件存储、OpenAPI、前端对接和部署证据收敛为可复核交付。",
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
class ProgressEvidence:
    project: ProjectConfig
    commits: list[Commit] = field(default_factory=list)
    status_items: list[StatusItem] = field(default_factory=list)
    run_records: list[Path] = field(default_factory=list)
    project_files: list[Path] = field(default_factory=list)


@dataclass
class ProgressAnalysis:
    stage_judgement: str
    meaningful_progress: list[str] = field(default_factory=list)
    evidence_noise: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    next_actions: list[str] = field(default_factory=list)
    confirmation_questions: list[str] = field(default_factory=list)
    apply_recommendation: str = ""


def resolve_project(value: str) -> ProjectConfig:
    project = PROJECTS.get(value)
    if project is None:
        choices = "、".join(sorted(PROJECTS))
        raise SystemExit(f"未知项目：{value}。可选：{choices}")
    return project


def run_git(args: list[str], *, cwd: Path) -> str:
    completed = subprocess.run(["git", "-c", "core.quotePath=false", *args], cwd=cwd, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        return ""
    return completed.stdout.rstrip("\n")


def now() -> dt.datetime:
    return dt.datetime.now(TZ)


def now_filename() -> str:
    return now().strftime("%Y-%m-%d-%H%M")


def now_date() -> str:
    return now().strftime("%Y-%m-%d")


def repo_relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def project_relative(path: Path, project: ProjectConfig) -> str:
    try:
        return path.resolve().relative_to(project.repo_dir.resolve()).as_posix()
    except ValueError:
        return repo_relative(path)


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(2, 1000):
        candidate = path.with_name(f"{path.stem}-{index}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"无法生成唯一文件名：{path}")


def collect_commits(project: ProjectConfig, since_hours: int, limit: int) -> list[Commit]:
    if not project.repo_dir.exists():
        return []
    since = f"{since_hours} hours ago"
    output = run_git(["log", f"--since={since}", f"--max-count={limit}", "--date=iso-strict", "--pretty=format:%h%x09%ad%x09%s"], cwd=project.repo_dir)
    commits: list[Commit] = []
    for line in output.splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3:
            commits.append(Commit(sha=parts[0], date=parts[1], subject=parts[2]))
    if commits:
        return commits
    fallback = run_git(["log", f"--max-count={min(limit, 5)}", "--date=iso-strict", "--pretty=format:%h%x09%ad%x09%s"], cwd=project.repo_dir)
    for line in fallback.splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3:
            commits.append(Commit(sha=parts[0], date=parts[1], subject=parts[2]))
    return commits


def collect_status(project: ProjectConfig) -> list[StatusItem]:
    if not project.repo_dir.exists():
        return []
    output = run_git(["status", "--short"], cwd=project.repo_dir)
    items: list[StatusItem] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        code = line[:2].strip() or "??"
        path = line[3:].strip() if len(line) > 3 else line.strip()
        items.append(StatusItem(code=code, path=path))
    return items


def is_project_run_record(path: Path, project: ProjectConfig) -> bool:
    if project.key == "my-mind":
        return True
    if path.name.startswith(f"项目进展巡检-{project.key}-"):
        return True
    try:
        head = path.read_text(encoding="utf-8", errors="ignore")[:2000]
    except OSError:
        return False
    return f"项目: {project.name}" in head or f"# {project.name} 项目进展巡检" in head


def collect_run_records(project: ProjectConfig, since_hours: int, limit: int) -> list[Path]:
    if not RUN_DIR.exists():
        return []
    cutoff = now().timestamp() - since_hours * 3600
    candidates = []
    for path in RUN_DIR.glob("*.md"):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if mtime >= cutoff and is_project_run_record(path, project):
            candidates.append(path)
    candidates.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    return candidates[:limit]


def collect_project_files(project: ProjectConfig) -> list[Path]:
    names = [
        "AGENTS.md",
        "项目总览.md",
        "项目上下文.md",
        "任务清单.md",
        "决策记录.md",
        "问题清单.md",
        "风险清单.md",
        "项目提示词.md",
        "项目进展.md",
        "变更证据.md",
        "项目周报.md",
    ]
    return [project.project_dir / name for name in names if (project.project_dir / name).exists()]


def classify_path(path: str, project: ProjectConfig | None = None) -> str:
    clean = path.strip('"')
    if project and project.key == "edu-agent":
        if clean.startswith("edu_agent/"):
            return "后端服务"
        if clean.startswith("frontend/"):
            return "前端"
        if clean.startswith("design/") or clean == "DESIGN.md":
            return "设计文档"
        if clean.startswith("tests/"):
            return "测试"
        if clean.startswith("scripts/"):
            return "脚本与评测"
        if clean.startswith("infra/") or clean.startswith("configs/") or clean.startswith(".env"):
            return "部署与配置"
        if clean.startswith("artifacts/") or clean.startswith("logs/") or clean.startswith("data/"):
            return "运行产物"
        return "其他"
    if clean.startswith(".codex/skills/"):
        return "技能与脚本"
    if clean.startswith("10_项目/"):
        return "项目管理"
    if clean.startswith("15_索引/") or clean.endswith(".base"):
        return "Obsidian 视图"
    if clean.startswith("20_资料库/") or clean.startswith("30_原子笔记/") or clean.startswith("65_洞察/") or clean.startswith("75_提示词库/"):
        return "长期知识"
    if clean.startswith("00_收件箱/") or clean.startswith("05_流转区/"):
        return "输入流转"
    if clean.startswith("85_运行记录/"):
        return "运行记录"
    if clean.startswith("design/") or clean == "README.md":
        return "文档设计"
    return "其他"


def summarize_status(items: list[StatusItem], project: ProjectConfig | None = None) -> dict[str, list[StatusItem]]:
    grouped: dict[str, list[StatusItem]] = {}
    for item in items:
        grouped.setdefault(classify_path(item.path, project), []).append(item)
    return grouped


def has_path(items: list[StatusItem], pattern: str) -> bool:
    return any(pattern in item.path for item in items)


def changed_paths(items: list[StatusItem]) -> list[str]:
    return [item.path.strip('"') for item in items]


def analyze_progress(evidence: ProgressEvidence) -> ProgressAnalysis:
    grouped = summarize_status(evidence.status_items, evidence.project)
    paths = changed_paths(evidence.status_items)
    meaningful: list[str] = []
    noise: list[str] = []
    risks: list[str] = []
    next_actions: list[str] = []
    questions: list[str] = []

    if evidence.project.key == "edu-agent":
        if grouped.get("后端服务"):
            meaningful.append("后端服务层有变化，可能涉及文件上传下载、OpenAPI、模型网关、任务处理或存储抽象。")
        if grouped.get("前端"):
            meaningful.append("前端工作台有变化，可能涉及在线排版、文件交互或接口对接体验。")
        if grouped.get("设计文档"):
            meaningful.append("设计文档有变化，说明 PDF 版面重排、前端对接或工程落地方案正在被固化。")
        if grouped.get("脚本与评测") or grouped.get("测试"):
            meaningful.append("评测脚本或测试有变化，说明 PDF 题目入库质量门禁和回归验证在继续推进。")
        if grouped.get("部署与配置"):
            meaningful.append("部署与配置层有变化，需要确认本地、测试和生产环境边界是否保持清晰。")
        if grouped.get("运行产物"):
            noise.append("artifacts、logs 或 data 变化多半是运行证据，除非被引用为门禁结果，否则不直接视为项目进展。")
        if any("question" in path.lower() or "pdf" in path.lower() for path in paths):
            next_actions.append("把 PDF 题目入库相关变更分成质量门禁、接口契约、前端展示和运行 artifact 四类复核。")
        if any(path.startswith(".env") for path in paths):
            risks.append("环境模板或配置文件发生变化，提交前需要确认没有真实密钥、内网地址或生产敏感信息。")
        if any(path.startswith("logs/") or path.startswith("artifacts/") for path in paths):
            risks.append("运行日志或样例 artifact 进入工作区，提交前需要判断是否只保留索引或样例子集。")
    elif has_path(evidence.status_items, ".codex/skills/project-progress/"):
        meaningful.append("项目进展巡检能力正在从证据采集升级为项目分析层，能把 commit、运行记录和工作区变化转成进展判断。")
        next_actions.append("观察新版项目进展巡检的分析质量，确认是否允许 `--apply` 将候选摘要追加到项目文件。")
    if has_path(evidence.status_items, ".codex/skills/knowledge-intake/"):
        meaningful.append("入库链路继续与项目管理联动，材料入库后可以产出项目影响建议。")
        next_actions.append("用 2-3 条真实入库材料验证项目影响建议是否能转化为任务、决策或风险。")
    if grouped.get("Obsidian 视图"):
        meaningful.append("Obsidian 项目视图和索引层继续完善，项目资料、洞察、提示词可以被集中查看。")
    if grouped.get("项目管理"):
        meaningful.append("项目管理承载文件有更新，项目进度、证据和周报开始进入知识库结构。")
    if grouped.get("文档设计"):
        meaningful.append("README 与设计稿有更新，说明系统规则正在被固化为可复用约定。")
    if grouped.get("长期知识"):
        meaningful.append("长期知识目录有新增入口或候选内容，说明资料从输入层向可复用知识层移动。")

    if evidence.project.key != "edu-agent" and grouped.get("输入流转"):
        noise.append("收件箱和流转区变化可能来自自动刷新，不能直接视为项目进展。")
    if grouped.get("运行记录"):
        noise.append("运行记录是过程证据，除非影响项目判断，否则不应直接写入项目进展结论。")
    if any("未命名.base" in path for path in paths):
        noise.append("发现 `未命名.base`，可能是 Obsidian 临时视图文件，需要确认是否删除或重命名。")
    if any(path == ".obsidian/graph.json" for path in paths):
        noise.append("`.obsidian/graph.json` 属于本地视图配置，是否纳入版本库需要单独判断。")

    if len(evidence.status_items) >= 30:
        risks.append("工作区变更较多，直接提交会混入自动刷新、运行记录和真正设计改动，建议分批整理。")
        next_actions.append("先按主题分批提交：项目进度巡检、Obsidian 关联层、入库项目影响、运行记录/样例证据。")
    if evidence.project.key != "edu-agent" and grouped.get("输入流转") and grouped.get("长期知识"):
        risks.append("输入流转和长期知识同时变化，需要确认哪些内容只是候选，哪些已经可以沉淀。")
    if not evidence.commits:
        risks.append("时间窗口内没有 commit，当前分析只能基于工作区状态，证据稳定性较弱。")
    if evidence.commits and evidence.status_items:
        risks.append("存在近期 commit 且工作区仍有大量未提交变更，项目进度可能横跨多个未闭合批次。")

    if not next_actions:
        next_actions.append("如果本轮无实质变化，只保留巡检报告，不回写项目进展。")
    next_actions.append("把真正影响项目方向的变化改写为项目进展，而不是照搬文件列表。")
    next_actions.append("需要提交时由 Codex 整理后手动分批 commit/push，不由定时任务自动执行。")

    if meaningful:
        questions.append("哪些进展已经足够稳定，可以从候选写入 `项目进展.md`？")
    if risks:
        questions.append("是否需要先清理或分批提交当前工作区，避免项目进展和临时证据混在一起？")
    if evidence.project.key != "edu-agent" and grouped.get("长期知识"):
        questions.append("长期知识目录新增内容中，哪些已经确认，哪些仍是候选？")
    if evidence.project.key == "edu-agent":
        questions.append("当前 `edu-agent` 工作区哪些变更应进入本轮提交，哪些只是运行证据或草稿？")

    if meaningful and len(evidence.status_items) < 20 and not risks:
        apply_recommendation = "可以考虑人工复核后运行 `--apply`，把分析摘要追加到项目文件。"
    else:
        apply_recommendation = "暂不建议自动 `--apply`；先由 Codex 复核并整理工作区，再决定哪些结论写入项目文件。"

    stage = evidence.project.stage_judgement
    return ProgressAnalysis(
        stage_judgement=stage,
        meaningful_progress=meaningful or ["暂无足够稳定的实质进展结论，当前更适合作为证据观察。"],
        evidence_noise=noise or ["未发现明显证据噪声。"],
        risks=risks or ["暂无明显新增风险。"],
        next_actions=list(dict.fromkeys(next_actions)),
        confirmation_questions=list(dict.fromkeys(questions)) or ["当前无需额外确认。"],
        apply_recommendation=apply_recommendation,
    )


def infer_progress_candidates(evidence: ProgressEvidence) -> list[str]:
    grouped = summarize_status(evidence.status_items, evidence.project)
    candidates: list[str] = []
    if evidence.project.key == "edu-agent":
        if grouped.get("后端服务"):
            candidates.append("后端服务层有变化，需要判断是否推进了文件、OpenAPI、模型或任务链路。")
        if grouped.get("前端"):
            candidates.append("前端有变化，需要判断是否推进了智能体工作台或在线排版交互。")
        if grouped.get("设计文档"):
            candidates.append("设计文档有变化，需要判断是否形成了新的工程约定或验收标准。")
        if grouped.get("脚本与评测") or grouped.get("测试"):
            candidates.append("测试和评测脚本有变化，可能代表质量门禁继续推进。")
        if evidence.commits:
            candidates.append("近期 commit 可作为 edu-agent 质量和生产化进展证据，但仍需解释项目影响。")
        return candidates or ["暂无明显 edu-agent 项目进展候选；可只保留为巡检记录。"]
    if grouped.get("技能与脚本"):
        candidates.append("技能与脚本层有新变化，可能代表自动化能力或后台流程推进。")
    if grouped.get("项目管理"):
        candidates.append("项目管理文件有变化，可能需要同步项目进展、任务、决策或风险。")
    if grouped.get("Obsidian 视图"):
        candidates.append("Obsidian 索引或 Bases 视图有变化，说明知识导航层继续完善。")
    if grouped.get("长期知识"):
        candidates.append("长期知识目录有新增或调整，需要判断是否已经从候选进入可复用状态。")
    if grouped.get("输入流转"):
        candidates.append("收件箱或流转区有变化，需要确认是否只是自动刷新，还是有新阅读/沉淀价值。")
    if grouped.get("文档设计"):
        candidates.append("设计文档或 README 有变化，需要判断是否代表新的系统约定。")
    if evidence.commits:
        candidates.append("近期 commit 可作为进展证据，但仍需解释项目影响。")
    if evidence.run_records:
        candidates.append("近期运行记录可作为自动化执行证据，适合进入变更证据层。")
    return candidates or ["暂无明显项目进展候选；可只保留为巡检记录。"]


def extend_bullets(lines: list[str], items: list[str]) -> None:
    lines.extend(f"- {item}" for item in items)


def render_report(evidence: ProgressEvidence, *, since_hours: int, apply: bool) -> str:
    generated = now().strftime("%Y-%m-%d %H:%M:%S %z")
    grouped = summarize_status(evidence.status_items, evidence.project)
    analysis = analyze_progress(evidence)
    lines = [
        "---",
        "类别: 运行记录",
        "记录类型: 项目进展巡检",
        f"项目: {evidence.project.name}",
        f"生成时间: {generated}",
        f"时间窗口小时: {since_hours}",
        "---",
        "",
        f"# {evidence.project.name} 项目进展巡检",
        "",
        "## 总览",
        "",
        f"- 项目：{evidence.project.name}",
        f"- 项目目录：`{repo_relative(evidence.project.project_dir)}`",
        f"- 证据仓库：`{evidence.project.repo_dir.as_posix()}`",
        f"- 模式：{'写入项目文件' if apply else '只生成候选报告'}",
        f"- 近期 commit：{len(evidence.commits)}",
        f"- 工作区变更：{len(evidence.status_items)}",
        f"- 近期运行记录：{len(evidence.run_records)}",
        f"- 项目文件：{len(evidence.project_files)}",
        "- 自动 commit：否",
        "",
        "## 进展候选",
        "",
    ]
    lines.extend(f"- {item}" for item in infer_progress_candidates(evidence))
    lines.extend(["", "## Codex 项目分析", ""])
    lines.extend(["### 阶段判断", "", f"- {analysis.stage_judgement}", ""])
    lines.extend(["### 有效进展", ""])
    extend_bullets(lines, analysis.meaningful_progress)
    lines.extend(["", "### 证据噪声", ""])
    extend_bullets(lines, analysis.evidence_noise)
    lines.extend(["", "### 风险与阻塞", ""])
    extend_bullets(lines, analysis.risks)
    lines.extend(["", "### 下一步建议", ""])
    extend_bullets(lines, analysis.next_actions)
    lines.extend(["", "### 需要确认", ""])
    extend_bullets(lines, analysis.confirmation_questions)
    lines.extend(["", "### 回写建议", "", f"- {analysis.apply_recommendation}"])
    lines.extend(["", "## Commit 证据", ""])
    if evidence.commits:
        for commit in evidence.commits:
            lines.append(f"- `{commit.sha}` {commit.date}：{commit.subject}")
    else:
        lines.append("- 时间窗口内没有 commit。")
    lines.extend(["", "## 工作区证据", ""])
    if grouped:
        for group, items in sorted(grouped.items()):
            lines.append(f"### {group}")
            lines.append("")
            for item in items[:20]:
                lines.append(f"- `{item.code}` `{item.path}`")
            if len(items) > 20:
                lines.append(f"- 另有 {len(items) - 20} 项未展开。")
            lines.append("")
    else:
        lines.append("- 工作区干净。")
    lines.extend(["", "## 运行记录证据", ""])
    if evidence.run_records:
        for path in evidence.run_records:
            lines.append(f"- `{repo_relative(path)}`")
    else:
        lines.append("- 时间窗口内没有新的运行记录。")
    lines.extend(["", "## 建议下一步", ""])
    lines.extend(
        [
            "1. 如果进展候选准确，再运行 `project_progress.py --write --apply` 追加到项目文件。",
            "2. 如果工作区变更较多，先由 Codex 分批整理、提交、推送。",
            "3. 不要让定时任务自动 commit；commit 仍由人工/Codex 在整理后执行。",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def append_once(path: Path, marker: str, section: str) -> None:
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if marker in text:
        return
    path.write_text(text.rstrip() + "\n\n" + marker + "\n" + section.rstrip() + "\n", encoding="utf-8")


def apply_to_project_files(evidence: ProgressEvidence, report_path: Path | None) -> None:
    stamp = now_filename()
    marker = f"<!-- project-progress:{evidence.project.key}:{stamp} -->"
    report_ref = f"`{repo_relative(report_path)}`" if report_path else "本次 dry-run 报告"
    candidates = infer_progress_candidates(evidence)
    analysis = analyze_progress(evidence)

    progress_section = "\n".join(
        [
            f"### {now_date()} 项目进展候选",
            "",
            f"- 来源报告：{report_ref}",
            "- 阶段判断：" + analysis.stage_judgement,
            "- 有效进展：",
            *[f"  - {item}" for item in analysis.meaningful_progress],
            "- 下一步建议：",
            *[f"  - {item}" for item in analysis.next_actions],
            "- 状态：候选，待确认后可改写为正式进展。",
        ]
    )
    evidence_section = "\n".join(
        [
            f"### {now_date()} 自动巡检证据",
            "",
            f"- 来源报告：{report_ref}",
            f"- 近期 commit：{len(evidence.commits)}",
            f"- 工作区变更：{len(evidence.status_items)}",
            f"- 近期运行记录：{len(evidence.run_records)}",
            f"- 证据噪声：{len(analysis.evidence_noise)}",
            "",
            "#### Commit",
            "",
            *([f"- `{commit.sha}`：{commit.subject}" for commit in evidence.commits] or ["- 暂无。"]),
            "",
            "#### 运行记录",
            "",
            *([f"- `{repo_relative(path)}`" for path in evidence.run_records] or ["- 暂无。"]),
            "",
            "#### 证据噪声",
            "",
            *[f"- {item}" for item in analysis.evidence_noise],
        ]
    )
    weekly_section = "\n".join(
        [
            f"### {now().strftime('%Y-W%V')} 自动周报候选",
            "",
            f"- 来源报告：{report_ref}",
            "- 本周候选进展：",
            *[f"  - {item}" for item in analysis.meaningful_progress],
            "- 本周风险：",
            *[f"  - {item}" for item in analysis.risks],
            "- 状态：候选，等待周复盘确认。",
        ]
    )
    progress_file = evidence.project.project_dir / "项目进展.md"
    evidence_file = evidence.project.project_dir / "变更证据.md"
    weekly_file = evidence.project.project_dir / "项目周报.md"
    append_once(progress_file, marker, progress_section)
    append_once(evidence_file, marker, evidence_section)
    append_once(weekly_file, marker, weekly_section)


def write_report(report: str, project: ProjectConfig) -> Path:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    path = unique_path(RUN_DIR / f"项目进展巡检-{project.key}-{now_filename()}.md")
    path.write_text(report, encoding="utf-8")
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate my-mind project progress candidates from git and run records.")
    parser.add_argument("--project", default="my-mind", help="Managed project key, e.g. my-mind or edu-agent.")
    parser.add_argument("--since-hours", type=int, default=24, help="Time window for commits and run records.")
    parser.add_argument("--commit-limit", type=int, default=20, help="Maximum commits to include.")
    parser.add_argument("--run-record-limit", type=int, default=20, help="Maximum run records to include.")
    parser.add_argument("--apply", action="store_true", help="Append candidate progress/evidence/week report sections to project files.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="Write report to 85_运行记录.")
    mode.add_argument("--dry-run", action="store_true", help="Print report only. This is the default.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project = resolve_project(args.project)
    evidence = ProgressEvidence(
        project=project,
        commits=collect_commits(project, args.since_hours, args.commit_limit),
        status_items=collect_status(project),
        run_records=collect_run_records(project, args.since_hours, args.run_record_limit),
        project_files=collect_project_files(project),
    )
    report = render_report(evidence, since_hours=args.since_hours, apply=args.apply)
    print(report, end="")
    report_path: Path | None = None
    if args.write:
        report_path = write_report(report, project)
        print(f"\n已写入项目进展巡检报告：{repo_relative(report_path)}")
    if args.apply:
        apply_to_project_files(evidence, report_path)
        print("已追加项目进展候选、变更证据和周报候选。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
