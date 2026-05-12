"""PDF → Markdown converter, runs inside Pyodide.

Uses pdfplumber (which wraps pdfminer.six) for text + table extraction,
then applies the same heuristics as src/convertmd/ on the Python side.
"""

import io
import re
from collections import Counter

import pdfplumber

BULLET_RE = re.compile(r"^\s*[•‣◦●○▪■□–—\-*⚫・]\s*(.+)")
ORDERED_RE = re.compile(r"^\s*(\d+)[.)]\s+(.*)")
PAGE_NUM_RE = re.compile(
    r"^\s*(?:[ivxlcdm]+|\d+|page\s+\d+|trang\s+\d+|\d+\s*[/of]\s*\d+)\s*$",
    re.IGNORECASE,
)
# Numeric heading: "1.", "1.1", "1.1.1" (also fullwidth digits / dots).
NUMERIC_HEADING_RE = re.compile(
    r"^\s*([０-９\d]+(?:[\.．][０-９\d]+)*)(?:[\.．][\s　]*|[\s　]+)[^\d\s０-９．\.]"
)
MONO_HINTS = ("mono", "courier", "consolas", "menlo")

_CJK_CHAR_RE = re.compile(r"[　-ヿ㐀-鿿＀-￯]")


def _collapse_vertical_cell(text):
    """Join stacked CJK chars (one char per line) into a single word.
    PDFs with narrow header columns often render labels vertically — pdfplumber
    extracts each char on its own line. Joining restores the readable form
    (e.g. "現\\n用\\n・\\n予\\n備\\n区\\n分" → "現用・予備区分").

    Heuristic: 3+ non-empty lines, every line ≤2 chars, at least one CJK char.
    Short non-CJK stacks (e.g. "Y/M/D") are left alone."""
    if not text or "\n" not in text:
        return text
    lines = [l.strip() for l in text.split("\n")]
    lines = [l for l in lines if l]
    if len(lines) < 3:
        return text
    if not all(len(l) <= 2 for l in lines):
        return text
    if not any(_CJK_CHAR_RE.search(l) for l in lines):
        return text
    return "".join(lines)

TABLE_SETTINGS = {
    "horizontal_strategy": "lines",
    "vertical_strategy": "lines",
    "edge_min_length": 20,
}
# Fallback when default detection misses rows at the bottom of a table because
# the bottom horizontal border isn't drawn (common in multi-page tables).
TABLE_SETTINGS_TEXT_ROWS = {
    "horizontal_strategy": "text",
    "vertical_strategy": "lines",
    "edge_min_length": 20,
}

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


def _filter_vertical_chars(chars, page_height, x_tol=3, min_column=5,
                            min_y_span_ratio=0.08, isolation_radius=15):
    """Drop chars that are part of vertical-text decoration columns
    (book-spine style or flowchart vertical labels). A char is dropped if BOTH:
      1. It belongs to a column of ≥`min_column` same-X chars spanning
         ≥`min_y_span_ratio` of page height.
      2. It has no horizontal neighbor within `isolation_radius` pt at its own Y
         (i.e. no adjacent in-word char on its row).
    `isolation_radius` is set so regular word-internal char spacing (~5–10 pt)
    counts as a neighbor, but flowchart column spacing (≥20 pt) does not.
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
    if size > body * 1.25:
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


_PURE_NUMBER_RE = re.compile(r"[\d０-９.,\-()（）\s]+")


def _is_pure_number(s):
    """True if s is just digits (ASCII/fullwidth) plus parens, dots, commas, signs."""
    s = (s or "").strip()
    if not s:
        return False
    return bool(_PURE_NUMBER_RE.fullmatch(s))


def _merge_split_cells_up(rows):
    """Undo cell-line splits from text-strategy table extraction. When a
    multi-line cell is split, the item label may land on either the first
    or the second physical line; the other line shows up as a "sparse"
    row carrying just the continuation in one column.
    Pair adjacent rows greedily: a sparse row alongside a dense row that
    shares the sparse column is merged into the dense row, regardless of
    direction. The dense row's existing text comes first when the dense
    row is above; otherwise the sparse text comes first.
    Skip the merge when both the dense cell and the sparse cell are pure
    numbers — these are distinct values (e.g., an annotation row showing
    the total record size right below the last position), not a split cell."""
    if not rows or len(rows) < 2:
        return rows
    rows = [list(r) for r in rows]
    n = len(rows)
    out = []
    i = 0
    def cols_with_text(row):
        return [c for c, v in enumerate(row) if v and v.strip()]
    while i < n:
        r = rows[i]
        r_cols = cols_with_text(r)
        if i + 1 < n:
            nxt = rows[i + 1]
            n_cols = cols_with_text(nxt)
            # Dense row followed by sparse row sharing a column → merge sparse into dense.
            if len(r_cols) >= 2 and len(n_cols) == 1 and n_cols[0] in r_cols:
                c = n_cols[0]
                if not (_is_pure_number(r[c]) and _is_pure_number(nxt[c])):
                    merged = list(r)
                    merged[c] = (r[c].strip() + " " + nxt[c].strip()).strip()
                    out.append(merged)
                    i += 2
                    continue
            # Sparse row followed by dense row sharing a column → merge sparse into dense.
            if len(r_cols) == 1 and len(n_cols) >= 2 and r_cols[0] in n_cols:
                c = r_cols[0]
                if not (_is_pure_number(r[c]) and _is_pure_number(nxt[c])):
                    merged = list(nxt)
                    merged[c] = (r[c].strip() + " " + nxt[c].strip()).strip()
                    out.append(merged)
                    i += 2
                    continue
        out.append(r)
        i += 1
    return out


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
    least one containing ':' (label-value indicator).

    Only fires when the rows being merged are *continuation rows* — rows that
    have content in column c but are mostly empty elsewhere. If those rows
    have their own labels in other columns, they are independent table rows,
    not vertical-text continuation, and must not be merged."""
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
        # Continuation rows have empty cells elsewhere. If most rows being
        # merged carry their own content in another column, they're separate
        # rows and merging would destroy data.
        other_filled = 0
        for i, _ in values[1:]:
            if any(rows[i][cc].strip() for cc in range(n_cols) if cc != c):
                other_filled += 1
        if other_filled >= len(values[1:]) // 2 + 1:
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


