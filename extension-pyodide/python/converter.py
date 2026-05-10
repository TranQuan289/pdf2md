"""PDF → Markdown converter, runs inside Pyodide.

Uses pdfplumber (which wraps pdfminer.six) for text + table extraction,
then applies the same heuristics as src/convertmd/ on the Python side.
"""

import io
import re
from collections import Counter

import pdfplumber

BULLET_RE = re.compile(r"^\s*[•‣◦●○▪■□–—\-*]\s+(.*)")
ORDERED_RE = re.compile(r"^\s*(\d+)[.)]\s+(.*)")
PAGE_NUM_RE = re.compile(
    r"^\s*(?:[ivxlcdm]+|\d+|page\s+\d+|trang\s+\d+|\d+\s*[/of]\s*\d+)\s*$",
    re.IGNORECASE,
)
MONO_HINTS = ("mono", "courier", "consolas", "menlo")

TABLE_SETTINGS = {"edge_min_length": 50}

BLANK_BOX_MAX_H = 30   # pt — higher → table/border, not an answer box
BLANK_BOX_MAX_W = 100  # pt

LINE_TOL = 2
PAGE_NUM_MAX_CHARS = 12
FOOTER_Y_RATIO = 0.88
HEADER_Y_RATIO = 0.05
GAP_RATIO = 0.08
WIDE_BLOCK_RATIO = 0.5


def _is_mono(font: str) -> bool:
    f = (font or "").lower()
    return any(h in f for h in MONO_HINTS)


def _detect_blank_boxes(page):
    """Small rectangles = fill-in-the-blank answer boxes (e.g. [15], [16])."""
    boxes = []
    try:
        for r in page.rects:
            w = r["x1"] - r["x0"]
            h = r["bottom"] - r["top"]
            if 8 <= h <= BLANK_BOX_MAX_H and 12 <= w <= BLANK_BOX_MAX_W:
                boxes.append((r["x0"], r["top"], r["x1"], r["bottom"]))
    except Exception:
        pass
    return boxes


def _apply_blank_markers(lines, blank_boxes):
    """Wrap chars inside blank-box rectangles with ' | ' markers."""
    if not blank_boxes:
        return lines
    out = []
    for line in lines:
        chars = line.get("chars", [])
        if not chars:
            out.append(line)
            continue
        in_blanks = [
            any(
                bx0 <= (c["x0"] + c["x1"]) / 2 <= bx1
                and by0 <= (c["top"] + c["bottom"]) / 2 <= by1
                for bx0, by0, bx1, by1 in blank_boxes
            )
            for c in chars
        ]
        if not any(in_blanks):
            out.append(line)
            continue
        parts = []
        prev = False
        for c, in_b in zip(chars, in_blanks):
            if in_b and not prev:
                parts.append(" | ")
            elif not in_b and prev:
                parts.append(" | ")
            parts.append(c["text"])
            prev = in_b
        if prev:
            parts.append(" | ")
        new_line = dict(line)
        new_line["text"] = "".join(parts).strip()
        out.append(new_line)
    return out


def _filter_vertical_chars(chars, page_height, x_tol=3, min_column=5, min_y_span_ratio=0.3, isolation_radius=30):
    """Drop chars that are part of vertical-text decoration columns
    (book-spine style). A char is dropped only if BOTH:
      1. It belongs to a column of ≥5 same-X chars spanning ≥30% page height
      2. It has no horizontal neighbor within `isolation_radius` pt at its own Y
         (i.e. it is isolated from regular horizontal text on its row)
    """
    if not chars or page_height <= 0:
        return chars
    groups = []
    for c in chars:
        placed = False
        for g in groups:
            if abs(c["x0"] - g[0]["x0"]) <= x_tol:
                g.append(c)
                placed = True
                break
        if not placed:
            groups.append([c])

    threshold = page_height * min_y_span_ratio
    bad = set()
    for g in groups:
        if len(g) < min_column:
            continue
        ys = [c["top"] for c in g]
        if max(ys) - min(ys) < threshold:
            continue
        for vc in g:
            has_neighbor = False
            for other in chars:
                if other is vc:
                    continue
                if abs(other["top"] - vc["top"]) > 2:
                    continue
                dx = abs(other["x0"] - vc["x0"])
                if dx <= x_tol or dx > isolation_radius:
                    continue
                has_neighbor = True
                break
            if not has_neighbor:
                bad.add(id(vc))
    if not bad:
        return chars
    return [c for c in chars if id(c) not in bad]


