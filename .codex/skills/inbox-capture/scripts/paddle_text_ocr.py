#!/usr/bin/env python3
"""Run local PaddleOCR text detection/recognition and emit JSON records."""

from __future__ import annotations

import contextlib
import json
import os
import sys
from pathlib import Path
from typing import Any


def extract_prediction_text(result: Any, min_score: float) -> tuple[str, list[str], list[float]]:
    item = result[0] if isinstance(result, list) and result else result
    if not isinstance(item, dict):
        return "", [], []
    texts = item.get("rec_texts") if isinstance(item.get("rec_texts"), list) else []
    scores = item.get("rec_scores") if isinstance(item.get("rec_scores"), list) else []
    lines: list[str] = []
    kept_scores: list[float] = []
    for index, raw_text in enumerate(texts):
        text = str(raw_text or "").strip()
        if not text:
            continue
        try:
            score = float(scores[index]) if index < len(scores) else 1.0
        except (TypeError, ValueError):
            score = 1.0
        if score < min_score:
            continue
        lines.append(text)
        kept_scores.append(score)
    return "\n".join(lines), lines, kept_scores


def main() -> int:
    image_paths = [Path(arg) for arg in sys.argv[1:]]
    if not image_paths:
        json.dump([], sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0

    missing = [str(path) for path in image_paths if not path.exists()]
    if missing:
        raise FileNotFoundError("图片不存在：" + ", ".join(missing))

    os.environ.setdefault("PADDLE_PDX_MODEL_SOURCE", "bos")
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")

    with contextlib.redirect_stdout(sys.stderr):
        from paddleocr import PaddleOCR

        predictor = PaddleOCR(
            text_detection_model_name=os.environ.get("MY_MIND_PADDLE_TEXT_DET_MODEL", "PP-OCRv5_server_det"),
            text_recognition_model_name=os.environ.get("MY_MIND_PADDLE_TEXT_REC_MODEL", "PP-OCRv5_server_rec"),
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
        )

    min_score = float(os.environ.get("MY_MIND_PADDLE_TEXT_MIN_SCORE", "0.45"))
    records: list[dict[str, Any]] = []
    for image_path in image_paths:
        record: dict[str, Any] = {
            "path": str(image_path),
            "backend": "PaddleOCR",
            "model": "PP-OCRv5_server_det+PP-OCRv5_server_rec",
            "text": "",
            "lines": [],
            "scores": [],
        }
        try:
            with contextlib.redirect_stdout(sys.stderr):
                result = predictor.predict(str(image_path))
            text, lines, scores = extract_prediction_text(result, min_score)
            record["text"] = text
            record["lines"] = lines
            record["scores"] = scores
        except Exception as exc:  # noqa: BLE001
            record["error"] = f"OCR 失败：{exc!r}"
        records.append(record)

    json.dump(records, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
