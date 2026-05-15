Two-letter word prefix lookup.

- Word candidates now start contextual meaning preview after a two-character prefix match, such as `ew te` for `test`.
- Prefix lookup is limited to word candidates and capped at three simultaneous prefetches to keep API usage bounded.
- Phrase targets still use visible phrase selection or slash queries such as `ew /in light of`.

No API key, `config.json`, or login state is included.
