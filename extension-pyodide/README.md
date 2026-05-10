# PDF to MD — Chrome Extension

Convert any PDF tab to Markdown, entirely inside your browser.  
Chuyển đổi PDF sang Markdown ngay trong trình duyệt, không cần server.

---

## English

### What it does

Opens a PDF tab in Chrome → click the extension → downloads a `.md` file. No files leave your machine.

Powered by **pdfplumber** (Python) running inside **Pyodide** (WebAssembly). Same conversion quality as a Python backend.

### Features

- 100% local — no uploads, no servers, no account required
- Extracts text, headings, bullet lists, numbered lists, tables
- Multi-column layout reordering
- Page number / header / footer filtering
- Complex tables rendered as HTML (`colspan`, `rowspan`)
- Multilingual: Latin, Vietnamese, Chinese, Japanese, Korean

### Install (Developer Mode)

1. Go to `chrome://extensions/`
2. Enable **Developer mode** (top-right toggle)
3. Click **Load unpacked** → select the `extension-pyodide/` folder
4. Open any PDF tab → click the extension icon → **Download as .md**

> First load takes 5–8 seconds (Pyodide initialising). Subsequent opens are instant.

### Requirements

- Chrome 116 or later

### Bundle size

~22 MB. Breakdown:

| Component | Size | Purpose |
|---|---|---|
| `pyodide.asm.wasm` | 8.3 MB | Python interpreter (WebAssembly) |
| `pdfminer_six.whl` | 6.3 MB | PDF parsing engine |
| `python_stdlib.zip` | 2.3 MB | Python standard library |
| `pyodide.asm.js` | 1.0 MB | JS ↔ WASM glue |
| Everything else | ~4 MB | Dependencies |

### Limitations

- **No OCR** — scanned/image-only PDFs produce no text
- **No SVG/diagrams** — visual elements are skipped
- **Encrypted PDFs** — not supported; shows an error message

### Project structure

```
extension-pyodide/
├── manifest.json          # MV3, Chrome 116+
├── popup.html / popup.js  # Extension UI
├── offscreen.html / offscreen.js  # Pyodide runtime
├── background.js          # Service worker
├── python/
│   └── converter.py       # PDF → Markdown logic
├── lib/
│   ├── pyodide/           # Pyodide core (~12 MB)
│   └── wheels/            # Pre-bundled Python packages (~7 MB)
└── icons/
```

---

## Tiếng Việt

### Chức năng

Mở tab PDF trong Chrome → bấm vào extension → tải file `.md` về máy. Toàn bộ xử lý diễn ra cục bộ, không có byte nào rời khỏi máy tính của bạn.

Sử dụng **pdfplumber** (Python) chạy bên trong **Pyodide** (WebAssembly) — chất lượng chuyển đổi tương đương backend Python thực sự.

### Tính năng

- 100% cục bộ — không upload, không server, không cần tài khoản
- Trích xuất văn bản, tiêu đề, danh sách, bảng biểu
- Tự động sắp xếp lại bố cục nhiều cột
- Lọc số trang, header, footer
- Bảng phức tạp được render HTML (`colspan`, `rowspan`)
- Đa ngôn ngữ: Latin, Tiếng Việt, Trung, Nhật, Hàn

### Cài đặt (Developer Mode)

1. Mở `chrome://extensions/`
2. Bật **Developer mode** (góc trên bên phải)
3. Bấm **Load unpacked** → chọn thư mục `extension-pyodide/`
4. Mở tab PDF bất kỳ → bấm icon extension → **Download as .md**

> Lần đầu mở mất 5–8 giây để Pyodide khởi động. Các lần sau nhanh hơn nhờ cache.

### Yêu cầu

- Chrome 116 trở lên

### Giới hạn

- **Không có OCR** — PDF scan (toàn hình ảnh) sẽ không trích xuất được text
- **Không xử lý SVG/biểu đồ** — bỏ qua các thành phần đồ họa
- **PDF mã hoá** — không hỗ trợ, sẽ hiện thông báo lỗi

---

with ❤ by **[Poji](https://www.facebook.com/po.jii01)**
