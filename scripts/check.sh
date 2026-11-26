#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m unittest discover -s tests -v
node --test tests/theme.test.js
node --check public/theme.js
node --check public/wordlist.js
node --check public/wordlist-head.js
