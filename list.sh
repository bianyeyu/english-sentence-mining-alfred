#!/bin/zsh
TOOL="${0:A:h}/english_anki.py"
/opt/homebrew/bin/python3 "$TOOL" list "$1"
