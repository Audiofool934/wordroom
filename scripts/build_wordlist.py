"""Build the Wordroom word list page from a SQLite word database (see the README for its columns)."""
import argparse
import json
import re
from base64 import b64encode
from contextlib import closing
from hashlib import sha256
from html import escape
from pathlib import Path
import sqlite3
from string import Template
from tempfile import NamedTemporaryFile

ROOT = Path(__file__).resolve().parents[1]
# Controls ship in the page so nothing is inserted while the list scrolls.
STEPPER = ('<div class="stepper"><button type="button" data-action="minus" aria-label="Lower score">−</button>'
           '<output></output><button type="button" data-action="plus" aria-label="Raise score">+</button></div>')


GROUP_SIZE = 30


def load_families():
    """Map each word to its family, from data/wordlist_families.json."""
    path = ROOT / "data/wordlist_families.json"
    if not path.is_file():
        return {}
    families = json.loads(path.read_text(encoding="utf-8"))["families"]
    return {word: tuple(family) for family in families for word in family}


def load_relations(data_dir):
    """Synonyms, antonyms and related words: lessons from an older Wordroom database first, then generated lists."""
    relations = {}
    generated = data_dir / "wordlist_relations.json"
    if generated.is_file():
        for word, lists in json.loads(generated.read_text(encoding="utf-8")).items():
            relations[word.casefold()] = {
                "Synonyms": lists.get("synonyms") or [],
                "Antonyms": lists.get("antonyms") or [],
                "Related": lists.get("related") or [],
            }
    path = data_dir / "wordroom.sqlite3"
    if not path.is_file():
        return relations
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        try:
            rows = db.execute("SELECT w.word, l.content FROM lessons l JOIN words w ON w.id = l.word_id").fetchall()
        except sqlite3.OperationalError:
            return relations
    for word, content in rows:
        lesson = json.loads(content)
        related = [c.get("word", "") for c in lesson.get("confusions") or [] if isinstance(c, dict)]
        relations[word.casefold()] = {
            "Synonyms": lesson.get("synonyms") or [],
            "Antonyms": lesson.get("antonyms") or [],
            "Related": related,
        }
    return relations


def load_sentences(data_dir):
    """GRE-style sentences from <data-dir>/wordlist_sentences.json, keyed by word.

    Each entry has en and zh, and may have parts, skeleton and note (the structure breakdown)."""
    path = data_dir / "wordlist_sentences.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def word_pattern(word):
    """Match the word, or each part of a phrase in order, as whole words."""
    parts = [re.escape(p) for p in word.split()]
    return re.compile(r"(?<![\w-])" + r"\s+".join(parts) + r"(?![\w-])", re.IGNORECASE)


def marked(sentence, word):
    """Escape the sentence and mark the first use of the word."""
    match = word_pattern(word).search(sentence) if word.strip() else None
    if not match:
        return escape(sentence)
    return (escape(sentence[:match.start()]) + f'<em>{escape(match.group())}</em>' + escape(sentence[match.end():]))


def structured(parts, word):
    """The sentence with each structural piece in its own span, and the first use of the word marked."""
    pattern, found, out = word_pattern(word), False, []
    for text, role in parts:
        inner = escape(text)
        if not found and pattern.search(text):
            inner, found = marked(text, word), True
        if role == "other" or not text.strip():
            out.append(inner)
            continue
        # Spaces stay outside the span, so an underline starts and ends on the words themselves.
        lead, trail = inner[:len(inner) - len(inner.lstrip())], inner[len(inner.rstrip()):]
        out.append(f'{lead}<span class="s-{role}">{inner.strip()}</span>{trail}')
    return "".join(out)


# A key for the structure colours, the same in every open row.
LEGEND = ('<p class="legend"><span class="s-core">Main clause</span><span class="s-clause">Clause</span>'
          '<span class="s-phrase">Modifier</span></p>')


def breakdown(item):
    if not (item.get("parts") and item.get("skeleton")):
        return ""
    note = f'<p class="note" lang="zh-CN">{escape(item["note"])}</p>' if item.get("note") else ""
    return (f'<div class="structure">{LEGEND}<p class="skeleton" lang="en">{escape(item["skeleton"])}</p>'
            f'{note}</div>')


def ordered(rows, families):
    """Keep the source order, but pull each family together where its first member appears."""
    first_row = {}
    for row in rows:
        first_row.setdefault(row[2], row)
    done, result = set(), []
    for row in first_row.values():
        word = row[2]
        if word in done:
            continue
        members = [m for m in families.get(word, (word,)) if m in first_row and m not in done]
        done.update(members)
        result.append([first_row[m] for m in members])
    return result


