"""Locate and read the nine EIN cells on the supplied W-9 PDF template."""

from __future__ import annotations

import fitz


def ein_cells(page: fitz.Page) -> list[fitz.Rect]:
    """Find the printed Part I EIN boxes from their label and vector borders."""
    labels = page.search_for("Employer identification number")
    if not labels:
        raise ValueError("W-9 employer identification number label was not found")
    label = max(labels, key=lambda rectangle: rectangle.x0)
    cells = [fitz.Rect(drawing["rect"]) for drawing in page.get_drawings()
             if 10 <= drawing["rect"].width <= 20
             and 18 <= drawing["rect"].height <= 30
             and label.y1 <= drawing["rect"].y0 <= label.y1 + 12
             and drawing["rect"].x0 >= label.x0 - 6]
    cells.sort(key=lambda rectangle: rectangle.x0)
    if len(cells) != 9:
        raise ValueError(f"Expected nine W-9 EIN boxes; found {len(cells)}")
    return cells


def centered_digit_origin(cell: fitz.Rect, digit: str, *, size: float = 10.5) -> tuple[float, float]:
    """Return a Helvetica text origin that centers one digit in a printed box."""
    font = fitz.Font(fontname="helv")
    width = fitz.get_text_length(digit, fontname="helv", fontsize=size)
    x = (cell.x0 + cell.x1 - width) / 2
    baseline = (cell.y0 + cell.y1 + (font.ascender + font.descender) * size) / 2
    return x, baseline


def read_ein_from_cells(page: fitz.Page) -> tuple[str | None, str]:
    """Read one selectable digit from each Part I box, never from page order."""
    try:
        cells = ein_cells(page)
    except ValueError as exc:
        return None, str(exc)
    characters = [character for block in page.get_text("rawdict").get("blocks", [])
                  if block.get("type") == 0
                  for line in block.get("lines", [])
                  for span in line.get("spans", [])
                  for character in span.get("chars", [])]
    digits = []
    for index, cell in enumerate(cells, 1):
        inside = [character["c"] for character in characters
                  if cell.contains(fitz.Point(
                      (character["bbox"][0] + character["bbox"][2]) / 2,
                      (character["bbox"][1] + character["bbox"][3]) / 2,
                  ))]
        if len(inside) != 1 or not inside[0].isascii() or not inside[0].isdigit():
            return None, f"W-9 EIN box {index} has {inside!r}; expected one digit"
        digits.append(inside[0])
    value = "".join(digits)
    return f"{value[:2]}-{value[2:]}", "Read nine digits from the printed W-9 EIN boxes"
