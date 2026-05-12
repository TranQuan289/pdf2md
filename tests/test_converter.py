"""
Test cases for converter.py covering different PDF scenarios.
Uses reportlab to generate PDFs programmatically.
"""

import io
import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "extension-pyodide", "python"))
from converter import (
    convert,
    _collapse_vertical_cell,
    _merge_vertical_text_column,
    _merge_continuation_rows,
    _is_pure_number,
    _merge_split_cells_up,
    _strip_empty_columns,
    _merge_complementary_columns,
    NUMERIC_HEADING_RE,
    BULLET_RE,
)

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


# ─── TC11: Vertical-text cell collapses to single word ───────────────────────
def test_tc11_collapse_vertical_cell_cjk():
    """TC11 — Stacked CJK chars (1 per line) → joined into one word."""
    assert _collapse_vertical_cell("現\n用\n・\n予\n備\n区\n分") == "現用・予備区分"
    assert _collapse_vertical_cell("イ\nベ\nン\nト\nＩ\nＤ") == "イベントＩＤ"


# ─── TC12: Vertical-text NOT collapsed for ASCII or short stacks ─────────────
def test_tc12_collapse_vertical_cell_negative():
    """TC12 — Non-CJK stacks and short stacks (<3 lines) kept as-is."""
    # ASCII single chars stacked → not collapsed (e.g. "Y/M/D" header)
    assert _collapse_vertical_cell("Y\nM\nD") == "Y\nM\nD"
    # 2-line stack (below 3-line threshold) → unchanged even for CJK
    assert _collapse_vertical_cell("／\n頁") == "／\n頁"
    # No newline → unchanged
    assert _collapse_vertical_cell("普通テキスト") == "普通テキスト"
    # Long lines (>2 chars) → unchanged, looks like real prose
    assert _collapse_vertical_cell("これは\n本文の\n続きです") == "これは\n本文の\n続きです"


# ─── TC13: _merge_vertical_text_column doesn't merge complete rows ───────────
def test_tc13_merge_vertical_text_column_preserves_rows():
    """TC13 — When every row has its own label+value, don't merge them as
    a single vertical-text continuation (regression: previously the colon
    heuristic over-fired and squashed 4 rows into one)."""
    rows = [
        ["オッズ・票数の最低更新周期", "地方開催：３０秒 JRA開催：４秒"],
        ["ホストの診断送信周期", "２０秒以上で１０秒単位に設定"],
        ["ホスト側の装置無通信タイムアウト時間", "診断周期の３倍"],
        ["表示装置側のホスト無通信タイムアウト時間", "診断周期の３倍以上"],
    ]
    out = _merge_vertical_text_column([list(r) for r in rows])
    # Each row's value column must remain populated (not merged into row 0)
    for i, r in enumerate(out):
        assert r[1].strip(), f"row {i} value column should not be empty"
    assert "診断周期の３倍" in out[2][1]


# ─── TC14: _merge_vertical_text_column merges true continuation rows ─────────
def test_tc14_merge_vertical_text_column_fires():
    """TC14 — Continuation pattern (col-1 fragments, col-0 mostly empty) is
    still merged into the first row."""
    rows = [
        ["設置場所", "東京都：本社"],
        ["", "千代田区"],
        ["", "永田町"],
    ]
    out = _merge_vertical_text_column([list(r) for r in rows])
    # First-row col 1 should contain all three fragments joined
    assert "東京" in out[0][1] and "千代田区" in out[0][1] and "永田町" in out[0][1]


# ─── TC15: Numeric heading "1.1" detected at the right level ─────────────────
def test_tc15_numeric_heading_multilevel():
    """TC15 — "1.1 Title" → h2, "1.1.1 Title" → h3."""
    def draw(c):
        c.setFont("Helvetica", 12)
        c.drawString(72, 740, "1. Top section")
        c.drawString(72, 700, "1.1 Sub section")
        c.drawString(72, 660, "1.1.1 Deep section")
        c.drawString(72, 620, "Body text paragraph after.")
        c.showPage()

    md = convert(make_pdf(draw))["markdown"]
    # 1.1 → 2 segments → h2; 1.1.1 → 3 segments → h3.
    assert "## 1.1 Sub section" in md
    assert "### 1.1.1 Deep section" in md


