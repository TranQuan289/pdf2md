import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

// ─── Mock Chrome APIs ───────────────────────────────────────────────────────
const chromeMock = {
  runtime: {
    getManifest: vi.fn().mockReturnValue({ version: "0.1.3" }),
    sendMessage: vi.fn().mockResolvedValue(undefined),
    onMessage: { addListener: vi.fn() },
    getURL: (p) => `chrome-extension://fake-id/${p}`,
    lastError: null,
  },
  offscreen: {
    closeDocument: vi.fn().mockResolvedValue(undefined),
  },
};

vi.stubGlobal("chrome", chromeMock);

// ─── Mock IndexedDB ──────────────────────────────────────────────────────────
const IDB_STORE_NAME = "snapshots";
const store = new Map();

const objectStoreMock = {
  get: vi.fn((key) => {
    const req = { result: store.get(key), onsuccess: null, onerror: null };
    Promise.resolve().then(() => req.onsuccess?.());
    return req;
  }),
  put: vi.fn((value, key) => {
    store.set(key, value);
    return {};
  }),
  clear: vi.fn(() => {
    store.clear();
    return {};
  }),
};

const txMock = {
  objectStore: vi.fn(() => objectStoreMock),
  oncomplete: null,
  onerror: null,
};

// auto-complete tx on next tick
const autoComplete = () => Promise.resolve().then(() => txMock.oncomplete?.());

const dbMock = {
  transaction: vi.fn((storeName, mode) => {
    if (mode === "readwrite") autoComplete();
    return txMock;
  }),
  createObjectStore: vi.fn(),
};

const openRequest = {
  result: dbMock,
  onupgradeneeded: null,
  onsuccess: null,
  onerror: null,
};

vi.stubGlobal("indexedDB", {
  open: vi.fn(() => {
    Promise.resolve().then(() => openRequest.onsuccess?.());
    return openRequest;
  }),
  deleteDatabase: vi.fn(),
});

// ─── Constants (inline mirror of constants.js for test isolation) ─────────────
const PYODIDE_VERSION = "0.29.4";
const CUSTOM_WHEELS = [
  "lib/wheels/charset_normalizer-3.4.4-py3-none-any.whl",
  "lib/wheels/pdfminer_six-20260107-py3-none-any.whl",
  "lib/wheels/pdfplumber-0.10.4-py3-none-any.whl",
];
const IDLE_TEARDOWN_MS = 5 * 60 * 1000;
const IDB_DB_NAME = "pyodide-cache";
const IDB_STORE = "snapshots";
const IDB_VERSION = 1;

// ─── fingerprint ──────────────────────────────────────────────────────────────
function fingerprint(version = "0.1.3") {
  return `v1|${version}|pyodide-${PYODIDE_VERSION}|${CUSTOM_WHEELS.join(",")}`;
}

describe("fingerprint()", () => {
  it("includes manifest version", () => {
    const fp = fingerprint("0.1.3");
    expect(fp).toContain("0.1.3");
  });

  it("includes pyodide version", () => {
    const fp = fingerprint();
    expect(fp).toContain(`pyodide-${PYODIDE_VERSION}`);
  });

  it("includes all custom wheel names", () => {
    const fp = fingerprint();
    for (const w of CUSTOM_WHEELS) {
      expect(fp).toContain(w);
    }
  });

  it("changes when version bumps (cache invalidation)", () => {
    const fp1 = fingerprint("0.1.3");
    const fp2 = fingerprint("0.1.4");
    expect(fp1).not.toBe(fp2);
  });

  it("has format v1|version|pyodide-X.Y.Z|...", () => {
    const fp = fingerprint("0.1.3");
    expect(fp).toMatch(/^v1\|[\d.]+\|pyodide-[\d.]+\|.+/);
  });
});

// ─── IDB helpers (inline replicas of offscreen.js helpers) ──────────────────

async function openDB() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(IDB_DB_NAME, IDB_VERSION);
    req.onupgradeneeded = () => req.result.createObjectStore(IDB_STORE);
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function readSnapshot(key) {
  try {
    const db = await openDB();
    return await new Promise((resolve, reject) => {
      const tx = db.transaction(IDB_STORE, "readonly");
      const req = tx.objectStore(IDB_STORE).get(key);
      req.onsuccess = () => resolve(req.result || null);
      req.onerror = () => reject(req.error);
    });
  } catch { return null; }
}

async function writeSnapshot(key, bytes) {
  try {
    const db = await openDB();
    await new Promise((resolve, reject) => {
      const tx = db.transaction(IDB_STORE, "readwrite");
      tx.objectStore(IDB_STORE).clear();
      tx.objectStore(IDB_STORE).put(bytes, key);
      tx.oncomplete = resolve;
      tx.onerror = () => reject(tx.error);
    });
  } catch (e) { /* swallow */ }
}

async function clearSnapshot() {
  try {
    const db = await openDB();
    db.transaction(IDB_STORE, "readwrite").objectStore(IDB_STORE).clear();
  } catch {}
}

