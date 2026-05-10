"""
10 test cases for converter.py covering different PDF scenarios.
Uses reportlab to generate PDFs programmatically.
"""

import io
import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "extension-pyodide", "python"))
from converter import convert

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet


def make_pdf(draw_fn) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    draw_fn(c)
    c.save()
    return buf.getvalue()


# ─── TC01: Plain text, single page ───────────────────────────────────────────
def test_tc01_plain_text():
    """TC01 — Văn bản thuần, 1 trang → output không rỗng."""
    def draw(c):
        c.setFont("Helvetica", 12)
        c.drawString(72, 700, "Hello World. This is a simple PDF document.")
        c.drawString(72, 680, "Second line of plain text here.")
        c.showPage()

    result = convert(make_pdf(draw))
    assert result["pages"] == 1
    assert "Hello World" in result["markdown"]
    assert "Second line" in result["markdown"]
    assert result["tables"] == 0


# ─── TC02: Multiple pages ─────────────────────────────────────────────────────
def test_tc02_multiple_pages():
    """TC02 — Nhiều trang → pages count đúng."""
    def draw(c):
        for i in range(1, 6):
            c.setFont("Helvetica", 12)
            c.drawString(72, 700, f"Page {i} content text goes here.")
            c.showPage()

    result = convert(make_pdf(draw))
    assert result["pages"] == 5
    assert "Page 1" in result["markdown"]
    assert "Page 5" in result["markdown"]


# ─── TC03: Headings detected by font size ─────────────────────────────────────
def test_tc03_headings():
    """TC03 — Font lớn hơn body → được nhận diện là heading (# prefix)."""
    def draw(c):
        c.setFont("Helvetica-Bold", 24)
        c.drawString(72, 750, "Big Heading Title")
        c.setFont("Helvetica-Bold", 18)
        c.drawString(72, 710, "Subheading Level Two")
        c.setFont("Helvetica", 12)
        c.drawString(72, 670, "Normal body text paragraph here.")
        c.showPage()

    result = convert(make_pdf(draw))
    md = result["markdown"]
    assert "# Big Heading Title" in md or "## Big Heading Title" in md
    assert "Normal body text" in md


# ─── TC04: Bullet list ───────────────────────────────────────────────────────
def test_tc04_bullet_list():
    """TC04 — Dấu bullet ASCII (* hoặc -) → được giữ dạng - item.

    Note: Helvetica không có Unicode mapping cho • (U+2022) nên pdfplumber
    decode thành (cid:127), không khớp BULLET_RE. Dùng * thay thế — đây là
    ký tự hợp lệ trong pattern và luôn decode đúng.
    """
    def draw(c):
        c.setFont("Helvetica", 12)
        items = ["* First bullet item", "* Second bullet item", "* Third bullet item"]
        y = 700
        for item in items:
            c.drawString(72, y, item)
            y -= 20
        c.showPage()

    result = convert(make_pdf(draw))
    md = result["markdown"]
    assert "- First bullet item" in md
    assert "- Second bullet item" in md
    assert "- Third bullet item" in md


# ─── TC04b: CID-encoded bullet (known limitation) ────────────────────────────
def test_tc04b_cid_bullet_not_recognized():
    """TC04b — Bullet • qua Helvetica bị encode thành (cid:127).
    Converter không crash, nội dung text vẫn xuất hiện trong output
    (dù không được format thành - item).
    """
    def draw(c):
        c.setFont("Helvetica", 12)
        c.drawString(72, 700, "• CID bullet text")
        c.showPage()

    result = convert(make_pdf(draw))
    # Không crash, nội dung vẫn có mặt
    assert "CID bullet text" in result["markdown"]


# ─── TC05: Ordered list ──────────────────────────────────────────────────────
def test_tc05_ordered_list():
    """TC05 — Danh sách đánh số → giữ định dạng 1. 2. 3."""
    def draw(c):
        c.setFont("Helvetica", 12)
        items = ["1. First ordered item", "2. Second ordered item", "3. Third ordered item"]
        y = 700
        for item in items:
            c.drawString(72, y, item)
            y -= 20
        c.showPage()

    result = convert(make_pdf(draw))
    md = result["markdown"]
    assert "1. First ordered item" in md
    assert "2. Second ordered item" in md
    assert "3. Third ordered item" in md