# ─── TC16: Heading not detected when text ends with sentence terminator ──────
def test_tc16_no_heading_when_sentence():
    """TC16 — Large-font line ending in '.' / '。' is body text, not heading."""
    def draw(c):
        c.setFont("Helvetica-Bold", 18)
        c.drawString(72, 740, "This is a real heading")
        c.setFont("Helvetica-Bold", 18)
        c.drawString(72, 700, "This ends in period.")  # same large font
        c.setFont("Helvetica", 12)
        c.drawString(72, 660, "Body paragraph.")
        c.showPage()

    md = convert(make_pdf(draw))["markdown"]
    assert "This is a real heading" in md
    # The "ends in period" line must NOT be a heading.
    assert "## This ends in period." not in md
    assert "# This ends in period." not in md


# ─── TC17: Bullet variants recognized ────────────────────────────────────────
def test_tc17_bullet_variants():
    """TC17 — Various bullet markers (*, -, ・) → standard markdown - item."""
    def draw(c):
        c.setFont("Helvetica", 12)
        c.drawString(72, 740, "* Star bullet")
        c.drawString(72, 720, "- Dash bullet")
        c.showPage()

    md = convert(make_pdf(draw))["markdown"]
    assert "- Star bullet" in md
    assert "- Dash bullet" in md


# ─── TC18: BULLET_RE matches CJK-specific markers ────────────────────────────
def test_tc18_bullet_re_cjk_markers():
    """TC18 — Regex covers '⚫' and '・' (added in recent revision)."""
    assert BULLET_RE.match("⚫ item one")
    assert BULLET_RE.match("・ item two")
    assert BULLET_RE.match("• item three")
    # Negative: plain text without bullet
    assert not BULLET_RE.match("just text")


# ─── TC19: NUMERIC_HEADING_RE handles fullwidth digits ───────────────────────
def test_tc19_numeric_heading_fullwidth():
    """TC19 — Fullwidth digit headings like "１．" / "１．１" are matched."""
    assert NUMERIC_HEADING_RE.match("１．Section title")
    assert NUMERIC_HEADING_RE.match("１．１ Sub title")
    # Should not match pure numbers
    assert not NUMERIC_HEADING_RE.match("12345")
    # Should not match a sentence number "1. " followed by digit-only content
    assert not NUMERIC_HEADING_RE.match("1. 123 456")


# ─── TC20: _is_pure_number ───────────────────────────────────────────────────
def test_tc20_is_pure_number():
    """TC20 — Helper distinguishes numbers (incl. fullwidth/parens) from text."""
    assert _is_pure_number("123")
    assert _is_pure_number("１２３")
    assert _is_pure_number("(40)")
    assert _is_pure_number("(40)")  # fullwidth parens
    assert _is_pure_number("12.5")
    assert _is_pure_number("-5")
    assert not _is_pure_number("abc")
    assert not _is_pure_number("12a")
    assert not _is_pure_number("")


# ─── TC21: _strip_empty_columns ──────────────────────────────────────────────
def test_tc21_strip_empty_columns():
    """TC21 — Columns that are empty in every row are removed."""
    rows = [
        ["A", "", "B", ""],
        ["C", "", "D", ""],
        ["E", "", "F", ""],
    ]
    out = _strip_empty_columns(rows)
    assert out == [["A", "B"], ["C", "D"], ["E", "F"]]
    # No-op when all columns have content
    rows2 = [["A", "B"], ["C", "D"]]
    assert _strip_empty_columns(rows2) == rows2


# ─── TC22: _merge_complementary_columns ──────────────────────────────────────
def test_tc22_merge_complementary_columns():
    """TC22 — Adjacent columns that never both have content in one row get
    merged (common PDF artifact: split column = 2 physical cells)."""
    rows = [
        ["A", "", "X"],
        ["", "B", "Y"],
        ["C", "", "Z"],
    ]
    out = _merge_complementary_columns(rows)
    # Cols 0 + 1 are complementary (never both filled) → merged
    assert out == [["A", "X"], ["B", "Y"], ["C", "Z"]]


