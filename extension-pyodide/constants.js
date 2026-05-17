export const PYODIDE_PACKAGES = ["micropip", "cryptography", "cffi", "pycparser"];

export const CUSTOM_WHEELS = [
  "lib/wheels/charset_normalizer-3.4.4-py3-none-any.whl",
  "lib/wheels/pdfminer_six-20260107-py3-none-any.whl",
  "lib/wheels/pdfplumber-0.10.4-py3-none-any.whl",
];
export const WHEELS_EMFS_DIR = "/tmp/wheels";

export const IDLE_TEARDOWN_MS = 30 * 60 * 1000;

export const STORAGE_KEY_HAS_USED = "hasUsedExtension";

export function getURLBase() { return chrome.runtime.getURL(""); }
export function getOffscreenURL() { return chrome.runtime.getURL("offscreen.html"); }
export function getPyodideIndexURL() { return chrome.runtime.getURL("lib/pyodide/"); }
export function getConverterURL() { return chrome.runtime.getURL("python/converter.py"); }
