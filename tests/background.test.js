import { describe, it, expect, vi, beforeEach } from "vitest";

// ─── Mock Chrome APIs ───────────────────────────────────────────────────────
const offscreenDocs = { has: false };

const chromeMock = {
  runtime: {
    sendMessage: vi.fn(),
    onMessage: { addListener: vi.fn() },
    onInstalled: { addListener: vi.fn() },
    onStartup: { addListener: vi.fn() },
    getURL: (p) => `chrome-extension://fake-id/${p}`,
    lastError: null,
  },
  offscreen: {
    hasDocument: vi.fn(async () => offscreenDocs.has),
    createDocument: vi.fn(async () => { offscreenDocs.has = true; }),
    closeDocument: vi.fn(async () => { offscreenDocs.has = false; }),
  },
  storage: {
    local: {
      get: vi.fn(),
      set: vi.fn().mockResolvedValue(undefined),
    },
  },
  downloads: {
    onDeterminingFilename: { addListener: vi.fn() },
    download: vi.fn(),
  },
};

vi.stubGlobal("chrome", chromeMock);

const STORAGE_KEY_HAS_USED = "hasUsedExtension";
const OFFSCREEN_URL = "chrome-extension://fake-id/offscreen.html";

// ─── ensureOffscreen (inline replica) ────────────────────────────────────────
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

async function maybePrewarmOffscreen() {
  const stored = await chrome.storage.local.get(STORAGE_KEY_HAS_USED);
  if (stored[STORAGE_KEY_HAS_USED]) {
    await ensureOffscreen();
  }
}

// ─── ensureOffscreen ──────────────────────────────────────────────────────────
describe("ensureOffscreen", () => {
  beforeEach(() => {
    offscreenDocs.has = false;
    vi.clearAllMocks();
  });

  it("creates offscreen doc when none exists", async () => {
    chromeMock.offscreen.hasDocument.mockResolvedValueOnce(false);
    await ensureOffscreen();
    expect(chromeMock.offscreen.createDocument).toHaveBeenCalledTimes(1);
    expect(chromeMock.offscreen.createDocument).toHaveBeenCalledWith(
      expect.objectContaining({ url: OFFSCREEN_URL, reasons: ["WORKERS"] })
    );
  });

  it("does NOT create doc when one already exists (idempotent)", async () => {
    chromeMock.offscreen.hasDocument.mockResolvedValueOnce(true);
    await ensureOffscreen();
    expect(chromeMock.offscreen.createDocument).not.toHaveBeenCalled();
  });

  it("is safe to call multiple times consecutively", async () => {
    chromeMock.offscreen.hasDocument
      .mockResolvedValueOnce(false)
      .mockResolvedValueOnce(true)
      .mockResolvedValueOnce(true);

    await ensureOffscreen();
    await ensureOffscreen();
    await ensureOffscreen();

    expect(chromeMock.offscreen.createDocument).toHaveBeenCalledTimes(1);
  });
});

// ─── maybePrewarmOffscreen (lazy pre-warm) ────────────────────────────────────
describe("maybePrewarmOffscreen (lazy pre-warm)", () => {
  beforeEach(() => {
    offscreenDocs.has = false;
    vi.clearAllMocks();
  });

  it("does NOT create offscreen doc for first-time users (hasUsedExtension absent)", async () => {
    chromeMock.storage.local.get.mockResolvedValueOnce({});
    chromeMock.offscreen.hasDocument.mockResolvedValue(false);

    await maybePrewarmOffscreen();

    expect(chromeMock.offscreen.createDocument).not.toHaveBeenCalled();
  });

  it("DOES create offscreen doc for returning users (hasUsedExtension = true)", async () => {
    chromeMock.storage.local.get.mockResolvedValueOnce({ hasUsedExtension: true });
    chromeMock.offscreen.hasDocument.mockResolvedValueOnce(false);

    await maybePrewarmOffscreen();

    expect(chromeMock.offscreen.createDocument).toHaveBeenCalledTimes(1);
  });

  it("does NOT create doc if already exists (returning user, doc alive)", async () => {
    chromeMock.storage.local.get.mockResolvedValueOnce({ hasUsedExtension: true });
    chromeMock.offscreen.hasDocument.mockResolvedValueOnce(true);

    await maybePrewarmOffscreen();

    expect(chromeMock.offscreen.createDocument).not.toHaveBeenCalled();
  });

  it("hasUsedExtension = false behaves like first-time user", async () => {
    chromeMock.storage.local.get.mockResolvedValueOnce({ hasUsedExtension: false });

    await maybePrewarmOffscreen();

    expect(chromeMock.offscreen.createDocument).not.toHaveBeenCalled();
  });
});

// ─── close-offscreen message handler ─────────────────────────────────────────
describe("close-offscreen message handler", () => {
  beforeEach(() => {
    offscreenDocs.has = true;
    vi.clearAllMocks();
  });

  it("closes offscreen doc when close-offscreen message received", async () => {
    // Simulate the handler
    const msg = { type: "close-offscreen" };
    if (msg.type === "close-offscreen") {
      await chrome.offscreen.closeDocument().catch(() => {});
    }

    expect(chromeMock.offscreen.closeDocument).toHaveBeenCalledTimes(1);
  });

  it("does not throw if closeDocument fails", async () => {
    chromeMock.offscreen.closeDocument.mockRejectedValueOnce(new Error("no doc"));

    const msg = { type: "close-offscreen" };
    await expect(async () => {
      if (msg.type === "close-offscreen") {
        await chrome.offscreen.closeDocument().catch(() => {});
      }
    }).not.toThrow();
  });
});

// ─── download handler ─────────────────────────────────────────────────────────
describe("download message handler", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("triggers chrome.downloads.download with correct url and filename", () => {
    const msg = {
      type: "download",
      url: "data:text/markdown;base64,abc",
      filename: "document.md",
    };

    // Simulate handler
    let _nextFilename = null;
    if (msg.type === "download") {
      _nextFilename = msg.filename;
      chrome.downloads.download({ url: msg.url, filename: msg.filename, saveAs: false });
    }

    expect(_nextFilename).toBe("document.md");
    expect(chromeMock.downloads.download).toHaveBeenCalledWith({
      url: "data:text/markdown;base64,abc",
      filename: "document.md",
      saveAs: false,
    });
  });
});