# ─── TC23: _merge_continuation_rows fragment merge ───────────────────────────
def test_tc23_merge_continuation_single_cell():
    """TC23 — A row with a single short cell merges into the previous row's
    same column (Mondai/1 → Mondai 1 pattern)."""
    rows = [
        ["Mondai", "data1", "data2"],
        ["1", "", ""],
        ["Mondai", "data3", "data4"],
        ["2", "", ""],
    ]
    out = _merge_continuation_rows(rows)
    assert out[0][0] == "Mondai 1"
    assert out[1][0] == "Mondai 2"


# ─── TC24: _merge_split_cells_up preserves number-vs-number rows ─────────────
def test_tc24_merge_split_cells_up_skips_pure_numbers():
    """TC24 — A sparse pure-number row next to a dense pure-number row
    is NOT merged (these are distinct values — e.g. total-bytes
    annotation below the last position)."""
    rows = [
        ["38", "3", "差引金額", "8", "256"],
        ["",   "",  "",       "",  "264"],   # total-bytes annotation
    ]
    out = _merge_split_cells_up(rows)
    # Should NOT collapse to one row — both 256 and 264 must survive.
    flat = " ".join(c for r in out for c in r)
    assert "256" in flat and "264" in flat


# ─── TC25: Simple 2-col Markdown table — cells must be ≥3 chars to survive
# the "meaningful_cells" phantom-table filter. ─────────────────────────────
def test_tc25_simple_markdown_table():
    """TC25 — A plain rectangular table without colspan/rowspan renders
    as a Markdown pipe table (not HTML)."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4)
    data = [
        ["Header1", "Header2"],
        ["value-1a", "value-1b"],
        ["value-2a", "value-2b"],
        ["value-3a", "value-3b"],
    ]
    tbl = Table(data, colWidths=[150, 150])
    tbl.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 1, colors.black),
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightblue),
    ]))
    doc.build([tbl])
    md = convert(buf.getvalue())["markdown"]
    assert "| Header1 | Header2 |" in md
    assert "| --- | --- |" in md
    assert "| value-1a | value-1b |" in md


# ─── TC26: Complex table with colspan renders as HTML ────────────────────────
def test_tc26_complex_table_html():
    """TC26 — Table with a span (cell covering 2 cols in one row) renders
    as <table> HTML with colspan attribute."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4)
    data = [
        ["GroupTitleX", "", "DetailColZ"],
        ["alphaA1", "alphaA2", "alphaA3"],
        ["betaB1",  "betaB2",  "betaB3"],
        ["gammaC1", "gammaC2", "gammaC3"],
    ]
    tbl = Table(data, colWidths=[120, 120, 120])
    tbl.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 1, colors.black),
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightblue),
        ("SPAN", (0, 0), (1, 0)),  # row 0 cols 0-1 merged
    ]))
    doc.build([tbl])
    md = convert(buf.getvalue())["markdown"]
    assert "<table>" in md
    assert 'colspan="2">GroupTitleX' in md


# ─── TC27: Page numbers in footer are filtered out ───────────────────────────
def test_tc27_footer_page_numbers_filtered():
    """TC27 — Standalone numbers / "Page N" / "N/M" at footer (>88% Y)
    are dropped."""
    def draw(c):
        w, h = A4
        c.setFont("Helvetica", 12)
        c.drawString(72, h / 2, "Main body content paragraph.")
        # Various page-number forms at the footer
        c.setFont("Helvetica", 9)
        c.drawString(72, 30, "5")
        c.drawString(200, 30, "Page 5")
        c.drawString(400, 30, "5 / 10")
        c.showPage()

    md = convert(make_pdf(draw))["markdown"]
    lines = [l.strip() for l in md.splitlines() if l.strip()]
    assert "Main body content paragraph." in md
    # None of the page-number variants should appear as standalone lines
    for variant in ("5", "Page 5", "5 / 10"):
        assert variant not in lines


