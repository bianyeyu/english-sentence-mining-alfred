Provider simplification release.

- Removed the Codex CLI login/setup path and the `ewsetup` Alfred action.
- Default lookup provider is now Groq through the OpenAI-compatible chat completions API.
- Added generic LLM configuration: `llm_api_key`, `llm_base_url`, and `llm_model`.
- Default model is `qwen/qwen3-32b`, but users can change it in Alfred's Configure Workflow panel.
- Kept public dictionary/translation fallback for no-key testing.

No API key, `config.json`, or login state is included.