# ===== HTML table rendering (for complex tables with colspan/rowspan) =====

def _grid_bounds(tbl):
    """Collect sorted unique x/y boundaries from all cell bboxes."""
    cells = [c for row in tbl.rows for c in row.cells if c is not None]
    xs = sorted({round(v) for c in cells for v in (c[0], c[2])})
    ys = sorted({round(v) for c in cells for v in (c[1], c[3])})
    return xs, ys


def _snap_idx(val, bounds):
    v = round(val)
    return min(range(len(bounds)), key=lambda i: abs(bounds[i] - v))


def _cell_spans(bbox, xs, ys):
    x0, y0, x1, y1 = bbox
    c0 = _snap_idx(x0, xs)
    c1 = _snap_idx(x1, xs)
    r0 = _snap_idx(y0, ys)
    r1 = _snap_idx(y1, ys)
    return r0, c0, max(1, r1 - r0), max(1, c1 - c0)


def _is_complex_table(tbl):
    """Return True if any cell has colspan or rowspan > 1."""
    try:
        xs, ys = _grid_bounds(tbl)
        for row in tbl.rows:
            for cell in row.cells:
                if cell is None:
                    return True  # rowspan-covered cell
                _, _, rs, cs = _cell_spans(cell, xs, ys)
                if rs > 1 or cs > 1:
                    return True
    except Exception:
        pass
    return False