# ─── TC06: Simple table ──────────────────────────────────────────────────────
def test_tc06_table():
    """TC06 — Bảng đơn giản → được render dạng Markdown table."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4)
    styles = getSampleStyleSheet()
    data = [
        ["Name", "Age", "City"],
        ["Alice", "30", "Hanoi"],
        ["Bob", "25", "HCMC"],
        ["Charlie", "35", "Danang"],
    ]
    table = Table(data, colWidths=[150, 80, 150])
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 1, colors.black),
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightblue),
    ]))
    doc.build([table])
    pdf_bytes = buf.getvalue()

    result = convert(pdf_bytes)
    md = result["markdown"]
    assert result["tables"] >= 1
    # Các giá trị phải xuất hiện trong output
    assert "Name" in md
    assert "Alice" in md
    assert "Bob" in md


# ─── TC07: Empty PDF (no text) ───────────────────────────────────────────────
def test_tc07_empty_pdf():
    """TC07 — PDF không có text → output là chuỗi rỗng hoặc chỉ whitespace."""
    def draw(c):
        # Vẽ hình chữ nhật không có text
        c.rect(72, 600, 200, 100)
        c.showPage()

    result = convert(make_pdf(draw))
    assert result["pages"] == 1
    assert result["tables"] == 0
    assert result["markdown"].strip() == ""


# ─── TC08: Page numbers filtered out ─────────────────────────────────────────
def test_tc08_page_numbers_filtered():
    """TC08 — Số trang ở header/footer → bị lọc khỏi output."""
    def draw(c):
        w, h = A4
        c.setFont("Helvetica", 12)
        # Body text
        c.drawString(72, h / 2, "Main content of the document.")
        # Footer page number (below 88% of page height → footer zone)
        c.setFont("Helvetica", 10)
        c.drawString(w / 2, 30, "5")  # page number at bottom
        c.showPage()

    result = convert(make_pdf(draw))
    md = result["markdown"]
    assert "Main content" in md
    # Số "5" đứng một mình phải bị lọc
    lines = [l.strip() for l in md.splitlines()]
    assert "5" not in lines


# ─── TC09: Vietnamese text ────────────────────────────────────────────────────
def test_tc09_vietnamese_text():
    """TC09 — Tiếng Việt → không bị mất ký tự."""
    def draw(c):
        # reportlab built-in fonts không hỗ trợ Unicode nên dùng ASCII
        # đại diện để kiểm tra flow xử lý
        c.setFont("Helvetica", 12)
        c.drawString(72, 700, "Truong Dai hoc Bach Khoa Ha Noi")
        c.drawString(72, 680, "Luan van tot nghiep khoa hoc may tinh")
        c.showPage()

    result = convert(make_pdf(draw))
    md = result["markdown"]
    assert "Truong Dai hoc" in md
    assert "Luan van" in md


# ─── TC10: Large multi-page document ─────────────────────────────────────────
def test_tc10_large_document():
    """TC10 — Tài liệu nhiều trang với nhiều nội dung → không crash, trả đúng page count."""
    def draw(c):
        for page_num in range(1, 21):
            c.setFont("Helvetica-Bold", 16)
            c.drawString(72, 750, f"Chapter {page_num}: Introduction")
            c.setFont("Helvetica", 12)
            y = 710
            for line_num in range(1, 16):
                c.drawString(72, y, f"Line {line_num} of page {page_num}: Lorem ipsum dolor sit amet.")
                y -= 20
            c.showPage()

    result = convert(make_pdf(draw))
    assert result["pages"] == 20
    assert "Chapter 1" in result["markdown"]
    assert "Chapter 20" in result["markdown"]
    assert len(result["markdown"]) > 1000
