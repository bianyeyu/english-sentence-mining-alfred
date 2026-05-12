#!/bin/zsh
TOOL="${0:A:h}/english_anki.py"
PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
for PYTHON in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3 python3; do
  if command -v "$PYTHON" >/dev/null 2>&1; then
    exec "$PYTHON" "$TOOL" capture "$1" "Alfred selection"
  fi
done
print -u2 "python3 not found. Install Python 3 or expose it in Alfred's PATH."
exit 127