def _render_html_table(tbl, extracted, page, blank_boxes,
                       override_xs=None, override_phantom_cols=None,
                       override_structural_cols=None):
    """Render a complex table as HTML with colspan/rowspan.

    Also handles two common PDF artifacts:
    - Phantom columns: empty columns from PDF grid lines → removed from output
    - Text boxes: bordered rectangles with 1 real column → rendered as single-column table
    - Blank answer boxes: small bordered squares within text → wrapped with | markers

    `override_xs` lets callers pass a unified x-boundary list shared by all
    continuation tables of the same logical PDF table. Without this, each page
    derives its own xs from its own cells; pages that lack a structural column
    end up with a shifted grid, breaking alignment after the HTML-level merge.
    `override_phantom_cols` is the phantom-col set computed across the whole
    continuation run (intersection); using it locally keeps real-on-other-pages
    cols visible here so they align with the pages where they're filled.

    Falls back to Markdown on any error.
    """
    try:
        xs, ys = _grid_bounds(tbl)
        if override_xs is not None:
            xs = list(override_xs)
        n_rows = max(1, len(ys) - 1)
        n_cols = max(1, len(xs) - 1)

        # grid[r][c] = {"text", "cs", "rs"} | "x" (span-occupied) | None
        grid = [[None] * n_cols for _ in range(n_rows)]

        for r_phys, tbl_row in enumerate(tbl.rows):
            ex_row = extracted[r_phys] if r_phys < len(extracted) else []
            for c_phys, cell in enumerate(tbl_row.cells):
                if cell is None:
                    continue
                r0, c0, rs, cs = _cell_spans(cell, xs, ys)
                if not (0 <= r0 < n_rows and 0 <= c0 < n_cols):
                    continue

                x0, y0, x1, y1 = cell
                text = (ex_row[c_phys] if c_phys < len(ex_row) else None) or ""

                # Apply blank-box markers when any answer box overlaps this cell
                if blank_boxes:
                    overlap = [b for b in blank_boxes
                               if b[0] < x1 and b[2] > x0 and b[1] < y1 and b[3] > y0]
                    if overlap:
                        try:
                            cell_chars = page.crop((x0, y0, x1, y1)).chars
                            cell_lines = _chars_to_lines(cell_chars)
                            cell_lines = _apply_blank_markers(cell_lines, overlap)
                            text = " ".join(
                                ln["text"].strip() for ln in cell_lines if ln["text"].strip()
                            )
                        except Exception:
                            pass

                grid[r0][c0] = {"text": text.strip(), "cs": cs, "rs": rs}
                for rr in range(r0, min(r0 + rs, n_rows)):
                    for cc in range(c0, min(c0 + cs, n_cols)):
                        if rr != r0 or cc != c0:
                            grid[rr][cc] = "x"

        if override_phantom_cols is not None:
            phantom_cols = set(override_phantom_cols)
        else:
            phantom_cols = _phantom_cols_for_grid(grid, n_rows, n_cols)

        # Expand empty rowspan placeholders into explicit per-row empty cells.
        # Rowspans for empty structural cells (level-indent placeholders) make
        # the rendered HTML inconsistent across rows — browsers auto-size cols
        # based on which rows declare cells at that col, so rows inside the
        # rowspan render at different x positions than rows that introduce
        # their own placeholder. Materialising each placeholder as a real <td>
        # gives every row the same cell count at every col and keeps the
        # visual alignment consistent.
        for r in range(n_rows):
            for c in range(n_cols):
                cell = grid[r][c]
                if not (isinstance(cell, dict) and not cell["text"] and cell["rs"] > 1 and cell["cs"] == 1):
                    continue
                for rr in range(r + 1, min(r + cell["rs"], n_rows)):
                    if grid[rr][c] == "x":
                        grid[rr][c] = {"text": "", "cs": 1, "rs": 1}
                cell["rs"] = 1

        non_phantom = [c for c in range(n_cols) if c not in phantom_cols]
        non_empty_cols = [c for c in non_phantom if any(
            isinstance(grid[r][c], dict) and grid[r][c]["text"]
            for r in range(n_rows)
        )]

        def esc(s):
            return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        # Text box: only 1 non-empty column (e.g. bordered reading passage).
        # Render as a simple single-column table preserving the visual border.
        if len(non_empty_cols) == 1:
            tc = non_empty_cols[0]
            out = ["<table>"]
            for r in range(n_rows):
                cell = grid[r][tc]
                if isinstance(cell, dict) and cell["text"]:
                    out.append(f"<tr><td>{esc(cell['text'])}</td></tr>")
            out.append("</table>")
            return "\n".join(out)

        # Detect structural columns: non-phantom cols that appear as span anchors
        # (cs>1) in some rows but never have independent (cs=1) content.
        # In such columns, rows with empty cs=1 cells are alignment artifacts
        # caused by colspan cells in other row groups → merge into adjacent cell.
        # For multi-page tables, callers pass a unified structural set that
        # accounts for spans on other pages (a col may be span-only on one
        # page but empty on another).
        if override_structural_cols is not None:
            structural_cols = set(override_structural_cols) - phantom_cols
        else:
            structural_cols = set()
            for c in non_phantom:
                has_cs1 = any(
                    isinstance(grid[r][c], dict) and grid[r][c]["text"] and grid[r][c]["cs"] == 1
                    for r in range(n_rows)
                )
                has_span = any(
                    isinstance(grid[r][c], dict) and grid[r][c]["text"] and grid[r][c]["cs"] > 1
                    for r in range(n_rows)
                )
                if not has_cs1 and has_span:
                    structural_cols.add(c)

        for r in range(n_rows):
            for c in range(n_cols - 1):
                if c not in structural_cols:
                    continue
                cell = grid[r][c]
                if not (isinstance(cell, dict) and not cell["text"] and cell["cs"] == 1):
                    continue
                nc = c + 1
                while nc < n_cols and nc in phantom_cols:
                    nc += 1
                if nc >= n_cols or not isinstance(grid[r][nc], dict):
                    continue
                next_cell = grid[r][nc]
                if next_cell["text"] and cell["rs"] == next_cell["rs"]:
                    grid[r][c] = {"text": next_cell["text"], "cs": nc - c + next_cell["cs"], "rs": next_cell["rs"]}
                    grid[r][nc] = "x"

        # Drop rows where no cell carries actual text. PDFs often produce
        # phantom horizontal grid lines that create empty filler rows between
        # data rows; these are pure noise. Shrink rowspans of cells whose span
        # crosses dropped rows so the visible table still aligns.
        keep_row = [
            any(isinstance(grid[r][c], dict) and grid[r][c]["text"]
                for c in range(n_cols))
            for r in range(n_rows)
        ]
        for r in range(n_rows):
            if not keep_row[r]:
                continue
            for c in range(n_cols):
                cell = grid[r][c]
                if isinstance(cell, dict) and cell["rs"] > 1:
                    new_rs = sum(
                        1 for rr in range(r, min(r + cell["rs"], n_rows))
                        if keep_row[rr]
                    )
                    cell["rs"] = max(1, new_rs)

        # Full table: skip phantom columns, adjust colspan to exclude them.
        # For None cells (no anchor) in non-phantom columns, emit empty <td>
        # so subsequent cells stay in their correct visual column.
        out = ["<table>"]
        for r in range(n_rows):
            if not keep_row[r]:
                continue
            row = grid[r]
            out.append("<tr>")
            for c, cell in enumerate(row):
                if cell == "x":
                    continue
                if c in phantom_cols:
                    if cell is None or not (isinstance(cell, dict) and cell["text"]):
                        continue
                if cell is None:
                    out.append('  <td></td>')
                    continue
                effective_cs = sum(
                    1 for cc in range(c, c + cell["cs"]) if cc not in phantom_cols
                )
                if effective_cs <= 0:
                    effective_cs = 1
                attrs = ""
                if effective_cs > 1:
                    attrs += f' colspan="{effective_cs}"'
                if cell["rs"] > 1:
                    attrs += f' rowspan="{cell["rs"]}"'
                out.append(f'  <td{attrs}>{esc(cell["text"])}</td>')
            out.append("</tr>")
        out.append("</table>")
        return "\n".join(out)

    except Exception:
        return _render_table(extracted)