def _chars_to_lines(chars):
    if not chars:
        return []
    chars = sorted(chars, key=lambda c: (c["top"], c["x0"]))
    grouped = []
    cur = [chars[0]]
    for c in chars[1:]:
        if abs(c["top"] - cur[0]["top"]) <= LINE_TOL:
            cur.append(c)
        else:
            grouped.append(sorted(cur, key=lambda c: c["x0"]))
            cur = [c]
    grouped.append(sorted(cur, key=lambda c: c["x0"]))
    lines = []
    for ln_chars in grouped:
        lines.append({
            "text": "".join(c["text"] for c in ln_chars),
            "chars": ln_chars,
            "top": min(c["top"] for c in ln_chars),
            "bottom": max(c["bottom"] for c in ln_chars),
            "x0": min(c["x0"] for c in ln_chars),
            "x1": max(c["x1"] for c in ln_chars),
            "size": max(c.get("size", 12) for c in ln_chars),
        })
    return _merge_vertical_text(lines)


def _merge_vertical_text(lines, x_tol=3, min_run=4):
    """Detect runs of consecutive short lines stacked at the same X position
    (book-spine style vertical text — typical on Vietnamese thesis covers).
    These are decorative; reconstructing reading order across multiple
    vertical columns is unreliable, so we drop them entirely."""
    out = []
    i = 0
    while i < len(lines):
        run = [lines[i]]
        j = i + 1
        while (
            j < len(lines)
            and len(lines[j]["text"].strip()) <= 2
            and len(run[-1]["text"].strip()) <= 2
            and abs(lines[j]["x0"] - run[0]["x0"]) <= x_tol
        ):
            run.append(lines[j])
            j += 1
        if len(run) >= min_run and all(len(l["text"].strip()) <= 2 for l in run):
            # Drop the vertical-text run.
            i = j
        else:
            out.append(lines[i])
            i += 1
    return out


def _filter_page_artifacts(lines, page_height):
    out = []
    for l in lines:
        text = l["text"].strip()
        if (
            text
            and len(text) <= PAGE_NUM_MAX_CHARS
            and (l["top"] > page_height * FOOTER_Y_RATIO or l["bottom"] < page_height * HEADER_Y_RATIO)
            and PAGE_NUM_RE.match(text)
        ):
            continue
        out.append(l)
    return out


def _body_size_from_chars(all_chars):
    """Body size = mode of char sizes weighted by char count, across the whole
    document (including table cells)."""
    sizes = Counter()
    for c in all_chars:
        k = round(c.get("size", 12) * 2) / 2
        sizes[k] += len(c["text"])
    return sizes.most_common(1)[0][0] if sizes else 12.0


def _heading_level(size, body):
    if size > body * 1.6:
        return 1
    if size > body * 1.3:
        return 2
    if size > body * 1.15:
        return 3
    return 0


def _reorder_columns(lines, page_width, page_height):
    """Vertical-gap projection multi-column reorder. Same algo as Python source."""
    if not lines or page_width <= 0:
        return lines
    res = max(int(page_width), 1)
    wide = page_width * WIDE_BLOCK_RATIO
    header_y = page_height * 0.1
    cover = bytearray(res)
    for l in lines:
        if (l["x1"] - l["x0"]) >= wide:
            continue
        if l["top"] < header_y:
            continue
        a = max(0, min(res - 1, int(l["x0"])))
        c = max(0, min(res - 1, int(l["x1"])))
        for x in range(a, c + 1):
            cover[x] = 1
    min_gap = page_width * GAP_RATIO
    gaps = []
    i = 0
    while i < res:
        if cover[i] == 0:
            j = i
            while j < res and cover[j] == 0:
                j += 1
            if (j - i) >= min_gap and i > 0 and j < res:
                gaps.append((i, j))
            i = j
        else:
            i += 1
    if not gaps:
        return sorted(lines, key=lambda l: (l["top"], l["x0"]))
    splits = [g[0] + (g[1] - g[0]) / 2 for g in gaps]
    boundaries = [0.0] + splits + [float(page_width)]

    def col_of(l):
        if (l["x1"] - l["x0"]) >= wide:
            return 0
        if l["top"] < header_y:
            return 0
        cx = (l["x0"] + l["x1"]) / 2
        for k in range(len(boundaries) - 1):
            if boundaries[k] <= cx < boundaries[k + 1]:
                return k
        return len(boundaries) - 2

    return sorted(lines, key=lambda l: (col_of(l), l["top"], l["x0"]))


