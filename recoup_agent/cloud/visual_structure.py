from __future__ import annotations

import io
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

from ..document_quality import StructuralIssue, bounds, critical_blocks, polygon_valid
from .documents import Page


def _horizontal_break(image: Image.Image, box: tuple[float, float, float, float]) -> float | None:
    width, height = image.size
    x0, y0, x1, y1 = box
    crop = image.crop((max(0, int(x0 * width)), max(0, int(y0 * height)),
                       min(width, int(x1 * width + 1)), min(height, int(y1 * height + 1))))
    if crop.width < 12 or crop.height < 10:
        return None
    rows = []
    pixels = list(crop.getdata())
    for y in range(crop.height):
        rows.append({x for x, value in enumerate(pixels[y*crop.width:(y+1)*crop.width])
                     if value < 160})
    occupied = [y for y, row in enumerate(rows) if len(row) >= 3]
    if not occupied:
        return None
    top, bottom = occupied[0], occupied[-1]
    glyph_height = bottom - top + 1
    for y in range(top + max(2, glyph_height // 4), bottom - max(2, glyph_height // 4)):
        if rows[y]:
            continue
        end = y
        while end < bottom and not rows[end]:
            end += 1
        if not 1 <= end-y <= glyph_height * 0.3:
            continue
        above = set.union(*rows[max(top, y-3):y])
        below = set.union(*rows[end:min(bottom+1, end+3)])
        overlap = len(above & below) / crop.width
        if overlap >= 0.2:
            return (y + end) / 2 + int(y0 * height)
    return None


def inspect_raster(file_path: str, pages: list[Page]) -> list[StructuralIssue]:
    issues = []
    suffix = Path(file_path).suffix.lower()
    for page in pages:
        targets = critical_blocks(page)
        if not targets:
            continue
        with TemporaryDirectory(prefix="recoup-structure-") as directory:
            if suffix == ".pdf":
                prefix = str(Path(directory) / "page")
                subprocess.run(
                    ["pdftoppm", "-f", str(page.number), "-l", str(page.number),
                     "-r", "150", "-scale-to", "2400", "-gray", "-singlefile", file_path, prefix],
                    check=True, capture_output=True, timeout=120)
                raw = Path(prefix + ".pgm").read_bytes()
            else:
                raw = Path(file_path).read_bytes()
            with Image.open(io.BytesIO(raw)) as original:
                image = original.convert("L")
                for index, block in targets:
                    breaks = []
                    for token in block.tokens:
                        if len(token.text.strip()) < 4 or not polygon_valid(token.polygon):
                            continue
                        row = _horizontal_break(image, bounds(token.polygon))
                        if row is not None:
                            breaks.append(row)
                    for row in breaks:
                        aligned = sum(abs(row-other) <= 2 for other in breaks)
                        line_tokens = sum(
                            bounds(t.polygon)[1] <= row / image.height <= bounds(t.polygon)[3]
                            for t in block.tokens if polygon_valid(t.polygon))
                        fragment_ratio = (line_tokens + aligned) / max(line_tokens, 1)
                        if aligned >= 3 and fragment_ratio >= 1.25:
                            issues.append(StructuralIssue(
                                page.number, "horizontal_text_break",
                                "A blank band splits critical line tokens into excess visual fragments.",
                                index, fragment_ratio, 1.25))
                            break
    return issues