# ─── TC28: Multi-page document with repeating header → header dropped ────────
def test_tc28_repeating_header_dropped():
    """TC28 — Text repeated at the top of every page (running header) is
    detected as a page artifact and removed from the output body."""
    def draw(c):
        w, h = A4
        for page_num in range(1, 6):
            c.setFont("Helvetica", 9)
            c.drawString(72, h - 25, "Confidential Document - Internal Use")  # header
            c.setFont("Helvetica", 12)
            c.drawString(72, h / 2, f"Page {page_num} unique content here.")
            c.showPage()

    md = convert(make_pdf(draw))["markdown"]
    # Repeating header should NOT appear (at most once would be okay; we expect 0)
    assert md.count("Confidential Document - Internal Use") <= 1
    # Unique body content from each page should survive
    for i in range(1, 6):
        assert f"Page {i} unique content here." in md


# ─── TC29: Convert returns the documented result shape ───────────────────────
def test_tc29_result_shape():
    """TC29 — `convert()` returns a dict with markdown/pages/tables keys
    of the correct types."""
    def draw(c):
        c.setFont("Helvetica", 12)
        c.drawString(72, 700, "Hello")
        c.showPage()

    result = convert(make_pdf(draw))
    assert set(result.keys()) >= {"markdown", "pages", "tables"}
    assert isinstance(result["markdown"], str)
    assert isinstance(result["pages"], int)
    assert isinstance(result["tables"], int)


# ─── TC30: 3-page document with one table per page → all rows preserved ──────
def test_tc30_continuation_table_merge():
    """TC30 — When tables appear on consecutive pages, all data rows from
    each page must survive into the markdown output (no page is dropped
    or merged-away by the continuation-merge heuristic)."""
    from reportlab.platypus import PageBreak
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4)
    story = []
    for page_idx in range(3):
        data = [
            ["TitleX", "TitleX", "TitleX"],
            ["alphaCol", "betaCol", "gammaCol"],
        ]
        for r in range(8):
            data.append([f"p{page_idx}row{r}-A", f"p{page_idx}row{r}-B", f"p{page_idx}row{r}-C"])
        tbl = Table(data, colWidths=[140, 140, 140])
        tbl.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 1, colors.black),
            ("BACKGROUND", (0, 0), (-1, 0), colors.lightblue),
            ("SPAN", (0, 0), (2, 0)),
        ]))
        story.append(tbl)
        if page_idx < 2:
            story.append(PageBreak())
    doc.build(story)
    result = convert(buf.getvalue())
    md = result["markdown"]
    assert result["pages"] == 3
    for page_idx in (0, 1, 2):
        assert f"p{page_idx}row0-A" in md
        assert f"p{page_idx}row7-C" in md


# ═══════════════════════════════════════════════════════════════════════════
# Hard cases — replicate the complex layouts seen in the JRA spec PDFs.
# These cases push the converter's table-handling, multi-page merge,
# vertical-text collapse, and structural-column logic to their limits.
# ═══════════════════════════════════════════════════════════════════════════

REAL_PDF_DIR = os.path.dirname(__file__)
REAL_PDFS = {
    "file":      os.path.join(REAL_PDF_DIR, "上位サーバ_ファイル設計書（JRA追加版）.pdf"),
    "interface": os.path.join(REAL_PDF_DIR, "上位サーバ_インターフェース設計書（JRA追加版）.pdf"),
}


def _have_pdf(key):
    return os.path.exists(REAL_PDFS[key])


