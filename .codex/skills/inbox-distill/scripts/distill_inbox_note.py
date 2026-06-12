#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")
PROJECT_NAME = "个人数据资产系统"


@dataclass
class InboxNote:
    path: Path
    frontmatter: dict[str, Any]
    body: str
    raw_text: str

    @property
    def title(self) -> str:
        return str(self.frontmatter.get("标题") or self.path.stem)

    @property
    def platform(self) -> str:
        return str(self.frontmatter.get("来源平台") or "未知")

    @property
    def author(self) -> str:
        return str(self.frontmatter.get("作者或频道") or "未知")


def clean_scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        value = value[1:-1]
    return value.strip()


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        return {}, text
    lines = text.splitlines()
    end_index = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            end_index = index
            break
    if end_index is None:
        return {}, text

    data: dict[str, Any] = {}
    current_key: str | None = None
    for raw_line in lines[1:end_index]:
        line = raw_line.rstrip()
        if not line.strip():
            continue
        if line.startswith("  - ") and current_key:
            data.setdefault(current_key, [])
            if isinstance(data[current_key], list):
                data[current_key].append(clean_scalar(line[4:]))
            continue
        if ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        key = key.strip()
        value = clean_scalar(raw_value)
        current_key = key
        data[key] = value if value else []
    body = "\n".join(lines[end_index + 1 :]).strip()
    return data, body


def load_note(path: Path) -> InboxNote:
    text = path.read_text(encoding="utf-8")
    frontmatter, body = parse_frontmatter(text)
    return InboxNote(path=path, frontmatter=frontmatter, body=body, raw_text=text)


def sanitize_filename_part(text: str, fallback: str = "未命名") -> str:
    text = re.sub(r"[\\/:*?\"<>|\r\n\t]+", " ", text or "").strip()
    text = re.sub(r"\s+", " ", text).strip(" .")
    return (text or fallback)[:80].rstrip()


def yaml_scalar(value: Any) -> str:
    if value is None or value == "":
        return ""
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    if any(ch in text for ch in [":", "#", "[", "]", "{", "}", ",", "\"", "'"]) or text.startswith(("-", "@", "`")):
        import json

        return json.dumps(text, ensure_ascii=False)
    return text


def extract_section(body: str, heading: str) -> str:
    pattern = re.compile(rf"^##+\s+{re.escape(heading)}\s*$", re.MULTILINE)
    match = pattern.search(body)
    if not match:
        return ""
    start = match.end()
    next_match = re.search(r"^##\s+", body[start:], flags=re.MULTILINE)
    end = start + next_match.start() if next_match else len(body)
    return body[start:end].strip()


def strip_bullets(text: str) -> list[str]:
    items: list[str] = []
    for line in text.splitlines():
        clean = line.strip()
        if clean.startswith(("-", "*")):
            clean = clean[1:].strip()
        if clean in {"↓", "->", "→"}:
            continue
        if len(clean) < 6:
            continue
        if clean and clean not in items:
            items.append(clean)
    return items


def compact_text(text: str, limit: int = 900) -> str:
    text = re.sub(r"\n{3,}", "\n\n", text.strip())
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n\n（摘录较长，候选提示词仅保留前段用于人工审阅。）"


def infer_prompt_purpose(note: InboxNote, source_text: str) -> str:
    lowered = source_text.lower()
    if "feature_coder" in source_text or "feature_reviewer" in source_text or "阶段门控" in source_text:
        return "为 Codex 任务选择合适的工程协作流程：小改动走 coder-reviewer 对抗闭环，大改动走方案、实现、验收的阶段门控。"
    if "skill" in lowered or "提示词" in source_text:
        return "把来源中的 Codex 使用经验整理成可复用提示词，帮助后续项目复用。"
    if "工作流" in source_text or "自动化" in source_text:
        return "把来源中的工作流整理成可执行协作流程，减少重复沟通。"
    return f"基于《{note.title}》整理一条可复用人工智能协作提示词。"


def infer_scenarios(source_text: str) -> list[str]:
    scenarios: list[str] = []
    if "小 Feature" in source_text or "feature_coder" in source_text:
        scenarios.append("范围明确、改动较小、风险中低的功能开发。")
    if "大 Feature" in source_text or "阶段" in source_text:
        scenarios.append("跨模块、跨仓库或上线风险较高的功能开发。")
    if "review" in source_text.lower() or "验收" in source_text:
        scenarios.append("需要把实现、审查和验收拆开的 Codex 协作。")
    if "默认只读" in source_text or "不提交" in source_text:
        scenarios.append("需要限制 agent 权限、避免破坏性操作的项目。")
    return scenarios or ["需要把一段外部经验转成可复用提示词时。", "需要让 Codex 先分析流程再执行任务时。"]


