import { describe, it, expect, vi, beforeEach } from "vitest";

// ─── Mock Chrome APIs ─────────────────────────────────────────────────────────
const chromeMock = {
  runtime: {
    getURL: vi.fn((p) => `chrome-extension://fake-id/${p}`),
    getManifest: vi.fn().mockReturnValue({ version: "0.1.3" }),
  },
};

vi.stubGlobal("chrome", chromeMock);

// Import AFTER stubbing chrome so module-scope code sees the mock
const {
  PYODIDE_PACKAGES,
  CUSTOM_WHEELS,
  WHEELS_EMFS_DIR,
  IDLE_TEARDOWN_MS,
  STORAGE_KEY_HAS_USED,
  getURLBase,
  getOffscreenURL,
  getPyodideIndexURL,
  getConverterURL,
} = await import("../extension-pyodide/constants.js");

// ─── Static constants ─────────────────────────────────────────────────────────
describe("static constants", () => {
  it("PYODIDE_PACKAGES does not include Pillow (removed in M1)", () => {
    expect(PYODIDE_PACKAGES).not.toContain("Pillow");
  });

  it("PYODIDE_PACKAGES includes required core packages", () => {
    expect(PYODIDE_PACKAGES).toContain("micropip");
    expect(PYODIDE_PACKAGES).toContain("cryptography");
    expect(PYODIDE_PACKAGES).toContain("cffi");
    expect(PYODIDE_PACKAGES).toContain("pycparser");
  });

  it("CUSTOM_WHEELS contains exactly 3 entries", () => {
    expect(CUSTOM_WHEELS).toHaveLength(3);
  });

  it("CUSTOM_WHEELS entries are .whl paths", () => {
    for (const w of CUSTOM_WHEELS) {
      expect(w).toMatch(/\.whl$/);
    }
  });

  it("CUSTOM_WHEELS includes pdfminer_six and pdfplumber", () => {
    const joined = CUSTOM_WHEELS.join(",");
    expect(joined).toContain("pdfminer_six");
    expect(joined).toContain("pdfplumber");
  });

  it("WHEELS_EMFS_DIR is an absolute EMFS path", () => {
    expect(WHEELS_EMFS_DIR).toMatch(/^\/tmp\//);
  });

  it("IDLE_TEARDOWN_MS is 30 minutes", () => {
    expect(IDLE_TEARDOWN_MS).toBe(1_800_000);
  });

  it("STORAGE_KEY_HAS_USED is a non-empty string", () => {
    expect(typeof STORAGE_KEY_HAS_USED).toBe("string");
    expect(STORAGE_KEY_HAS_USED.length).toBeGreaterThan(0);
  });
});

// ─── Lazy URL getters ─────────────────────────────────────────────────────────
// Key insight: getURL functions must NOT be called at module load time.
// They are lazy — called inside async functions after chrome.runtime is ready.
describe("lazy URL getters (called after chrome.runtime is available)", () => {
  beforeEach(() => {
    chromeMock.runtime.getURL.mockClear();
  });

  it("getURLBase() calls chrome.runtime.getURL with empty string", () => {
    const result = getURLBase();
    expect(chromeMock.runtime.getURL).toHaveBeenCalledWith("");
    expect(result).toBe("chrome-extension://fake-id/");
  });

  it("getOffscreenURL() returns correct offscreen.html path", () => {
    const result = getOffscreenURL();
    expect(result).toBe("chrome-extension://fake-id/offscreen.html");
  });

  it("getPyodideIndexURL() returns lib/pyodide/ path", () => {
    const result = getPyodideIndexURL();
    expect(result).toContain("lib/pyodide/");
  });

  it("getConverterURL() returns python/converter.py path", () => {
    const result = getConverterURL();
    expect(result).toContain("python/converter.py");
  });

  it("getters are functions, not eager-evaluated strings", () => {
    expect(typeof getURLBase).toBe("function");
    expect(typeof getOffscreenURL).toBe("function");
    expect(typeof getPyodideIndexURL).toBe("function");
    expect(typeof getConverterURL).toBe("function");
  });

  it("each getter call invokes chrome.runtime.getURL exactly once", () => {
    getURLBase();
    expect(chromeMock.runtime.getURL).toHaveBeenCalledTimes(1);
    chromeMock.runtime.getURL.mockClear();

    getOffscreenURL();
    expect(chromeMock.runtime.getURL).toHaveBeenCalledTimes(1);
  });
});

// ─── Edge cases: chrome.runtime unavailable at module load ───────────────────
describe("edge case: chrome.runtime.getURL missing would crash eager constants", () => {
  it("static constants do NOT call chrome.runtime.getURL (safe to import anytime)", () => {
    // If URL_BASE were still a static constant, this call count would be >0
    // from the module load above. Since we removed it, count starts at 0.
    const callsBefore = chromeMock.runtime.getURL.mock.calls.length;

    // Access static constants — none should trigger getURL
    void PYODIDE_PACKAGES;
    void CUSTOM_WHEELS;
    void IDLE_TEARDOWN_MS;

    expect(chromeMock.runtime.getURL.mock.calls.length).toBe(callsBefore);
  });

  it("getters called before chrome exists would throw — validate guard behavior", () => {
    const origChrome = globalThis.chrome;
    // Simulate chrome.runtime.getURL being broken
    vi.stubGlobal("chrome", { runtime: {} });

    expect(() => getURLBase()).toThrow();

    vi.stubGlobal("chrome", origChrome);
  });
});
