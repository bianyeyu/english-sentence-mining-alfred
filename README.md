# English Sentence Mining Alfred Workflow

![Icon candidates](assets/icons/icon-candidates-contact-sheet.png)

## What It Does

Select an English sentence anywhere on macOS, invoke Alfred Universal Actions or the workflow hotkey, choose the unknown word or phrase, and write a structured card to Anki.

The Anki card contains:

- target word or phrase
- original sentence
- sentence with the target highlighted
- common word meaning
- meaning in this sentence
- full sentence translation
- part of speech
- source and created time

## Installed Pieces

- Tool script: `5_Workspace_工作台/EnglishSentenceMining/english_anki.py`
- Alfred workflow: `~/Alfred/Alfred.alfredpreferences/workflows/user.workflow.93D36CB2-90F3-45C7-8B10-7758AC5F87A6/`
- Runtime cache and pending queue: `~/Library/Application Support/Alfred/Workflow Data/com.dustandlight.english-sentence-mining/`
- Event log: `~/Library/Application Support/Alfred/Workflow Data/com.dustandlight.english-sentence-mining/events.jsonl`

## Main Use

1. Open Anki once. AnkiConnect must be enabled.
2. Run `ewsetup` once in Alfred to sign in to Codex with ChatGPT. The default isolated login/cache directory is `~/.codex-ew`.
3. Select one English sentence anywhere.
4. Invoke Alfred Universal Actions. Alfred's default selection hotkey is configured in `Alfred Preferences > Features > Universal Actions`.
5. Choose `ew`.
6. Read the sentence translation at the top of the Alfred results, then pick the unknown word or phrase.
7. The card is written to `English::Sentence Mining`.
8. Anki Browser opens to the newly created note so the write is visible immediately.

The workflow uses Codex through the local Codex CLI by default:

```text
model: gpt-5.3-codex-spark
service_tier: default
reasoning_effort: low
CODEX_HOME: ~/.codex-ew
```

The Codex login is not bundled with the workflow. Each Mac signs in locally and consumes that user's own ChatGPT/Codex allowance.

## Install

Download the latest `English-Sentence-Mining-*.alfredworkflow` from GitHub Releases, open it with Alfred, then run `ewsetup`.

Requirements:

- Alfred with workflows enabled
- Anki plus the AnkiConnect add-on
- Codex CLI available on the Mac for ChatGPT subscription lookups

The workflow icon defaults to candidate 1. The generated alternatives are kept in `assets/icons/candidates/` if you want to swap the icon later.

## Phrase Targets

The list now includes both single-word candidates and short phrase chunks.

If the exact phrase is not shown, keep the current sentence cached and type a slash query in Alfred:

```text
ew /in light of
```

That creates a one-off target for the current sentence and writes the phrase into the existing Anki `Word` field. The Anki model is intentionally kept unchanged for compatibility; the card is tagged with `target-phrase`.

## Optional Hotkey

The workflow includes a hotkey trigger, but no key is bound by default to avoid collisions.

Set it in:

`Alfred Preferences > Workflows > English Sentence Mining > Hotkey Trigger`

Recommended settings:

- Hotkey: choose your preferred key, for example `Option + E`
- Argument: `Selection in macOS`

After that:

`select sentence -> hotkey -> choose word or phrase -> Anki`

## Manual Fallback

Open Alfred and type:

```text
ew The conceptualization of a Life Operating System has undergone a radical transformation.
```

Then choose the target word or phrase.

## Lookup Quality

By default the workflow tries the local Codex CLI first, using ChatGPT subscription access. `ew` displays a cached full-sentence translation at the top of Alfred's results. When the typed query narrows to one exact target, or when using slash mode, it also previews the target meaning before writing to Anki.

Run setup from Alfred:

```text
ewsetup
```

If Alfred cannot find the Codex executable, set the workflow variable `codex_path` to the absolute path, for example:

```text
/Users/you/.npm-global/bin/codex
```

For a clean per-workflow login, keep `codex_home` as:

```text
~/.codex-ew
```

To reuse your normal Codex CLI login instead, set `codex_home` to:

```text
~/.codex
```

Free public dictionary/translation fallback remains enabled. That is useful for testing and for users without Codex, but the `Meaning In Context` field may be less precise.

For higher quality contextual meanings, copy `config.example.json` to `config.json`, then fill:

```json
{
  "codex_enabled": true,
  "codex_home": "~/.codex-ew",
  "codex_model": "gpt-5.3-codex-spark",
  "codex_service_tier": "",
  "codex_reasoning_effort": "low",
  "openai_base_url": "https://api.openai.com/v1",
  "openai_model": "",
  "public_fallback": true,
  "open_browser_after_add": true
}
```

OpenAI API key mode is still supported as a fallback path. Provide the key through Alfred's environment or the shell environment:

```zsh
export OPENAI_API_KEY="..."
export OPENAI_MODEL="your-preferred-small-model"
```

If Alfred does not inherit shell environment variables, add `openai_api_key` and `openai_model` directly to `config.json`.

## Community Sharing

This workflow is safe to share as long as no local credential files are included.

Do not bundle or publish:

- `~/.codex/auth.json`
- `~/.codex-ew/auth.json`
- `config.json` if it contains `openai_api_key`
- Alfred exported environment values containing credentials

Other users install the workflow, install Codex CLI, run `ewsetup`, and sign in with their own ChatGPT account. Their lookups run locally on their machine and use their own Codex allowance.

## Pending Queue

If Anki is not reachable, the workflow opens Anki and waits briefly. If AnkiConnect is still unavailable, it stores the card in:

`~/Library/Application Support/Alfred/Workflow Data/com.dustandlight.english-sentence-mining/pending_cards.jsonl`

To retry, open Alfred with `ew` and choose `刷新待写入 Anki 的队列`.