def _normalize_artifact_text(text):
    """Normalize header/footer text for cross-page comparison:
    - unify circled-C variants
    - strip year tokens (e.g. "2023", "2016-2023")
    - drop standalone digit tokens (page numbers)
    - strip leading digit runs glued to letters ("72All" → "All")
    Embedded digits inside other words are kept so "Chapter 1" stays distinct."""
    text = text.replace("ⓒ", "©").replace("Ⓒ", "©")
    text = re.sub(r"\b(?:19|20)\d{2}(?:[-–]\d{4})?\b", "", text)
    tokens = []
    for t in re.split(r"\s+", text):
        if not t:
            continue
        if re.fullmatch(r"[\d０-９]+", t):
            continue
        m = re.match(r"^[\d０-９]+([A-Za-z　-鿿].*)", t)
        if m:
            t = m.group(1)
        if t:
            tokens.append(t)
    return " ".join(tokens).strip()


def _collect_repeating_artifacts(pages_lines, n_pages, top_ratio=0.15, bottom_ratio=0.12, min_ratio=0.10):
    """Find lines that repeat across many pages in the top/bottom margins.
    Returns a set of normalized texts to drop."""
    if n_pages < 3:
        return set()
    top_counts = Counter()
    bot_counts = Counter()
    for page_idx, (lines, ph) in enumerate(pages_lines):
        seen_top = set()
        seen_bot = set()
        for l in lines:
            t = l["text"].strip()
            if not t or len(t) < 2:
                continue
            norm = _normalize_artifact_text(t)
            if not norm or len(norm) < 2:
                continue
            if l["top"] < ph * top_ratio:
                seen_top.add(norm)
            elif l["bottom"] > ph * (1 - bottom_ratio):
                seen_bot.add(norm)
        for n in seen_top:
            top_counts[n] += 1
        for n in seen_bot:
            bot_counts[n] += 1
    threshold = max(2, int(n_pages * min_ratio))
    artifacts = set()
    for n, c in top_counts.items():
        if c >= threshold:
            artifacts.add(n)
    for n, c in bot_counts.items():
        if c >= threshold:
            artifacts.add(n)
    return artifacts


def _is_artifact_line(line, ph, artifacts, top_ratio=0.15, bottom_ratio=0.12):
    if not artifacts:
        return False
    in_top = line["top"] < ph * top_ratio
    in_bot = line["bottom"] > ph * (1 - bottom_ratio)
    if not (in_top or in_bot):
        return False
    text = line["text"].strip()
    # Don't drop numeric-prefixed headings — those are real section headings
    # repeated as running headers, useful for structure.
    if NUMERIC_HEADING_RE.match(text):
        return False
    norm = _normalize_artifact_text(text)
    return norm in artifacts


