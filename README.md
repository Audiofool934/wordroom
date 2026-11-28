# Wordroom

Wordroom is a GRE word list you scan, not a deck of cards.
Each row shows a word and its pronunciation.
Tap a row to open it, hear the word, and read its meaning, a long academic example sentence and related words.
Mark the words you are unsure of with + and archive the ones you know.

It runs on your own computer with Python and SQLite, with no third-party packages and no build step.
The interface is English, in the Nord palette, light and dark.

## What it does

- **One long list in groups of about 30.**
  Words that share a root (confound, confounded, confounding) sit together, joined by a thin bar.
- **A sentence view.**
  The paragraph button in the toolbar shows each word's example sentence under its row, so the list reads like a passage.
  The sentences are long and complex, in the style of GRE reading passages.
- **Open rows.**
  An open row shows the sentence with its structure marked: the main clause's skeleton is underlined, subordinate clauses are violet and modifiers are teal.
  Under it come the Chinese translation, a short structure note, the word's meaning, and its family, synonyms, antonyms and related words.
  A related word that is in the list is a link to it.
- **Scores.**
  + and − set a score; words above zero appear under Review.
  Swipe a row left to archive it, with Undo; archived words can be restored later.
- **Your place.**
  Scores sync by spelling and the last row you were on is saved, so another device opens where you stopped.
- **Optional word sets.**
  The Word set menu reads `public/wordlist-super-picks.json`, a local mapping from words to source tags.
  Use `fill` for words in completion stems or choices, `completion` for answer choices specifically, and `reading` for words in reading passages, stems or choices.
  A word can carry multiple tags and shares one score across all sets.
- **Speech.**
  Words and sentences are read by the device's own English voice; nothing is sent anywhere.

## The word database

Wordroom does not include a word list.
You supply a SQLite file with a `words` table:

| Column | Content |
| --- | --- |
| `group_number`, `position` | The word's group and place in it, which give each row a stable id |
| `word` | The headword |
| `phonetic` | IPA, may be empty |
| `meaning_zh` | The meaning shown in the row |
| `example_en`, `example_zh` | A fallback example sentence and translation |

Build the page from it:

```sh
python3 scripts/build_wordlist.py path/to/words.sqlite3 --data-dir .runtime
```

This writes `wordlist.html` and `wordlist-details.json` into the data directory.
Run it again whenever you add generated content.

### Optional generated content

The sentence and relation scripts call an OpenAI-compatible chat API (DeepSeek by default).
Set a provider and key once; the key is stored in the data directory with owner-only permissions:

```sh
python3 providers.py --data-dir .runtime
```

Then, from the repository folder:

```sh
python3 scripts/generate_wordlist_sentences.py path/to/words.sqlite3 --data-dir .runtime
python3 scripts/generate_wordlist_relations.py path/to/words.sqlite3 --data-dir .runtime
```

Both scripts save after each batch, so a stopped run resumes where it left off.
Try `--limit 16` first; these calls cost money.

Word families come from a stemmer with a reviewed list of corrections:

```sh
uv run --python 3.12 --with nltk==3.9.1 scripts/build_word_families.py path/to/words.sqlite3 data/wordlist_families.json
```

## Run it

```sh
python3 server.py --open
```

The list opens at <http://127.0.0.1:8768>.
Use `--port 8769` if that port is taken, and `--data-dir` to keep the data somewhere other than `.runtime/`.
On macOS you can also double-click `start.command`.

### On your phone

Wordroom listens only on this computer.
To use it from a phone, run it behind [Tailscale Serve](https://tailscale.com/kb/1312/serve), which provides HTTPS on your tailnet and tells Wordroom who is asking:

```sh
python3 server.py --port 8770 --data-dir "$HOME/wordroom-data" \
  --public-origin https://<machine>.<tailnet>.ts.net --tailscale-user <your-tailscale-login>
tailscale serve --bg 8770
```

Only the named Tailscale user can read or change the list.
In Safari, use Share, then Add to Home Screen, to open it as an app.

## Your data

Scores and your place live in `wordroom.sqlite3` in the data directory.
Generated sentences and relations live beside it as JSON files.
Back up the database at any time:

```sh
python3 store.py --data-dir .runtime
```

This writes a consistent copy to `backups/` in the data directory.
A database from an older Wordroom version keeps its other tables; the list still reads lesson synonyms from it.

## Develop

See [docs/development.md](docs/development.md).
Run every check with:

```sh
bash scripts/check.sh
```

## License

The code is released under the [MIT License](LICENSE).
The license covers this repository only, not any word list, book or test content you use with it.