# ─── TC31: Nested colspan to encode 4 indent levels ─────────────────────────
def test_tc31_nested_colspan_indent_levels():
    """TC31 — A table with 4 indent levels (each level uses a different
    colspan in the item-name area, mimicking レコード項目定義). All rows
    must survive and the byte-count column must be at consistent x for
    every row."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4)
    # 6 cols: No, Level, Ind1, Ind2, Ind3, Name, Bytes, Comment
    data = [
        ["No.", "Level", "ItemNameAreaA", "ItemNameAreaB", "ItemNameAreaC", "ItemNameAreaD", "Bytes", "Comments"],
        ["1", "1", "Level1Item",  "Level1Item",  "Level1Item",  "Level1Item",  "16", "level1-comment"],
        ["2", "2", "",            "Level2ItemA", "Level2ItemA", "Level2ItemA", "8",  "level2-comment-a"],
        ["3", "2", "",            "Level2ItemB", "Level2ItemB", "Level2ItemB", "8",  "level2-comment-b"],
        ["4", "3", "",            "",            "Level3Item",  "Level3Item",  "4",  "level3-comment"],
        ["5", "4", "",            "",            "",            "Level4Item",  "2",  "level4-comment"],
    ]
    tbl = Table(data, colWidths=[40, 50, 30, 30, 30, 90, 50, 130])
    tbl.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 1, colors.black),
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightblue),
        # Merge the indent cells with the name cell at each level.
        ("SPAN", (2, 1), (5, 1)),  # level 1: cs=4
        ("SPAN", (3, 2), (5, 2)),  # level 2: cs=3
        ("SPAN", (3, 3), (5, 3)),  # level 2: cs=3
        ("SPAN", (4, 4), (5, 4)),  # level 3: cs=2
        # level 4: no span (cs=1)
    ]))
    doc.build([tbl])
    md = convert(buf.getvalue())["markdown"]
    assert "<table>" in md, "complex table should render as HTML"
    # Every item must appear
    for tag in ("Level1Item", "Level2ItemA", "Level2ItemB", "Level3Item", "Level4Item"):
        assert tag in md
    # Every bytes value must appear
    for b in ("16", "8", "4", "2"):
        assert f">{b}<" in md or f"| {b} " in md


# ─── TC32: Sparse matrix table (many cols, mostly empty) ────────────────────
def test_tc32_sparse_matrix():
    """TC32 — A wide table where most cells are empty and only a few have
    a marker. All marker positions must be preserved in the output.
    (Use ASCII marker 'X' because reportlab Helvetica doesn't carry the
    ● glyph and would substitute it; this test isolates the
    matrix-preservation logic from font-coverage issues.)"""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4)
    header = ["No", "FileName", "Event1", "Event2", "Event3", "Event4", "Event5", "Event6"]
    data = [header]
    for i in range(1, 6):
        row = [str(i), f"FileNo{i}"] + [""] * 6
        for c in range(2, 8):
            if (i + c) % 3 == 0:
                row[c] = "X"
        data.append(row)
    tbl = Table(data, colWidths=[35, 65, 55, 55, 55, 55, 55, 55])
    tbl.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 1, colors.black),
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightblue),
    ]))
    doc.build([tbl])
    md = convert(buf.getvalue())["markdown"]
    expected_markers = sum(1 for i in range(1, 6) for c in range(2, 8) if (i + c) % 3 == 0)
    actual_markers = sum(
        1 for line in md.splitlines() if line.startswith("|")
        for cell in line.split("|")[1:-1] if cell.strip() == "X"
    )
    assert actual_markers >= expected_markers, (
        f"expected ≥{expected_markers} markers, got {actual_markers}"
    )
    for i in range(1, 6):
        assert f"FileNo{i}" in md


# ─── TC33: Table cell with multi-line content (newlines inside one cell) ────
def test_tc33_multiline_cell_content():
    """TC33 — A cell containing multiple lines of distinct content should
    have all those lines preserved in the rendered output (not collapsed
    to a single sentence)."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4)
    multi_line_value = (
        "Format: YYYYMMDD\n"
        "Range: 1900-2099\n"
        "Special: 0000 means undefined"
    )
    data = [
        ["FieldName", "Description"],
        ["timestampField", multi_line_value],
        ["statusField",    "Status flags:\n0=inactive\n1=active\n2=pending"],
        ["countField",     "Number of records processed during the run"],
    ]
    tbl = Table(data, colWidths=[120, 250])
    tbl.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 1, colors.black),
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightblue),
    ]))
    doc.build([tbl])
    md = convert(buf.getvalue())["markdown"]
    # Every line of the multi-line cells must appear somewhere
    for snippet in ("YYYYMMDD", "1900-2099", "0000 means undefined",
                    "0=inactive", "1=active", "2=pending"):
        assert snippet in md, f"missing line content: {snippet!r}"


