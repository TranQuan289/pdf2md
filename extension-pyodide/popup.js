"use strict";

const $ = (id) => document.getElementById(id);
const status = (msg) => ($("status").textContent = msg);
const PDF_RE = /\.pdf(?:[?#]|$)/i;

let converting = false;
let progressTimer = null;
let progressPct = 0;

function startFakeProgress() {
  progressPct = 0;
  status("Converting… 0%");
  progressTimer = setInterval(() => {
    if (progressPct < 95) status(`Converting… ${++progressPct}%`);
  }, 500);
}

function stopFakeProgress() {
  if (progressTimer) { clearInterval(progressTimer); progressTimer = null; }
}

function setConverting(active) {
  converting = active;
  const btn = $("convert");
  btn.disabled = active;
  btn.setAttribute("aria-disabled", String(active));
  if (active) btn.setAttribute("aria-busy", "true");
  else btn.removeAttribute("aria-busy");
}

// ===== Filename detection =====
const UUID_RE = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i;
const isUuidLike = (s) => !s || UUID_RE.test(s);

const sanitizeStem = (s) =>
  (s || "")
    .replace(/[/\\:*?"<>|]/g, "_")
    .replace(/\s+/g, " ")
    .trim();

const cleanStem = (name) => {
  const stem = sanitizeStem(name).replace(/\.pdf$/i, "");
  return stem && !isUuidLike(stem) ? stem : null;
};

function tryUrlPath(url) {
  if (!url || url.startsWith("blob:")) return null;
  try {
    const u = new URL(url);
    const last = decodeURIComponent(u.pathname.split("/").pop() || "");
    return cleanStem(last);
  } catch { return null; }
}

function tryUrlParams(url) {
  if (!url) return null;
  try {
    const params = new URL(url).searchParams;
    for (const key of ["file", "filename", "name", "download", "doc", "pdf"]) {
      const v = params.get(key);
      if (!v) continue;
      const s = cleanStem(decodeURIComponent(v));
      if (s) return s;
    }
    return null;
  } catch { return null; }
}

function tryTabTitle(title) {
  if (!title) return null;
  let t = title.trim();
  t = t.replace(/\s*[-–—]\s*(Google Chrome|Mozilla Firefox|Brave|Edge|Safari)\s*$/i, "");
  t = t.replace(/\s*[-–—|]\s*(Google Drive|Dropbox|OneDrive|Notion|Box|GitHub)\s*$/i, "");
  t = t.replace(/^\s*(?:PDF Viewer|PDF|Loading)\s*[-–—:]\s*/i, "");
  t = t.replace(/^[\p{Emoji}\p{So}\p{Sk}]+\s*/u, "");
  t = t.replace(/^https?:\/\/[^/]+\//, "");
  return cleanStem(t);
}

async function tryOpenerTab(openerTabId) {
  if (!openerTabId) return null;
  try {
    const opener = await chrome.tabs.get(openerTabId);
    return tryTabTitle(opener.title);
  } catch { return null; }
}

function tryContentDisposition(header) {
  if (!header) return null;
  let m = header.match(/filename\*\s*=\s*[^']*'[^']*'([^;]+)/i);
  if (m) {
    try { return cleanStem(decodeURIComponent(m[1].trim().replace(/^"|"$/g, ""))); }
    catch { return cleanStem(m[1].trim().replace(/^"|"$/g, "")); }
  }
  m = header.match(/filename\s*=\s*"?([^";]+)"?/i);
  return m ? cleanStem(m[1]) : null;
}

function domainBasedFallback(url) {
  let domain = "";
  try {
    if (url && url.startsWith("blob:")) domain = new URL(url.slice(5)).hostname;
    else if (url) domain = new URL(url).hostname;
  } catch {}
  domain = (domain || "").replace(/^www\./, "").replace(/[^\w.-]/g, "");
  if (!domain) domain = "document";
  return `${domain}-${new Date().toISOString().slice(0, 10)}`;
}

function ensureValidStem(stem) {
  let s = (stem || "").trim().replace(/^[\s.-]+|[\s.-]+$/g, "");
  if (!s || /^[^a-zA-Z0-9À-￿]/.test(s)) return `document-${new Date().toISOString().slice(0, 10)}`;
  return s;
}

async function detectFilename(tab) {
  const strategies = [
    ["url-path", () => tryUrlPath(tab?.url)],
    ["url-params", () => tryUrlParams(tab?.url)],
    ["tab-title", () => tryTabTitle(tab?.title)],
    ["opener-tab", async () => await tryOpenerTab(tab?.openerTabId)],
  ];
  for (const [, fn] of strategies) {
    const result = await fn();
    if (result) return result;
  }
  return null;
}

// ===== PDF detection =====
async function detectIsPdf(url) {
  if (!url || url.startsWith("chrome://") || url.startsWith("chrome-extension://")) return false;
  if (PDF_RE.test(url) || url.startsWith("blob:")) return true;
  try {
    const r = await fetch(url, { method: "HEAD" });
    return (r.headers.get("content-type") || "").toLowerCase().includes("pdf");
  } catch { return false; }
}

// ===== Result handler =====
let displayStem = null;
let currentTabId = null;

function handleResult({ markdown, filename, pages, tables }, sendResponse) {
  const blob = new Blob([markdown], { type: "text/markdown;charset=utf-8" });
  const blobUrl = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = blobUrl;
  a.download = filename;
  a.rel = "noopener";
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  setTimeout(() => { document.body.removeChild(a); URL.revokeObjectURL(blobUrl); }, 1000);
  if (currentTabId) chrome.storage.session.remove(`stem_${currentTabId}`).catch(() => {});
  stopFakeProgress();
  status(`Done — ${pages} pages, ${tables} tables, ${markdown.length.toLocaleString()} chars.`);
  setConverting(false);
  sendResponse({ ok: true });
}

// ===== Messaging =====
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.target !== "popup") return false;
  switch (msg.type) {
    case "progress": status(msg.message); break;
    case "ready":
      status("Ready.");
      $("convert").disabled = false;
      $("convert").setAttribute("aria-disabled", "false");
      break;
    case "result":
      handleResult(msg, sendResponse);
      return true;
    case "error":
      stopFakeProgress();
      status(`Error: ${msg.message}`);
      if (converting) setConverting(false);
      break;
  }
  return false;
});

// ===== Filename cache (per tab, session-scoped) =====
async function loadCachedStem(tabId) {
  try {
    const data = await chrome.storage.session.get(`stem_${tabId}`);
    return data[`stem_${tabId}`] || null;
  } catch { return null; }
}

async function saveCachedStem(tabId, stem) {
  try { await chrome.storage.session.set({ [`stem_${tabId}`]: stem }); } catch {}
}

// ===== Main =====
async function init() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const url = tab?.url || "";
  currentTabId = tab?.id || null;

  displayStem = await loadCachedStem(currentTabId) || await detectFilename(tab);
  if (displayStem) saveCachedStem(currentTabId, displayStem);
  $("filename").textContent = displayStem
    ? `${displayStem}.pdf`
    : "(auto-detected after fetch)";

  const isPdf = await detectIsPdf(url);
  if (!isPdf) {
    status("Current tab is not a PDF.");
    return;
  }

  chrome.runtime.sendMessage({ type: "ensure-offscreen" });

  status("Checking Python runtime…");
  try {
    const resp = await chrome.runtime.sendMessage({ target: "offscreen", type: "get-status" });
    if (resp?.ready) {
      status("Ready.");
      $("convert").disabled = false;
      $("convert").setAttribute("aria-disabled", "false");
    } else {
      status("Loading Python runtime…");
    }
  } catch {
    status("Loading Python runtime…");
  }

  $("convert").addEventListener("click", async () => {
    setConverting(true);
    const controller = new AbortController();
    const fetchTimeout = setTimeout(() => controller.abort(), 120_000);
    try {
      status("Fetching PDF…");
      const resp = await fetch(url, { signal: controller.signal });
      clearTimeout(fetchTimeout);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

      if (!displayStem) {
        const fromCd = tryContentDisposition(resp.headers.get("content-disposition"));
        displayStem = fromCd || domainBasedFallback(url);
        $("filename").textContent = `${displayStem}.pdf`;
        saveCachedStem(currentTabId, displayStem);
      }

      const pdfBytes = await resp.arrayBuffer();
      const bytes = new Uint8Array(pdfBytes);
      let binary = "";
      for (let i = 0; i < bytes.length; i += 8192) {
        binary += String.fromCharCode.apply(null, bytes.subarray(i, i + 8192));
      }
      startFakeProgress();
      chrome.runtime.sendMessage({
        target: "offscreen",
        type: "convert",
        pdfBase64: btoa(binary),
        filename: `${ensureValidStem(displayStem)}.md`,
      });
    } catch (e) {
      clearTimeout(fetchTimeout);
      stopFakeProgress();
      const msg = e.name === "AbortError" ? "Fetch timed out (>2 min)." : e.message;
      status(`Error: ${msg}`);
      setConverting(false);
    }
  });
}

init();
