#!/usr/bin/env python3
"""Run local PaddleOCR-VL OCR and emit JSON records.

This helper is intentionally subprocess-friendly: noisy model logs go to stderr,
and stdout is reserved for machine-readable JSON.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any


DEFAULT_MODEL_DIRS = [
    Path(os.environ.get("MY_MIND_PADDLEOCR_VL_MODEL_DIR", "")),
    Path("/Users/linsuchang/Desktop/work/models/PaddleOCR-VL-1.6"),
    Path("/Users/linsuchang/Desktop/work/models/paddleocr-vl"),
]


def existing_model_dir() -> Path:
    for path in DEFAULT_MODEL_DIRS:
        if path and path.exists() and (path / "model.safetensors").exists():
            return path
    raise FileNotFoundError("未找到可用 PaddleOCR-VL 模型目录")


def compat_model_dir(source: Path, target_name: str, temp_root: Path) -> Path:
    target = temp_root / f"{source.name}-{target_name}"
    target.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        if item.name == "inference.yml":
            continue
        link = target / item.name
        if not link.exists():
            link.symlink_to(item)
    (target / "inference.yml").write_text(f"Global:\n  model_name: {target_name}\n", encoding="utf-8")
    return target


def extract_result_text(result: Any) -> str:
    markdown = getattr(result, "markdown", None)
    if isinstance(markdown, dict):
        text = str(markdown.get("markdown_texts") or "").strip()
        if text:
            return text

    json_result = getattr(result, "json", None)
    if isinstance(json_result, dict):
        res = json_result.get("res") if isinstance(json_result.get("res"), dict) else json_result
        blocks = res.get("parsing_res_list") if isinstance(res, dict) else None
        if isinstance(blocks, list):
            parts = []
            for block in blocks:
                if isinstance(block, dict) and block.get("block_content"):
                    parts.append(str(block["block_content"]).strip())
            text = "\n".join(part for part in parts if part).strip()
            if text:
                return text
    return str(result).strip()


def build_pipeline(model_dir: Path, temp_root: Path) -> tuple[Any, dict[str, str]]:
    with contextlib.redirect_stdout(sys.stderr):
        from paddleocr import PaddleOCRVL

    attempts = [
        {
            "pipeline_version": "v1.6",
            "model_name": "PaddleOCR-VL-1.6-0.9B",
            "model_dir": model_dir,
            "compatibility": "native-v1.6",
        },
        {
            "pipeline_version": "v1.5",
            "model_name": "PaddleOCR-VL-1.6-0.9B",
            "model_dir": model_dir,
            "compatibility": "native-name-on-v1.5",
        },
        {
            "pipeline_version": "v1.5",
            "model_name": "PaddleOCR-VL-1.5-0.9B",
            "model_dir": compat_model_dir(model_dir, "PaddleOCR-VL-1.5-0.9B", temp_root),
            "compatibility": "v1.6-weights-via-v1.5-registration",
        },
        {
            "pipeline_version": "v1",
            "model_name": "PaddleOCR-VL-0.9B",
            "model_dir": compat_model_dir(model_dir, "PaddleOCR-VL-0.9B", temp_root),
            "compatibility": "v1.6-weights-via-v1-registration",
        },
    ]

    errors: list[str] = []
    for attempt in attempts:
        try:
            with contextlib.redirect_stdout(sys.stderr):
                pipeline = PaddleOCRVL(
                    pipeline_version=attempt["pipeline_version"],
                    vl_rec_model_name=attempt["model_name"],
                    vl_rec_model_dir=str(attempt["model_dir"]),
                    use_doc_orientation_classify=False,
                    use_doc_unwarping=False,
                    use_layout_detection=False,
                    use_chart_recognition=False,
                    use_seal_recognition=False,
                    use_ocr_for_image_block=True,
                )
            return pipeline, {
                "backend": "PaddleOCR-VL",
                "model": "PaddleOCR-VL-1.6",
                "model_dir": str(model_dir),
                "pipeline_version": attempt["pipeline_version"],
                "registered_model_name": attempt["model_name"],
                "compatibility": attempt["compatibility"],
            }
        except Exception as exc:  # noqa: BLE001
            errors.append(
                f"{attempt['pipeline_version']} / {attempt['model_name']} / {attempt['compatibility']}: {exc!r}"
            )
    raise RuntimeError("PaddleOCR-VL 初始化失败：\n" + "\n".join(errors))


def main() -> int:
    image_paths = [Path(arg) for arg in sys.argv[1:]]
    if not image_paths:
        json.dump([], sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0

    missing = [str(path) for path in image_paths if not path.exists()]
    if missing:
        raise FileNotFoundError("图片不存在：" + ", ".join(missing))

    model_dir = existing_model_dir()
    records: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="my-mind-paddleocr-vl-") as temp_dir_name:
        temp_root = Path(temp_dir_name)
        pipeline, meta = build_pipeline(model_dir, temp_root)
        for image_path in image_paths:
            record: dict[str, Any] = {"path": str(image_path), **meta, "text": "", "lines": []}
            try:
                with contextlib.redirect_stdout(sys.stderr):
                    output = pipeline.predict(str(image_path))
                texts = [extract_result_text(result) for result in output]
                text = "\n".join(part for part in texts if part.strip()).strip()
                record["text"] = text
                record["lines"] = [line.strip() for line in text.splitlines() if line.strip()]
            except Exception as exc:  # noqa: BLE001
                record["error"] = f"OCR 失败：{exc!r}"
            records.append(record)

    json.dump(records, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
