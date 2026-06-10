---
name: inbox-capture
description: Capture links or raw snippets into the my-mind inbox as Chinese Markdown notes. Use when the user says 收件箱录入, 入箱, 丢到收件箱, capture this link, or provides YouTube/抖音/小红书/X/video/article links that should be saved before later整理/萃取. First version collects basic link/video metadata with yt-dlp when available and writes to 00_收件箱 without promoting knowledge.
---

# Inbox Capture

Use this skill to turn user-provided links or rough snippets into standardized `00_收件箱/` Markdown notes.

## Scope

First version:

- Accept one or more links.
- Detect platform from URL and `yt-dlp` extractor metadata when available.
- Collect basic information: title, author/channel, publish date, duration, platform, original URL, description excerpt, tags, categories, thumbnail, subtitle availability, parse status, failure reason.
- For YouTube links, inspect the public watch page for external transcript links. If a Lex Fridman official transcript page is found, extract transcript source metadata, chapter list, segment count, and estimated word count without copying the full transcript.
- When `yt-dlp` exposes YouTube subtitle URLs, fetch and parse the selected subtitle track to collect source, language, format, segment count, and estimated word count. Do not write subtitle text into the note by default.
- Write a Chinese Markdown inbox note.
- Preserve the original URL/content.

Out of scope for first version:

- Downloading video/audio.
- Whisper transcription.
- Copying long copyrighted transcripts into the repository.
- Copying subtitle text or lyrics into the repository by default.
- Promoting content into `20_资料库/`, `30_原子笔记/`, or `65_洞察/`.
- Logging into platforms or using cookies.

## Workflow

1. If the user provides links, run:

```bash
python3 .codex/skills/inbox-capture/scripts/capture_link.py "<URL>"
```

For multiple links, pass them all:

```bash
python3 .codex/skills/inbox-capture/scripts/capture_link.py "<URL1>" "<URL2>"
```

2. Read the script output and report created file paths.
3. If parsing fails, keep the generated note. A failed parse is still useful because it preserves the link, timestamp, platform guess, and failure reason.
4. Do not move content out of `00_收件箱/` unless the user explicitly asks for整理/萃取.

## Output Rules

- File path format: `00_收件箱/YYYY-MM-DD 平台 - 标题.md`.
- Use Chinese visible fields.
- Set `处理状态: 待分拣`.
- Set `解析状态` to one of `已解析`, `部分解析`, `解析失败`.
- Set `敏感状态: 未知` unless the user-provided text clearly contains credentials, private personal data, or secrets.

## Notes

- `yt-dlp` works best for YouTube and many public video pages. 抖音、小红书、X may fail or require cookies/login; first version records the failure instead of forcing a risky workaround.
- When an official transcript source is discovered, keep the source URL and structured metadata, then let later整理/萃取 produce summaries and key points instead of copying the full transcript.
- When subtitles are parsed, keep only metadata and counts in the inbox note. Later整理/萃取 should produce original summaries rather than storing verbatim subtitle text.
- If no URL is provided and the user gives plain text, create a regular inbox note manually using the same metadata shape.
