# Changelog

## 1.0.0

First public release.

- Matching follows the query Notes actually runs, `"*search*"cwdt`: the first word matches
  inside words, later words match word starts, word order is free.
- Text is tokenized the way Spotlight indexes it: compounds such as `alpha-beta`, emails, URLs
  and numbers are searchable whole and in parts, camelCase and letter/digit runs are split, and
  apostrophes follow Spotlight's contraction and elision rules. Checked against Spotlight on a
  417-query synthetic corpus.
- Notes' sort order: exact title, notes before attachments, newest first. The first two results
  are Top Hits.
- Date phrases with Notes' calendar semantics ("last month" starts on the 1st of the previous
  month).
- Filter tokens (`--has`), attachment results with their parent notes, locked notes by title.
- Notes' ranking weights, age buckets and the two unused sort strategies (`--strategy`).
- The switched-off passes, available as options: Spotlight's `f066` fuzzy matcher (`--fuzzy`),
  spell-checker expansion (`--spell`), spelling and lemma queries (`--spell-api`) and related
  words (`--related`).
- Sources: folders of text and Markdown files, JSON, and Apple Notes with folders and accounts.
- `query` and `spotlight` commands to print Notes' query or run it on files with `mdfind`.