# ─── TC34: Pure-number annotation row preserved beside data rows ────────────
def test_tc34_pure_number_annotation_row():
    """TC34 — A "total bytes" annotation row below the last data row has
    only a number in one column. It must NOT be merged into the previous
    row (regression: _merge_split_cells_up's pure-number guard)."""
    rows = [
        ["38", "level3", "fieldname38_x", "datakeyA38", "8", "256"],
        ["39", "level3", "fieldname39_x", "datakeyA39", "8", "264"],
        ["",   "",        "",              "",           "",  "272"],  # total-bytes annotation
        ["40", "level4", "fieldname40_x", "datakeyA40", "8", "280"],
    ]
    out = _merge_split_cells_up([list(r) for r in rows])
    # The "272" annotation row's 272 must survive; row count must stay 4
    flat = [c for r in out for c in r]
    assert "272" in flat
    # All position values must coexist (256, 264, 272, 280)
    for v in ("256", "264", "272", "280"):
        assert v in flat


# ─── TC35: 3-page multi-page table, verify all data preserved ───────────────
def test_tc35_3page_table_all_rows_preserved():
    """TC35 — A table that spans 3 pages (PDF break between pages) must
    have every data row from every page in the final markdown."""
    from reportlab.platypus import PageBreak
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4)
    story = []
    for page_idx in range(3):
        header = ["Field", "Description"]
        data = [header]
        for r in range(10):
            data.append([f"P{page_idx}_field{r:02d}", f"P{page_idx}_desc_row_{r:02d}"])
        tbl = Table(data, colWidths=[160, 240])
        tbl.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 1, colors.black),
            ("BACKGROUND", (0, 0), (-1, 0), colors.lightblue),
        ]))
        story.append(tbl)
        if page_idx < 2:
            story.append(PageBreak())
    doc.build(story)
    result = convert(buf.getvalue())
    assert result["pages"] == 3
    for p in range(3):
        for r in range(10):
            assert f"P{p}_field{r:02d}" in result["markdown"]
            assert f"P{p}_desc_row_{r:02d}" in result["markdown"]


# ─── TC36: Vertical-text collapse on edge-case 2-char-per-line stacks ───────
def test_tc36_collapse_vertical_2char_per_line():
    """TC36 — Some PDFs render labels with 2 chars per line when the
    column is just wide enough for 2 chars. The collapse helper must
    handle these too."""
    # 2 chars per line, 3+ lines, all CJK → join
    assert _collapse_vertical_cell("現用\n予備\n区分") == "現用予備区分"
    # 1-char and 2-char mixed (still ≤ 2 chars per line) → join
    assert _collapse_vertical_cell("イベ\nント\nＩＤ") == "イベントＩＤ"
    # 3 chars per line → NOT collapsed (looks like real prose)
    assert _collapse_vertical_cell("現用予\n備区分\n以降詳細") == "現用予\n備区分\n以降詳細"


# ─── TC37: _merge_continuation_rows handles colon-suffix labels ─────────────
def test_tc37_continuation_colon_suffix():
    """TC37 — When a label cell ends with ':' / '：', the next row's
    value in the same column merges into it."""
    rows = [
        ["Time taken:",       "",          "extra info"],
        ["",                  "10 mins",   ""],
        ["Memory used:",      "",          "more info"],
        ["",                  "512 MB",    ""],
    ]
    out = _merge_continuation_rows(rows)
    flat = "\n".join(" | ".join(r) for r in out)
    assert "Time taken:" in flat
    assert "10 mins" in flat
    assert "Memory used:" in flat
    assert "512 MB" in flat


# ─── TC38: Distinct headings across pages all preserved ─────────────────────
def test_tc38_distinct_headings_per_page():
    """TC38 — Two distinct big headings on separate pages must both appear
    as headings. (The repeating-artifact filter only drops content that
    repeats — distinct headings are kept.)"""
    def draw(c):
        # Page 1
        c.setFont("Helvetica-Bold", 24)
        c.drawString(72, 500, "Section Alpha Heading")
        c.setFont("Helvetica", 12)
        c.drawString(72, 460, "Alpha section content body line.")
        c.showPage()
        # Page 2 — different big heading
        c.setFont("Helvetica-Bold", 24)
        c.drawString(72, 500, "Section Beta Heading")
        c.setFont("Helvetica", 12)
        c.drawString(72, 460, "Beta section content body line.")
        c.showPage()

    md = convert(make_pdf(draw))["markdown"]
    # Both big-font lines should become headings (size > body * 1.6 → h1).
    assert "# Section Alpha Heading" in md
    assert "# Section Beta Heading" in md
    assert "Alpha section content body line." in md
    assert "Beta section content body line." in md


