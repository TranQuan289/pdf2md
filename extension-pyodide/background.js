import { STORAGE_KEY_HAS_USED, getOffscreenURL } from "./constants.js";

async function ensureOffscreen() {
  const has = await chrome.offscreen.hasDocument();
  if (!has) {
    await chrome.offscreen.createDocument({
      url: getOffscreenURL(),
      reasons: ["WORKERS"],
      justification: "Run Pyodide Python runtime for PDF conversion",
    });
  }
}

async function maybePrewarmOffscreen() {
  const stored = await chrome.storage.local.get(STORAGE_KEY_HAS_USED);
  if (stored[STORAGE_KEY_HAS_USED]) {
    await ensureOffscreen();
  }
}

chrome.runtime.onInstalled.addListener(maybePrewarmOffscreen);
chrome.runtime.onStartup.addListener(maybePrewarmOffscreen);
maybePrewarmOffscreen();

let _nextFilename = null;
chrome.downloads.onDeterminingFilename.addListener((item, suggest) => {
  if (_nextFilename) {
    suggest({ filename: _nextFilename, conflictAction: "uniquify" });
    _nextFilename = null;
  }
});

chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === "ensure-offscreen") ensureOffscreen();
  if (msg.type === "close-offscreen") {
    chrome.offscreen.closeDocument().catch(() => {});
  }
  if (msg.type === "download") {
    _nextFilename = msg.filename;
    chrome.downloads.download({ url: msg.url, filename: msg.filename, saveAs: false });
  }
});