def _merge_continuation_tables(markdown):
    """Merge consecutive <table> blocks for the same logical PDF table — they
    typically span multiple pages with header rows repeating. Keep the first
    table's header (rows up to and including the column header row), then
    append only data rows from the following tables."""
    pattern = re.compile(r"<table>([\s\S]*?)</table>", re.MULTILINE)
    fname_re = re.compile(r"<td[^>]*>ファイル名\s*\n?\s*[（\(](.+?)[）\)]")
    # Inline parens variant: ファイル名 cell may be empty, parens in next cell.
    paren_only_re = re.compile(r"<td[^>]*>[（\(]([^）\)]{1,40})[）\)]</td>")
    title_re = re.compile(
        r"<td[^>]*>(レコード項目定義|拡張外部公開ファイル一覧|JRA-DB取扱い一覧|"
        r"地方競馬共同TZS.*?タイミング|ファイル構造図.*?)</td>"
    )
    tables = list(pattern.finditer(markdown))
    if len(tables) < 2:
        return markdown

    def get_id(body):
        """Identifier = (table_title, fname). Either may be None.
        Two tables match if both fields agree and at least one is non-None."""
        title_m = title_re.search(body)
        title = title_m.group(1).strip() if title_m else None
        fname_m = fname_re.search(body)
        fname = fname_m.group(1).strip() if fname_m else None
        if fname is None:
            # Fallback: first parenthesised value that looks like a file name
            # (Japanese, contains no slashes etc.). Used for continuation pages
            # where the ファイル名 cell is split across multiple table cells.
            for pm in paren_only_re.finditer(body[:1500]):
                v = pm.group(1).strip()
                if v and v not in ("地方競馬共同TZS", " ", "ＪＲＡ－ＤＢファイル"):
                    fname = v
                    break
        return (title, fname)

    def split_header_data(body):
        """Return (header_rows_text, [data_rows_html]).
        Header rows = rows until and including the row that contains "項番"
        or "区分" (column header). Data rows = everything after."""
        rows = re.findall(r"<tr>[\s\S]*?</tr>", body)
        if not rows:
            return body, []
        header_end = 0
        for i, r in enumerate(rows):
            if re.search(r"<td[^>]*>(?:項番|区分|Ｎｏ\.|No\.|項\s*目)</td>", r):
                header_end = i + 1
                break
        if header_end == 0:
            # Fallback: assume first 2 rows are headers
            header_end = min(2, len(rows))
        return rows[:header_end], rows[header_end:]

    # Group consecutive tables with the same (title, fname) identifier.
    out_parts = []
    last_end = 0
    i = 0
    while i < len(tables):
        m = tables[i]
        body = m.group(1)
        cur_id = get_id(body)
        title, fname = cur_id
        if not title and not fname:
            out_parts.append(markdown[last_end:m.end()])
            last_end = m.end()
            i += 1
            continue
        # Find run of consecutive same-id tables (only allow whitespace between)
        run = [i]
        j = i + 1
        while j < len(tables):
            next_m = tables[j]
            between = markdown[tables[j-1].end():next_m.start()]
            if between.strip():
                break
            n_title, n_fname = get_id(next_m.group(1))
            # Both tables must agree on both title and fname (when present).
            # An unknown fname (None) on a follow-up table is NOT treated as
            # a match — it would let unrelated tables collapse into the run.
            same_title = (title or "") == (n_title or "")
            same_fname = (fname or "") == (n_fname or "") and fname is not None
            if not (same_title and same_fname):
                break
            run.append(j)
            j += 1
        if len(run) == 1:
            out_parts.append(markdown[last_end:m.end()])
            last_end = m.end()
            i += 1
            continue
        # Build merged table
        first_body = tables[run[0]].group(1)
        head_rows, first_data = split_header_data(first_body)
        merged_data = list(first_data)
        for k in run[1:]:
            _, data = split_header_data(tables[k].group(1))
            merged_data.extend(data)
        merged_body = "\n".join(head_rows + merged_data)
        merged_html = f"<table>\n{merged_body}\n</table>"
        # Emit content up to first table's start, then merged
        out_parts.append(markdown[last_end:m.start()])
        out_parts.append(merged_html)
        last_end = tables[run[-1]].end()
        i = run[-1] + 1
    out_parts.append(markdown[last_end:])
    return "".join(out_parts)


