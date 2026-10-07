"""Minimal dependency-free PDF writer for NSM evidence reports.

It produces A4 text documents (headings, paragraphs, key/value blocks and
simple tables) with the PDF base-14 Helvetica fonts and WinAnsi encoding, which
covers Italian accented text.  Output is deterministic for identical input, so
the archived SHA-256 of a report is reproducible.
"""
from __future__ import annotations

PAGE_WIDTH = 595.28
PAGE_HEIGHT = 841.89
MARGIN_X = 42.0
MARGIN_TOP = 48.0
MARGIN_BOTTOM = 52.0
CONTENT_WIDTH = PAGE_WIDTH - 2 * MARGIN_X
# Average Helvetica glyph width as a fraction of the font size; used to wrap
# and truncate conservatively without embedding font metrics.
_AVG_GLYPH = 0.52


def _encode(text) -> bytes:
    value = str(text if text is not None else "")
    value = value.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    raw = value.encode("cp1252", errors="replace")
    return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def _max_chars(width: float, size: float) -> int:
    return max(1, int(width / (size * _AVG_GLYPH)))


def _truncate(text, width: float, size: float) -> str:
    value = str(text if text is not None else "")
    limit = _max_chars(width, size)
    if len(value) <= limit:
        return value
    return value[: max(1, limit - 1)] + "…"


def _wrap(text, width: float, size: float) -> list[str]:
    limit = _max_chars(width, size)
    lines: list[str] = []
    for paragraph in str(text if text is not None else "").splitlines() or [""]:
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = ""
        for word in words:
            while len(word) > limit:
                if current:
                    lines.append(current)
                    current = ""
                lines.append(word[:limit])
                word = word[limit:]
            candidate = f"{current} {word}" if current else word
            if len(candidate) <= limit:
                current = candidate
            else:
                lines.append(current)
                current = word
        if current:
            lines.append(current)
    return lines


