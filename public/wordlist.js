"use strict";

(() => {
  const $ = s => document.querySelector(s);
  const jump = $("#jump"), more = $("#more"), view = $("#view"), query = $("#query");
  const toolbar = $(".toolbar"), resume = $("#resume"), notice = $(".notice");
  const positionKey = "wordroom.wordlist.bbgre.position.v1";
  const bookmarkKey = "wordroom.wordlist.bbgre.bookmark.v2";
  const listMeaningKey = "wordroom.wordlist.listMeanings";
  const sentencesKey = "wordroom.wordlist.sentences";
  const scoreKey = "wordroom.wordlist.bbgre.scores.v1";
  const local = location.protocol === "file:";
  const endpoint = "/api/wordlist/scores";
  const scores = new Map();
  const collections = new Map();
  const collectionNames = {all: "Words", super: "Picks", fill: "Completion", completion: "Choices", reading: "Reading"};
  const collectionPicker = $("#collection");
  const activeLabel = view.querySelector('input[value="active"] + span').firstChild;
  let collection = "all", collectionLoad;
  let entryCollection = new URLSearchParams(location.search).get("collection");
  if (!Object.hasOwn(collectionNames, entryCollection)) entryCollection = null;
  const groups = [...document.querySelectorAll(".group")].map(section => ({
    section, label: section.querySelector("h2 small"), link: jump.querySelector('a[href="#' + section.id + '"]'),
    rows: [...section.querySelectorAll(".word")].map(row => {
      const word = row.querySelector("b").textContent;
      const text = (word + " " + row.querySelector(".meaning").textContent).toLowerCase();
      return {row, word, text};
    }),
  }));
  const groupByElement = new Map(groups.map(g => [g.section, g]));
  const counters = Object.fromEntries([...view.querySelectorAll("[data-count]")].map(el => [el.dataset.count, el]));
  let activeGroup, scrollTimer, noticeTimer, noticeAction, token, ready = false;
  let gesture, revealed, suppressClickUntil = 0;
  let pending = Promise.resolve();
  let confirmed, appliedRevision = -1, interaction = 0, scrollArmed = false, deferred;
  let foreground = true;
  history.scrollRestoration = "manual";

  function stored(key) {
    try { return localStorage.getItem(key); } catch { return null; }
  }
  function remember(key, value) {
    try { localStorage.setItem(key, value); } catch { /* Optional preference. */ }
  }

  // Pronunciation uses the device's own English voice, so nothing leaves the phone.
  const speech = "speechSynthesis" in window && "SpeechSynthesisUtterance" in window ? window.speechSynthesis : null;
  let voice = null, speaking = null;
  function pickVoice() {
    if (!speech) return;
    const rank = v => (/premium/i.test(v.name) ? 4 : /enhanced/i.test(v.name) ? 3 : 0)
      + (/^(samantha|ava|allison|evan|nathan|zoe|tom|susan)\b/i.test(v.name) ? 2 : 0)
      + (/google us english/i.test(v.name) ? 2 : 0) + (v.localService ? 1 : 0);
    const english = speech.getVoices().filter(v => /^en[-_]US/i.test(v.lang) && !/novelty|eloquence|whisper|bells|bad news|jester/i.test(v.name));
    voice = english.sort((a, b) => rank(b) - rank(a))[0] || null;
  }
  if (speech) {
    pickVoice();
    speech.addEventListener?.("voiceschanged", pickVoice);
  }
  function stopSpeaking() {
    speaking?.classList.remove("speaking");
    speaking = null;
    speech?.cancel();
  }
  function say(text, button, rate) {
    if (!speech) return;
    const again = speaking === button;
    stopSpeaking();
    if (again && button?.dataset.speak === "sentence") return; // A second tap stops a sentence.
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = "en-US";
    if (voice) utterance.voice = voice;
    utterance.rate = rate;
    speaking = button;
    button?.classList.add("speaking");
    const done = () => { if (speaking === button) { button?.classList.remove("speaking"); speaking = null; } };
    utterance.onend = done;
    utterance.onerror = done;
    speech.speak(utterance);
  }
  const speakerIcon = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2 6h2.5L8 3v10L4.5 10H2z"/>'
    + '<path d="M10.3 5.2a.75.75 0 0 1 1.06.04 4 4 0 0 1 0 5.52.75.75 0 1 1-1.1-1.02 2.5 2.5 0 0 0 0-3.48.75.75 0 0 1 .04-1.06z"/></svg>';

  function loadCollections() {
    if (local || collections.size) return;
    if (!collectionLoad) {
      collectionLoad = fetch("/wordlist-super-picks.json").then(async response => {
        if (response.status === 404) return; // This install has no word sets; the list stays hidden.
        if (!response.ok) throw Error("Couldn't load Super Picks.");
        const membership = await response.json();
        const sets = {super: new Set(), fill: new Set(), completion: new Set(), reading: new Set()};
        for (const [word, subsets] of Object.entries(membership)) {
          sets.super.add(word);
          for (const subset of subsets) {
            if (Object.hasOwn(sets, subset)) sets[subset].add(word);
          }
        }
        for (const [key, words] of Object.entries(sets)) {
          const count = collectionPicker.querySelector('[data-set="' + key + '"]');
          // Older pages and membership files only have the choices and reading filters.
          if (!count || (key === "fill" && !words.size)) continue;
          collections.set(key, words);
          count.textContent = words.size.toLocaleString("en-US");
          count.closest("label").hidden = false;
        }
        selectCollection(collection);
        collectionPicker.hidden = false;
      }).catch(error => {
        collectionLoad = null;
        throw error;
      });
    }
    return collectionLoad;
  }
  function selectCollection(name) {
    collection = collections.has(name) ? name : "all";
    const radio = [...collectionPicker.elements].find(input => input.value === collection);
    if (radio) radio.checked = true;
    activeLabel.textContent = collection === "all" ? "Words " : collectionNames[collection] + " ";
    // Keep an explicitly opened collection link consistent if the reader switches ranges.
    const url = new URL(location.href);
    if (!local && url.searchParams.has("collection") && url.searchParams.get("collection") !== collection) {
      url.searchParams.set("collection", collection);
      history.replaceState(history.state, "", url);
    }
  }
  function inCollection(word) { return collection === "all" || collections.get(collection).has(word); }
  function selectedView() { return view.querySelector(":checked").value; }
  function selectView(name) {
    const radio = [...view.elements].find(input => input.value === name);
    if (radio) radio.checked = true;
  }
  function localBookmark() {
    try { return JSON.parse(stored(bookmarkKey)) || {row: stored(positionKey)}; }
    catch { return {row: stored(positionKey)}; }
  }
  function rowFor(position) {
    if (!position) return null;
    const row = document.getElementById(position.row);
    if (row?.classList.contains("word")) return row;
    if (position.word) {
      for (const group of groups) {
        const match = group.rows.find(item => item.word === position.word);
        if (match) return match.row;
      }
    }
    return null;
  }
  function bookmark(row) {
    return {row: row.id, view: selectedView(), collection, query: query.value, expanded: row.querySelector("details").open};
  }
  function samePlace(a, b) {
    return a && b && (a.collection || "all") === (b.collection || "all") && ["row","view","query","expanded"].every(key => a[key] === b[key]);
  }
  function accept(position) {
    if (!position || (confirmed?.revision ?? -1) > (position.revision ?? 0)) return;
    confirmed = position;
    remember(bookmarkKey, JSON.stringify(position));
    showResume(position);
  }
  function touch() {
    interaction++;
    scrollArmed = false;
    clearTimeout(scrollTimer);
    deferred = null;
  }
  function restore(position) {
    const row = rowFor(position);
    if (!row) return;
    const word = row.querySelector("b").textContent;
    selectCollection(position.collection || "all");
    if (!inCollection(word)) selectCollection("all");
    selectView(position.view || "active");
    query.value = position.query || "";
    filter();
    if (row.hidden) {
      query.value = "";
      selectView(viewFor(scoreOf(row.querySelector("b").textContent)));
      filter();
    }
    const group = groupByElement.get(row.closest(".group"));
    if (position.expanded) extras(row);
    row.querySelector("details").open = !!position.expanded;
    setGroup(group);
    row.scrollIntoView({block:"start", behavior:"instant"});
    appliedRevision = position.revision ?? 0;
  }
  function savePlace(position, direct = false) {
    if (!ready || !position) return;
    const run = async () => {
      if (samePlace(confirmed, position) && !direct) return;
      if (local) {
        const latest = localBookmark();
        accept({...position, revision: (latest?.revision || 0) + 1});
        appliedRevision = confirmed.revision;
        return;
      }
      const base = confirmed?.revision || 0;
      const data = await post({...position, base, direct}, "/api/wordlist/position");
      accept(data.position);
      if (samePlace(data.position, position)) appliedRevision = data.position.revision;
    };
    pending = pending.then(run).catch(() => showNotice("Couldn't sync your place.", "Retry", () => savePlace(position, true)));
  }

  function showNotice(text, actionLabel, action) {
    clearTimeout(noticeTimer);
    notice.querySelector("span").textContent = text;
    const button = notice.querySelector("button");
    button.hidden = !action;
    button.textContent = actionLabel || "";
    noticeAction = action;
    notice.hidden = false;
    if (!action || actionLabel === "Undo") noticeTimer = setTimeout(() => { notice.hidden = true; }, 6000);
  }
  notice.querySelector("button").addEventListener("click", () => {
    notice.hidden = true;
    noticeAction?.();
  });
  function scoreOf(word) { return scores.get(word)?.[0] ?? 0; }
  // Each word is in exactly one view: unmarked words, words marked for review, or archived words.
  function viewFor(score) { return score < 0 ? "archive" : score > 0 ? "review" : "active"; }
  function displayRow(row, score) {
    if (row.dataset.score === String(score)) return;
    row.dataset.score = score;
    row.dataset.tone = Math.min(3, Math.max(score, 0));
    const stepper = row.querySelector(".stepper");
    stepper.querySelector("output").textContent = score > 0 ? score : "";
    stepper.hidden = score < 0;
    if (score < 0) extras(row);
    if (row.extras) {
      row.extras.restore.hidden = score >= 0;
      row.extras.archive.hidden = score < 0;
    }
  }
  function control(action, label, text, className = "") {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.action = action;
    button.setAttribute("aria-label", label);
    button.textContent = text;
    if (className) button.className = className;
    return button;
  }
  // Row details (the coloured sentence, translation, structure, meaning and word lists) live in a separate
  // file, so the page stays small. It loads soon after the list appears; a row that opens first waits for it.
  const embeddedDetails = document.getElementById("wordlist-details");
  let detailData = null, detailsLoad = null;
  function loadDetails() {
    if (!detailsLoad) {
      detailsLoad = (embeddedDetails ? Promise.resolve(JSON.parse(embeddedDetails.textContent))
        : fetch("/wordlist-details.json").then(response => {
          if (!response.ok) throw Error("details " + response.status);
          return response.json();
        })).then(data => { detailData = data; }, error => { detailsLoad = null; throw error; });
    }
    return detailsLoad;
  }
  function fill(row) {
    if (row.dataset.filled) return;
    if (!detailData) {
      loadDetails().then(() => fill(row), () => showNotice("Couldn't load word details.", "Retry", () => fill(row)));
      return;
    }
    row.dataset.filled = "1";
    const entry = detailData[row.id];
    if (!entry) return;
    const [sentence, detail] = entry;
    if (sentence) row.querySelector(".sentence").innerHTML = sentence;
    row.querySelector(".example").insertAdjacentHTML("afterbegin", detail);
  }
  document.addEventListener("toggle", e => {
    if (e.target.open && e.target.matches?.(".word details")) fill(e.target.closest(".word"));
  }, true);

  // Buttons that only matter after a tap, a swipe or an archive are made on first use, never while scrolling.
  function extras(row) {
    if (row.extras) return row.extras;
    const word = row.querySelector("b").textContent;
    const actions = document.createElement("div");
    actions.className = "row";
    if (speech && row.querySelector(".sentence")) {
      const play = document.createElement("button");
      play.type = "button";
      play.dataset.speak = "sentence";
      play.setAttribute("aria-label", "Play the example sentence");
      play.innerHTML = speakerIcon + "Play sentence";
      actions.append(play);
    }
    const archive = control("archive", "Archive " + word, "Archive", "archive-action");
    actions.append(archive);
    row.querySelector(".example").append(actions);
    const restore = control("restore", "Move " + word + " back to the list", "Restore", "restore");
    row.querySelector(".face").append(restore);
    row.prepend(control("archive", "Confirm archive " + word, "Archive", "swipe-action"));
    const score = scoreOf(word);
    restore.hidden = score >= 0;
    archive.hidden = score < 0;
    row.extras = {restore, archive};
    return row.extras;
  }
  function showResume(position) {
    const row = rowFor(position);
    resume.hidden = !row;
    if (row) {
      resume.href = "#" + row.id;
      resume.textContent = "Continue at " + row.querySelector("b").textContent;
    }
  }
  function setGroup(group) {
    if (group === activeGroup) return;
    activeGroup?.link.removeAttribute("aria-current");
    activeGroup = group;
    group?.link.setAttribute("aria-current", "location");
    $("#current-group").textContent = group ? "Group " + group.section.dataset.group.padStart(2, "0") : "Groups";
  }
  function track(remember) {
    if (!ready) return;
    const bounds = toolbar.getBoundingClientRect();
    const item = document.elementFromPoint(bounds.left + bounds.width / 2, bounds.bottom + 14);
    const section = item?.closest(".group");
    if (!section || jump.open || more.open) return;
    const group = groupByElement.get(section);
    setGroup(group);
    const row = item.closest(".word") || section.querySelector(".word:not([hidden])");
    if (remember && row) savePlace(bookmark(row));
  }
  function clearSwipe() {
    if (revealed) {
      revealed.classList.remove("revealed");
      revealed = null;
    }
  }
  function plural(n, one, many = one + "s") { return n.toLocaleString("en-US") + " " + (n === 1 ? one : many); }
  // Join only family members that are next to each other in what is shown now.
  // A family member with no visible relative beside it gets no bar.
  function linkFamilies() {
    for (const group of groups) {
      const shown = group.rows.filter(({row}) => !row.hidden).map(({row}) => row);
      shown.forEach((row, i) => {
        const fam = row.dataset.fam;
        const joinsUp = Boolean(fam) && shown[i - 1]?.dataset.fam === fam;
        const joinsDown = Boolean(fam) && shown[i + 1]?.dataset.fam === fam;
        row.classList.toggle("fam", joinsUp || joinsDown);
        row.classList.toggle("fam-start", !joinsUp);
        row.classList.toggle("fam-end", !joinsDown);
      });
    }
  }
  function filter() {
    const term = query.value.trim().toLowerCase();
    const range = selectedView();
    const totals = {active: 0, review: 0, archive: 0};
    let matches = 0, first;
    for (const group of groups) {
      let count = 0;
      for (const {row, word, text} of group.rows) {
        const score = scoreOf(word);
        const included = inCollection(word);
        if (included) totals[viewFor(score)]++;
        const inView = viewFor(score) === range;
        const visible = included && inView && (!term || text.includes(term));
        if (row.hidden === visible) row.hidden = !visible;
        if (visible) count++;
        displayRow(row, score);
      }
      if (group.section.hidden === Boolean(count)) group.section.hidden = !count;
      group.link.hidden = !count;
      group.section.style.setProperty("--rows", String(count));
      const label = plural(count, "word");
      if (group.label.textContent !== label) group.label.textContent = label;
      if (count && !first) first = group;
      matches += count;
    }
    for (const [name, el] of Object.entries(counters)) {
      const value = totals[name] ? totals[name].toLocaleString("en-US") : "";
      if (el.textContent !== value) el.textContent = value;
    }
    const result = $("#result");
    result.hidden = matches > 0 && !term;
    result.textContent = matches ? plural(matches, "match", "matches") :
      term ? "No word or meaning matches “" + query.value.trim() + "”." :
      range === "review" ? "Nothing to review yet. Tap + on a word you're unsure of." :
      range === "archive" ? "No archived words yet. Swipe a word left to archive it." :
      collection === "all" ? "Every word is in Review or Archived." : "Every word in this collection is in Review or Archived.";
    linkFamilies();
    if (!activeGroup || activeGroup.section.hidden) setGroup(first || null);
    if (revealed?.hidden) clearSwipe();
    return first;
  }
  function applyScore(word, score, revision) {
    if (revision <= (scores.get(word)?.[1] || 0)) return false;
    scores.set(word, [score, revision]);
    return true;
  }
  async function refresh(follow = false) {
    const started = interaction;
    const initial = !ready;
    let values, position;
    if (local) {
      values = JSON.parse(stored(scoreKey) || "{}");
      position = localBookmark();
    } else {
      const [response] = await Promise.all([fetch(endpoint, {cache: "no-store"}), loadCollections()?.catch(() => {
        showNotice("Couldn't load Super Picks.", "Retry", () => sync(true));
      })]);
      if (!response.ok) throw Error(response.status === 401 || response.status === 403
        ? "You're signed out. Reload to sign in." : "Couldn't load your marks.");
      const state = await response.json();
      token = state.token;
      values = state.scores;
      position = state.position;
    }
    let changed = false;
    for (const [word, [score, revision]] of Object.entries(values)) {
      if (applyScore(word, score, revision)) changed = true;
    }
    if (!ready) {
      ready = true;
      view.disabled = false;
      collectionPicker.disabled = false;
      changed = true;
    }
    if (entryCollection && collections.size && started === interaction) {
      const requested = entryCollection;
      entryCollection = null;
      const saved = position || localBookmark();
      if (requested !== (saved.collection || "all") || !rowFor(saved)) {
        if (rowFor(saved)) accept(saved);
        selectCollection(requested);
        selectView("active");
        query.value = "";
        setGroup(filter() || null);
        window.scrollTo(0, 0);
        // A focus event alone should not replace this explicit navigation with an older bookmark.
        appliedRevision = saved.revision ?? 0;
        return;
      }
      follow = true;
    }
    if (changed) filter();
    if ((initial || follow) && started === interaction) {
      const target = position || {...localBookmark(), revision: 0};
      if (target.collection && target.collection !== "all" && !collections.size && !local) return;
      if (rowFor(target) && (initial || (target.revision ?? 0) > appliedRevision)
          && (target.revision ?? 0) >= (confirmed?.revision ?? 0)) {
        accept(target);
        restore(target);
      } else if (initial && !rowFor(target) && location.hash) {
        const anchor = document.getElementById(location.hash.slice(1));
        if (anchor?.matches(".word, .group")) anchor.scrollIntoView({block:"start"});
      }
    }
  }
  function sync(follow = false) {
    pending.then(() => refresh(follow)).catch(error => showNotice(error.message || "Couldn't load your marks.", "Retry", () => sync(follow)));
  }
  function localEdit(word, action, undoRevision) {
    const values = JSON.parse(localStorage.getItem(scoreKey) || "{}");
    const last = Object.hasOwn(values, word) ? values[word] : [0, 0, 0];
    const [previous, lastRevision, beforeArchive] = last;
    let score;
    if (action === "undo" || action === "restore") {
      if (previous !== -1 || (action === "undo" && lastRevision !== undoRevision)) throw conflict();
      score = action === "undo" ? beforeArchive : 0;
    } else {
      if (previous === -1) throw conflict();
      score = action === "archive" ? -1 : previous + (action === "plus" ? 1 : -1);
    }
    const revision = lastRevision + 1;
    values[word] = [score, revision, previous];
    localStorage.setItem(scoreKey, JSON.stringify(values));
    return {word, previous, score, revision};
  }
  function conflict() {
    const err = Error("conflict");
    err.status = 409;
    return err;
  }
  // The server answers in Chinese; the page speaks for itself by status instead.
  function explain(err) {
    if (err.status === 409) return "This word changed on another device. Showing the latest.";
    if (err.status === 401 || err.status === 403) return "You're signed out. Reload to sign in.";
    return err.status ? "Couldn't save that." : "Couldn't confirm it was saved.";
  }
  async function post(payload, url = endpoint) {
    const response = await fetch(url, {method: "POST", keepalive: true,
      headers: {"Content-Type": "application/json", ...(token ? {"X-Wordroom-Token": token} : {})}, body: JSON.stringify(payload)});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const err = Error(data.error || "save failed");
      err.status = response.status;
      throw err;
    }
    return data;
  }
  function change(row, action, undoRevision = 0, requestId = crypto.randomUUID()) {
    touch();
    const word = row.querySelector("b").textContent;
    const position = bookmark(row);
    const run = async () => {
      try {
        let data;
        if (local) {
          const edit = () => localEdit(word, action, undoRevision);
          data = navigator.locks ? await navigator.locks.request(scoreKey, edit) : edit();
          // Like the server: a word that leaves its view takes the saved place with it.
          const range = data.score === -1 ? "archive" : position.view === "archive" ||
            (position.view === "review" && data.score === 0) ? "active" :
            position.view === "active" && data.score > 0 ? "review" : position.view;
          data.position = {...position, view: range, revision: (localBookmark()?.revision || 0) + 1};
        } else {
          data = await post({word, action, undo_revision: undoRevision, request_id: requestId, position});
        }
        accept(data.position);
        if (data.position) appliedRevision = data.position.revision;
        if (data.revision < (scores.get(word)?.[1] || 0)) return;
        if (applyScore(word, data.score, data.revision)) filter();
        if (data.score === -1) showNotice("Archived " + word, "Undo", () => change(row, "undo", data.revision));
        if (action === "restore" || action === "undo") showNotice(word + " is back in the list");
        if (action === "plus" && data.previous === 0) showNotice("Moved " + word + " to Review", "Undo", () => change(row, "minus"));
        if (action === "minus" && data.score === 0) {
          // Leaving Review can be undone; undoing a move to Review only confirms it.
          if (position.view === "review") showNotice(word + " is back in the list", "Undo", () => change(row, "plus"));
          else showNotice(word + " is back in the list");
        }
      } catch (err) {
        try { await refresh(); } catch { /* Keep the last confirmed state. */ }
        if (err.status === 409 || err.status === 401 || err.status === 403) showNotice(explain(err));
        else showNotice(explain(err), "Retry", () => change(row, action, undoRevision, requestId));
      }
    };
    pending = pending.then(run, run);
  }
  function closeMenus() { jump.open = false; more.open = false; }
  function switchView(e) {
    touch(); clearSwipe(); closeMenus();
    const first = filter();
    setGroup(first || null);
    window.scrollTo(0, 0);
    const row = first?.section.querySelector(".word:not([hidden])");
    if (row) {
      deferred = () => savePlace(bookmark(row), true);
      if (e.target === query) scrollTimer = setTimeout(flush, 280);
      else flush();
    }
  }
  function flush() {
    clearTimeout(scrollTimer);
    const callback = deferred;
    deferred = null;
    callback?.();
  }
  query.addEventListener("input", switchView);
  query.addEventListener("keydown", e => { if (e.key === "Enter") query.blur(); });
  view.addEventListener("change", switchView);
  collectionPicker.addEventListener("change", e => {
    entryCollection = null;
    selectCollection(e.target.value);
    switchView(e);
  });

  // wordlist-head.js applies both list preferences before the first paint.
  const root = document.documentElement;
  // Keep the row (or group heading) under the toolbar in place while rows above it change height.
  function keepPlace(change) {
    const bounds = toolbar.getBoundingClientRect();
    const anchor = document.elementFromPoint(bounds.left + bounds.width / 2, bounds.bottom + 14)?.closest(".word, .group");
    const before = anchor?.getBoundingClientRect().top;
    change();
    if (anchor) window.scrollBy(0, anchor.getBoundingClientRect().top - before);
  }
  const listMeanings = $("#list-meanings");
  listMeanings.checked = root.classList.contains("list-meanings");
  listMeanings.addEventListener("change", () => {
    keepPlace(() => root.classList.toggle("list-meanings", listMeanings.checked));
    remember(listMeaningKey, String(listMeanings.checked));
  });
  const sentenceButton = $("#sentences");
  function showSentences(on) {
    root.classList.toggle("sentences", on);
    sentenceButton.setAttribute("aria-pressed", String(on));
    sentenceButton.setAttribute("aria-label", on ? "Hide example sentences" : "Show example sentences");
  }
  showSentences(root.classList.contains("sentences"));
  sentenceButton.addEventListener("click", () => {
    touch(); closeMenus();
    const on = !root.classList.contains("sentences");
    keepPlace(() => showSentences(on));
    remember(sentencesKey, String(on));
  });

  const appearance = $("#appearance");
  function showAppearance() {
    const value = window.WordroomTheme?.getPreference() || "system";
    const radio = [...appearance.elements].find(input => input.value === value);
    if (radio) radio.checked = true;
  }
  appearance.addEventListener("change", e => window.WordroomTheme?.setPreference(e.target.value));

  // A tap on a synonym or antonym goes to that word, opens it and says it.
  function openRelated(id) {
    const row = document.getElementById(id);
    if (!row) return;
    if (!inCollection(row.querySelector("b").textContent)) selectCollection("all");
    if (row.hidden) {
      query.value = "";
      view.querySelector('input[value="' + viewFor(scoreOf(row.querySelector("b").textContent)) + '"]').checked = true;
      filter();
    }
    extras(row);
    row.querySelector("details").open = true;
    row.scrollIntoView({block: "start"});
    say(row.querySelector("b").textContent, row.querySelector(".ipa"), .85);
    savePlace(bookmark(row), true);
  }
  resume.addEventListener("click", e => {
    e.preventDefault(); touch(); closeMenus(); restore(confirmed);
  });
  jump.querySelector("nav").addEventListener("click", e => {
    const link = e.target.closest("a");
    if (!link) return;
    e.preventDefault(); touch(); closeMenus();
    const group = groupByElement.get(document.getElementById(link.hash.slice(1)));
    setGroup(group);
    group.section.scrollIntoView({block:"start"});
    const row = group.section.querySelector(".word:not([hidden])");
    if (row) savePlace(bookmark(row), true);
  });
  document.addEventListener("click", e => {
    // A tap outside an open menu only dismisses it, as on iOS.
    const open = [jump, more].find(menu => menu.open && !menu.contains(e.target));
    if (open && !e.target.closest(".toolbar")) {
      closeMenus();
      e.preventDefault();
      e.stopPropagation();
    }
  }, true);
  document.addEventListener("click", e => {
    if (!jump.contains(e.target)) jump.open = false;
    if (!more.contains(e.target)) more.open = false;
    const related = e.target.closest(".relations a");
    if (related) {
      e.preventDefault(); touch();
      openRelated(related.hash.slice(1));
      return;
    }
    const speaker = e.target.closest("button[data-speak]");
    if (speaker) {
      say(speaker.closest(".word").querySelector(".sentence").textContent, speaker, .95);
      return;
    }
    const button = e.target.closest("button[data-action]");
    if (revealed && (!revealed.contains(e.target) || button)) clearSwipe();
    if (button) change(button.closest(".word"), button.dataset.action);
    else if (e.target.closest(".word summary")) {
      touch();
      const row = e.target.closest(".word");
      // Opening a row also pronounces it; this runs inside the tap, which iOS requires for speech.
      if (!row.querySelector("details").open) {
        extras(row);
        say(row.querySelector("b").textContent, row.querySelector(".ipa"), .85);
      } else if (speaking && row.contains(speaking)) stopSpeaking();
      // The default summary action runs after the click handler.
      setTimeout(() => { if (row.isConnected) savePlace(bookmark(row), true); }, 0);
    }
  });
  for (const menu of [jump, more]) {
    menu.addEventListener("keydown", e => {
      if (e.key === "Escape") { menu.open = false; menu.querySelector("summary").focus(); }
    });
  }
  more.addEventListener("toggle", () => { if (more.open) showAppearance(); });
  function armScroll(e) {
    if (!ready || e.target.closest?.("#jump, #more, input, #view")) return;
    interaction++;
    scrollArmed = true;
  }
  window.addEventListener("wheel", armScroll, {passive:true});
  window.addEventListener("touchmove", armScroll, {passive:true});
  window.addEventListener("pointerdown", e => {
    if (e.target === document.documentElement || e.button === 1) armScroll(e);
  });
  window.addEventListener("keydown", e => {
    if (["ArrowDown","ArrowUp","PageDown","PageUp","Home","End"," "].includes(e.key)) armScroll(e);
  });
  let trackFrame = 0;
  window.addEventListener("scroll", () => {
    if (!scrollArmed) {
      if (!trackFrame) trackFrame = requestAnimationFrame(() => { trackFrame = 0; track(false); });
      return;
    }
    clearTimeout(scrollTimer);
    deferred = () => { scrollArmed = false; track(true); };
    scrollTimer = setTimeout(flush, 180);
  }, {passive:true});
  function deactivate() { flush(); foreground = false; }
  function activate() {
    if (foreground || document.hidden) return;
    foreground = true;
    sync(true);
  }
  window.addEventListener("pagehide", () => { stopSpeaking(); deactivate(); });
  window.addEventListener("pageshow", e => { if (e.persisted) activate(); });
  window.addEventListener("blur", deactivate);
  window.addEventListener("focus", activate);
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) { stopSpeaking(); deactivate(); } else activate();
  });
  window.addEventListener("storage", e => { if (local && e.key === scoreKey) sync(); });
  if (!local) setInterval(() => { if (!document.hidden) sync(); }, 15000);

  const entries = $("#entries");
  entries.addEventListener("pointerdown", e => {
    // A fresh contact is a new action, even immediately after an archive swipe.
    suppressClickUntil = 0;
    if (!ready || !e.isPrimary || e.pointerType === "mouse" || !e.target.closest("summary") || selectedView() === "archive") return;
    const row = e.target.closest(".word");
    if (!row) return;
    clearSwipe();
    gesture = {row, id:e.pointerId, x:e.clientX, y:e.clientY, distance:0};
  });
  entries.addEventListener("pointermove", e => {
    if (!gesture || e.pointerId !== gesture.id) return;
    const dx = gesture.x - e.clientX, dy = Math.abs(gesture.y - e.clientY);
    if (!gesture.distance && (dy > 10 && dy >= dx || dx < -12)) { gesture = null; return; }
    if (!gesture.distance && (dx < 12 || dx < dy * 1.3)) return;
    if (!gesture.distance) extras(gesture.row);
    gesture.row.setPointerCapture(e.pointerId);
    gesture.distance = Math.max(dx, 0);
    gesture.row.classList.add("dragging");
    gesture.row.style.setProperty("--slide", -Math.min(gesture.distance, gesture.row.clientWidth * .55) + "px");
  });
  function endSwipe(e) {
    if (!gesture || e.pointerId !== gesture.id) return;
    const {row, distance} = gesture;
    gesture = null;
    row.classList.remove("dragging");
    row.style.removeProperty("--slide");
    if (e.type === "pointercancel" || !distance) return;
    suppressClickUntil = performance.now() + 350;
    if (distance >= Math.min(165, row.clientWidth * .38)) change(row, "archive");
    else if (distance >= 32) { revealed = row; row.classList.add("revealed"); }
  }
  entries.addEventListener("pointerup", endSwipe);
  entries.addEventListener("pointercancel", endSwipe);
  entries.addEventListener("click", e => {
    if (performance.now() < suppressClickUntil && !e.target.closest("button")) {
      e.preventDefault(); e.stopPropagation();
    } else if (revealed?.contains(e.target) && !e.target.closest("button")) {
      clearSwipe(); e.preventDefault(); e.stopPropagation();
    }
  }, true);
  setGroup(groups[0]);
  $("#filters").hidden = false;
  sentenceButton.hidden = false;
  for (const item of document.querySelectorAll(".menu .toggle, .menu .appearance")) item.hidden = false;
  window.addEventListener("wordroom:themechange", showAppearance);
  const online = $("#online");
  if (local && online) {
    online.hidden = false;
    if (!new URLSearchParams(location.search).has("offline")) {
      const probe = new Image();
      let untouched = true;
      for (const event of ["pointerdown", "keydown", "wheel", "touchstart"])
        window.addEventListener(event, () => { untouched = false; }, {once:true, passive:true});
      const expiry = setTimeout(() => { probe.onload = null; }, 3500);
      probe.onload = () => {
        clearTimeout(expiry);
        if (untouched && probe.naturalWidth === 192) {
          const fallback = rowFor(localBookmark());
          location.replace(online.href + (fallback ? "#" + fallback.id : location.hash));
        }
      };
      probe.src = new URL("/icons/icon-192.png", online.href) + "?reach=" + Date.now();
    }
  }
  sync(true);
  setTimeout(() => loadDetails().catch(() => { /* A row that opens retries and reports the failure. */ }), 1200);
})();
