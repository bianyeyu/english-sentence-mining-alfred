Portability fix release.

- Replaced hardcoded `/opt/homebrew/bin/python3` wrapper calls with a Python 3 fallback chain for Apple Silicon Homebrew, Intel Homebrew, Apple system Python, and Alfred `PATH`.
- Keeps the v0.1.0 feature set: Codex CLI login via `ewsetup`, Spark defaults, live Alfred previews, Anki write-through, and generated icon assets.

No Codex login state, OpenAI API key, or local `config.json` is included.
