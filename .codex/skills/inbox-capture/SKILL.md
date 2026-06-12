---
name: inbox-capture
description: Capture links or raw snippets into the my-mind inbox as Chinese Markdown notes. Use when the user says 收件箱录入, 入箱, 丢到收件箱, capture this link, or provides YouTube/抖音/小红书/X/video/article links that should be saved before later整理/萃取. First version collects link/video metadata, OCRs public Xiaohongshu image notes, defaults to transcribing public short videos with exposed media/audio URLs, and writes to 00_收件箱 without promoting knowledge.
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
- For public 小红书 share links, resolve the short link and parse the public note page/initial state for title, note copy, author, publish date, cover, note ID, image count, image URLs, and interaction counts when available.
- For public 小红书 image notes, run local PP-OCRv5 text OCR by default on the first exposed public images, using BOS as the model source and Apple Vision only as fallback. Use `--no-image-ocr` only when the frontdesk path must avoid OCR entirely. Use `--image-ocr-backend paddleocr-vl` only for后台文档解析实验, because PaddleOCR-VL is a heavy document-parsing VLM.
- For public short video links whose media/audio URL is exposed by the platform page or `yt-dlp`, extract audio with `ffmpeg` by default, transcribe with an available backend, normalize transcription output to Simplified Chinese, apply a small glossary for common AI/product terms such as Codex, OpenAI, Skill, Prompt, and PPT, then write a content summary, key points, full timeline excerpt, and full transcription excerpt. Use `--no-extract-content` only when the frontdesk path must skip video transcription.
- Write a Chinese Markdown inbox note.
- Preserve the original URL/content.
- Mark a downstream content gate: `内容质量: 可推送 / 需核验 / 需继续解析` plus `质量门禁`. Frontdesk push should skip `需继续解析` by default.

Out of scope for first version:

- Full automatic transcription for long videos beyond the configured duration limit.
- Copying long third-party transcripts during normal capture.
- Copying subtitle text or lyrics into the repository by default.
- Promoting content into `20_资料库/`, `30_原子笔记/`, or `65_洞察/`.
- Logging into platforms or using cookies.
- Bypassing platform access controls, crawling comments, or downloading media files.

## Workflow

1. If the user provides links, run:

```bash
python3 .codex/skills/inbox-capture/scripts/capture_link.py "<URL>"
```

If OpenClaw/frontdesk needs an emergency fast path without OCR:

```bash
python3 .codex/skills/inbox-capture/scripts/capture_link.py --no-image-ocr "<URL>"
```

For multiple links, pass them all:

```bash
python3 .codex/skills/inbox-capture/scripts/capture_link.py "<URL1>" "<URL2>"
```

2. Public short videos are transcribed by default when the parser exposes a media/audio URL. If OpenClaw/frontdesk needs an emergency fast path without video transcription, run:

```bash
python3 .codex/skills/inbox-capture/scripts/capture_link.py --no-extract-content "<视频链接>"
```

If the user explicitly asks to force a content extraction attempt even when auto mode cannot identify a media URL first, run:

```bash
python3 .codex/skills/inbox-capture/scripts/capture_link.py --extract-content --transcribe-backend faster-whisper --transcribe-model small --max-transcribe-seconds 360 "<视频链接>"
```

For OpenClaw/frontdesk video intake, distinguish two states clearly:

- Basic capture only: metadata, copy, cover, and links were captured, but `内容摘录来源` is empty because media/audio URL was unavailable, video exceeded the duration limit, backend failed, or `--no-extract-content` was used.
- Content extraction: the default auto path or forced `--extract-content` generated `视频内容摘录`, `摘要`, `关键点`, `时间线摘录`, and `转写摘录`.

If a video note has empty `内容摘录来源`, tell the user it is “已入箱基础信息，视频内容尚未转写，需后台继续解析”.

3. Read the script output and report created file paths.
4. If parsing fails, keep the generated note. A failed parse is still useful because it preserves the link, timestamp, platform guess, and failure reason.
5. Do not move content out of `00_收件箱/` unless the user explicitly asks for整理/萃取.

## Output Rules

- File path format: `00_收件箱/YYYY-MM-DD 平台 - 标题.md`.
- Use Chinese visible fields.
- Set `处理状态: 待分拣`.
- Set `解析状态` to one of `已解析`, `部分解析`, `解析失败`.
- Set `内容质量` to one of `可推送`, `需核验`, `需继续解析`.
- Set `质量门禁` to a short reason that downstream triage and frontdesk push can reuse.
- Set `敏感状态: 未知` unless the user-provided text clearly contains credentials, private personal data, or secrets.

## Notes

- `yt-dlp` works best for YouTube and many public video pages. 抖音、小红书 may fail through `yt-dlp`, so this skill uses public HTML fallback parsing for basic note metadata before recording a failure.
- When an official transcript source is discovered, keep the source URL and structured metadata, then let later整理/萃取 produce summaries and key points instead of copying the full transcript.
- When subtitles are parsed, keep only metadata and counts in the inbox note. Later整理/萃取 should produce original summaries rather than storing verbatim subtitle text.
- 抖音/小红书公开页字段会随平台页面结构变化而波动；若公开页不暴露字段，保留链接和失败说明，不使用登录态或 cookie。
- 小红书图文 OCR 默认使用普通 `PaddleOCR` 的 `PP-OCRv5_server_det + PP-OCRv5_server_rec`，模型源固定为 BOS，避免 HuggingFace/Xet 下载卡住。Apple Vision 速度快但样例错字较多，只作为兜底。PaddleOCR-VL-1.6 位于 `/Users/linsuchang/Desktop/work/models/PaddleOCR-VL-1.6`，但它是 0.9B 文档解析 VLM，当前本机 `paddleocr` 版本只支持 v1/v1.5 管线，跑 1.6 需要兼容路径，不能放在 OpenClaw 前台默认链路里。后台需要实验文档级解析时，显式使用 `--image-ocr-backend paddleocr-vl`；完整图集可配合 `--max-ocr-images 12`。
- Content extraction needs `ffmpeg` plus a transcription backend. The script auto-detects `whisper` CLI, installed `faster-whisper`, or `~/.cache/my-mind/faster-whisper-venv/bin/python`; default auto mode uses the `small` model and skips videos longer than 360 seconds unless `--max-transcribe-seconds` is raised.
- `tiny` is useful for quick validation but has many Chinese recognition errors. Use `small` or `medium` when the extracted content needs to be promoted into long-term knowledge.
- If ASR phonetically misreads product terms (for example Codex as Chinese-sounding words), add conservative corrections to the transcript glossary rather than manually fixing each future note.
- Video transcription intentionally keeps full paragraph excerpts in the inbox. Use it for content that should be inspected before later整理/萃取; use `--no-extract-content` only for emergency speed or backend troubleshooting.
- If no URL is provided and the user gives plain text, create a regular inbox note manually using the same metadata shape.
