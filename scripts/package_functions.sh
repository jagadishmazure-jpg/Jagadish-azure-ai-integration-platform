#!/usr/bin/env sh
# azd prepackage hook: vendor the aiip package next to function_app.py so the Functions remote
# build can import it (the Flex Consumption build installs functions/requirements.txt only).
set -eu
root="$(cd "$(dirname "$0")/.." && pwd)"
rm -rf "$root/functions/aiip"
cp -R "$root/src/aiip" "$root/functions/aiip"
find "$root/functions/aiip" -name '__pycache__' -type d -prune -exec rm -rf {} +
echo "copied src/aiip -> functions/aiip"
