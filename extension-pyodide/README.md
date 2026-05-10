# convert-md (Pyodide variant)

Chrome extension chạy **Python pdfplumber thật** trong browser qua Pyodide (CPython compile sang WebAssembly). 100% local, không upload — quality tương đương backend Python.

## So sánh với extension JS

| | `extension/` (JS) | `extension-pyodide/` (Python) |
|---|---|---|
| Engine | PDF.js + heuristics tự viết | **pdfplumber thật** (Python + pdfminer.six) |
| Bundle | ~2.9MB | **~21MB** |
| Startup | <1s | 3-8s lần đầu (cache sau) |
| Quality | ~80% | **~95%** (= Python backend) |
| Maintenance | Tôi maintain JS port | Update pdfplumber là tự động hưởng |

→ Chọn `extension-pyodide/` nếu cần quality cao và không quan tâm bundle size.
→ Chọn `extension/` nếu muốn bundle nhẹ + startup nhanh, chấp nhận quality thấp hơn.

## Cài đặt (developer mode)

1. `chrome://extensions/`
2. Bật **Developer mode**
3. **Load unpacked** → chọn thư mục `extension-pyodide/`
4. Lần đầu mở popup mất 5-8s để load Pyodide — sau đó cache, lần sau nhanh

## Cấu trúc

```
extension-pyodide/
├── manifest.json              # MV3 + CSP cho WASM
├── popup.{html,css,js}
├── lib/
│   ├── pyodide/               # Pyodide 0.29.4 core (~12MB)
│   │   ├── pyodide.mjs
│   │   ├── pyodide.asm.wasm   # Python interpreter compile sang WASM
│   │   ├── pyodide.asm.js
│   │   ├── python_stdlib.zip  # Python stdlib
│   │   └── pyodide-lock.json
│   └── wheels/                # Pre-bundled .whl (~9MB)
│       ├── pdfminer_six-*.whl
│       ├── pdfplumber-0.10.4.whl
│       ├── charset_normalizer-*.whl
│       ├── cryptography-*.whl
│       ├── cffi-*.whl
│       ├── pycparser-*.whl
│       └── pillow-*.whl
├── python/
│   └── converter.py           # Logic Python — port từ src/convertmd/
└── icons/
```

## Flow hoạt động

```
User click extension icon
  ↓
popup.js bootstrap:
  1. loadPyodide() ← load WASM + Python stdlib
  2. micropip.install(các wheel local)
  3. fetch(converter.py) → pyodide.runPython()
  ↓
User click "Tải dưới dạng .md"
  ↓
fetch(currentTabUrl) → arrayBuffer → bytes
  ↓
pyodide.runPython("convert(bytes)"):
  1. pdfplumber.open(io.BytesIO(bytes))
  2. Cho mỗi page: extract chars + tables
  3. Filter page-number artifacts
  4. Reorder columns (multi-column logic)
  5. Build markdown (heading/list/table)
  ↓
Blob → anchor[download] click → .md file
```

## Lưu ý kỹ thuật

- **CSP cho WASM**: manifest có `script-src 'self' 'wasm-unsafe-eval'` để Chrome cho phép Pyodide chạy.
- **Wheels offline**: tất cả `.whl` bundle local — không cần network sau khi cài extension.
- **pymupdf KHÔNG dùng** vì chưa có WASM build trong Pyodide. Chỉ dùng pdfplumber + pdfminer.six (đều pure Python). Vẫn cover được hầu hết tính năng.
- **Service worker không cần** — popup tự handle hết (Pyodide chạy trong popup context, mỗi lần mở popup load lại WASM, browser cache giữa lần).

## Tested

| PDF | Pyodide ext | Python convertmd | Match |
|---|---|---|---|
| sample_table.pdf | 1 table 4×3, "# Pricing Table" | giống | ✅ |
| Đồ án Việt 90 trang | 39 tables, 78K chars | 39 tables, 76K chars | ~95% |
| arXiv | (chưa test) | 8 tables | TBD |

## Limitation

- **Bundle to**: ~21MB extension. Lần đầu mở mất 5-8s.
- **PyMuPDF không có**: heading detection có thể kém precision so với Python convertmd vì pdfplumber chỉ cho size/font ở char level (đủ dùng nhưng không có font flag bold detection chính xác bằng PyMuPDF).
- **OCR không có**: pytesseract cần binary tesseract → không port được sang WASM.
- **First load slow**: Pyodide download + init mất vài giây. Cache giữa lần OK.