def chunks(family_rows):
    """Groups of about GROUP_SIZE words that never split a family."""
    groups, current = [], []
    for family in family_rows:
        if current and sum(map(len, current)) + len(family) > GROUP_SIZE:
            groups.append(current)
            current = []
        current.append(family)
    if current:
        groups.append(current)
    return groups


def relation_list(relations, word, ids, family=()):
    # Other forms of the same root come first, so they are never lost among the other lists.
    entry = {"Family": [w for w in family if w.casefold() != word.casefold()], **relations.get(word.casefold(), {})}
    parts, seen = [], {word.casefold()}
    for label, words in entry.items():
        # Each word appears once: a synonym is not listed again as related.
        words = [w for w in dict.fromkeys(w.strip() for w in words) if w and w.casefold() not in seen]
        seen.update(w.casefold() for w in words)
        if not words:
            continue
        items = []
        for w in words:
            target = ids.get(w.casefold())
            items.append(f'<a href="#{target}">{escape(w)}</a>' if target else f'<span>{escape(w)}</span>')
        parts.append(f'<div><dt>{label}</dt><dd lang="en">{"".join(items)}</dd></div>')
    return f'<dl class="relations">{"".join(parts)}</dl>' if parts else ""


def render_pairs(data_dir, links, sections):
    """Add the private pair book in source order. Each row is one review item."""
    path = data_dir / "wordlist_pairs.private.json" if data_dir else None
    if not path or not path.is_file():
        return ""
    pairs = json.loads(path.read_text(encoding="utf-8"))
    for start in range(0, len(pairs), GROUP_SIZE):
        number = start // GROUP_SIZE + 1
        group = pairs[start:start + GROUP_SIZE]
        entries = []
        for position, pair in enumerate(group, start + 1):
            left, right = pair["words"]
            # Bookmarks survive rebuilding the list and inserting new pairs.
            key = "\n".join(sorted([left.casefold(), right.casefold()]))
            row_id = "p-" + sha256(key.encode()).hexdigest()[:16]
            glosses = "".join(
                f'<p><span lang="en">{escape(word)}</span> · {escape(gloss)}</p>'
                for word, gloss in zip((left, right), pair["glosses"], strict=True)
            )
            pages = "、".join(str(int(page)) for page in pair["pages"])
            entries.append(
                f'<article class="word pair" id="{row_id}" data-filled="1"><div class="face"><details><summary>'
                f'<span class="number">{position:02}</span><span class="head"><b lang="en">'
                f'<span>{escape(left)}</span> / <span>{escape(right)}</span></b></span>'
                f'<span class="meaning" lang="zh-CN">{escape(pair["meaning"])}</span></summary>'
                f'<div class="example" lang="zh-CN">{glosses}<p class="note">原词表第 {pages} 页</p></div>'
                f'</details>{STEPPER}</div></article>'
            )
        links.append(f'<a href="#pairs-{number}" aria-label="Pair group {number}" hidden>{number:02}</a>')
        sections.append(
            f'<section class="group" id="pairs-{number}" data-group="{number}" hidden>'
            f'<h2>六选二精选 · {number:02} <small>{len(group)} pairs</small></h2>'
            + "\n".join(entries) + '</section>'
        )
    if not pairs:
        return ""
    return ('<label><input type="radio" name="collection" value="pairs"><span lang="zh-CN">六选二精选'
            f'<small>按词对复习</small></span><b data-set="pairs">{len(pairs)}</b></label>')


