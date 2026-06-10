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
- Collect basic information: title, author/channel, author ID, content ID, publish date, duration, platform, original URL, description/copy excerpt, tags, categories, thumbnail, interaction counts, subtitle availability, parse status, failure reason.
- For YouTube links, inspect the public watch page for external transcript links. If a Lex Fridman official transcript page is found, extract transcript source metadata, chapter list, segment count, and estimated word count without copying the full transcript.
- When `yt-dlp` exposes YouTube subtitle URLs, fetch and parse the selected subtitle track to collect source, language, format, segment count, and estimated word count. Do not write subtitle text into the note by default.
- For public 抖音 share links, resolve the short link and parse the mobile public page for title/copy, author, publish date, duration, cover, public media URL, content ID, and interaction counts when available.
- For public 小红书 share links, resolve the short link and parse the public note page/initial state for title, note copy, author, publish date, cover, note ID, image count, and interaction counts when available.
- When explicitly requested with `--extract-content`, extract audio from a public media URL with `ffmpeg`, transcribe with an available backend, normalize transcription output to Simplified Chinese, then write a content summary, key points, full timeline excerpt, and full transcription excerpt.
- Write a Chinese Markdown inbox note.
- Preserve the original URL/content.

Out of scope for first version:

- Automatic video/audio download during normal capture.
- Automatic Whisper transcription during normal capture.
- Copying long transcripts during normal capture.
- Copying subtitle text or lyrics into the repository by default.
- Promoting content into `20_资料库/`, `30_原子笔记/`, or `65_洞察/`.
- Logging into platforms or using cookies.
- Bypassing platform access controls, crawling comments, or downloading media files.

## Workflow

1. If the user provides links, run:

```bash
python3 .codex/skills/inbox-capture/scripts/capture_link.py "<URL>"
```

For multiple links, pass them all:

```bash
python3 .codex/skills/inbox-capture/scripts/capture_link.py "<URL1>" "<URL2>"
```

2. If the user explicitly asks for video content extraction/transcription, run:

```bash
python3 .codex/skills/inbox-capture/scripts/capture_link.py --extract-content --transcribe-backend faster-whisper --transcribe-model tiny "<URL>"
```

3. Read the script output and report created file paths.
4. If parsing fails, keep the generated note. A failed parse is still useful because it preserves the link, timestamp, platform guess, and failure reason.
5. Do not move content out of `00_收件箱/` unless the user explicitly asks for整理/萃取.

## Output Rules

- File path format: `00_收件箱/YYYY-MM-DD 平台 - 标题.md`.
- Use Chinese visible fields.
- Set `处理状态: 待分拣`.
- Set `解析状态` to one of `已解析`, `部分解析`, `解析失败`.
- Set `敏感状态: 未知` unless the user-provided text clearly contains credentials, private personal data, or secrets.

## Notes

- `yt-dlp` works best for YouTube and many public video pages. 抖音、小红书 may fail through `yt-dlp`, so this skill uses public HTML fallback parsing for basic note metadata before recording a failure.
- When an official transcript source is discovered, keep the source URL and structured metadata, then let later整理/萃取 produce summaries and key points instead of copying the full transcript.
- When subtitles are parsed, keep only metadata and counts in the inbox note. Later整理/萃取 should produce original summaries rather than storing verbatim subtitle text.
- 抖音/小红书公开页字段会随平台页面结构变化而波动；若公开页不暴露字段，保留链接和失败说明，不使用登录态或 cookie。
- Content extraction needs `ffmpeg` plus a transcription backend. The script auto-detects `whisper` CLI, installed `faster-whisper`, or `~/.cache/my-mind/faster-whisper-venv/bin/python`.
- `tiny` is useful for quick validation but has many Chinese recognition errors. Use `small` or `medium` when the extracted content needs to be promoted into long-term knowledge.
- `--extract-content` intentionally removes transcript excerpt length limits after the user asks for full paragraphs; use it only for content that should be inspected in the inbox.
- If no URL is provided and the user gives plain text, create a regular inbox note manually using the same metadata shape.
