"""Prototype: build one sourced, checked reading trail from a word list and encyclopedia articles.

A trail is a few short passages about one idea. Each passage brings in a handful of words from
the list. Every sentence cites the source paragraphs it relies on, and a separate model call
checks each sentence against those paragraphs. A sentence that fails is rewritten once and
checked again, then dropped. See docs/learning-system.md.

Usage:
  python3.12 scripts/prototype_trail.py --words <list.txt|export.sqlite3> --provider-dir <data dir> \\
    --topic "How synthesizers learned to imitate anything" --article Synthesizer --article "Moog synthesizer" \\
    --out .runtime/trails/synthesis

The provider directory is read only, for the model settings. Results go to --out only.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import date
from html import escape
import json
from pathlib import Path
import re
import sqlite3
import sys
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from providers import ProviderSettings, request_json  # noqa: E402
from build_wordlist import word_pattern, write_page  # noqa: E402

WIKI = "https://en.wikipedia.org/w/api.php"
USER_AGENT = "Wordroom-prototype/0.1 (learning trail research; https://github.com/Audiofool934/wordroom)"
SKIP_SECTIONS = {"see also", "references", "notes", "further reading", "external links", "bibliography", "sources",
                 "citations", "footnotes", "discography"}
MAX_PARAGRAPHS = 45

PLAN = (
    "You plan a reading trail for a vocabulary learner. A trail is a few short passages about one idea, "
    "written in the style of a GRE Reading Comprehension passage, that together teach true facts from the given sources "
    "and bring in words from the learner's word list. "
    "Choose an arc of {passages} passages that builds understanding step by step. For each passage give a heading, "
    "a one-sentence focus, and the paragraph ids (from the sources) it will draw on; use only paragraphs that are given. "
    "Then choose about {per} target words for each passage from the word list: words that can be used naturally and "
    "accurately when stating that passage's facts. A word must fit what the sources actually say: do not pick a word of "
    "praise or judgment (such as sophisticated or influential) unless the sources make that judgment themselves. "
    "Skip the topic's own technical terms and very common words; prefer harder, more useful words. "
    "Use each word only once in the trail, spelled exactly as in the list. "
    'Reply with JSON only: {{"title": "...", "intro": "one sentence", "passages": [{{"heading": "...", "focus": "...", '
    '"paragraphs": ["S1.P3"], "targets": ["word"]}}]}}'
)
WRITE = (
    "You write one passage of a reading trail for a vocabulary learner. Write 6 to 9 sentences, about 150 to 220 words, "
    "in the dense, precise style of a GRE Reading Comprehension passage. Vary sentence structure. "
    "Every sentence must be fully supported by the source paragraphs it cites: state no fact, date, name, number or cause "
    "that those paragraphs do not state. Do not add outside knowledge, even if you are sure of it. "
    "A sentence that only frames or connects ideas must still cite the paragraph it frames. "
    "Use every target word once, in the sense its meaning hint gives, spelled exactly as given (a verb in its base form). "
    "Use each word as an author would, never defining it inline (no 'that is', no dashes or parentheses that gloss it). "
    "Build a line of thought across the sentences, with contrast, cause and consequence where the sources support them, "
    "and never repeat a fact already stated in this passage or listed as covered before. "
    "Never mention the sources, paragraphs, passage or article in the text. "
    "If a target word cannot be used truthfully, leave it out rather than bend a fact. "
    "For each target word, also give a short English gloss of the sense you used. "
    'Reply with JSON only: {"sentences": [{"text": "...", "cites": ["S1.P3"], "words": ["target"]}], '
    '"glosses": {"target": "sense as used"}}'
)
CHECK = (
    "You check a reading passage for factual support. For each numbered sentence you get the source paragraphs it cites. "
    "Judge only whether those paragraphs support everything the sentence states. Ignore style and word choice. "
    "supported: every claim is stated in or directly implied by the paragraphs. "
    "partial: some claim (a detail, date, cause, number, name or degree) goes beyond the paragraphs. "
    "unsupported: the main claim is not in the paragraphs or contradicts them. "
    "Paraphrase and summary are fine; added specifics are not. "
    'Reply with JSON only: {"verdicts": [{"index": 0, "verdict": "supported", "reason": "short"}]}'
)
REWRITE = (
    "Rewrite one sentence so that the cited source paragraphs fully support everything it states. "
    "Keep the target words, spelled exactly as given and in the same sense, when they can be used truthfully; "
    "otherwise leave a word out. Remove any claim the paragraphs do not state. "
    "Never mention the sources, paragraphs, passage or what they do or do not say; write only about the subject. "
    'Reply with JSON only: {"text": "...", "words": ["targets still used"]}'
)


def read_words(path):
    """Entries from a SQLite export (word, meaning_zh) or a text list ("word" or "word<TAB>meaning" per line)."""
    path = Path(path)
    if path.suffix in {".sqlite3", ".sqlite", ".db"}:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            rows = db.execute("SELECT word, meaning_zh FROM words ORDER BY rowid").fetchall()
    else:
        rows = [(line.split("\t") + [""])[:2] for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    entries = {}
    for word, meaning in rows:
        word = word.strip()
        if word and word.casefold() not in entries:
            entries[word.casefold()] = {"word": word, "meaning": (meaning or "").strip()}
    return entries


def fetch_article(title):
    query = {"action": "query", "prop": "extracts|revisions", "explaintext": 1, "rvprop": "ids|timestamp",
             "titles": title, "redirects": 1, "format": "json", "formatversion": 2}
    with urlopen(Request(WIKI + "?" + urlencode(query), headers={"User-Agent": USER_AGENT}), timeout=60) as response:
        page = json.load(response)["query"]["pages"][0]
    if page.get("missing"):
        raise SystemExit(f"No article named {title!r}.")
    revision = page["revisions"][0]
    return {"title": page["title"], "extract": page["extract"], "revision": revision["revid"],
            "url": "https://en.wikipedia.org/wiki/" + quote(page["title"].replace(" ", "_")),
            "retrieved": date.today().isoformat(), "license": "CC BY-SA 4.0"}


def paragraphs(extract, limit=MAX_PARAGRAPHS):
    """Body paragraphs with their section, skipping reference sections and short lines."""
    section, out = "", []
    for line in extract.splitlines():
        line = line.strip()
        heading = re.fullmatch(r"=+\s*(.*?)\s*=+", line)
        if heading:
            section = heading.group(1)
            continue
        if section.casefold() in SKIP_SECTIONS or len(line) < 120:
            continue
        out.append({"section": section or "Introduction", "text": line})
        if len(out) >= limit:
            break
    return out


def check_plan(plan, entries, paragraph_ids):
    """Keep passages with known paragraphs and list words; drop repeated or unknown words."""
    used, passages = set(), []
    for item in plan.get("passages") or []:
        cites = [p for p in item.get("paragraphs") or [] if p in paragraph_ids]
        targets = []
        for word in item.get("targets") or []:
            key = str(word).strip().casefold()
            if key in entries and key not in used:
                used.add(key)
                targets.append(entries[key]["word"])
        if cites and targets and item.get("heading"):
            passages.append({"heading": str(item["heading"]), "focus": str(item.get("focus") or ""),
                             "paragraphs": cites, "targets": targets})
    if not passages:
        raise ValueError("The plan has no usable passages.")
    return {"title": str(plan.get("title") or "Untitled trail"), "intro": str(plan.get("intro") or ""), "passages": passages}


# Text about the sources rather than the subject, which a support check would wrongly pass.
SELF_REFERENCE = re.compile(r"\b(paragraphs?|passages?|the (cited )?sources?|cited|the article)\b", re.IGNORECASE)


def check_sentences(written, targets, paragraph_ids):
    """Sentences with valid citations and no talk about the sources, and the target words each one contains."""
    sentences = []
    for item in written.get("sentences") or []:
        text = " ".join(str(item.get("text") or "").split())
        cites = [c for c in item.get("cites") or [] if c in paragraph_ids]
        if not text or not cites or SELF_REFERENCE.search(text):
            continue
        words = [w for w in targets if word_pattern(w).search(text)]
        sentences.append({"text": text, "cites": cites, "words": words})
    return sentences


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--words", required=True, help="Word list: text file or SQLite export")
    parser.add_argument("--provider-dir", type=Path, required=True, help="Data dir holding the model settings (read only)")
    parser.add_argument("--topic", required=True)
    parser.add_argument("--article", action="append", required=True, help="Encyclopedia article title; repeat")
    parser.add_argument("--passages", type=int, default=5)
    parser.add_argument("--per-passage", type=int, default=5)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    entries = read_words(args.words)
    provider, config = ProviderSettings(args.provider_dir).active()
    usage = {"prompt_tokens": 0, "completion_tokens": 0}

    def ask(system, task):
        parsed, used = request_json(provider, config, system, task)
        for key in usage:
            usage[key] += used.get(key, 0)
        return parsed

    sources, paragraph_text = [], {}
    for n, title in enumerate(args.article, start=1):
        article = fetch_article(title)
        article["paragraphs"] = []
        for m, para in enumerate(paragraphs(article.pop("extract")), start=1):
            pid = f"S{n}.P{m}"
            paragraph_text[pid] = para["text"]
            article["paragraphs"].append({"id": pid, **para})
        sources.append(article)
        print(f"source S{n}: {article['title']} ({len(article['paragraphs'])} paragraphs)", flush=True)

    outline = [{"id": p["id"], "section": p["section"], "text": p["text"]} for s in sources for p in s["paragraphs"]]
    plan = check_plan(ask(PLAN.format(passages=args.passages, per=args.per_passage),
                          {"topic": args.topic, "sources": outline, "word_list": [e["word"] for e in entries.values()]}),
                      entries, paragraph_text)
    print(f"plan: {plan['title']} - {[len(p['targets']) for p in plan['passages']]} targets", flush=True)

    def build(index_passage):
        index, passage = index_passage
        targets = passage["targets"]
        cited = {pid: paragraph_text[pid] for pid in passage["paragraphs"]}
        written = ask(WRITE, {"trail": plan["title"], "heading": passage["heading"], "focus": passage["focus"],
                              "covered_before": [f'{p["heading"]}: {p["focus"]}' for p in plan["passages"][:index]],
                              "targets": [{"word": w, "meaning_hint": entries[w.casefold()]["meaning"]} for w in targets],
                              "paragraphs": cited})
        sentences = check_sentences(written, targets, paragraph_text)
        log = []

        def verify(items):
            task = {"sentences": [{"index": i, "text": s["text"],
                                   "paragraphs": {c: paragraph_text[c] for c in s["cites"]}} for i, s in items]}
            verdicts = {v.get("index"): v for v in ask(CHECK, task).get("verdicts") or [] if isinstance(v, dict)}
            return {i: verdicts.get(i, {"verdict": "unsupported", "reason": "no verdict"}) for i, _ in items}

        verdicts = verify(list(enumerate(sentences)))
        for i, sentence in enumerate(sentences):
            sentence["check"] = verdicts[i]
        failed = [i for i, s in enumerate(sentences) if s["check"].get("verdict") != "supported"]
        for i in failed:
            original = sentences[i]
            rewritten = ask(REWRITE, {"sentence": original["text"], "reason": original["check"].get("reason", ""),
                                      "targets": original["words"],
                                      "paragraphs": {c: paragraph_text[c] for c in original["cites"]}})
            text = " ".join(str(rewritten.get("text") or "").split())
            if text and not SELF_REFERENCE.search(text):
                words = [w for w in original["words"] if word_pattern(w).search(text)]
                candidate = {**original, "text": text, "words": words, "rewritten_from": original["text"]}
                candidate["check"] = verify([(0, candidate)])[0]
                log.append({"before": original["text"], "after": text, "first": original["check"],
                            "second": candidate["check"]})
                sentences[i] = candidate
        kept = [s for s in sentences if s["check"].get("verdict") == "supported"]
        dropped = [s for s in sentences if s["check"].get("verdict") != "supported"]
        used = {w for s in kept for w in s["words"]}
        glosses = {w: str(g) for w, g in (written.get("glosses") or {}).items() if w in targets}
        print(f"passage {index + 1}: {len(kept)} kept, {len(dropped)} dropped, {len(failed)} rewritten, "
              f"words {len(used)}/{len(targets)}", flush=True)
        return {**passage, "sentences": kept, "dropped": dropped, "rewrites": log,
                "glosses": glosses, "missing_words": [w for w in targets if w not in used]}

    with ThreadPoolExecutor(max_workers=len(plan["passages"])) as pool:
        passages = list(pool.map(build, enumerate(plan["passages"])))

    trail = {"topic": args.topic, "title": plan["title"], "intro": plan["intro"], "created": date.today().isoformat(),
             "model": config["model"], "usage": usage, "sources": sources, "passages": passages,
             "meanings": {w: entries[w.casefold()]["meaning"] for p in passages for w in p["targets"]}}
    args.out.mkdir(parents=True, exist_ok=True)
    write_page(args.out / "trail.json", json.dumps(trail, ensure_ascii=False, indent=1))
    write_page(args.out / "trail.html", render(trail))
    print(f"wrote {args.out}/trail.json and trail.html; tokens {usage}", flush=True)


def render(trail):
    """A standalone reading page in the word list's Nord style."""
    source_index = {p["id"]: (n, s) for n, s in enumerate(trail["sources"], start=1) for p in s["paragraphs"]}
    paragraph_text = {p["id"]: p["text"] for s in trail["sources"] for p in s["paragraphs"]}
    meanings = trail.get("meanings", {})
    blocks = []
    for number, passage in enumerate(trail["passages"], start=1):
        glosses = passage.get("glosses", {})
        sentences = []
        for sentence in passage["sentences"]:
            text = sentence["text"]
            # Find every word on the plain text first, so one word never matches inside another's markup.
            spans = sorted((m.start(), m.end(), w) for w in sentence["words"] for m in [word_pattern(w).search(text)] if m)
            html, at = "", 0
            for start, end, word in spans:
                if start < at:
                    continue
                note = glosses.get(word, "") + (" · " + meanings[word] if meanings.get(word) else "")
                html += (escape(text[at:start]) + f'<button class="target" type="button" data-note="{escape(note)}">'
                         + escape(text[start:end]) + "</button>")
                at = end
            html += escape(text[at:])
            marks = "".join(
                f'<button class="cite" type="button" data-cite="{escape(c)}" aria-label="Source {source_index[c][0]}, '
                f'paragraph {escape(c.split(".")[1][1:])}">{source_index[c][0]}</button>' for c in sentence["cites"])
            sentences.append(f'<span class="s">{html}<sup>{marks}</sup></span>')
        words = " · ".join(escape(w) for w in passage["targets"] if w not in passage.get("missing_words", []))
        blocks.append(f'<section><h2><span>{number:02}</span>{escape(passage["heading"])}</h2>'
                      f'<p class="passage" lang="en">{" ".join(sentences)}</p>'
                      f'<p class="words">{words}</p></section>')
    sources = "".join(
        f'<li><a href="{escape(s["url"])}?oldid={s["revision"]}">{escape(s["title"])}</a>, Wikipedia, '
        f'revision {s["revision"]}, retrieved {escape(s["retrieved"])}, {escape(s["license"])}</li>' for s in trail["sources"])
    checked = sum(len(p["sentences"]) for p in trail["passages"])
    dropped = sum(len(p["dropped"]) for p in trail["passages"])
    data = json.dumps(paragraph_text, ensure_ascii=False).replace("</", "<\\/")
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{escape(trail["title"])} · Wordroom trail</title>
<style>
:root {{ color-scheme: light; --paper:#eceff4; --fill:#d8dee9; --line:#d8dee9; --ink:#2e3440; --text:#4c566a; --accent:#4a617f;
  --serif: ui-serif, "New York", "Iowan Old Style", Georgia, serif; font-family: -apple-system, system-ui, "PingFang SC", sans-serif; }}
