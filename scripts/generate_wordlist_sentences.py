"""Write a GRE-style example sentence, its Chinese translation and a structure breakdown for each word in the word database.

The sentences read like GRE reading passages: long, academic and complex in structure.
Each word gets a sentence pattern and a topic from fixed lists, picked by a hash of the word,
so the list varies in a way the model cannot drift away from.
The breakdown splits the sentence into its main clause, subordinate clauses and modifiers,
and adds the main clause's skeleton and a short Chinese note on the structure.
Results go to <data-dir>/wordlist_sentences.json, keyed by word. The file is written
after each batch, so a stopped run resumes where it left off. Words without a breakdown
(from the first version of this script) are written again. An entry that fails a check
is dropped, and a rerun asks for it again.

Usage: python3.12 scripts/generate_wordlist_sentences.py <words.sqlite3> --data-dir <dir> [--limit N]
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing
import json
from zlib import crc32
from pathlib import Path
import sqlite3
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from providers import ProviderSettings, request_json  # noqa: E402
from build_wordlist import word_pattern, write_page  # noqa: E402

BATCH = 8
ROLES = ("core", "clause", "phrase", "other")
PATTERNS = (
    "open with the grammatical subject, then interrupt it with a nonrestrictive relative clause (', which ...,' or ', whose ...,')",
    "open with a participial phrase ('Drawing on ...,' / 'Long regarded as ...,') before the main clause",
    "open with 'Far from ...' or 'Rather than ...' and then state the actual claim",
    "use a cleft or pseudo-cleft ('It is ... that ...' or 'What ... is ...')",
    "use inversion after a limiting phrase ('Only after ... did ...', 'Not until ... did ...', 'Nowhere is ... more ...')",
    "put an appositive right after the subject (', a ... ,') and end with a 'that' clause",
    "state the main claim first and end with a contrast introduced by 'whereas' or 'even as'",
    "open with a concession: 'Although', 'Though', 'Granted that' or 'While'",
    "interrupt the main clause with a parenthetical between dashes",
    "use a correlative structure ('not only ... but also ...', 'less ... than ...', 'not so much ... as ...')",
    "open with a conditional ('Had ...,', 'Were ...,', 'If ...,') and draw a consequence",
    "use a long noun phrase or a gerund phrase as the subject of the main clause",
    "join two independent clauses with a semicolon and a word such as 'indeed', 'nevertheless' or 'thus'",
    "use a comparative structure ('the more ..., the more ...' or 'no less ... than ...')",
    "report a scholarly debate: 'Some historians contend that ...; others, however, ...'",
    "end the sentence with an absolute phrase or a participial phrase that adds a consequence",
)
TOPICS = (
    "evolutionary biology", "ecology", "astronomy", "geology", "chemistry", "physics", "the history of science",
    "medicine and public health", "economics", "economic history", "political history", "colonial history",
    "sociology", "anthropology", "archaeology", "linguistics", "psychology", "literary criticism", "the novel",
    "poetry", "art history", "music", "architecture", "philosophy", "the history of ideas", "law", "education",
    "film and photography", "urban studies", "technology and its history",
)
SYSTEM = (
    "You write example sentences for a GRE vocabulary list. Each sentence must read like a line from a GRE "
    "Reading Comprehension passage, so the student practises dense academic prose while learning the word. "
    "For each entry, write ONE sentence of 35 to 60 words that uses the entry word in the sense shown by its Chinese meaning. "
    "Use the entry word exactly as spelled (capitalize only at the start of the sentence); for a phrase such as 'attributed to', keep the whole phrase together. "
    "For a verb, keep the exact base form, for example after 'to', a modal such as 'could', or 'did not'; never add -s, -ed or -ing. "
    "Each entry gives a topic and a sentence pattern. Write about that topic and build the sentence on that pattern. "
    "The pattern is required: do not fall back on an opening 'Although' unless the pattern asks for a concession. "
    "The context must make the word's meaning inferable, so a careful reader could guess it. "
    "Keep facts plausible and general; do not invent precise statistics or quotations, and never name a living person. "
    "Then give a faithful, natural Chinese translation of the whole sentence. "
    "Then break the sentence down for a reader who is learning to parse long sentences. "
    "parts: the whole sentence cut into consecutive pieces, as [text, role] pairs. Joined together in order, the texts "
    "must give back the sentence exactly, character for character, including spaces and punctuation. Roles: "
    "core = the words of the main clause's skeleton (its subject, main verb, and object or complement; for a 'that' clause "
    "that is the object of the main verb, include its own subject and verb too); "
    "clause = a subordinate or relative clause that is not part of the skeleton; "
    "phrase = a modifier such as a participial, appositive, absolute or prepositional phrase, or a parenthetical; "
    "other = anything else, such as connecting words and the parts of the main clause that are not in the skeleton. "
    "The core pieces, read in order, must give the skeleton and nothing more: at most about 15 words in all. "
    "Objects' and nouns' modifiers inside the main clause are phrase or clause pieces, not core. "
    "When the sentence has two independent clauses (joined by a semicolon or 'and'/'but'), mark the skeleton of each as core. "
    "Keep the other pieces fairly large: whole clauses and phrases, not single words. "
    "Example: 'Drawing on parish records, historians have argued that the plague, which spread along trade routes, "
    "transformed rural labor markets more quickly than earlier scholars assumed.' gives parts "
    '[["Drawing on parish records", "phrase"], [", ", "other"], ["historians have argued", "core"], [" that ", "other"], '
    '["the plague", "core"], [", which spread along trade routes,", "clause"], [" transformed rural labor markets", "core"], '
    '[" more quickly", "other"], [" than earlier scholars assumed", "clause"], [".", "other"]] '
    "and skeleton 'Historians have argued that the plague transformed rural labor markets.' "
    "skeleton: the main clause reduced to its bare skeleton in English, at most 15 words. "
    "note: one short Chinese sentence that names the structure, for example "
    "'让步状语从句 + 主句，主句宾语带 that 从句，句末为分词短语。' "
    'Reply with JSON only: {"entries": [{"word": "...", "en": "...", "zh": "...", "parts": [["...", "core"]], '
    '"skeleton": "...", "note": "..."}]} in the same order as the input.'
)


def assignment(word):
    """A fixed pattern and topic for each word, spread evenly over the lists."""
    h = crc32(word.encode("utf-8"))
    return PATTERNS[h % len(PATTERNS)], TOPICS[(h // len(PATTERNS)) % len(TOPICS)]


def clean_parts(parts, en):
    """Return the parts when they rebuild the sentence exactly and every role is known, else None."""
    if not isinstance(parts, list) or not parts:
        return None
    out = []
    for part in parts:
        if not (isinstance(part, list) and len(part) == 2 and all(isinstance(x, str) for x in part)) or part[1] not in ROLES:
            return None
        if out and out[-1][1] == part[1]:
            out[-1][0] += part[0]  # Merge neighbours with the same role.
        elif part[0]:
            out.append(list(part))
    if "".join(text for text, _ in out) != en or not any(role == "core" for _, role in out):
        return None
    return out


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
        item = found.get(word.casefold()) or {}
        en = str(item.get("en") or "").strip()
        zh, skeleton, note = (" ".join(str(item.get(k) or "").split()) for k in ("zh", "skeleton", "note"))
        parts = clean_parts(item.get("parts"), en)
        core = sum(len(text.split()) for text, role in (parts or []) if role == "core")
        if (20 <= len(en.split()) <= 80 and zh and skeleton and note and parts and core <= 24
                and word_pattern(word).search(en)):
            result[word] = {"en": en, "zh": zh, "parts": parts, "skeleton": skeleton, "note": note}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", type=Path)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--limit", type=int, help="Only process this many words (for a trial)")
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    with closing(sqlite3.connect(args.source.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        rows = db.execute("SELECT word, meaning_zh, example_en FROM words ORDER BY group_number, position").fetchall()
    target = args.data_dir / "wordlist_sentences.json"
    done = json.loads(target.read_text(encoding="utf-8")) if target.is_file() else {}
    todo = list({r[0]: r for r in rows if "parts" not in done.get(r[0], {})}.values())
    if args.limit:
        todo = todo[:args.limit]
    print(f"{len(done) - len(todo)} done, {len(todo)} to generate", flush=True)
    if not todo:
        return

    provider, config = ProviderSettings(args.data_dir).active()
    lock = threading.Lock()
    usage = {"prompt_tokens": 0, "completion_tokens": 0}
    failed = []

    def run(batch):
        task = {"entries": [{"word": w, "meaning_zh": m, "pattern": assignment(w)[0], "topic": assignment(w)[1]}
                            for w, m, _ in batch]}
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
    print(f"finished: {len(done)} words saved, {len(failed)} missing {failed[:20]}, tokens {usage}", flush=True)


if __name__ == "__main__":
    main()
