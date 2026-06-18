#!/usr/bin/env python3
"""Review recent code quality risk for managed my-mind projects."""

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
    code_roots: tuple[str, ...]
    test_roots: tuple[str, ...]


PROJECTS = {
    "my-mind": ProjectConfig(
        key="my-mind",
        name="个人数据资产系统",
        project_dir=ROOT / "10_项目/个人数据资产系统",
        repo_dir=ROOT,
        code_roots=(".codex/skills/",),
        test_roots=("tests/",),
    ),
    "edu-agent": ProjectConfig(
        key="edu-agent",
        name="edu-agent",
        project_dir=ROOT / "10_项目/edu-agent",
        repo_dir=ROOT.parent / "edu-agent",
        code_roots=("edu_agent/", "frontend/", "scripts/"),
        test_roots=("tests/", "evals/"),
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


@dataclass(frozen=True)
class Numstat:
    added: int
    deleted: int
    path: str


@dataclass
class CodeEvidence:
    project: ProjectConfig
    commits: list[Commit] = field(default_factory=list)
    status_items: list[StatusItem] = field(default_factory=list)
    numstat: list[Numstat] = field(default_factory=list)
    added_lines: list[tuple[str, str]] = field(default_factory=list)
    latest_decision_review: Path | None = None


@dataclass
class QualityReview:
    level: str
    conclusion: str
    risks: list[str]
    smells: list[str]
    required_checks: list[str]
    commit_gates: list[str]
    batches: list[str]
    open_questions: list[str]


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


def collect_numstat(project: ProjectConfig) -> list[Numstat]:
    output = run_git(["diff", "--numstat", "HEAD", "--"], cwd=project.repo_dir)
    stats: list[Numstat] = []
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        try:
            added = int(parts[0]) if parts[0].isdigit() else 0
            deleted = int(parts[1]) if parts[1].isdigit() else 0
        except ValueError:
            added = deleted = 0
        stats.append(Numstat(added=added, deleted=deleted, path=parts[2]))
    return stats


def collect_added_lines(project: ProjectConfig, max_lines: int) -> list[tuple[str, str]]:
    output = run_git(["diff", "--unified=0", "HEAD", "--"], cwd=project.repo_dir)
    added: list[tuple[str, str]] = []
    current_path = ""
    for line in output.splitlines():
        if line.startswith("+++ b/"):
            current_path = line[6:]
            continue
        if not line.startswith("+") or line.startswith("+++"):
            continue
        text = line[1:]
        if text.strip():
            added.append((current_path, text))
        if len(added) >= max_lines:
            break
    return added


def latest_file(pattern: str) -> Path | None:
    files = sorted(RUN_DIR.glob(pattern), key=lambda path: path.stat().st_mtime, reverse=True)
    return files[0] if files else None


def latest_decision_review(project: ProjectConfig) -> Path | None:
    return latest_file(f"决策审视-{project.key}-*.md")


def gather(project: ProjectConfig, args: argparse.Namespace) -> CodeEvidence:
    return CodeEvidence(
        project=project,
        commits=collect_commits(project, args.since_hours, args.commit_limit),
        status_items=collect_status(project),
        numstat=collect_numstat(project),
        added_lines=collect_added_lines(project, args.max_added_lines),
        latest_decision_review=latest_decision_review(project),
    )


def classify_path(path: str, project: ProjectConfig) -> str:
    clean = path.strip('"')
    if project.key == "edu-agent":
        if clean.startswith("edu_agent/"):
            return "后端代码"
        if clean.startswith("frontend/"):
            return "前端代码"
        if clean.startswith("tests/") or clean.startswith("evals/"):
            return "测试"
        if clean.startswith("scripts/"):
            return "脚本"
        if clean.startswith("design/") or clean == "DESIGN.md":
            return "设计文档"
        if clean.startswith("artifacts/") or clean.startswith("logs/") or clean.startswith("data/"):
            return "运行产物"
        if clean.startswith(".env") or clean.startswith("configs/") or clean.startswith("infra/"):
            return "配置"
        return "其他"
    if clean.startswith(".codex/skills/"):
        return "技能代码"
    if clean.startswith("85_运行记录/"):
        return "运行记录"
    if clean.startswith("00_收件箱/") or clean.startswith("05_流转区/"):
        return "输入流转"
    if clean.startswith("10_项目/"):
        return "项目管理"
    if clean.startswith("design/") or clean == "README.md":
        return "文档"
    if clean.endswith(".py") or clean.endswith(".js") or clean.endswith(".ts") or clean.endswith(".tsx"):
        return "代码"
    return "其他"


def count_by_category(items: list[StatusItem], project: ProjectConfig) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        category = classify_path(item.path, project)
        counts[category] = counts.get(category, 0) + 1
    return counts


def changed_paths(evidence: CodeEvidence) -> list[str]:
    paths = {item.path.strip('"') for item in evidence.status_items}
    paths.update(stat.path for stat in evidence.numstat)
    return sorted(paths)


def is_code_path(path: str, project: ProjectConfig) -> bool:
    if any(path.startswith(root) for root in project.code_roots):
        return True
    return path.endswith((".py", ".js", ".jsx", ".ts", ".tsx", ".sh"))


def is_test_path(path: str, project: ProjectConfig) -> bool:
    return any(path.startswith(root) for root in project.test_roots) or re.search(r"(^|/)(test|tests|spec|__tests__)", path) is not None


def added_deleted(evidence: CodeEvidence) -> tuple[int, int]:
    return sum(item.added for item in evidence.numstat), sum(item.deleted for item in evidence.numstat)


def detect_smells(evidence: CodeEvidence) -> list[str]:
    smells: list[str] = []
    for path, line in evidence.added_lines:
        stripped = line.strip()
        if re.search(r"except\s+Exception\s*:", stripped):
            smells.append(f"`{path}` 新增宽泛异常捕获，需要确认错误不会被吞掉。")
        if "TODO" in stripped or "FIXME" in stripped:
            smells.append(f"`{path}` 新增 TODO/FIXME，提交前要决定是实现、记录为任务还是移除。")
        if re.search(r"\bprint\s*\(", stripped) and not path.startswith("scripts/"):
            smells.append(f"`{path}` 新增 print 调试输出，产品代码提交前要确认必要性。")
        if "/Users/" in stripped or "Desktop/work" in stripped:
            smells.append(f"`{path}` 新增本机绝对路径，可能降低可移植性。")
        if re.search(r"(api[_-]?key|secret|token|password)\s*[:=]", stripped, flags=re.I):
            smells.append(f"`{path}` 新增疑似凭证字段，提交前必须核验没有真实密钥。")
        if stripped in {"pass", "..."}:
            smells.append(f"`{path}` 新增占位实现，不能作为完成状态。")
    return list(dict.fromkeys(smells))[:10]


def build_review(evidence: CodeEvidence) -> QualityReview:
    project = evidence.project
    paths = changed_paths(evidence)
    categories = count_by_category(evidence.status_items, project)
    code_paths = [path for path in paths if is_code_path(path, project)]
    test_paths = [path for path in paths if is_test_path(path, project)]
    added, deleted = added_deleted(evidence)
    risks: list[str] = []
    checks: list[str] = []
    gates: list[str] = []
    batches: list[str] = []
    questions: list[str] = []

    if code_paths and not test_paths:
        risks.append("代码有变化但没有测试文件变化，至少需要说明已运行的验证或补一个代表性回归。")
        checks.append("按变更范围运行最小测试；没有测试时，写明手工验证步骤和剩余风险。")
    if len(paths) >= 30 or added + deleted >= 2000:
        risks.append(f"变更范围较大：{len(paths)} 个路径，{added}+/{deleted}- 行，review 容易漏掉行为回归。")
        gates.append("提交前按主题拆批，避免代码、运行产物和文档混在一个 commit。")
    elif len(paths) >= 12 or added + deleted >= 600:
        risks.append(f"变更范围中等：{len(paths)} 个路径，{added}+/{deleted}- 行，需要明确提交边界。")
    if categories.get("配置"):
        risks.append("配置或环境相关文件有变化，需要确认没有真实凭证、生产地址或本地私有路径。")
        gates.append("配置变更必须单独 review，并确认 `.env`、token、内网地址没有进入提交。")
    if categories.get("运行记录") or categories.get("运行产物"):
        risks.append("运行记录或 artifact 与代码同处工作区，不能把运行状态当作代码质量证据。")
        batches.append("运行记录/样例证据单独提交或归档，代码批次只保留必要源码和测试。")
    if project.key == "my-mind" and categories.get("技能代码"):
        checks.append("对新增或修改的 skill 运行 quick_validate，并对 Python 脚本运行 py_compile。")
        gates.append("skill 变更必须包含触发边界、输出路径和不会自动提交/推送的说明。")
        batches.append("skill 代码与 README/design 文档放同一功能批；运行记录另起批次。")
    if project.key == "edu-agent" and (categories.get("后端代码") or categories.get("前端代码")):
        checks.append("后端变更至少运行相关单测或接口 smoke；前端变更至少运行 lint/build 或目标页面 smoke。")
        gates.append("接口契约、存储路径、模型/OCR 调用和前端展示必须有一条可复核验证证据。")
    if categories.get("输入流转"):
        batches.append("收件箱和流转区状态作为数据状态批处理，不和代码质量修复混在一起。")

    smells = detect_smells(evidence)
    if smells:
        risks.append("新增 diff 出现 AI 代码味道或提交 hygiene 风险，需要人工复核。")
    if not paths:
        checks.append("工作区干净；如需审视最近提交，请结合 `git show` 或指定 commit 范围。")
    if evidence.latest_decision_review:
        questions.append(f"代码变更是否回应了最新决策审视，而不是绕开它：`{repo_rel(evidence.latest_decision_review)}`")

    if not batches:
        batches.append("当前无需强制拆批；如果准备提交，仍先确认代码、文档、运行状态边界。")
    if not gates:
        gates.append("提交前至少保留一条验证证据，不能只写“看起来正常”。")
    if not questions:
        questions.append("是否需要 Codex 进入正式 code review 模式，对具体 diff 给出文件行级发现？")

    if any("凭证" in risk or "配置" in risk or "占位" in risk for risk in risks + smells) or len(paths) >= 30:
        level = "红色"
        conclusion = "先停下来做质量门禁和拆批，再继续新增功能。"
    elif risks or smells:
        level = "黄色"
        conclusion = "可以继续，但必须补验证、收敛提交边界，并人工复核风险点。"
    else:
        level = "绿色"
        conclusion = "未发现明显代码质量风险；仍需按实际变更运行目标验证。"

    return QualityReview(
        level=level,
        conclusion=conclusion,
        risks=list(dict.fromkeys(risks)) or ["未发现明显高风险项。"],
        smells=smells or ["未在新增 diff 中发现明显 AI 代码味道。"],
        required_checks=list(dict.fromkeys(checks)) or ["运行与本次变更最接近的测试、lint、typecheck 或 smoke。"],
        commit_gates=list(dict.fromkeys(gates)),
        batches=list(dict.fromkeys(batches)),
        open_questions=list(dict.fromkeys(questions)),
    )


def add_bullets(lines: list[str], values: list[str]) -> None:
    lines.extend(f"- {value}" for value in values)


def render_project(evidence: CodeEvidence) -> str:
    review = build_review(evidence)
    added, deleted = added_deleted(evidence)
    categories = count_by_category(evidence.status_items, evidence.project)
    lines = [
        "---",
        "类别: 运行记录",
        "记录类型: 代码质量审视",
        f"项目: {evidence.project.name}",
        f"项目键: {evidence.project.key}",
        f"生成时间: {now_text()}",
        "审视模型建议: gpt-5.4 / xhigh",
        f"质量等级: {review.level}",
        f"建议动作: {review.required_checks[0] if review.required_checks else '继续观察'}",
        "---",
        "",
        f"# {evidence.project.name} 代码质量审视",
        "",
        "## 总览",
        "",
        f"- 质量等级：{review.level}",
        f"- 结论：{review.conclusion}",
        f"- 近期 commit：{len(evidence.commits)}",
        f"- 工作区路径：{len(changed_paths(evidence))}",
        f"- Diff 行数：{added}+ / {deleted}-",
        "- 自动修复：否",
        "- 自动提交：否",
        "",
        "## 质量结论",
        "",
        f"- {review.conclusion}",
        "",
        "## 主要风险",
        "",
    ]
    add_bullets(lines, review.risks)
    lines.extend(["", "## AI 代码味道", ""])
    add_bullets(lines, review.smells)
    lines.extend(["", "## 必要验证", ""])
    add_bullets(lines, review.required_checks)
    lines.extend(["", "## 提交前门禁", ""])
    add_bullets(lines, review.commit_gates)
    lines.extend(["", "## 建议分批", ""])
    add_bullets(lines, review.batches)
    lines.extend(["", "## 需要确认", ""])
    add_bullets(lines, review.open_questions)
    lines.extend(["", "## 变更分类", ""])
    if categories:
        for category, count in sorted(categories.items(), key=lambda item: (-item[1], item[0])):
            lines.append(f"- {category}：{count} 项")
    else:
        lines.append("- 工作区干净。")
    lines.extend(["", "## Diff 统计", ""])
    if evidence.numstat:
        for stat in sorted(evidence.numstat, key=lambda item: (-(item.added + item.deleted), item.path))[:20]:
            lines.append(f"- `{stat.path}`：{stat.added}+ / {stat.deleted}-")
        if len(evidence.numstat) > 20:
            lines.append(f"- 另有 {len(evidence.numstat) - 20} 项未展开。")
    else:
        lines.append("- 当前没有可统计的 diff。")
    lines.extend(["", "## Commit 证据", ""])
    if evidence.commits:
        for commit in evidence.commits[:10]:
            lines.append(f"- `{commit.sha}` {commit.date}：{commit.subject}")
    else:
        lines.append("- 时间窗口内没有 commit。")
    lines.extend(["", "## 证据来源", ""])
    lines.append(f"- 代码仓库：`{evidence.project.repo_dir.as_posix()}`")
    if evidence.latest_decision_review:
        lines.append(f"- 决策审视：`{repo_rel(evidence.latest_decision_review)}`")
    return "\n".join(lines).rstrip() + "\n"


def write_report(project: ProjectConfig, report: str) -> Path:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    path = RUN_DIR / f"代码质量审视-{project.key}-{now_filename()}.md"
    if path.exists():
        for index in range(2, 1000):
            candidate = RUN_DIR / f"代码质量审视-{project.key}-{now_filename()}-{index}.md"
            if not candidate.exists():
                path = candidate
                break
    path.write_text(report, encoding="utf-8")
    (DASHBOARD_DIR / f"代码质量审视-{project.key}.md").write_text(report, encoding="utf-8")
    return path


def parse_frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---\n"):
        return {}
    data: dict[str, str] = {}
    for line in text.splitlines()[1:]:
        if line.strip() == "---":
            break
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip().strip('"')
    return data


def summary_from_reports(paths: list[Path]) -> str:
    lines = ["# 代码质量审视", "", f"- 更新时间：{now_text()}", ""]
    for path in paths:
        meta = parse_frontmatter(read_text(path))
        lines.append(f"## {meta.get('项目', path.stem)}")
        lines.append("")
        lines.append(f"- 质量等级：{meta.get('质量等级', '未知')}")
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
    parser = argparse.ArgumentParser(description="Review recent code quality risk.")
    parser.add_argument("--project", default="my-mind", help="Project key: my-mind, edu-agent, or all.")
    parser.add_argument("--since-hours", type=int, default=24)
    parser.add_argument("--commit-limit", type=int, default=20)
    parser.add_argument("--max-added-lines", type=int, default=500)
    parser.add_argument("--format", choices=["markdown", "json"], default="markdown")
    parser.add_argument("--write", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    reports: list[tuple[ProjectConfig, CodeEvidence, str, Path | None]] = []
    for project in resolve_projects(args.project):
        evidence = gather(project, args)
        report = render_project(evidence)
        path = write_report(project, report) if args.write else None
        reports.append((project, evidence, report, path))

    if args.format == "json":
        payload: dict[str, Any] = {"generated_at": now_text(), "projects": []}
        for project, evidence, report, path in reports:
            meta = parse_frontmatter(report)
            added, deleted = added_deleted(evidence)
            payload["projects"].append(
                {
                    "project": project.name,
                    "project_key": project.key,
                    "quality_level": meta.get("质量等级", ""),
                    "suggested_action": meta.get("建议动作", ""),
                    "paths": len(changed_paths(evidence)),
                    "added": added,
                    "deleted": deleted,
                    "report_path": repo_rel(path) if path else "",
                }
            )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for _, _, report, path in reports:
            print(report, end="")
            if path:
                print(f"\n已写入代码质量审视报告：{repo_rel(path)}\n")

    if args.write:
        written = [path for _, _, _, path in reports if path]
        if written:
            (DASHBOARD_DIR / "代码质量审视.md").write_text(summary_from_reports(written), encoding="utf-8")
            print(f"已刷新固定代码质量审视：{repo_rel(DASHBOARD_DIR / '代码质量审视.md')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