@media (prefers-color-scheme: dark) {{ :root {{ color-scheme: dark; --paper:#2e3440; --fill:#3b4252; --line:#434c5e; --ink:#eceff4; --text:#bcc3d0; --accent:#97c7d5; }} }}
* {{ box-sizing: border-box; }} body {{ margin:0; background:var(--paper); color:var(--text); }}
main {{ max-width: 40rem; margin: 0 auto; padding: max(1.5rem, env(safe-area-inset-top)) 1.25rem 30vh; }}
.kicker {{ font: 600 .75rem/1.2 ui-monospace, Menlo, monospace; letter-spacing:.08em; text-transform:uppercase; }}
h1 {{ font-family:var(--serif); font-weight:500; color:var(--ink); font-size:1.9rem; line-height:1.2; margin:.4rem 0 .6rem; }}
.intro {{ font-size:1rem; line-height:1.55; margin:0 0 .5rem; }}
.meta {{ font-size:.8125rem; margin:0 0 1.5rem; }}
h2 {{ display:flex; gap:.7rem; align-items:baseline; font-family:var(--serif); font-weight:500; color:var(--ink); font-size:1.25rem; margin:2.2rem 0 .7rem; }}
h2 span {{ font: 600 .75rem ui-monospace, Menlo, monospace; color:var(--text); }}
.passage {{ font-family:var(--serif); font-size:1.125rem; line-height:1.7; color:var(--ink); margin:0; }}
button {{ font:inherit; color:inherit; background:none; border:0; padding:0; cursor:pointer; }}
.target {{ font-weight:600; color:var(--accent); text-decoration: underline 1.5px; text-underline-offset:.2em; text-decoration-color: color-mix(in srgb, var(--accent) 40%, transparent); }}
sup {{ line-height:0; }} .cite {{ font: 600 .6875rem ui-monospace, Menlo, monospace; color:var(--text); padding:0 .12rem; min-width:1rem; }}
.words {{ font-size:.8125rem; letter-spacing:.02em; margin:.7rem 0 0; }}
.pop {{ position:fixed; left:1rem; right:1rem; bottom:max(1rem, env(safe-area-inset-bottom)); max-width:38rem; margin:0 auto; max-height:45vh; overflow:auto;
  background:var(--paper); color:var(--ink); border:1px solid var(--line); border-radius:1rem; padding:.9rem 1rem; box-shadow:0 10px 30px rgb(0 0 0 / .18); font-size:.9375rem; line-height:1.5; }}
.pop b {{ display:block; font-size:.75rem; letter-spacing:.06em; text-transform:uppercase; color:var(--text); margin-bottom:.3rem; }}
footer {{ margin-top:3rem; border-top:1px solid var(--line); padding-top:1rem; font-size:.8125rem; line-height:1.5; }}
footer a {{ color:var(--accent); }} footer ol {{ padding-left:1.2rem; }}
</style></head><body><main>
<p class="kicker">Wordroom trail · prototype</p>
<h1>{escape(trail["title"])}</h1>
<p class="intro">{escape(trail["intro"])}</p>
<p class="meta">{len(trail["passages"])} passages · {sum(len(p["targets"]) - len(p.get("missing_words", [])) for p in trail["passages"])} words · {checked} sentences checked against their sources{f", {dropped} dropped" if dropped else ""}. Tap a word for its sense, a number for its source.</p>
{"".join(blocks)}
<footer><p>Sources. Every sentence cites the paragraphs it relies on, and a separate model call checked it against them. Model checks can still miss errors.</p>
<ol>{sources}</ol><p>Passages adapt text from Wikipedia, available under CC BY-SA 4.0.</p></footer>
</main><div class="pop" hidden></div>
<script type="application/json" id="paragraphs">{data}</script>
<script>
const paragraphs = JSON.parse(document.getElementById("paragraphs").textContent);
const pop = document.querySelector(".pop");
document.addEventListener("click", e => {{
  const target = e.target.closest(".target"), cite = e.target.closest(".cite");
  if (!target && !cite) {{ if (!pop.contains(e.target)) pop.hidden = true; return; }}
  pop.replaceChildren();
  const label = document.createElement("b"), body = document.createElement("div");
  if (target) {{ label.textContent = target.textContent; body.textContent = target.dataset.note; }}
  else {{ label.textContent = "Source " + cite.textContent + ", paragraph " + cite.dataset.cite.split(".P")[1]; body.textContent = paragraphs[cite.dataset.cite]; }}
  pop.append(label, body); pop.hidden = false;
}});
</script></body></html>
'''


if __name__ == "__main__":
    main()
