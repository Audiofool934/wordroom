"use strict";

(() => {
  const storageKey = "wordroom-theme";
  const preferences = new Set(["system", "light", "dark"]);
  const system = window.matchMedia("(prefers-color-scheme: dark)");
  let preference = "system";

  try {
    const saved = window.localStorage.getItem(storageKey);
    if (preferences.has(saved)) preference = saved;
  } catch {
    // The theme still works when browser storage is unavailable.
  }

  function getTheme() {
    if (preference !== "system") return preference;
    return system.matches ? "dark" : "light";
  }

  function apply() {
    const theme = getTheme();
    document.documentElement.dataset.theme = theme;
    document.querySelectorAll('meta[name="theme-color"]').forEach(meta => {
      // A page names its bar colours; Solarized paper is the fallback.
      meta.content = meta.dataset?.[theme] || (theme === "dark" ? "#002b36" : "#fdf6e3");
    });
    window.dispatchEvent(new window.CustomEvent("wordroom:themechange", {
      detail: {preference, theme},
    }));
  }

  function setPreference(value) {
    if (!preferences.has(value)) throw new TypeError("Unknown theme preference");
    preference = value;
    try {
      window.localStorage.setItem(storageKey, value);
    } catch {
      // Keep the in-memory choice for this page when storage is unavailable.
    }
    apply();
  }

  window.WordroomTheme = {
    getPreference: () => preference,
    getTheme,
    setPreference,
    toggle: () => setPreference(getTheme() === "dark" ? "light" : "dark"),
  };

  system.addEventListener("change", () => {
    if (preference === "system") apply();
  });
  apply();
})();
