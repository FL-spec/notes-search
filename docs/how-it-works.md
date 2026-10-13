# How Apple Notes search works

Apple Notes turns your search into a single Spotlight query, filters the matches by any date in
the search, sorts them and marks the first two as Top Hits. Each step is implemented in
notes-search, and each section ends with the module that implements it. How the rules were found
is in [reverse-engineering.md](reverse-engineering.md), and how they were checked is in
[validation.md](validation.md).

Everything here describes Notes on macOS 26.

## The pipeline

```
what you type
  │
  ├─ 1. main query       "*your search*"cwdt against title, body, authors, photo labels
  ├─ 2. date query       only if the search contains a date phrase
  ├─ (off) extra passes  fuzzy, substring, spelling, related words
  │
  ├─ attachment hits bring in the notes they belong to
  ├─ 3. sort             exact title → notes before attachments → newest first
  ├─ 4. Top Hits         the first two results
  └─ 5. delivery         in batches, as many rows as fit on screen
```

## 1. The main query

Notes takes the whole search string, wraps it in wildcards and gives it to Spotlight as one
query per searchable field:

```
_ICItemDisplayName="*lisbon tram*"cwdt
  || kMDItemTextContent="*lisbon tram*"cwdt
  || kMDItemAuthors="*lisbon tram*"cwdt
  || …
```

You can print this query for any search with `notes-search query "…"`.

The four letters after the closing quote are Spotlight match options:

| Flag | Meaning |
| --- | --- |
| `c` | ignore case |
| `d` | ignore diacritics, so `cafe` matches *café* (and `strasse` matches *Straße*) |
| `w` | match on word boundaries |
| `t` | split the search string into words before matching |

With the `t` flag, Spotlight splits `*lisbon tram*` into the words `lisbon` and `tram` and keeps
the wildcards at the two ends, which gives three rules.

**The first word matches anywhere inside a word.** It carries the leading `*`, so `ack` finds
*back*, `orvat` finds *Quorvatix*, and a single letter finds every note containing it.

**Every later word must match the start of a word.** Only the last word carries a trailing `*`,
but in practice every word after the first behaves as a prefix. `orvat release` finds a note
containing *Quorvatix* and *release*, and so does `orvat rel`. `orvat lease` doesn't, because
*lease* is not the start of any word.

**All words must appear, in any order, in the same field.** `names quorvatix` and
`quorvatix names` find the same notes. `release orvat` finds nothing, though: *orvat* is now a
later word, so it has to be the start of a word.

The fields are:

| Field | Contents |
| --- | --- |
| `_ICItemDisplayName` | the note's title |
| `kMDItemTextContent` | the note's full text, **including the title line** |
| `kMDItemAuthors` | author names, for shared notes |
| `kMDItemPhotosSceneClassificationLabels`, `…Synonyms` | labels from on-device image recognition, such as "dog" or "beach" |

Because the text field includes the title, a title word and a body word combine: `orvat names`
finds a note titled *Quorvatix 20 Release* whose body says *Fix names*.

### How text is split into searchable words

Spotlight doesn't search the raw text. It searches the words it stored when the note was
indexed, and it stores more than one version of anything that contains punctuation. These rules
were measured by running Notes' query through `mdfind` on a synthetic corpus (see
[validation.md](validation.md)). In the two-word examples, the first word (*call*, *write*,
*open*…) comes from elsewhere in the same note:

| Text | Stored as | So these searches match |
| --- | --- | --- |
| `alpha-beta` | `alpha-beta`, `alpha`, `beta` | `alpha-beta`, `call beta` |
| `user@example.com` | the address, `example.com`, `.com`, `com`, `user`, `example` | `write example.com`, `write com` |
| `https://site.example.org/path?q=x` | the URL, `site.example.org`, `example.org`, `path`, `q=x`, … | `open example.org`, `open path` |
| `zx_snake_case` | the whole, `snake_case`, `case`, `zx`, `snake` | `notes snake_case`, `notes case` |
| `ParseThing` | `parsething`, `parse`, `thing` | `function thing` |
| `ABC123def`, `zx2026q3` | `abc`, `123`, `def`; `zx2026`, `q3` | `street def`, `period q3` |
| `#project`, `websites:` | `#project`, `project`; `websites` | `#proj`, `plan project` |
| `can't`, `maribel's` | kept whole, plus `can`, `maribel` | `can't`, `maribel's` |
| `l'amour`, `o'neil` | split: `l`, `amour`; `o`, `neil` | `lab amour`, not `l'amour` |

The search side has its own rules:

- **Search words are separated by spaces only,** so `alpha-beta` is one word and needs the
  hyphen: it finds *alpha-beta* but not *alpha beta*.
- **The first word keeps its punctuation.** `#proj` finds *#project* but not *project*. If the
  first word ends in punctuation and has none elsewhere, it finds nothing: `notes.`, `user@`
  and `https://` all come back empty. `3.14.` and `user@example.` still work, because they
  already contain punctuation. A trailing hyphen or apostrophe is fine.
- **Later words ignore punctuation at either end:** `sent on friday.` finds *Friday*.
- **Accents and case are ignored, and ß matches ss.** Ligatures are not: `financial` doesn't
  find *ﬁnancial* typed with the single ﬁ character.
- **Emoji and other symbols match like letters:** `🎉` finds notes containing it.

Implementation: `engine.default_match`, `text.words`, `text.search_terms`, `text.fold`.

### What the main query doesn't do

