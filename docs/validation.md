# Validation

notes-search should return the same notes as Apple Notes, in the same order. Three checks tested
that, each against a different reference:

| Check | Question | Result |
| --- | --- | --- |
| Notes.app, real library | Does it agree with the app on real notes? | 54 of 54 searches identical |
| Spotlight, synthetic corpus | Are the matching rules right for text nobody tuned them on? | 414 of 417 queries identical |
| Spotlight, typo variants | Is the fuzzy matcher right? | no false rejections in 1,180 checks |

The first two found real errors, which are fixed. The remaining differences are listed at the end
of section 2.

## 1. Notes.app on a real library

The library was a real iCloud account with 17 notes in two folders, one of them locked, on macOS
26. Searches were typed into the Notes search field with All iCloud selected, and the result list
was read from the screen. The same library was exported with `notes-search index apple-notes` and
searched with the same text. A search passed only if the notes returned, their order and the two
Top Hits all matched.

42 searches were used while working out the rules. They covered:

- single words, prefixes, single letters and fragments from inside words (`ack` for *back*)
- two- to four-word searches where the first word, a later word, or both matched only inside a
  word, in both orders, and searches with one word that matched nothing
- words joined by underscores
- accents (`agua` finds *água*)
- exact titles that should rank first
- date phrases (`last month`, `notes from july`, `yesterday`)
- a locked note, searched by title
- misspellings in English and Portuguese

Twelve more were held out: their results were predicted with notes-search first, then checked in
Notes. They included a title ending in a colon, hyphenated text (`pt-pt`), URLs (`https`) and
short common words (`ai` and `the`, with 8 and 9 results).

All 54 were identical. Two rules were corrected on the way: matching inside words, and
underscores as word separators. The tokenizer changed again later (section 2), and the 54 were
replayed afterwards with the same result.

The notes and the searches aren't published, because the library is private.

## 2. Spotlight on a synthetic corpus

A single library can hide mistakes: if no note contains `node.js`, nothing tests how `node.js` is
matched. The second check uses a corpus written for the purpose, with Spotlight itself as the
reference.

Spotlight can stand in for Notes because Notes doesn't match text itself. It sends
`field="*search*"cwdt` to Spotlight and shows what comes back. Running the identical query
through `mdfind` against the same text, saved as plain files, exercises the same matching engine.
Section 1 confirms the connection: the rules measured this way are the ones Notes showed.

The corpus is 25 short notes, in [`scripts/lab/corpus.json`](../scripts/lab/corpus.json), each
aimed at something a typical library might not contain:

- accents in several languages, `ß`, ligatures, camelCase
- `node.js`, `snake_case`, `kebab-case`
- email addresses, URLs with paths and query strings
- numbers with dots and commas, mixed letters and digits
- hashtags, mentions, apostrophes in English, French and Irish forms
- Japanese text and emoji
- notes that differ only in their modification time

The 417 queries are in six sets, in [`scripts/lab/queries.json`](../scripts/lab/queries.json):

| Set | Queries | Purpose | Result |
| --- | --- | --- | --- |
| tuning | 69 | first pass over every feature of the corpus | 69 / 69 |
| held-out-1 | 65 | written after the first round of fixes, not used to make them | 64 / 65 |
| punctuation-probes | 82 | investigating punctuation once differences appeared | 82 / 82 |
| held-out-2 | 45 | written after the punctuation fixes | 44 / 45 |
| tokenizer-probes | 85 | investigating compounds, apostrophes, camelCase | 85 / 85 |
| held-out-final | 71 | written last, after all changes; never used for a fix | 70 / 71 |

The first run found real errors. notes-search matched the real library perfectly but got 7 of the
69 tuning queries wrong on the corpus: it split `quorvatix-beta` into two words where Spotlight
keeps the hyphen, folded the ligature ﬁ into "fi", and couldn't search for an emoji. The probe
sets traced each difference to a rule, and those rules are documented in
[how-it-works.md](how-it-works.md#how-text-is-split-into-searchable-words).

Spotlight builds its index in two passes. Plain words are searchable within seconds, but emails,
URLs, hyphenated words and numbers with separators only become searchable in a second pass that
can take minutes. A run started too early matched only 349 of the 417 queries, almost all of the
failures being searches with punctuation; the same files queried later matched 414. Notes uses
the same index, so a note you just edited may briefly not be found by a search like
`name@example.com`. The lab script waits for the second pass.

Three queries still differ:

- `n'roll` and `n'r` match *rock'n'roll* in notes-search and nothing in Spotlight. Both are
  fragments starting at an inner apostrophe. No rule found so far explains every apostrophe case
  at once, and fitting these two would mean special-casing them.
- The single digit `4` matches every file in Spotlight, including files without a 4 in them.
  So do `0` and `3`; `1`, `7` and `9` don't. The same happens without the `t` flag. This looks
  like Spotlight matching file metadata such as timestamps rather than the text, so it isn't
  modelled. Whether Notes items behave the same way hasn't been tested.

To reproduce the comparison on a Mac:

```bash
python3 scripts/matching_lab.py fuzzylab/corpus
```

## 3. The fuzzy matcher

Shipping Notes doesn't use fuzzy matching, but the matcher behind `--fuzzy` is meant to equal
Spotlight's. [`scripts/fuzzy_lab.py`](../scripts/fuzzy_lab.py) writes 295 misspellings of four
words (substitutions, deletions, insertions, swaps, and two and three errors at every position),
indexes them, and queries each word at four thresholds.

| Threshold | Agreement |
| --- | --- |
| `f080` | exact |
| `f066` (Notes) | every match Spotlight returns is accepted; Spotlight returns fewer, up to 28 and 39 fewer for the longest word in two runs |
| `f050`, `f033` | same pattern |

The shortfall comes from Spotlight capping how many fuzzy candidates it considers per query, and
the cap depends on the rest of the index, which is why it varies between runs. A typo it dropped
among 295 files matched among 13. notes-search doesn't model the cap, so with `--fuzzy` it can
return more results than Spotlight would.

To reproduce it:

```bash
python3 scripts/fuzzy_lab.py fuzzylab/typos
```

## What hasn't been tested against Notes.app

The synthetic corpus was created as real notes in a test folder, but it wasn't searched side by
side in the app. Section 2 tests the same engine through `mdfind` instead.

Whether Notes' own search includes notes in Recently Deleted wasn't checked. notes-search skips
them by default; `--include-deleted` indexes them.

Attachments weren't tested, because the real library had none. Attachment search and parent notes
follow Notes' code.

Everything was measured on macOS 26, with English and Portuguese text plus the corpus' French,
German and Japanese samples. Other macOS versions and languages haven't been checked.