def build_prompt_body(note: InboxNote, source_text: str) -> str:
    if "feature_coder" in source_text or "feature_reviewer" in source_text:
        return """请先判断当前任务属于“小 Feature”还是“大 Feature”，并按对应流程推进。

判断标准：

- 小 Feature：范围明确、改动较小、风险中低。
- 大 Feature：跨模块、跨仓库、上线风险较高，或需求需要先收敛。

小 Feature 流程：

1. Human 给出需求、边界和验收标准。
2. `feature_coder` 只做满足需求的最小改动。
3. `feature_reviewer` 对实现做对抗审查，重点找 blocker、遗漏场景和测试缺口。
4. `feature_coder` 修复 blocker。
5. `feature_reviewer` 复审。
6. Human 做最后 review。

大 Feature 流程：

1. 阶段一：方案确认，Human 主导。Codex 负责查证、提方案、列风险和验收标准。
2. 阶段二：Squad 实现，agent 主导。Codex 拆分任务、并行取证、实现和自测。
3. 阶段三：验收，Human 主导。Codex 提供验证证据、风险清单和剩余问题。

角色和权限：

- 主 Agent 负责理解需求、控制范围和最终整合。
- Subagents 负责独立取证、审查、复现和查官方文档。
- 默认只读；只有实现、UI 复现、验收取证等明确需要写入的角色允许 workspace-write。
- 不提交、不推送、不做破坏性操作，除非 Human 明确要求。
- 所有结论必须基于真实文件、日志、请求链或官方来源。
- 控制并发和递归，避免 token 与时间失控。

输出：

- 任务类型判断。
- 采用的流程。
- 角色分配。
- 风险和权限边界。
- 验收方式。
- 需要 Human 最终确认的点。"""
    return f"""请把下面来源材料整理成可执行的 Codex 协作流程或提示词候选。

来源主题：{note.title}

要求：

1. 先提炼可复用场景。
2. 再给出一段可以直接复制使用的提示词。
3. 区分来源事实、你的整理和需要验证的判断。
4. 不要把来源内容当作已经确认的长期知识。
5. 输出可执行步骤、风险和人工确认点。

来源摘录：

{compact_text(source_text, 1400)}"""


def build_prompt_candidate(note: InboxNote) -> tuple[str, str]:
    copy = extract_section(note.body, "文案摘录")
    summary = extract_section(note.body, "摘要")
    key_points = extract_section(note.body, "关键点")
    reading_notes = extract_section(note.body, "阅读思考")
    source_text = "\n\n".join(part for part in [copy, summary, key_points, reading_notes] if part).strip() or note.body[:1800]
    title = sanitize_filename_part(note.title)
    purpose = infer_prompt_purpose(note, source_text)
    scenarios = infer_scenarios(source_text)
    prompt_body = build_prompt_body(note, source_text)
    today = dt.datetime.now(TZ).strftime("%Y-%m-%d")
    source_rel = note.path.as_posix()
    source_url = note.frontmatter.get("来源链接") or note.frontmatter.get("原始链接") or ""
    tags = note.frontmatter.get("标签")
    tag_lines = []
    if isinstance(tags, list):
        tag_lines = [f"- {tag}" for tag in tags]

    content = [
        f"# {title}",
        "",
            "## 用途",
            "",
            purpose,
            "",
            "## 用户阅读思考",
            "",
            reading_notes or "待补充。用户阅读并与 Codex 交流后，可把思考回写到来源笔记的“阅读思考”章节。",
            "",
            "## 来源",
            "",
            f"- 来源资料：`{source_rel}`",
            f"- 来源平台：{note.platform}",
        f"- 作者：{note.author}",
        f"- 来源链接：{source_url or '未知'}",
        f"- 整理时间：{today}",
        "- 状态：候选提示词，基于来源文案和摘录整理，不是来源逐字稿",
        "",
        "## 适用场景",
        "",
        *[f"- {item}" for item in scenarios],
        "",
        "## 提示词",
        "",
        prompt_body,
        "",
        "## 来源要点",
        "",
    ]
    points = strip_bullets(key_points)[:8] or strip_bullets(copy)[:8]
    if points:
        content.extend(f"- {point}" for point in points)
    else:
        content.append("- 待人工补充。")
    content.extend(
        [
            "",
            "## 标签",
            "",
            *(tag_lines or ["- 待补充"]),
            "",
            "## 使用建议",
            "",
            "- 先用于分析和流程选择，再决定是否进入正式 skill。",
            "- 如果连续两次以上被复用，再考虑沉淀为 `.codex/skills/`。",
            "- 使用时保留人工确认点，不让 Codex 自动提交、推送或确认长期知识。",
            "",
            "## 注意事项",
            "",
            "- 这是候选提示词，不是已验证方法论。",
            "- 社媒来源可能省略上下文，正式采用前应结合项目实际验证。",
            "- 如果涉及权限、提交、推送或生产环境操作，必须单独确认。",
        ]
    )
    return title, "\n".join(content).rstrip() + "\n"