It doesn't correct typos. Notes' code asks the system spell checker for guesses whenever a word
looks misspelled, and builds a second query with them. In Notes.app that second query never added
a result: `configuraton` didn't find *configuration*, and the Portuguese `bateiras` didn't find
*baterias*. notes-search keeps the code path behind `--spell` for experimenting and leaves it off
by default.

## 2. Date phrases

When the search contains a date phrase, Notes also runs a natural-language query, using a private
Apple parser, that combines the remaining words with a date range. The ranges are calendar
periods. `yesterday`, `last month` and `from july` were measured in Notes.app; the other phrases
follow the same calendar pattern in notes-search but weren't checked against the app:

| Phrase | Range |
| --- | --- |
| `today` | since midnight |
| `yesterday` | all of yesterday |
| `this week` / `last week` | since the start of this week / the previous week |
| `this month` / `last month` | since the 1st of this month / the previous month |
| `this year` / `last year` | since 1 January of this year / the previous year |
| `in july`, `from july 2025` | that calendar month |
| `last 10 days` | the last ten days |

"last month" starts on the 1st of the previous month: on 13 October it means *1 September until
now*, not *the last 30 days*. The words around a date phrase are dropped, so `notes from july`
returns every note from July.

notes-search also understands `folder:Name`, `folder:"Two words"`, `#tag` and `with attachments`.
Notes' parser handles much more than this; see the limitations in the README.

Implementation: `nl.parse`.

## 3. Sort order

Notes has three sort strategies. The app uses strategy 0:

1. **Exact title match first.** A note whose title equals the search, ignoring case and accents,
   comes first, even if it is the oldest match.
2. **Notes before attachments.**
3. **Newest first,** by modification date, then by creation date.

The strategy has more keys after these: whether the result was a prefix match, its relevance
score and which fields matched. In every result list checked in Notes.app, they didn't change the
order. Results that matched only inside a word were mixed by date with prefix matches, which
suggests the lazily computed prefix flag isn't set yet when Notes sorts. The score could only
matter for two notes edited in the same second.

notes-search computes the relevance score with Notes' formula. Each field adds a fixed weight when
one of the search words matches it, more for a prefix match than for a fuzzy or mid-word match:

| Field | prefix | fuzzy | inside a word |
| --- | --- | --- | --- |
| title | 32 | 0.5 | 1/64 |
| text | 16 | 0.25 | 1/128 |
| authors | 8 | 0.125 | 1/256 |

Attachments get smaller weights (title 4, text 2). The other two strategies use the score:

- **Strategy 1** sorts by score, then recency.
- **Strategy 2** groups results into logarithmic age buckets,
  `max(2, trunc(ln(age in seconds) × 1.286 − 6.142))`, and sorts by bucket, then score. An hour
  old is bucket 4, a day is 8, a week 10 and a year 16.

Both are reachable with `--strategy`.

Implementation: `ranking.py`, `engine._score`, `engine._sort_key`.

## 4. Top Hits, attachments and locked notes

**Top Hits are the first two results** after sorting, whatever they are. There is no score
threshold. With one result, that result is the only Top Hit.

**Attachments are searched as items of their own,** with the file name as the title. When an
attachment matches, the note it belongs to is added to the results as well, so you see both. This
comes from Notes' code; the library used for live testing had no attachments.

**Locked notes are searchable by title only.** Their contents are encrypted and never indexed,
and they sort by date like everything else.

Implementation: `engine.SearchEngine.search`, `engine.SearchEngine.sections`.

## 5. Delivery in batches

Notes doesn't wait for every result. Results arrive in batches sized to the list, as many rows as
fit on screen, and each batch is merged into the sections as it arrives. `search_batched` gives
the same shape with a fixed batch size.

Implementation: `engine.SearchEngine.search_batched`.

## Filters (search tokens)

The chips under the search field, such as *Checklists*, *Locked* or *Attachments*, add an
attribute condition to the query:

```
(_ICItemHasChecklists = 1) && (…main query…)
```

According to Notes' code, a chip with no text typed lists every note that has the attribute. In
notes-search the chips are `--has checklists`, `--has locked` and so on.

Implementation: `query.joint_query`, `query.TOKEN_ATTRIBUTES`.

## The features Notes leaves switched off

Notes' code contains four search passes behind internal settings that aren't set on a normal Mac.
notes-search implements them from that code, as opt-in flags. The limits for related words
(five neighbours, distance 1.0) are guesses, because Apple's values weren't recovered.

**Fuzzy and substring passes (`--fuzzy`).** For searches longer than two characters, a
typo-tolerant query runs, followed by a per-word substring query. The typo tolerance is
Spotlight's own `f066` matcher:

```
edit budget = min(3, floor(word length × 34 / 100))
match       = Levenshtein distance ≤ budget       (a swap of two letters counts as 2)
```

Words of three to five letters get one typo, six to eight letters get two, and nine or more get
three. Spotlight also compares against prefixes of each indexed word, so `recip` can match
*recipes*. It caps the number of candidate words it considers, and that cap depends on what else
is in the index, so it occasionally drops a match this formula allows.

**Spelling (`--spell-api`).** Exact queries for the spell checker's first guess and for the word's
dictionary form (*running* → *run*).

**Related words (`--related`).** Exact queries for neighbours in Apple's word embedding
(*recipe* → *cookbook*, *dessert*).

**Spell-checker expansion (`--spell`).** Described under the main query. Notes asks for it, but it
never added results in testing.

Implementation: `text.fuzzy_word_match`, `macos.py`, `engine.SearchEngine._extra_queries`.
