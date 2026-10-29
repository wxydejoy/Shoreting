#!/usr/bin/env bash
# Open Music.app, or YouTube Music search when a query is given.
set -euo pipefail
query="${1:-}"
if [[ -z "$query" ]]; then
  open -a Music 2>/dev/null || open "https://music.youtube.com"
  exit 0
fi
encoded="$(
  /usr/bin/python3 -c 'import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))' "$query"
)"
open "https://music.youtube.com/search?q=${encoded}"
