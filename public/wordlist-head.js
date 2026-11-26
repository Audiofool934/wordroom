"use strict";
// List preferences apply before the first paint, so rows do not change height after the page appears.
(() => {
  const read = key => { try { return localStorage.getItem(key) === "true"; } catch { return false; } };
  const root = document.documentElement;
  root.classList.toggle("sentences", read("wordroom.wordlist.sentences"));
  root.classList.toggle("list-meanings", read("wordroom.wordlist.listMeanings"));
})();
