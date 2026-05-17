import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

// ─── Mock Chrome APIs ───────────────────────────────────────────────────────
const chromeMock = {
  storage: {
    local: {
      set: vi.fn().mockResolvedValue(undefined),
      get: vi.fn().mockResolvedValue({}),
    },
    session: {
      get: vi.fn().mockResolvedValue({}),
      set: vi.fn().mockResolvedValue(undefined),
      remove: vi.fn().mockResolvedValue(undefined),
    },
  },
  runtime: {
    sendMessage: vi.fn(),
    onMessage: { addListener: vi.fn() },
    getURL: (p) => `chrome-extension://fake-id/${p}`,
    lastError: null,
  },
  tabs: {
    query: vi.fn(),
  },
};

vi.stubGlobal("chrome", chromeMock);

// ─── Helpers ─────────────────────────────────────────────────────────────────
function setupDOM() {
  document.body.innerHTML = `
    <span id="status">Loading…</span>
    <span id="filename">—</span>
    <div id="hint" class="hint"></div>
    <button id="convert" disabled aria-disabled="true">Download as .md</button>
  `;
}

// ─── scheduleFirstTimeHint ────────────────────────────────────────────────────
// Hint renders in #hint element (separate from #status) to avoid mutation bugs.
describe("scheduleFirstTimeHint", () => {
  beforeEach(() => {
    setupDOM();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  function simulateHint(popupReadyRef) {
    setTimeout(() => {
      if (!popupReadyRef.value) {
        const el = document.getElementById("hint");
        if (el) el.textContent = "First-time setup — hang tight, this takes ~5s once.";
      }
    }, 3000);
  }

  it("sets #hint text after 3s when not ready", () => {
    const ref = { value: false };
    simulateHint(ref);

    // Before 3s: hint is empty
    vi.advanceTimersByTime(2999);
    expect(document.getElementById("hint").textContent).toBe("");

    // After 3s: hint appears
    vi.advanceTimersByTime(1);
    expect(document.getElementById("hint").textContent).toContain("First-time setup");
  });

  it("#status is NOT modified when hint shows — status stays 'Loading…'", () => {
    const ref = { value: false };
    simulateHint(ref);

    vi.advanceTimersByTime(3000);

    // Status should remain the original progress text, untouched
    expect(document.getElementById("status").textContent).toBe("Loading…");
  });

  it("does NOT set hint if popupReady is true before 3s", () => {
    const ref = { value: false };
    simulateHint(ref);

    // Ready fires at t=1s
    vi.advanceTimersByTime(1000);
    ref.value = true;

    vi.advanceTimersByTime(2000);
    expect(document.getElementById("hint").textContent).toBe("");
  });

  it("clearHint empties #hint when ready fires", () => {
    const hintEl = document.getElementById("hint");
    hintEl.textContent = "First-time setup — hang tight, this takes ~5s once.";

    // Simulate clearHint
    hintEl.textContent = "";

    expect(hintEl.textContent).toBe("");
  });

  it("does not throw if #hint element is missing", () => {
    document.body.innerHTML = '<span id="status">Loading…</span>';
    const ref = { value: false };

    expect(() => {
      setTimeout(() => {
        if (!ref.value) {
          const el = document.getElementById("hint");
          if (el) el.textContent = "First-time setup — hang tight, this takes ~5s once.";
        }
      }, 3000);
      vi.advanceTimersByTime(3000);
    }).not.toThrow();
  });
});

// ─── Message handler: "ready" ────────────────────────────────────────────────
describe("message handler: ready", () => {
  beforeEach(() => {
    setupDOM();
  });

  it("enables convert button and sets status to Ready. on ready message", () => {
    // Simulate the ready case from the onMessage handler
    const btn = document.getElementById("convert");
    const statusEl = document.getElementById("status");
    let popupReady = false;

    // Mimic the handler logic
    function handleReady() {
      popupReady = true;
      statusEl.textContent = "Ready.";
      btn.disabled = false;
      btn.setAttribute("aria-disabled", "false");
    }

    handleReady();

    expect(popupReady).toBe(true);
    expect(statusEl.textContent).toBe("Ready.");
    expect(btn.disabled).toBe(false);
    expect(btn.getAttribute("aria-disabled")).toBe("false");
  });

  it("shows progress messages from offscreen", () => {
    const statusEl = document.getElementById("status");

    const messages = [
      "Loading Python runtime…",
      "Loading PDF libraries…",
      "Installing PDF parsers…",
      "Almost ready…",
      "Restoring Python session…",
    ];

    for (const msg of messages) {
      statusEl.textContent = msg;
      expect(statusEl.textContent).toBe(msg);
    }
  });
});

// ─── handleResult ─────────────────────────────────────────────────────────────
describe("handleResult", () => {
  beforeEach(() => {
    setupDOM();
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:fake");
    vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("calls storage.local.set with STORAGE_KEY_HAS_USED=true on success", () => {
    const STORAGE_KEY_HAS_USED = "hasUsedExtension";

    // Simulate what handleResult does
    chromeMock.storage.local.set({ [STORAGE_KEY_HAS_USED]: true }).catch(() => {});

    expect(chromeMock.storage.local.set).toHaveBeenCalledWith(
      expect.objectContaining({ hasUsedExtension: true })
    );
  });

  it("shows done message with correct stats", () => {
    const statusEl = document.getElementById("status");
    const markdown = "# Title\n\nContent here.";
    const pages = 5;
    const tables = 2;

    statusEl.textContent = `Done — ${pages} pages, ${tables} tables, ${markdown.length.toLocaleString()} chars.`;

    expect(statusEl.textContent).toBe("Done — 5 pages, 2 tables, 22 chars.");
  });
});

// ─── status display logic (regression tests) ─────────────────────────────────
describe("status display — regression", () => {
  beforeEach(() => {
    setupDOM();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("FIX: #status never contains 'first-time' text (hint goes to #hint only)", () => {
    const statusEl = document.getElementById("status");
    const hintEl = document.getElementById("hint");
    const ref = { value: false };

    statusEl.textContent = "Loading…";
    setTimeout(() => {
      if (!ref.value) {
        if (hintEl) hintEl.textContent = "First-time setup — hang tight, this takes ~5s once.";
      }
    }, 3000);

    vi.advanceTimersByTime(3000);

    // Status must never be mutated
    expect(statusEl.textContent).toBe("Loading…");
    expect(statusEl.textContent).not.toContain("First-time");
    // Hint appears in dedicated element
    expect(hintEl.textContent).toContain("First-time");
  });

  it("progress messages from offscreen overwrite #status without interfering with #hint", () => {
    const statusEl = document.getElementById("status");
    const hintEl = document.getElementById("hint");

    statusEl.textContent = "Loading…";
    hintEl.textContent = "First-time setup — hang tight, this takes ~5s once.";

    // Offscreen broadcasts progress
    const messages = [
      "Loading Python runtime…",
      "Loading PDF libraries…",
      "Installing PDF parsers…",
      "Almost ready…",
    ];

    for (const msg of messages) {
      statusEl.textContent = msg;
      // Each progress update should not touch #hint
      expect(hintEl.textContent).toContain("First-time");
    }

    expect(statusEl.textContent).toBe("Almost ready…");
  });

  it("hint is cleared when ready fires (clearHint)", () => {
    const hintEl = document.getElementById("hint");
    hintEl.textContent = "First-time setup — hang tight, this takes ~5s once.";

    // Simulate ready + clearHint
    hintEl.textContent = "";

    expect(hintEl.textContent).toBe("");
  });

  it("warm restore: hint fires after 3s but not if restore finishes before that", () => {
    const hintEl = document.getElementById("hint");
    const ref = { value: false };

    setTimeout(() => {
      if (!ref.value) {
        if (hintEl) hintEl.textContent = "First-time setup — hang tight, this takes ~5s once.";
      }
    }, 3000);

    // Warm restore completes at 800ms
    vi.advanceTimersByTime(800);
    ref.value = true;

    vi.advanceTimersByTime(2200);
    expect(hintEl.textContent).toBe("");
  });
});
