Phrase prefix lookup.

- Phrase candidates now start contextual meaning preview from the first non-stopword prefix, such as `li` for `in light of`.
- Phrase prefix preview is capped at two simultaneous prefetches to keep API usage bounded.
- Word prefix preview from v0.2.2 remains unchanged.

No API key, `config.json`, or login state is included.