class PdfDocument:
    def __init__(self, title: str):
        self.title = title
        self.pages: list[list[bytes]] = []
        self.y = 0.0
        self._new_page()

    # -- layout primitives -------------------------------------------------
    def _new_page(self) -> None:
        self.pages.append([])
        self.y = PAGE_HEIGHT - MARGIN_TOP

    def _ensure(self, height: float) -> bool:
        if self.y - height < MARGIN_BOTTOM:
            self._new_page()
            return True
        return False

    def _text(self, x: float, y: float, text, size: float = 9.5, bold: bool = False, gray: float | None = None):
        font = b"/F2" if bold else b"/F1"
        color = b"%.2f g " % gray if gray is not None else b"0 g "
        self.pages[-1].append(
            color + b"BT " + font + b" %.1f Tf %.2f %.2f Td (" % (size, x, y) + _encode(text) + b") Tj ET"
        )

    def _rule(self, y: float, gray: float = 0.75) -> None:
        self.pages[-1].append(
            b"%.2f G 0.5 w %.2f %.2f m %.2f %.2f l S" % (gray, MARGIN_X, y, PAGE_WIDTH - MARGIN_X, y)
        )

    def _fill(self, x: float, y: float, width: float, height: float, gray: float) -> None:
        self.pages[-1].append(b"%.2f g %.2f %.2f %.2f %.2f re f" % (gray, x, y, width, height))

    # -- public building blocks -------------------------------------------
    def spacer(self, height: float = 8.0) -> None:
        self.y -= height

    def heading(self, text: str, level: int = 1) -> None:
        size = {1: 16.0, 2: 12.5, 3: 10.5}.get(level, 10.5)
        self._ensure(size + 18)
        if level <= 2:
            self.spacer(6)
        self._text(MARGIN_X, self.y - size, text, size=size, bold=True)
        self.y -= size + 6
        if level == 2:
            self._rule(self.y + 2)
            self.y -= 4

    def paragraph(self, text: str, size: float = 9.5, gray: float | None = None, bold: bool = False) -> None:
        for line in _wrap(text, CONTENT_WIDTH, size):
            self._ensure(size + 4)
            self._text(MARGIN_X, self.y - size, line, size=size, gray=gray, bold=bold)
            self.y -= size + 3.5
        self.y -= 2

    def key_values(self, pairs: list[tuple[str, object]], key_width: float = 220.0, size: float = 9.5) -> None:
        for key, value in pairs:
            lines = _wrap(value, CONTENT_WIDTH - key_width, size)
            self._ensure((size + 3.5) * len(lines) + 2)
            self._text(MARGIN_X, self.y - size, _truncate(key, key_width - 8, size), size=size, gray=0.35)
            for line in lines:
                self._text(MARGIN_X + key_width, self.y - size, line, size=size)
                self.y -= size + 3.5
        self.y -= 3

    def table(self, headers: list[str], rows: list[list[object]], widths: list[float], size: float = 8.0) -> None:
        total = sum(widths)
        scale = CONTENT_WIDTH / total if total > CONTENT_WIDTH else 1.0
        widths = [width * scale for width in widths]
        row_height = size + 6

        def draw_header():
            self._fill(MARGIN_X, self.y - row_height + 1, sum(widths), row_height, 0.90)
            x = MARGIN_X + 3
            for header, width in zip(headers, widths):
                self._text(x, self.y - size - 1.5, _truncate(header, width - 6, size), size=size, bold=True)
                x += width
            self.y -= row_height

        self._ensure(row_height * 2)
        draw_header()
        for row in rows:
            if self._ensure(row_height):
                draw_header()
            x = MARGIN_X + 3
            for cell, width in zip(row, widths):
                self._text(x, self.y - size - 1.5, _truncate(cell, width - 6, size), size=size)
                x += width
            self.y -= row_height
            self._rule(self.y + 1, gray=0.88)
        self.y -= 4

    # -- serialization -----------------------------------------------------
    def render(self, footer: str = "") -> bytes:
        total = len(self.pages)
        streams = []
        for index, operations in enumerate(self.pages, start=1):
            footer_ops = [
                b"0.45 g BT /F1 7.5 Tf %.2f %.2f Td (" % (MARGIN_X, 28.0) + _encode(footer) + b") Tj ET",
                b"0.45 g BT /F1 7.5 Tf %.2f %.2f Td (" % (PAGE_WIDTH - MARGIN_X - 60, 28.0)
                + _encode(f"Pagina {index}/{total}")
                + b") Tj ET",
            ]
            streams.append(b"\n".join(operations + footer_ops))

        objects: list[bytes] = []
        # 1 catalog, 2 pages, 3 font regular, 4 font bold, 5 info, then page/content pairs.
        page_ids = [6 + 2 * index for index in range(total)]
        objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
        kids = b" ".join(b"%d 0 R" % page_id for page_id in page_ids)
        objects.append(b"<< /Type /Pages /Kids [" + kids + b"] /Count %d >>" % total)
        objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
        objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
        objects.append(b"<< /Title (" + _encode(self.title) + b") /Producer (NSM Platform) >>")
        for index, stream in enumerate(streams):
            content_id = page_ids[index] + 1
            objects.append(
                b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %.2f %.2f] " % (PAGE_WIDTH, PAGE_HEIGHT)
                + b"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents %d 0 R >>" % content_id
            )
            objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")

        output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for number, body in enumerate(objects, start=1):
            offsets.append(len(output))
            output += b"%d 0 obj\n" % number + body + b"\nendobj\n"
        xref = len(output)
        output += b"xref\n0 %d\n" % (len(objects) + 1)
        output += b"0000000000 65535 f \n"
        for offset in offsets:
            output += b"%010d 00000 n \n" % offset
        output += b"trailer\n<< /Size %d /Root 1 0 R /Info 5 0 R >>\n" % (len(objects) + 1)
        output += b"startxref\n%d\n%%%%EOF\n" % xref
        return bytes(output)