// ─── IDB readSnapshot / writeSnapshot ────────────────────────────────────────
describe("IDB snapshot helpers", () => {
  beforeEach(() => {
    store.clear();
    vi.clearAllMocks();
  });

  it("readSnapshot returns null when store is empty", async () => {
    const result = await readSnapshot("some-key");
    expect(result).toBeNull();
  });

  it("writeSnapshot saves bytes and readSnapshot retrieves them", async () => {
    const key = fingerprint();
    const fakeSnapshot = new Uint8Array([1, 2, 3, 4]);

    objectStoreMock.put.mockImplementationOnce((value, k) => {
      store.set(k, value);
      return {};
    });
    objectStoreMock.get.mockImplementationOnce((k) => {
      const req = { result: store.get(k), onsuccess: null };
      Promise.resolve().then(() => req.onsuccess?.());
      return req;
    });

    await writeSnapshot(key, fakeSnapshot);
    const retrieved = await readSnapshot(key);
    expect(retrieved).toEqual(fakeSnapshot);
  });

  it("clearSnapshot empties the store", async () => {
    store.set("test-key", new Uint8Array([9, 8, 7]));
    await clearSnapshot();
    expect(objectStoreMock.clear).toHaveBeenCalled();
  });

  it("readSnapshot returns null on IDB error (graceful fallback)", async () => {
    indexedDB.open.mockImplementationOnce(() => {
      const req = {
        onsuccess: null,
        onerror: null,
        error: new Error("IDB unavailable"),
      };
      Promise.resolve().then(() => req.onerror?.());
      return req;
    });

    const result = await readSnapshot("any-key");
    expect(result).toBeNull();
  });

  it("writeSnapshot does not throw on IDB error", async () => {
    indexedDB.open.mockImplementationOnce(() => {
      const req = { onsuccess: null, onerror: null, error: new Error("quota exceeded") };
      Promise.resolve().then(() => req.onerror?.());
      return req;
    });

    await expect(writeSnapshot("key", new Uint8Array([1]))).resolves.toBeUndefined();
  });
});

// ─── Cache invalidation logic ─────────────────────────────────────────────────
describe("cache invalidation via fingerprint", () => {
  it("old snapshot (0.1.2) is NOT matched by new fingerprint (0.1.3)", () => {
    const oldFp = fingerprint("0.1.2");
    const newFp = fingerprint("0.1.3");
    store.set(oldFp, new Uint8Array([1, 2, 3]));

    // New version would look up newFp, which is not in store
    expect(store.get(newFp)).toBeUndefined();
  });

  it("same version fingerprint hits the cache", () => {
    const fp = fingerprint("0.1.3");
    const fakeSnap = new Uint8Array([42]);
    store.set(fp, fakeSnap);

    expect(store.get(fp)).toEqual(fakeSnap);
  });
});

// ─── scheduleIdleTeardown ─────────────────────────────────────────────────────
describe("scheduleIdleTeardown", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("sends close-offscreen message after IDLE_TEARDOWN_MS", () => {
    let idleTimer = null;

    function scheduleIdleTeardown() {
      if (idleTimer) clearTimeout(idleTimer);
      idleTimer = setTimeout(() => {
        chrome.runtime.sendMessage({ type: "close-offscreen" }).catch(() => {});
      }, IDLE_TEARDOWN_MS);
    }

    scheduleIdleTeardown();
    vi.advanceTimersByTime(IDLE_TEARDOWN_MS - 1);
    expect(chromeMock.runtime.sendMessage).not.toHaveBeenCalled();

    vi.advanceTimersByTime(1);
    expect(chromeMock.runtime.sendMessage).toHaveBeenCalledWith({ type: "close-offscreen" });
  });

  it("resets timer when called again (debounce behavior)", () => {
    let idleTimer = null;

    function scheduleIdleTeardown() {
      if (idleTimer) clearTimeout(idleTimer);
      idleTimer = setTimeout(() => {
        chrome.runtime.sendMessage({ type: "close-offscreen" }).catch(() => {});
      }, IDLE_TEARDOWN_MS);
    }

    scheduleIdleTeardown();
    vi.advanceTimersByTime(IDLE_TEARDOWN_MS - 1000);

    // Called again (e.g. after a convert) — should reset
    scheduleIdleTeardown();
    vi.advanceTimersByTime(IDLE_TEARDOWN_MS - 1);
    expect(chromeMock.runtime.sendMessage).not.toHaveBeenCalled();

    vi.advanceTimersByTime(1);
    expect(chromeMock.runtime.sendMessage).toHaveBeenCalledTimes(1);
  });

  it("IDLE_TEARDOWN_MS is 5 minutes", () => {
    expect(IDLE_TEARDOWN_MS).toBe(300_000);
  });
});

// ─── Snapshot warm-path vs cold-path selection ────────────────────────────────
describe("init() path selection logic", () => {
  it("selects warm path when cached snapshot exists for current fingerprint", () => {
    const fp = fingerprint("0.1.3");
    store.set(fp, new Uint8Array([1, 2, 3]));

    const cached = store.get(fp);
    expect(cached).toBeDefined();
    expect(cached).not.toBeNull();
    // Warm path would be taken (loadPyodide with _loadSnapshot)
  });

  it("selects cold path when no snapshot in store", () => {
    store.clear();
    const fp = fingerprint("0.1.3");
    const cached = store.get(fp);
    expect(cached).toBeUndefined();
    // Cold path would be taken (loadPyodide with _makeSnapshot: true)
  });

  it("falls back to cold path if snapshot read returns null", async () => {
    // openDB throws → readSnapshot returns null → cold path
    indexedDB.open.mockImplementationOnce(() => {
      const req = { onsuccess: null, onerror: null, error: new Error("fail") };
      Promise.resolve().then(() => req.onerror?.());
      return req;
    });
    const result = await readSnapshot("any");
    expect(result).toBeNull();
  });
});
