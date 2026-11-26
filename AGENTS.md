# Wordroom development

Wordroom is a scannable GRE word list served from your own computer.
Work on feature branches and open pull requests; merging runs the tests and deploys nothing.

## Structure

- `server.py` serves the word list page, its static files and the score API, on loopback only.
- `store.py` owns the SQLite tables for scores and the saved reading place, and makes backups.
- `domain.py` holds the shared error type and input checks.
- `providers.py` holds model provider settings and the JSON request helper used by the generation scripts.
- `scripts/build_wordlist.py` builds `wordlist.html` and `wordlist-details.json` from a word database into the data directory.
- `scripts/generate_wordlist_sentences.py` and `scripts/generate_wordlist_relations.py` add generated content.
- `public/` and `templates/` contain plain HTML, CSS and JavaScript, with no build step.

## Data

No word list, book or test-question content is committed; keep it that way.
`data/wordlist_families.json` and `public/wordlist-super-picks.json` are local files and stay ignored.
Never print, commit or copy API keys into documentation or test fixtures.
Use temporary data directories for tests.
Keep `.runtime/` and `.qa/` ignored by Git.
Generation uses paid provider APIs; run it only when the user asks.

A long-running instance keeps its data in its own `--data-dir`, outside every checkout.
Never point tests or development servers at a real instance's data directory.
Do not replace an instance's database with a developer copy.
Its database may hold tables from older versions; leave them in place.
Keep schema changes compatible with the previous version; rolling back code does not restore the database.

## Validation

Run `bash scripts/check.sh` for every change.
Verify affected UI flows in a browser at phone width, in light and dark mode, when changing user-facing behavior.
A model's own check is not proof that generated content is correct.

## Launch

Run `python3 server.py --open` with Python 3.11 or later.
Use `--port 8769` if the default port 8768 is occupied.
No third-party Python packages are required.
See `docs/development.md` for previews and for running behind Tailscale Serve.
