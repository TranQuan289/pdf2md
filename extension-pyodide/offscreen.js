import { loadPyodide } from "./lib/pyodide/pyodide.mjs";

const URL_BASE = chrome.runtime.getURL("");
const PYODIDE_PACKAGES = ["micropip", "cryptography", "cffi", "Pillow", "pycparser"];
const CUSTOM_WHEELS = [
  "lib/wheels/charset_normalizer-3.4.4-py3-none-any.whl",
  "lib/wheels/pdfminer_six-20260107-py3-none-any.whl",
  "lib/wheels/pdfplumber-0.10.4-py3-none-any.whl",
];

let pyodide = null;
let ready = false;

function broadcast(msg) {
  chrome.runtime.sendMessage(msg).catch(() => {});
}

async function init() {
  try {
    broadcast({ target: "popup", type: "progress", message: "Đang load Python runtime (~12MB)..." });
    pyodide = await loadPyodide({ indexURL: URL_BASE + "lib/pyodide/" });

    broadcast({ target: "popup", type: "progress", message: "Đang load cryptography + cffi + pillow..." });
    await pyodide.loadPackage(PYODIDE_PACKAGES);

    broadcast({ target: "popup", type: "progress", message: "Đang cài pdfplumber + pdfminer.six..." });
    try { pyodide.FS.mkdirTree("/tmp/wheels"); } catch (_) {}
    const emfsPaths = [];
    for (const w of CUSTOM_WHEELS) {
      const fname = w.split("/").pop();
      const buf = await fetch(URL_BASE + w).then((r) => r.arrayBuffer());
      pyodide.FS.writeFile(`/tmp/wheels/${fname}`, new Uint8Array(buf));
      emfsPaths.push(`emfs:/tmp/wheels/${fname}`);
    }
    pyodide.globals.set("_wheels", emfsPaths);
    await pyodide.runPythonAsync(`
import micropip
await micropip.install(list(_wheels), deps=False)
`);

    broadcast({ target: "popup", type: "progress", message: "Đang load converter Python..." });
    const code = await fetch(URL_BASE + "python/converter.py").then((r) => r.text());
    pyodide.runPython(code);

    ready = true;
    broadcast({ target: "popup", type: "ready" });
  } catch (e) {
    broadcast({ target: "popup", type: "error", message: e.message });
  }
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.target !== "offscreen") return false;

  if (msg.type === "get-status") {
    sendResponse({ ready });
    return false;
  }

  if (msg.type === "convert") {
    if (!ready) {
      broadcast({ target: "popup", type: "error", message: "Pyodide chưa sẵn sàng." });
      return false;
    }
    (async () => {
      try {
        const data = Uint8Array.from(atob(msg.pdfBase64), (c) => c.charCodeAt(0));
        pyodide.globals.set("_pdf_bytes", data);
        const result = pyodide
          .runPython("convert(bytes(_pdf_bytes))")
          .toJs({ dict_converter: Object.fromEntries });

        const popupHandled = await new Promise((resolve) => {
          chrome.runtime.sendMessage(
            { target: "popup", type: "result", markdown: result.markdown, filename: msg.filename, pages: result.pages, tables: result.tables },
            (resp) => resolve(!chrome.runtime.lastError && resp?.ok)
          );
        });

        if (!popupHandled) {
          const enc = new TextEncoder().encode(result.markdown);
          let bin = "";
          for (let i = 0; i < enc.length; i++) bin += String.fromCharCode(enc[i]);
          chrome.runtime.sendMessage({
            type: "download",
            url: `data:text/markdown;charset=utf-8;base64,${btoa(bin)}`,
            filename: msg.filename,
          });
        }
      } catch (e) {
        broadcast({ target: "popup", type: "error", message: e.message });
      }
    })();
    return false;
  }

  return false;
});

init();
