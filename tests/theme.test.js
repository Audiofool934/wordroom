"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../public/theme.js"), "utf8");
const STORAGE_KEY = "wordroom-theme";

function boot({stored = null, systemDark = false, storageFails = false} = {}) {
  const storage = new Map(stored === null ? [] : [[STORAGE_KEY, stored]]);
  const root = {dataset: {}};
  const themeColor = {content: "#fdf6e3"};
  const mediaListeners = new Set();
  const windowListeners = new Map();
  const events = [];
  const media = {
    matches: systemDark,
    addEventListener(type, listener) {
      if (type === "change") mediaListeners.add(listener);
    },
  };
  const window = {
    matchMedia(query) {
      assert.equal(query, "(prefers-color-scheme: dark)");
      return media;
    },
    localStorage: {
      getItem(key) {
        if (storageFails) throw new Error("Storage unavailable");
        return storage.get(key) ?? null;
      },
      setItem(key, value) {
        if (storageFails) throw new Error("Storage unavailable");
        storage.set(key, value);
      },
    },
    CustomEvent: class CustomEvent {
      constructor(type, options = {}) {
        this.type = type;
        this.detail = options.detail;
      }
    },
    addEventListener(type, listener) {
      if (!windowListeners.has(type)) windowListeners.set(type, new Set());
      windowListeners.get(type).add(listener);
    },
    dispatchEvent(event) {
      events.push(event);
      for (const listener of windowListeners.get(event.type) || []) listener(event);
      return true;
    },
  };
  const document = {
    documentElement: root,
    querySelectorAll(selector) {
      return selector === 'meta[name="theme-color"]' ? [themeColor] : [];
    },
  };
  vm.runInNewContext(source, {window, document, console});
  return {
    api: window.WordroomTheme,
    root,
    themeColor,
    events,
    stored: () => storage.get(STORAGE_KEY) ?? null,
    setSystemDark(matches) {
      media.matches = matches;
      for (const listener of mediaListeners) listener({matches});
    },
  };
}

test("system dark preference applies immediately and updates browser chrome", () => {
  const harness = boot({systemDark: true});
  assert.equal(harness.api.getPreference(), "system");
  assert.equal(harness.api.getTheme(), "dark");
  assert.equal(harness.root.dataset.theme, "dark");
  assert.equal(harness.themeColor.content, "#002b36");
});

test("manual toggle persists the resolved choice across a reload", () => {
  const first = boot({stored: "dark", systemDark: false});
  assert.equal(first.root.dataset.theme, "dark");
  first.api.toggle();
  assert.equal(first.root.dataset.theme, "light");
  assert.equal(first.stored(), "light");
  assert.equal(first.events.at(-1).type, "wordroom:themechange");
  assert.equal(first.events.at(-1).detail.preference, "light");
  assert.equal(first.events.at(-1).detail.theme, "light");

  const reloaded = boot({stored: first.stored(), systemDark: true});
  assert.equal(reloaded.api.getPreference(), "light");
  assert.equal(reloaded.root.dataset.theme, "light");
  assert.equal(reloaded.themeColor.content, "#fdf6e3");
});

test("system mode follows changes while an explicit choice remains stable", () => {
  const harness = boot({systemDark: false});
  assert.equal(harness.root.dataset.theme, "light");
  harness.setSystemDark(true);
  assert.equal(harness.root.dataset.theme, "dark");

  harness.api.setPreference("light");
  harness.setSystemDark(false);
  assert.equal(harness.root.dataset.theme, "light");
  harness.setSystemDark(true);
  assert.equal(harness.root.dataset.theme, "light");

  harness.api.setPreference("system");
  assert.equal(harness.root.dataset.theme, "dark");
  assert.equal(harness.stored(), "system");
});

test("invalid saved values and unavailable storage fall back safely", () => {
  const invalid = boot({stored: "blue", systemDark: true});
  assert.equal(invalid.api.getPreference(), "system");
  assert.equal(invalid.root.dataset.theme, "dark");

  const unavailable = boot({storageFails: true});
  assert.doesNotThrow(() => unavailable.api.toggle());
  assert.equal(unavailable.root.dataset.theme, "dark");
  assert.equal(unavailable.api.getPreference(), "dark");
});

test("the dark palette redefines every colour the light palette sets", () => {
  const styles = fs.readFileSync(path.join(__dirname, "../public/wordlist.css"), "utf8");
  const light = styles.match(/:root\s*\{([^}]*)\}/);
  const dark = styles.match(/:root\[data-theme="dark"\]\s*\{([^}]*)\}/);
  assert.ok(light && dark, "Expected light and dark palettes");
  assert.match(dark[1], /color-scheme:\s*dark/);
  const colours = [...light[1].matchAll(/--([a-z0-9-]+):\s*(#|rgb|[0-9]+%)/g)].map(match => match[1]);
  assert.ok(colours.length >= 10, "Expected the light palette to set its colours");
  for (const token of colours) {
    assert.match(dark[1], new RegExp(`--${token}:`), `Missing --${token} in the dark palette`);
  }
});
