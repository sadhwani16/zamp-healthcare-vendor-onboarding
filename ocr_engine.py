"""Local EasyOCR page reading using the layout reconstruction from Risk Hunter.

The source project renders PDFs at 200 DPI, preserves full resolution for
EasyOCR, groups bounding boxes into rows, and keeps word coordinates for review.
No network OCR service receives document content.
"""

from __future__ import annotations

import os
from functools import lru_cache

import cv2
import fitz
import numpy as np


@lru_cache(maxsize=2)
def reader(gpu: bool | None = None):
    import easyocr

    if gpu is None:
        device = os.getenv("ZAMP_OCR_DEVICE", "auto").lower()
        if device == "cpu":
            gpu = False
        else:
            try:
                import torch
                gpu = bool(torch.cuda.is_available())
            except ImportError:
                gpu = False
    return easyocr.Reader(["en"], gpu=gpu)


def _layout(results: list, width: int, *, min_confidence: float = 0.30,
            page_width: int = 120) -> tuple[str, list[dict]]:
    """Sort OCR boxes into rows and retain page coordinates for audit display."""
    words: list[dict] = []
    for box, text, confidence in results:
        if not str(text).strip() or float(confidence) < min_confidence:
            continue
        points = [(float(x), float(y)) for x, y in box]
        x0, x1 = min(x for x, _ in points), max(x for x, _ in points)
        y0, y1 = min(y for _, y in points), max(y for _, y in points)
        words.append({"text": str(text).strip(), "confidence": float(confidence),
                      "bbox": [round(x0, 1), round(y0, 1), round(x1, 1), round(y1, 1)]})
    if not words:
        return "", []
    words.sort(key=lambda item: (item["bbox"][1], item["bbox"][0]))
    average_height = sum(item["bbox"][3] - item["bbox"][1] for item in words) / len(words)
    threshold = max(8.0, average_height * 0.6)
    rows: list[list[dict]] = []
    for word in words:
        if rows and abs(word["bbox"][1] - rows[-1][0]["bbox"][1]) <= threshold:
            rows[-1].append(word)
        else:
            rows.append([word])
    lines = []
    for row in rows:
        row.sort(key=lambda item: item["bbox"][0])
        line = ""
        for word in row:
            column = int(word["bbox"][0] / max(width, 1) * page_width)
            line += " " * max(1 if line else 0, column - len(line)) + word["text"]
        lines.append(line.rstrip())
    return "\n".join(lines), words


def read_scanned_pages(pdf_bytes: bytes, page_indices: list[int],
                       *, dpi: int = 200) -> dict:
    texts, confidences, sharpnesses, evidence = [], [], [], []
    with fitz.open(stream=pdf_bytes, filetype="pdf") as pdf:
        for index in page_indices:
            page = pdf[index]
            pixmap = page.get_pixmap(dpi=dpi, alpha=False)
            pixels = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
                pixmap.height, pixmap.width, pixmap.n)[:, :, :3]
            bgr = cv2.cvtColor(np.ascontiguousarray(pixels), cv2.COLOR_RGB2BGR)
            sharpnesses.append(float(cv2.Laplacian(
                cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var()))
            results = reader().readtext(bgr, detail=1, paragraph=False,
                                        canvas_size=max(pixmap.width, pixmap.height, 2560),
                                        mag_ratio=1.0)
            text, words = _layout(results, pixmap.width)
            texts.append(text)
            confidences.extend(word["confidence"] for word in words)
            evidence.extend({**word, "page": index + 1} for word in words)
    ordered = sorted(confidences)
    middle = len(ordered) // 2
    median_confidence = ((ordered[middle - 1] + ordered[middle]) / 2 if len(ordered) % 2 == 0
                         else ordered[middle]) if ordered else 0.0
    return {
        "text": "\n".join(text for text in texts if text),
        "sharpness": min(sharpnesses) if sharpnesses else 0.0,
        "ocr_confidence": median_confidence,
        "ocr_engine": "EasyOCR",
        "ocr_regions": evidence,
    }