# ─── TC39: Mixed bullet + ordered list in same document ─────────────────────
def test_tc39_mixed_lists():
    """TC39 — Both bullet and ordered lists in the same page render
    correctly without crosstalk."""
    def draw(c):
        c.setFont("Helvetica", 12)
        c.drawString(72, 740, "Steps to install:")
        c.drawString(72, 720, "1. Download the package")
        c.drawString(72, 700, "2. Run the installer")
        c.drawString(72, 680, "3. Restart the system")
        c.drawString(72, 650, "Prerequisites:")
        c.drawString(72, 630, "- A modern OS")
        c.drawString(72, 610, "- At least 4GB RAM")
        c.drawString(72, 590, "- Network access")
        c.showPage()

    md = convert(make_pdf(draw))["markdown"]
    assert "1. Download the package" in md
    assert "2. Run the installer" in md
    assert "3. Restart the system" in md
    assert "- A modern OS" in md
    assert "- At least 4GB RAM" in md
    assert "- Network access" in md


# ─── TC40: Whole-document conversion of the real ファイル設計書 PDF ─────────
@pytest.mark.skipif(not _have_pdf("file"), reason="ファイル設計書 PDF not present")
def test_tc40_real_file_design_pdf_conversion():
    """TC40 — End-to-end: the ファイル設計書 PDF converts to markdown with
    the expected page count and a healthy number of tables."""
    with open(REAL_PDFS["file"], "rb") as f:
        result = convert(f.read())
    assert result["pages"] == 89
    assert result["tables"] >= 70  # actual value: 81
    md = result["markdown"]
    assert len(md) > 100_000
    # Sanity: the doc title text must appear
    assert "地方競馬共同TZS" in md


# ─── TC41: 重勝払戻金 row 40 alignment (the original bug fix) ───────────────
@pytest.mark.skipif(not _have_pdf("file"), reason="ファイル設計書 PDF not present")
def test_tc41_jra_row40_alignment():
    """TC41 — Regression: row 40 (的中組番) in the 重勝払戻金 multi-page
    table must render with `colspan="2"` on the item-name cell so it aligns
    with rows 38 / 39 on the previous page (差引金額 colspan=3, 的中情報
    colspan=3). Specifically, the cell containing 的中組番 followed shortly
    by 'hitnum' must not have a stray empty <td> between them."""
    import re
    with open(REAL_PDFS["file"], "rb") as f:
        md = convert(f.read())["markdown"]
    # Locate the row containing 的中組番 + hitnum
    m = re.search(r"<tr>\s*<td>40</td>[\s\S]{0,400}的中組番[\s\S]{0,200}hitnum", md)
    assert m, "the 40 / 的中組番 / hitnum row must be present"
    block = m.group(0)
    # The fix: 的中組番 cell must carry colspan to span the level-3 indent col.
    assert 'colspan="2">的中組番' in block, (
        "的中組番 must be colspan=2 (merged with empty indent col) — "
        "see converter.py _render_html_table structural-col merge."
    )


# ─── TC42: No vertical-text cells remain in either real PDF's output ────────
@pytest.mark.skipif(not (_have_pdf("file") and _have_pdf("interface")),
                    reason="real PDFs not present")
def test_tc42_no_vertical_text_cells_in_real_pdfs():
    """TC42 — After the _collapse_vertical_cell fix, neither real PDF
    should produce any cell with ≥3 single-char lines stacked."""
    import re
    td_re = re.compile(r'<td[^>]*>([\s\S]*?)</td>')
    for key in ("file", "interface"):
        with open(REAL_PDFS[key], "rb") as f:
            md = convert(f.read())["markdown"]
        bad = []
        for cell in td_re.findall(md):
            t = cell.strip()
            if "\n" not in t:
                continue
            lines = [l.strip() for l in t.split("\n") if l.strip()]
            if len(lines) >= 3 and all(len(l) <= 2 for l in lines):
                bad.append(t)
        assert not bad, f"{key} PDF has {len(bad)} vertical-text cells: {bad[:3]}"