def set_scalar_field(lines: list[str], key: str, value: str) -> list[str]:
    prefix = f"{key}:"
    for index, line in enumerate(lines):
        if line.startswith(prefix):
            lines[index] = f"{key}: {value}"
            return lines
    insert_at = 1
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            insert_at = index
            break
    lines.insert(insert_at, f"{key}: {value}")
    return lines


def set_list_field(lines: list[str], key: str, values: list[str]) -> list[str]:
    prefix = f"{key}:"
    start = None
    for index, line in enumerate(lines):
        if line.startswith(prefix):
            start = index
            break
    rendered = [f"{key}:"] + [f"  - {yaml_scalar(value)}" for value in values]
    if start is None:
        insert_at = 1
        for index, line in enumerate(lines[1:], start=1):
            if line.strip() == "---":
                insert_at = index
                break
        return lines[:insert_at] + rendered + lines[insert_at:]

    end = start + 1
    while end < len(lines) and lines[end].startswith("  - "):
        end += 1
    return lines[:start] + rendered + lines[end:]


def existing_list(frontmatter: dict[str, Any], key: str) -> list[str]:
    value = frontmatter.get(key)
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip() not in {"", "[]"}]
    if value:
        if str(value).strip() == "[]":
            return []
        return [str(value)]
    return []


def merge_unique(values: list[str], additions: list[str]) -> list[str]:
    result = list(values)
    for item in additions:
        if item and item not in result:
            result.append(item)
    return result


def relative_link(from_path: Path, to_path: Path) -> str:
    rel = Path("..") / to_path
    return rel.as_posix().replace(" ", "%20")


def build_updated_source(note: InboxNote, target_path: Path, target_title: str) -> str:
    lines = note.raw_text.splitlines()
    if not lines or lines[0] != "---":
        raise ValueError("来源笔记缺少 frontmatter，暂不自动回写。")
    lines = set_scalar_field(lines, "处理状态", "已处理")
    projects = merge_unique(existing_list(note.frontmatter, "关联项目"), [PROJECT_NAME])
    topics = merge_unique(existing_list(note.frontmatter, "主题"), ["Codex", "提示词", "工作流"])
    lines = set_list_field(lines, "关联项目", projects)
    lines = set_list_field(lines, "主题", topics)
    updated = "\n".join(lines).rstrip() + "\n"

    today = dt.datetime.now(TZ).strftime("%Y-%m-%d")
    link = relative_link(note.path, target_path)
    record = f"- {today}：已根据用户确认继续沉淀，整理为候选提示词：[{target_title}]({link})。状态：候选，尚未确认长期知识或项目决策。"
    if record in updated:
        return updated
    if "## 沉淀记录" in updated:
        updated = re.sub(r"(## 沉淀记录\n\n)", rf"\1{record}\n", updated, count=1)
    elif "## 原始链接" in updated:
        updated = updated.replace("## 原始链接", f"## 沉淀记录\n\n{record}\n\n## 原始链接", 1)
    else:
        updated = updated.rstrip() + f"\n\n## 沉淀记录\n\n{record}\n"
    return updated.rstrip() + "\n"


def write_if_allowed(path: Path, content: str, force: bool) -> None:
    if path.exists() and not force:
        raise FileExistsError(f"目标文件已存在：{path}。如需覆盖，请加 --force。")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Distill one my-mind inbox note into a candidate artifact.")
    parser.add_argument("--source", required=True, help="Source inbox note path.")
    parser.add_argument("--target", choices=["prompt"], default="prompt", help="Distillation target type. First version only supports prompt.")
    parser.add_argument("--output-dir", default="75_提示词库", help="Output directory for prompt candidates.")
    parser.add_argument("--write", action="store_true", help="Write files and update source. Default is dry-run preview.")
    parser.add_argument("--force", action="store_true", help="Allow overwriting existing target file.")
    args = parser.parse_args()

    source_path = Path(args.source)
    note = load_note(source_path)
    title, candidate = build_prompt_candidate(note)
    target_path = Path(args.output_dir) / f"{title}.md"
    updated_source = build_updated_source(note, target_path, title)

    print(f"来源：{source_path}")
    print(f"目标：{target_path}")
    print(f"模式：{'写入' if args.write else 'dry-run'}")
    print("")
    print("## 候选提示词预览")
    print("")
    print(candidate)
    print("## 来源回写预览")
    print("")
    for line in updated_source.splitlines()[:80]:
        print(line)
    if len(updated_source.splitlines()) > 80:
        print("...")

    if not args.write:
        print("\n未写入。加 --write 后生成候选提示词并回写来源。")
        return 0

    write_if_allowed(target_path, candidate, args.force)
    source_path.write_text(updated_source, encoding="utf-8")
    print(f"\n已写入候选提示词：{target_path}")
    print(f"已回写来源状态：{source_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"错误：{exc}", file=sys.stderr)
        raise SystemExit(1)