def _filter_nested_tables(tables, tol=2):
    """Drop tables whose bbox is contained within another table's bbox.
    These are typically labels-inside-cells that pdfplumber detects as
    separate tables, causing duplicate content."""
    if len(tables) <= 1:
        return tables
    sorted_tbls = sorted(
        tables,
        key=lambda t: (t.bbox[2] - t.bbox[0]) * (t.bbox[3] - t.bbox[1]),
        reverse=True,
    )
    kept = []
    for tbl in sorted_tbls:
        b = tbl.bbox
        nested = False
        for kept_tbl in kept:
            k = kept_tbl.bbox
            if (
                b[0] >= k[0] - tol
                and b[1] >= k[1] - tol
                and b[2] <= k[2] + tol
                and b[3] <= k[3] + tol
            ):
                nested = True
                break
        if not nested:
            kept.append(tbl)
    return kept


def _strip_empty_columns(rows):
    """Drop columns that are empty in every row (artifacts of invisible
    PDF column separators / merged cells)."""
    if not rows:
        return rows
    n_cols = max(len(r) for r in rows)
    keep = [
        c for c in range(n_cols)
        if any(c < len(r) and r[c] for r in rows)
    ]
    if len(keep) == n_cols:
        return rows
    return [[r[c] if c < len(r) else "" for c in keep] for r in rows]


def _merge_continuation_rows(rows):
    """Merge fragment-rows into the previous row's same columns.
    Handles two patterns:
      1. Single non-empty cell row (e.g. "Mondai\\n1" → "Mondai 1")
      2. All non-empty cells are short fragments OR continuations of a label
         that ends with ':' (e.g. "Thời gian:" + "10 phút" → "Thời gian: 10 phút")
    """
    if not rows or len(rows) < 2:
        return rows
    out = [list(rows[0])]
    for r in rows[1:]:
        non_empty = [(i, v) for i, v in enumerate(r) if v]
        if not non_empty:
            out.append(list(r))
            continue
        prev = out[-1]

        def can_merge_cell(col, val):
            if col >= len(prev) or not prev[col]:
                return False
            v = val.strip()
            p = prev[col].strip()
            # Very short fragment (1-3 chars) — likely a number or letter
            # continuation. CJK words are typically ≥ 4 chars, so this avoids
            # merging legitimate short CJK rows.
            if len(v) <= 3:
                return True
            # Cur itself ends with separator → it's a label fragment
            if v.endswith((":", "：")):
                return True
            # Prev ends with separator → cur is the value for that label
            if p.endswith((":", "：", "-", "–")):
                return True
            return False

        # Avoid merging a row that has roughly the same "density" as prev —
        # those are typically two separate data rows that just happen to be
        # short (e.g. CJK 2-char labels). True continuation rows are sparse.
        prev_count = sum(1 for v in prev if v)
        cur_count = len(non_empty)
        single_cell = cur_count == 1 and can_merge_cell(*non_empty[0])
        all_continuations = (
            cur_count > 1
            and cur_count * 2 <= prev_count
            and all(can_merge_cell(i, v) for i, v in non_empty)
        )

        if single_cell or all_continuations:
            for i, v in non_empty:
                if i < len(prev) and prev[i]:
                    prev[i] = (prev[i] + " " + v).strip()
                else:
                    while len(prev) <= i:
                        prev.append("")
                    prev[i] = v
            continue
        out.append(list(r))
    return out


def _merge_vertical_text_column(rows):
    """For each column, detect if it contains a vertical-text continuation
    pattern (label-value sequence) and merge all non-empty cells into the
    first one. Triggered when the column has multiple short cells with at
    least one containing ':' (label-value indicator)."""
    if not rows or len(rows) < 3:
        return rows
    n_cols = max(len(r) for r in rows)
    rows = [list(r) + [""] * (n_cols - len(r)) for r in rows]
    for c in range(n_cols):
        values = [(i, rows[i][c]) for i in range(len(rows)) if rows[i][c]]
        if len(values) < 2:
            continue
        has_colon = any(":" in v or "：" in v for _, v in values)
        all_short = all(len(v) < 25 for _, v in values)
        if not (has_colon and all_short):
            continue
        first_idx = values[0][0]
        merged = " ".join(v for _, v in values)
        rows[first_idx][c] = merged
        for i, _ in values[1:]:
            rows[i][c] = ""
    return rows


def _merge_complementary_columns(rows):
    """Merge adjacent column pairs that never both have content in any row.
    PDFs with merged cells often produce N visible columns rendered as 2N
    physical columns (cell content alternates which one is filled)."""
    if not rows or not rows[0]:
        return rows
    n_cols = max(len(r) for r in rows)
    padded = [r + [""] * (n_cols - len(r)) for r in rows]
    cols = list(zip(*padded))
    merged_cols = []
    i = 0
    while i < len(cols):
        if i + 1 < len(cols):
            a, b = cols[i], cols[i + 1]
            both = sum(1 for x, y in zip(a, b) if x and y)
            either = sum(1 for x, y in zip(a, b) if x or y)
            if both == 0 and either >= 2:
                merged_cols.append(tuple(x or y for x, y in zip(a, b)))
                i += 2
                continue
        merged_cols.append(cols[i])
        i += 1
    if len(merged_cols) == n_cols:
        return rows
    return [list(row) for row in zip(*merged_cols)]


