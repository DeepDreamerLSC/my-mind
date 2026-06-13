---
name: obsidian-linking
description: Generate Obsidian link suggestions for my-mind notes. Use when the user asks for Obsidian 关联建议, 双链建议, 图谱整理, MOC linkage, or wants to connect a note to projects, domains, topics, insights, or Feishu sync views without directly promoting knowledge.
---

# Obsidian 关联建议

## Overview

This skill helps my-mind connect notes through Obsidian-native links. It scans the local Markdown knowledge base, suggests project/domain/topic links for a target note, and writes a reviewable report without modifying the source note by default.

Use this skill when:

- The user asks to generate Obsidian 双链/反链/图谱/关联建议 for one or more notes.
- A new candidate note has entered `20_资料库/`, `65_洞察/`, or `75_提示词库/` and needs project/topic linkage.
- The user wants to review knowledge graph hygiene before syncing or promoting content.
- OpenClaw needs a safe, read-only way to prepare link suggestions.

Do not use it for ordinary inbox capture, triage, Feishu publishing, or final knowledge promotion unless the user explicitly asks for link suggestions as part of that workflow.

## Workflow

1. Identify the target note path.
2. Run the helper script from the repository root:

   ```bash
   python3 .codex/skills/obsidian-linking/scripts/suggest_links.py \
     --note "20_资料库/管理与组织/某条资料.md" \
     --write
   ```

3. Read the generated report in `85_运行记录/关联建议-*.md`.
4. If the user asks to apply suggestions, edit the source note conservatively:
   - Prefer front matter fields `关联项目`, `关联领域`, `主题`.
   - Use Obsidian wikilinks, for example `[[10_项目/个人数据资产系统/项目总览|个人数据资产系统]]`.
   - Keep existing values unless clearly wrong.
   - Do not convert a candidate note into confirmed knowledge merely because links were suggested.

## Script

`scripts/suggest_links.py` performs a local, standard-library-only scan:

- Candidate targets: `10_项目/`, `15_索引/`, `20_资料库/`, `30_原子笔记/`, `35_主动回忆/`, `60_行业情报/`, `65_洞察/`, `75_提示词库/`, `80_复盘/`.
- Excluded process folders: `00_收件箱/`, `05_流转区/`, `85_运行记录/`, `.git/`, `.obsidian/`.
- Output: Markdown report with field normalization suggestions and ranked link candidates.

Useful options:

```bash
python3 .codex/skills/obsidian-linking/scripts/suggest_links.py --note "path/to/note.md"
python3 .codex/skills/obsidian-linking/scripts/suggest_links.py --note "path/to/note.md" --limit 20
python3 .codex/skills/obsidian-linking/scripts/suggest_links.py --note "path/a.md" --note "path/b.md" --write
```

## Output Boundary

The report is a suggestion artifact, not a source of truth. Long-term knowledge still needs the existing my-mind gates:

- Original evidence preserved first.
- Candidate status retained until confirmed.
- User reading feedback or project use determines whether content becomes "化为己有".
