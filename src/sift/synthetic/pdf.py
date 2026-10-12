"""A tiny PDF writer (text only, Helvetica, WinAnsi encoding) for the synthetic restaurant menus.

Enough for a menu a crawler has to read as text; no external dependency."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Line:
    text: str
    size: int = 11
    bold: bool = False
    x: int = 56
    gap: int = 16


def _escape(text: str) -> bytes:
    raw = text.encode("cp1252", errors="replace")
    return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def make_pdf(lines: list[Line]) -> bytes:
    pages: list[list[tuple[Line, int]]] = [[]]
    y = 790
    for line in lines:
        if y - line.gap < 56:
            pages.append([])
            y = 790
        y -= line.gap
        pages[-1].append((line, y))
    objects: list[bytes] = []
    # 1: catalog, 2: pages, 3: font regular, 4: font bold, then (page, content) pairs
    page_ids = [5 + 2 * i for i in range(len(pages))]
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
    for page_lines, pid in zip(pages, page_ids, strict=True):
        stream = b"".join(
            b"BT /"
            + (b"F2" if line.bold else b"F1")
            + f" {line.size} Tf {line.x} {y} Td (".encode()
            + _escape(line.text)
            + b") Tj ET\n"
            for line, y in page_lines
        )
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents {pid + 1} 0 R >>".encode()
        )
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"endstream")
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("latin-1")
    return bytes(out)