def _dedupe_consecutive_headings(lines):
    """Drop heading repeats from running headers. A heading at level L is
    skipped when the most recent heading at level L (within the same parent
    scope — i.e. no higher-level heading has appeared since) has the same
    text. The trailing blank line emitted with the heading is also skipped."""
    out = []
    latest = {}  # level → heading line text
    skip_next_blank = False
    for l in lines:
        if skip_next_blank:
            skip_next_blank = False
            if l == "":
                continue
        m = re.match(r"^(#+)\s+(.*)$", l)
        if m:
            lvl = len(m.group(1))
            if latest.get(lvl) == l:
                skip_next_blank = True
                continue
            # New heading at level lvl → invalidate deeper levels.
            for L in list(latest):
                if L >= lvl:
                    latest.pop(L, None)
            latest[lvl] = l
        out.append(l)
    return out


def _line_inside_any_bbox(line, bboxes):
    cx = (line["x0"] + line["x1"]) / 2
    cy = (line["top"] + line["bottom"]) / 2
    for x0, y0, x1, y1 in bboxes:
        if x0 <= cx <= x1 and y0 <= cy <= y1:
            return True
    return False


_FNAME_RE = re.compile(r"ファイル名\s*\n?\s*[（(]([^（()）]+)[）)]")
_TITLE_VALUES = ("レコード項目定義", "拡張外部公開ファイル一覧", "JRA-DB取扱い一覧")


def _table_identifier(rows):
    """Extract (title, file_name) from the first rows of a レコード項目定義-style
    table. Used to group continuation pages so they can share a unified
    x-boundary list and stay aligned after the HTML-level merge."""
    if not rows:
        return (None, None)
    title = None
    fname = None
    for row in rows[:2]:
        for cell in row:
            if not cell:
                continue
            t = cell.strip()
            if not title and t in _TITLE_VALUES:
                title = t
            if not fname:
                m = _FNAME_RE.search(t)
                if m:
                    fname = m.group(1).strip()
    return (title, fname)


def _unified_xs(xs_list, tol=2):
    """Merge multiple xs sequences into one sorted list, treating values
    within `tol` points as the same boundary (keeps the average)."""
    all_xs = sorted(x for xs in xs_list for x in xs)
    if not all_xs:
        return []
    out = [all_xs[0]]
    for x in all_xs[1:]:
        if x - out[-1] <= tol:
            out[-1] = (out[-1] + x) / 2
        else:
            out.append(x)
    return out


def _build_grid(tbl, extracted, xs, ys):
    """Build the {dict|"x"|None} grid from a pdfplumber table snapped to
    the given xs/ys."""
    n_rows = max(1, len(ys) - 1)
    n_cols = max(1, len(xs) - 1)
    grid = [[None] * n_cols for _ in range(n_rows)]
    for r_phys, tbl_row in enumerate(tbl.rows):
        ex_row = extracted[r_phys] if r_phys < len(extracted) else []
        for c_phys, cell in enumerate(tbl_row.cells):
            if cell is None:
                continue
            r0, c0, rs, cs = _cell_spans(cell, xs, ys)
            if not (0 <= r0 < n_rows and 0 <= c0 < n_cols):
                continue
            text = (ex_row[c_phys] if c_phys < len(ex_row) else None) or ""
            grid[r0][c0] = {"text": text.strip(), "cs": cs, "rs": rs}
            for rr in range(r0, min(r0 + rs, n_rows)):
                for cc in range(c0, min(c0 + cs, n_cols)):
                    if rr != r0 or cc != c0:
                        grid[rr][cc] = "x"
    return grid


def _phantom_cols_for_grid(grid, n_rows, n_cols):
    phantom = set()
    for c in range(n_cols):
        col_cells = [grid[r][c] for r in range(n_rows)]
        if any(isinstance(cell, dict) and cell["text"] for cell in col_cells):
            continue
        non_x = [cell for cell in col_cells if cell != "x"]
        if not non_x:
            phantom.add(c)
            continue
        if all(isinstance(cell, dict) and not cell["text"] for cell in non_x):
            coverage = sum(cell["rs"] for cell in non_x)
            if coverage >= n_rows * 0.5:
                continue
        empty = sum(1 for cell in non_x
                    if cell is None or (isinstance(cell, dict) and not cell["text"]))
        if empty / len(non_x) >= 0.8:
            phantom.add(c)
    return phantom


