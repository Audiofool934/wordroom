"""Generate synonyms, antonyms and related words for the word database with the active provider.

Words that already have a lesson in an older Wordroom database are skipped; the page uses the lesson's lists.
Results go to <data-dir>/wordlist_relations.json. The file is written after each batch,
so a stopped run resumes where it left off. It never writes to the database.

Usage: python3.12 scripts/generate_wordlist_relations.py <words.sqlite3> --data-dir <dir> [--limit N]
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from providers import ProviderSettings, request_json  # noqa: E402
from build_wordlist import load_relations, write_page  # noqa: E402

BATCH = 20
KEYS = ("synonyms", "antonyms", "related")
SYSTEM = (
    "You are a GRE verbal tutor. For each entry, give word lists for the sense shown by its Chinese meaning and example. "
    "synonyms: up to 5 single words (or fixed two-word phrases) that a GRE Sentence Equivalence or Text Completion item could pair with it in that sense; prefer GRE-level vocabulary; no loose associations. "
    "antonyms: up to 4 words with the opposite meaning in that sense; give an empty list when there is no clear antonym. "
    "related: up to 3 words a GRE student should learn beside it: near-synonyms that differ in tone, degree or scope, or well-known confusables (such as eminent/imminent). "
    "A related word must be close in meaning or a genuine, common confusion; never list a word only because it is spelled alike. Give fewer or none rather than pad. "
    "Never list the entry itself or another form of the same root (no confound -> confounded). A word may appear in only one list. "
    'Reply with JSON only: {"entries": [{"word": "...", "synonyms": [...], "antonyms": [...], "related": [...]}]} in the same order as the input.'
)


def clean(values, word):
    out = []
    for value in values if isinstance(values, list) else []:
        if not isinstance(value, str):
            continue
        value = " ".join(value.split())
        if 0 < len(value) <= 40 and value.casefold() != word.casefold() and value.casefold() not in {v.casefold() for v in out}:
            out.append(value)
    return out[:6]


def validate(batch, parsed):
    entries = parsed.get("entries") if isinstance(parsed, dict) else None
    if not isinstance(entries, list):
        raise ValueError("no entries list")
    found = {}
    for item in entries:
        if isinstance(item, dict) and isinstance(item.get("word"), str):
            found[item["word"].strip().casefold()] = item
    result = {}
    for word, *_ in batch:
        item = found.get(word.casefold())
        if not item:
            continue
        lists, seen = {}, set()
        for key in KEYS:
            lists[key] = [v for v in clean(item.get(key), word) if v.casefold() not in seen]
            seen.update(v.casefold() for v in lists[key])
        result[word] = lists
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", type=Path)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--limit", type=int, help="Only process this many words (for a trial)")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()

    with closing(sqlite3.connect(args.source.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        rows = db.execute("SELECT word, meaning_zh, example_en FROM words ORDER BY group_number, position").fetchall()
    lessons = load_relations(args.data_dir)
    target = args.data_dir / "wordlist_relations.json"
    done = json.loads(target.read_text(encoding="utf-8")) if target.is_file() else {}
    todo = list({r[0]: r for r in rows if r[0].casefold() not in lessons and r[0] not in done}.values())
    if args.limit:
        todo = todo[:args.limit]
    print(f"{len(done)} done, {len(todo)} to generate", flush=True)
    if not todo:
        return

    provider, config = ProviderSettings(args.data_dir).active()
    lock = threading.Lock()
    usage = {"prompt_tokens": 0, "completion_tokens": 0}
    failed = []

    def run(batch):
        task = {"entries": [{"word": w, "meaning_zh": m, "example": e} for w, m, e in batch]}
        parsed, used = request_json(provider, config, SYSTEM, task)
        return validate(batch, parsed), used

    batches = [todo[i:i + BATCH] for i in range(0, len(todo), BATCH)]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(run, b): b for b in batches}
        for n, future in enumerate(as_completed(futures), start=1):
            batch = futures[future]
            try:
                result, used = future.result()
            except Exception as exc:  # Keep going; a rerun retries the missing words.
                failed.extend(w for w, *_ in batch)
                print(f"batch {n}/{len(batches)} failed: {type(exc).__name__}: {str(exc)[:120]}", flush=True)
                continue
            with lock:
                done.update(result)
                failed.extend(w for w, *_ in batch if w not in result)
                for key in usage:
                    usage[key] += used.get(key, 0)
                write_page(target, json.dumps(done, ensure_ascii=False, indent=0, sort_keys=True))
            print(f"batch {n}/{len(batches)}: {len(result)}/{len(batch)} words, total {len(done)}", flush=True)
    print(f"finished: {len(done)} words saved, {len(failed)} missing, tokens {usage}", flush=True)


if __name__ == "__main__":
    main()
