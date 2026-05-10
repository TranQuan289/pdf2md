const OFFSCREEN_URL = chrome.runtime.getURL("offscreen.html");

async function ensureOffscreen() {
  const has = await chrome.offscreen.hasDocument();
  if (!has) {
    await chrome.offscreen.createDocument({
      url: OFFSCREEN_URL,
      reasons: ["WORKERS"],
      justification: "Run Pyodide Python runtime for PDF conversion",
    });
  }
}

chrome.runtime.onInstalled.addListener(ensureOffscreen);
chrome.runtime.onStartup.addListener(ensureOffscreen);

let _nextFilename = null;
chrome.downloads.onDeterminingFilename.addListener((item, suggest) => {
  if (_nextFilename) {
    suggest({ filename: _nextFilename, conflictAction: "uniquify" });
    _nextFilename = null;
  }
});

chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === "ensure-offscreen") ensureOffscreen();
  if (msg.type === "download") {
    _nextFilename = msg.filename;
    chrome.downloads.download({ url: msg.url, filename: msg.filename, saveAs: false });
  }
});