def _compute_table_unified_xs(tables_data):
    """Group consecutive tables by (title, file_name) identifier. For each
    group with >1 table, attach a unified xs list and a shared phantom_cols
    set (intersection across all pages of the run) to each member, so the
    rendered rows stay aligned after the HTML-level merge."""
    # Flatten to a list in page order
    flat = []
    for pd in tables_data:
        for tbl in pd["tables"]:
            flat.append(tbl)
    if len(flat) < 2:
        return
    i = 0
    while i < len(flat):
        ident = _table_identifier(flat[i]["rows"])
        if not (ident[0] or ident[1]):
            i += 1
            continue
        run = [i]
        j = i + 1
        while j < len(flat):
            nid = _table_identifier(flat[j]["rows"])
            same_title = (ident[0] or "") == (nid[0] or "")
            same_fname = (ident[1] or "") == (nid[1] or "") and ident[1] is not None
            if not (same_title and same_fname):
                break
            run.append(j)
            j += 1
        if len(run) > 1:
            xs_list = [_grid_bounds(flat[k]["tbl"])[0] for k in run]
            uxs = _unified_xs(xs_list)
            # Compute phantom_cols per page, take intersection: a col is only
            # phantom if it's phantom on every page of the run. This prevents
            # a col that has real content on one page (e.g., level-5 items
            # only on later pages) from being dropped on the others.
            # Also compute structural_cols across the union of all pages:
            # a col is structural when, across the whole run, it only ever
            # carries cs>1 anchors and never standalone cs=1 content. Without
            # this, a page that has only empty cells at the col would miss
            # the merge of empty + next-cell, breaking visual alignment.
            shared_phantom = None
            has_cs1_run = [False] * (len(uxs) - 1)
            has_span_run = [False] * (len(uxs) - 1)
            for k in run:
                tbl = flat[k]["tbl"]
                _, ys = _grid_bounds(tbl)
                n_rows_k = max(1, len(ys) - 1)
                n_cols_k = max(1, len(uxs) - 1)
                grid = _build_grid(tbl, flat[k]["rows"], uxs, ys)
                ph = _phantom_cols_for_grid(grid, n_rows_k, n_cols_k)
                shared_phantom = ph if shared_phantom is None else (shared_phantom & ph)
                for c in range(n_cols_k):
                    for r in range(n_rows_k):
                        cell = grid[r][c]
                        if not (isinstance(cell, dict) and cell["text"]):
                            continue
                        if cell["cs"] == 1:
                            has_cs1_run[c] = True
                        elif cell["cs"] > 1:
                            has_span_run[c] = True
            shared_structural = {
                c for c in range(len(uxs) - 1)
                if has_span_run[c] and not has_cs1_run[c]
            }
            for k in run:
                flat[k]["override_xs"] = uxs
                flat[k]["override_phantom_cols"] = shared_phantom or set()
                flat[k]["override_structural_cols"] = shared_structural
        i = j if j > i else i + 1