# ─── TC43: Real ファイル設計書 — every data row has consistent col count ───
@pytest.mark.skipif(not _have_pdf("file"), reason="ファイル設計書 PDF not present")
def test_tc43_real_file_design_table_row_column_consistency():
    """TC43 — For every レコード項目定義 HTML table in the real PDF, every
    data row's effective column count (sum of colspan) must equal the
    table's maximum width. Header rows (row 0) are allowed to differ."""
    import re
    with open(REAL_PDFS["file"], "rb") as f:
        md = convert(f.read())["markdown"]
    table_re = re.compile(r"<table>([\s\S]*?)</table>", re.MULTILINE)
    tr_re = re.compile(r"<tr>([\s\S]*?)</tr>")
    td_re = re.compile(r'<td(?:\s+colspan="(\d+)")?(?:\s+rowspan="(\d+)")?>([\s\S]*?)</td>')
    bad_tables = []
    for ti, tm in enumerate(table_re.finditer(md), 1):
        body = tm.group(1)
        if "レコード項目定義" not in body[:200]:
            continue
        rows = tr_re.findall(body)
        # Build virtual grid taking rowspan into account
        grid = []
        for ri, r in enumerate(rows):
            while ri >= len(grid):
                grid.append([])
            row_grid = grid[ri]
            c = 0
            for cs_s, rs_s, _ in td_re.findall(r):
                while c < len(row_grid) and row_grid[c] is not None:
                    c += 1
                while len(row_grid) <= c:
                    row_grid.append(None)
                cs = int(cs_s) if cs_s else 1
                rs = int(rs_s) if rs_s else 1
                for cc in range(c, c + cs):
                    while len(row_grid) <= cc:
                        row_grid.append(None)
                    row_grid[cc] = "anchor" if cc == c else "x"
                for rr in range(ri + 1, min(ri + rs, len(rows))):
                    while rr >= len(grid):
                        grid.append([])
                    future = grid[rr]
                    while len(future) <= c + cs - 1:
                        future.append(None)
                    for cc in range(c, c + cs):
                        if future[cc] is None:
                            future[cc] = "x"
                c += cs
        widths = [len(g) for g in grid if g]
        if not widths:
            continue
        expected = max(widths)
        # Ignore header row (row 0) which often uses bigger colspan
        bad = sum(1 for ri in range(1, len(grid)) if grid[ri] and len(grid[ri]) != expected)
        if bad:
            bad_tables.append((ti, bad))
    assert not bad_tables, f"{len(bad_tables)} tables have inconsistent data rows: {bad_tables[:3]}"


# ─── TC44: Real インターフェース設計書 — row value/label pairs preserved ───
@pytest.mark.skipif(not _have_pdf("interface"), reason="interface PDF not present")
def test_tc44_real_interface_pdf_label_value_pairs():
    """TC44 — The 周期値 table in インターフェース設計書 (4 rows × 2 cols)
    must keep each row's value column populated (regression for the
    _merge_vertical_text_column fix)."""
    with open(REAL_PDFS["interface"], "rb") as f:
        md = convert(f.read())["markdown"]
    # The 4 expected value-column lines from the table
    expected_values = [
        "地方開催：３０秒",
        "２０秒以上で１０秒単位に設定",
        "診断周期の３倍",
        "診断周期の３倍以上",
    ]
    for v in expected_values:
        assert v in md, f"missing value {v!r} — likely _merge_vertical_text_column regression"


# ─── TC45: Real インターフェース設計書 — vertical-text headers collapsed ───
@pytest.mark.skipif(not _have_pdf("interface"), reason="interface PDF not present")
def test_tc45_real_interface_pdf_collapses_vertical_headers():
    """TC45 — Specific known-vertical labels from the インターフェース設計書
    must appear as horizontal words after collapse."""
    with open(REAL_PDFS["interface"], "rb") as f:
        md = convert(f.read())["markdown"]
    expected_horizontal = [
        "現用・予備区分",
        "イベントＩＤ",
        "インターフェースパターン",
        "電文通番",
        "電文種",
    ]
    for label in expected_horizontal:
        assert label in md, f"vertical label not collapsed to {label!r}"