def _render_table(rows):
    if not rows:
        return ""
    rows = _merge_continuation_rows(rows)
    rows = _merge_vertical_text_column(rows)
    rows = _strip_empty_columns(rows)
    rows = _merge_complementary_columns(rows)
    if not rows or not rows[0]:
        return ""
    headers = rows[0]
    n = len(headers)
    if n == 0:
        return ""

    def cell(s):
        return (s or "").replace("\n", " ").replace("|", "\\|").strip()

    out = ["| " + " | ".join(cell(c) for c in headers) + " |"]
    out.append("| " + " | ".join(["---"] * n) + " |")
    for r in rows[1:]:
        padded = list(r) + [""] * (n - len(r))
        out.append("| " + " | ".join(cell(c) for c in padded[:n]) + " |")
    return "\n".join(out)


def _line_inside_any_bbox(line, bboxes):
    cx = (line["x0"] + line["x1"]) / 2
    cy = (line["top"] + line["bottom"]) / 2
    for x0, y0, x1, y1 in bboxes:
        if x0 <= cx <= x1 and y0 <= cy <= y1:
            return True
    return False


def convert(pdf_bytes):
    pdf = pdfplumber.open(io.BytesIO(pdf_bytes))
    try:
        pages_data = []
        total_tables = 0
        all_chars = []

        for page in pdf.pages:
            blank_boxes = _detect_blank_boxes(page)
            chars = _filter_vertical_chars(page.chars, page.height)
            all_chars.extend(chars)
            lines = _chars_to_lines(chars)
            lines = _filter_page_artifacts(lines, page.height)
            lines = _apply_blank_markers(lines, blank_boxes)

            try:
                pdfp_tables = page.find_tables(table_settings=TABLE_SETTINGS)
                pdfp_tables = _filter_nested_tables(pdfp_tables)
            except Exception:
                pdfp_tables = []
            tables = []
            table_bboxes = []
            for tbl in pdfp_tables:
                rows = tbl.extract()
                if not rows or len(rows) < 2:
                    continue
                n_cols = max(len(r) for r in rows)
                if n_cols < 2:
                    continue
                rows = [[(c or "").strip() for c in r] for r in rows]
                if not any(any(c for c in r) for r in rows):
                    continue
                tables.append({"rows": rows, "bbox": tuple(tbl.bbox)})
                table_bboxes.append(tuple(tbl.bbox))
            total_tables += len(tables)

            non_table_lines = [l for l in lines if not _line_inside_any_bbox(l, table_bboxes)]
            non_table_lines = _reorder_columns(non_table_lines, page.width, page.height)

            pages_data.append({
                "lines": non_table_lines,
                "tables": tables,
                "width": page.width,
                "height": page.height,
            })

        body = _body_size_from_chars(all_chars)
        out = []

        for pd in pages_data:
            # Build mixed list of items (tables + lines), sort by Y so tables
            # appear in their original visual position rather than all-first.
            items = []
            for tbl in pd["tables"]:
                items.append({"y": tbl["bbox"][1], "kind": "table", "data": tbl})
            for ln in pd["lines"]:
                items.append({"y": ln["top"], "kind": "line", "data": ln})
            items.sort(key=lambda x: x["y"])

            for item in items:
                if item["kind"] == "table":
                    out.append(_render_table(item["data"]["rows"]))
                    out.append("")
                    continue
                ln = item["data"]
                text = ln["text"].strip()
                if not text:
                    continue
                lvl = _heading_level(ln["size"], body)
                if lvl:
                    out.append("#" * lvl + " " + text)
                    out.append("")
                    continue
                m = BULLET_RE.match(text)
                if m:
                    out.append("- " + m.group(1))
                    continue
                m = ORDERED_RE.match(text)
                if m:
                    out.append(f"{m.group(1)}. {m.group(2)}")
                    continue
                if all(_is_mono(c.get("fontname", "")) for c in ln["chars"]):
                    out.append("    " + text)
                    continue
                out.append(text)
            out.append("")

        markdown = "\n".join(out).rstrip() + "\n"
        return {
            "markdown": markdown,
            "pages": len(pages_data),
            "tables": total_tables,
        }
    finally:
        pdf.close()