def convert(pdf_bytes):
    pdf = pdfplumber.open(io.BytesIO(pdf_bytes))
    try:
        pages_data = []
        total_tables = 0
        all_chars = []

        raw_pages_lines = []  # (lines, page_height) per page — for artifact detection
        for page in pdf.pages:
            raw_blank_boxes = _detect_blank_boxes(page)
            chars = _filter_vertical_chars(page.chars, page.height)
            all_chars.extend(chars)
            lines = _chars_to_lines(chars)
            lines = _filter_page_artifacts(lines, page.height)
            raw_pages_lines.append((lines, page.height))

            try:
                pdfp_tables = page.find_tables(table_settings=TABLE_SETTINGS)
                # Re-run with text-row detection for any table whose bbox stops
                # well above the page bottom — multi-page tables often lack the
                # final horizontal border so the line strategy clips rows.
                if pdfp_tables:
                    extended = []
                    for t in pdfp_tables:
                        below = page.height - t.bbox[3]
                        if below > 30:
                            t_alt = next((
                                u for u in page.find_tables(table_settings=TABLE_SETTINGS_TEXT_ROWS)
                                if abs(u.bbox[0] - t.bbox[0]) < 5 and abs(u.bbox[2] - t.bbox[2]) < 5
                                and u.bbox[3] > t.bbox[3]
                            ), None)
                            extended.append(t_alt if t_alt is not None else t)
                        else:
                            extended.append(t)
                    pdfp_tables = extended
                pdfp_tables = _filter_nested_tables(pdfp_tables)
            except Exception:
                pdfp_tables = []

            # Filter blank boxes: remove those that are table cell borders.
            # Small rectangles detected inside table bboxes are cell borders, not answer blanks.
            tbl_regions = [tbl.bbox for tbl in pdfp_tables]
            blank_boxes = [
                b for b in raw_blank_boxes
                if not any(
                    b[0] >= tr[0] - 1 and b[2] <= tr[2] + 1
                    and b[1] >= tr[1] - 1 and b[3] <= tr[3] + 1
                    for tr in tbl_regions
                )
            ]

            lines = _apply_blank_markers(lines, blank_boxes)

            tables = []
            table_bboxes = []
            for tbl in pdfp_tables:
                rows = tbl.extract()
                if not rows or len(rows) < 2:
                    continue
                n_cols = max(len(r) for r in rows)
                if n_cols < 2:
                    continue
                rows = [[_collapse_vertical_cell((c or "").strip()) for c in r] for r in rows]
                rows = _merge_split_cells_up(rows)
                if not any(any(c for c in r) for r in rows):
                    continue
                # Skip phantom tables: tables where every non-empty cell
                # contains a fragment repeated >50% (a signature of vertical
                # text or diagram labels misinterpreted as a table).
                meaningful_cells = 0
                for r in rows:
                    for c in r:
                        if not c or len(c) <= 2:
                            continue
                        parts = c.split("\n") if "\n" in c else [c]
                        parts = [p.strip() for p in parts if p.strip()]
                        if len(parts) >= 2 and len(set(parts)) <= len(parts) / 2:
                            continue  # duplicated fragments
                        meaningful_cells += 1
                if meaningful_cells < 4:
                    continue
                tables.append({"rows": rows, "bbox": tuple(tbl.bbox), "tbl": tbl,
                               "page": page, "blank_boxes": blank_boxes})
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

        artifacts = _collect_repeating_artifacts(raw_pages_lines, len(raw_pages_lines))
        for pd in pages_data:
            pd["lines"] = [l for l in pd["lines"] if not _is_artifact_line(l, pd["height"], artifacts)]

        _compute_table_unified_xs(pages_data)

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

            prev_bottom = None  # Bottom Y of previous emitted text line on this page.
            for item in items:
                if item["kind"] == "table":
                    tbl_data = item["data"]
                    tbl_obj = tbl_data.get("tbl")
                    if tbl_obj and _is_complex_table(tbl_obj):
                        out.append(_render_html_table(
                            tbl_obj, tbl_data["rows"],
                            tbl_data["page"], tbl_data["blank_boxes"],
                            override_xs=tbl_data.get("override_xs"),
                            override_phantom_cols=tbl_data.get("override_phantom_cols"),
                            override_structural_cols=tbl_data.get("override_structural_cols"),
                        ))
                    else:
                        out.append(_render_table(tbl_data["rows"]))
                    out.append("")
                    prev_bottom = None
                    continue
                ln = item["data"]
                text = ln["text"].strip()
                if not text:
                    continue
                lvl = _heading_level(ln["size"], body)
                # Lines ending with a sentence terminator are descriptive text,
                # not headings (even if pdfplumber reports a slightly larger
                # font from inline formatting).
                if lvl and text.rstrip().endswith(("。", ".", "．")):
                    lvl = 0
                # Size-only headings additionally require a visual gap above —
                # real headings start a new block. Use the PDF Y coords: if the
                # gap to the previous line is less than ~0.6× line height, this
                # line is a continuation, not a heading.
                if lvl and prev_bottom is not None:
                    gap = ln["top"] - prev_bottom
                    line_h = max(ln["bottom"] - ln["top"], body)
                    if gap < line_h * 0.6:
                        lvl = 0
                # Numeric heading override: "1.", "1.1", "1.1.1" with short
                # text become headings with level matching dot-depth.
                # Multi-segment (≥2 dots) is unambiguously a heading; single
                # segment "1." also requires slightly larger font to avoid
                # confusing with ordered list items.
                nm = NUMERIC_HEADING_RE.match(text)
                if (nm and len(text) <= 100
                        and not text.rstrip().endswith(("。", ".", "．"))):
                    segs = [s for s in re.split(r"[\.．]", nm.group(1)) if s]
                    if len(segs) >= 2 or ln["size"] >= body * 1.05:
                        plvl = min(max(len(segs), 1), 4)
                        lvl = plvl if not lvl else min(lvl, plvl)
                if lvl:
                    out.append("#" * lvl + " " + text)
                    out.append("")
                    prev_bottom = ln["bottom"]
                    continue
                m = BULLET_RE.match(text)
                if m:
                    out.append("- " + m.group(1))
                    prev_bottom = ln["bottom"]
                    continue
                m = ORDERED_RE.match(text)
                if m:
                    out.append(f"{m.group(1)}. {m.group(2)}")
                    prev_bottom = ln["bottom"]
                    continue
                if all(_is_mono(c.get("fontname", "")) for c in ln["chars"]):
                    out.append("    " + text)
                    prev_bottom = ln["bottom"]
                    continue
                out.append(text)
                prev_bottom = ln["bottom"]
            out.append("")

        out = _dedupe_consecutive_headings(out)
        markdown = "\n".join(out).rstrip() + "\n"
        markdown = _merge_continuation_tables(markdown)
        return {
            "markdown": markdown,
            "pages": len(pages_data),
            "tables": total_tables,
        }
    finally:
        pdf.close()