def render(source, data_dir=None):
    """The page and its details.

    The page holds only what a closed row shows. Details (the coloured sentence, translation,
    structure, meaning and word lists) are keyed by row id, so the page loads them separately
    and fills a row when it opens. This keeps the page small enough to stay loaded on a phone."""
    with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        rows = db.execute(
            "SELECT group_number, position, word, phonetic, meaning_zh, example_en, example_zh "
            "FROM words ORDER BY group_number, position"
        ).fetchall()
    if not rows:
        raise ValueError("The word database is empty.")

    families = load_families()
    relations = load_relations(data_dir) if data_dir else {}
    sentences = load_sentences(data_dir) if data_dir else {}
    family_rows = ordered(rows, families)
    # Row ids keep the source group and position, so saved places and links stay valid.
    ids = {}
    for family in family_rows:
        for row in family:
            ids.setdefault(row[2].casefold(), f"w-{row[0]}-{row[1]}")

    sections, links, family_number, details = [], [], 0, {}
    for number, group in enumerate(chunks(family_rows), start=1):
        entries, pos = [], 0
        for family in group:
            family_number += 1
            members = [r[2] for r in family]
            for index, (source_group, source_pos, word, phonetic, meaning, en, zh) in enumerate(family):
                item = sentences.get(word) or {"en": en, "zh": zh}
                en, zh = item["en"], item["zh"]
                pos += 1
                kin, fam = "", ""
                if len(family) > 1:
                    # The page relinks these classes whenever rows are hidden or shown.
                    kin = " fam" + (" fam-start" if index == 0 else "") + (" fam-end" if index == len(family) - 1 else "")
                    fam = f' data-fam="{family_number}"'
                head = f'<b lang="en">{escape(word)}</b>'
                if phonetic:
                    head += f'<span class="ipa" lang="en">{escape(phonetic)}</span>'
                # The sentence sits in the row itself, so the sentence view can show it without opening the row.
                # Its structure colours arrive with the details.
                sentence = f'<span class="sentence" lang="en">{marked(en, word)}</span>' if en else ""
                detail = f'<p class="translation" lang="zh-CN">{escape(zh)}</p>' if zh else ""
                detail += breakdown(item)
                # The sentence view moves the meaning below the translation.
                detail += f'<p class="gloss" lang="zh-CN">{escape(meaning)}</p>'
                detail += relation_list(relations, word, ids, members)
                row_id = f"w-{source_group}-{source_pos}"
                details[row_id] = [structured(item["parts"], word) if item.get("parts") else "", detail]
                entries.append(
                    f'<article class="word{kin}" id="w-{source_group}-{source_pos}"{fam}><div class="face"><details><summary>'
                    f'<span class="number">{pos:02}</span><span class="head">{head}</span>'
                    f'<span class="meaning" lang="zh-CN">{escape(meaning)}</span>{sentence}</summary>'
                    f'<div class="example"></div></details>{STEPPER}</div></article>'
                )
        links.append(f'<a href="#g-{number}" aria-label="Group {number}">{number:02}</a>')
        sections.append(
            f'<section class="group" id="g-{number}" data-group="{number}" style="--rows:{len(entries)}">'
            f'<h2>Group {number:02} <small>{len(entries)} words</small></h2>'
            + "\n".join(entries) + '</section>'
        )

    pair_choice = render_pairs(data_dir, links, sections)
    total = len(ids)
    template = Template((ROOT / "templates/wordlist.html").read_text(encoding="utf-8"))
    html = template.substitute(
        pair_choice=pair_choice, group_links="".join(links), word_groups="\n".join(sections), total=f"{total:,}",
    )
    return html, details


def details_json(details):
    return json.dumps(details, ensure_ascii=False, separators=(",", ":"))


def standalone(html, details, online_url=None):
    """A single file that works from disk. With online_url, it offers (and tries) the online list first.

    The details travel inside the file, since there is no server to fetch them from."""
    public = ROOT / "public"
    icon = "data:image/svg+xml;base64," + b64encode((public / "favicon.svg").read_bytes()).decode()
    replacements = {
        '<script src="/theme.js"></script>': '<script>' + (public / "theme.js").read_text() + '</script>',
        '<script src="/wordlist-head.js"></script>': '<script>' + (public / "wordlist-head.js").read_text() + '</script>',
        '<link rel="stylesheet" href="/wordlist.css">': '<style>' + (public / "wordlist.css").read_text() + '</style>',
        '<script src="/wordlist.js" defer></script>': '',
        '</body>': ('<script type="application/json" id="wordlist-details">'
                    + details_json(details).replace("</", "<\\/") + '</script>'
                    + '<script>' + (public / "wordlist.js").read_text() + '</script></body>'),
        'href="/favicon.svg"': f'href="{icon}"',
        '<link rel="apple-touch-icon" href="/icons/apple-touch-icon.png">': '',
        '<link rel="manifest" href="/manifest.webmanifest">': '',
    }
    if online_url:
        base = online_url.rstrip("/") + "/"
        replacements['<a id="online" href="/"'] = f'<a id="online" href="{escape(base)}"'
    else:
        # Without a server there is no online list to link to.
        replacements['<a id="online" href="/" hidden>Open the online list</a>'] = ''
    for original, replacement in replacements.items():
        if original not in html:
            raise ValueError(f"Standalone template is missing {original}")
        html = html.replace(original, replacement)
    return html


def write_page(target, html):
    target.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent, delete=False) as f:
        temp = Path(f.name)
        try:
            f.write(html)
        except BaseException:
            temp.unlink(missing_ok=True)
            raise
    try:
        temp.replace(target)
    finally:
        temp.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--standalone", type=Path, help="Also write a self-contained local copy")
    parser.add_argument("--online-url", help="The standalone copy links to, and first tries, this online instance")
    args = parser.parse_args()
    html, details = render(args.source, args.data_dir)
    # Details first, so a page never points at details from an older build.
    write_page(args.data_dir / "wordlist-details.json", details_json(details))
    write_page(args.data_dir / "wordlist.html", html)
    if args.standalone:
        write_page(args.standalone, standalone(html, details, args.online_url))


if __name__ == "__main__":
    main()
