import { loadPyodide } from "./lib/pyodide/pyodide.mjs";
import {
  PYODIDE_PACKAGES,
  CUSTOM_WHEELS,
  WHEELS_EMFS_DIR,
  IDLE_TEARDOWN_MS,
  getURLBase,
  getPyodideIndexURL,
  getConverterURL,
} from "./constants.js";

let pyodide = null;
let ready = false;
let idleTimer = null;

function broadcast(msg) {
  chrome.runtime.sendMessage(msg).catch(() => {});
}

function scheduleIdleTeardown() {
  if (idleTimer) clearTimeout(idleTimer);
  idleTimer = setTimeout(() => {
    chrome.runtime.sendMessage({ type: "close-offscreen" }).catch(() => {});
  }, IDLE_TEARDOWN_MS);
}

async function init() {
  const t0 = performance.now();
  try {
    broadcast({ target: "popup", type: "progress", message: "Loading Python runtime…" });
    pyodide = await loadPyodide({
      indexURL: getPyodideIndexURL(),
      fullStdLib: false,
    });

    broadcast({ target: "popup", type: "progress", message: "Loading PDF libraries…" });
    await pyodide.loadPackage(PYODIDE_PACKAGES);

    broadcast({ target: "popup", type: "progress", message: "Installing PDF parsers…" });
    try { pyodide.FS.mkdirTree(WHEELS_EMFS_DIR); } catch (_) {}
    const emfsPaths = [];
    for (const w of CUSTOM_WHEELS) {
      const fname = w.split("/").pop();
      const buf = await fetch(getURLBase() + w).then((r) => r.arrayBuffer());
      pyodide.FS.writeFile(`${WHEELS_EMFS_DIR}/${fname}`, new Uint8Array(buf));
      emfsPaths.push(`emfs:${WHEELS_EMFS_DIR}/${fname}`);
    }
    pyodide.globals.set("_wheels", emfsPaths);
    await pyodide.runPythonAsync(`
import micropip
await micropip.install(list(_wheels), deps=False)
`);

    broadcast({ target: "popup", type: "progress", message: "Almost ready…" });
    const code = await fetch(getConverterURL()).then((r) => r.text());
    pyodide.runPython(code);

    ready = true;
    broadcast({ target: "popup", type: "ready" });
    console.log(`[pyodide] loaded: ${(performance.now() - t0).toFixed(0)}ms`);
    scheduleIdleTeardown();
  } catch (e) {
    const msg = String(e?.message || e || "");
    let hint;
    if (/memory|oom/i.test(msg)) {
      hint = "Not enough memory to load Python runtime — try closing other tabs.";
    } else if (/wasm/i.test(msg)) {
      hint = "WebAssembly failed — Chrome 116+ required. Please update your browser.";
    } else if (/fetch|load|network/i.test(msg)) {
      hint = "Failed to load required files — the extension may be corrupted. Try reinstalling.";
    } else {
      hint = "Failed to start Python runtime — try reloading the extension.";
    }
    broadcast({ target: "popup", type: "error", message: hint });
  }
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.target !== "offscreen") return false;

  if (msg.type === "get-status") {
    sendResponse({ ready });
    return false;
  }

  if (msg.type === "convert") {
    if (!ready) {
      broadcast({ target: "popup", type: "error", message: "Not ready yet — please wait." });
      return false;
    }
    (async () => {
      try {
        const data = Uint8Array.from(atob(msg.pdfBase64), (c) => c.charCodeAt(0));
        pyodide.globals.set("_pdf_bytes", data);
        const result = pyodide
          .runPython("convert(bytes(_pdf_bytes))")
          .toJs({ dict_converter: Object.fromEntries });
        pyodide.globals.delete("_pdf_bytes");

        const popupHandled = await new Promise((resolve) => {
          chrome.runtime.sendMessage(
            { target: "popup", type: "result", markdown: result.markdown, filename: msg.filename, pages: result.pages, tables: result.tables },
            (resp) => resolve(!chrome.runtime.lastError && resp?.ok)
          );
        });

        if (!popupHandled) {
          const bytes = new TextEncoder().encode(result.markdown);
          const bin = Array.from(bytes, (b) => String.fromCharCode(b)).join("");
          chrome.runtime.sendMessage({
            type: "download",
            url: `data:text/markdown;charset=utf-8;base64,${btoa(bin)}`,
            filename: msg.filename,
          });
        }

        scheduleIdleTeardown();
      } catch (e) {
        const raw = String(e?.message || e || "");
        let errMsg;
        if (/PDFEncryption|password/i.test(raw)) {
          errMsg = "This PDF is password-protected. Remove the password first, then retry.";
        } else if (/PdfReadError|PDFSyntax|PdfStreamError|invalid pdf/i.test(raw)) {
          errMsg = "Cannot read this PDF — file may be corrupted. Try re-downloading it.";
        } else if (/MemoryError|oom/i.test(raw)) {
          errMsg = "Not enough memory — try a smaller PDF (recommended: under 50 pages).";
        } else if (/TypeError|AttributeError|KeyError/i.test(raw)) {
          errMsg = "Unsupported PDF format — conversion failed.";
        } else {
          errMsg = "Conversion failed — unsupported or damaged PDF.";
        }
        broadcast({ target: "popup", type: "error", message: errMsg });
      }
    })();
    return false;
  }

  return false;
});

init();
