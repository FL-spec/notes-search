# Reverse engineering notes

The behaviour described in [how-it-works.md](how-it-works.md) was worked out from three kinds of
evidence. The Apple symbols behind each finding are named here, so the work can be checked or
extended.

## Method

Matching and ordering rules were each confirmed by at least two of these. The ranking weights and
the features Notes leaves switched off rest on the code alone.

1. Static analysis of the shipped binaries: the `Notes` executable and its Spotlight index
   extension, and the private frameworks `NotesShared`, `NotesUI` and `NotesSupport`, plus
   Spotlight's `SpotlightIndex` and `MetadataUtilities`. The frameworks live in the dyld shared
   cache, so they were extracted with [ipsw](https://github.com/blacktop/ipsw) first.
   Decompilation used Ghidra 12.1 through [REA](https://github.com/morluto/rea), with `ipsw`
   disassembly for quick lookups of selectors and string constants.
2. Spotlight experiments with `mdfind`. Notes stores its index in Spotlight, so any query
   Notes builds can be run against plain files with the same engine, which isolates
   Spotlight's behaviour from Notes'.
3. Side-by-side tests in Notes.app: type a search, read the result list, compare it with
   notes-search on the same library. See [validation.md](validation.md).

Twice, a conclusion drawn from the decompiled code turned out to be wrong in the running app.
Both cases are described at the end.

All findings are from macOS 26 on Apple silicon (arm64e).

## The search request

`-[ICMSearchQueryController searchForSearchString:tokens:suggestionsResponder:batchSize:modernResultsOnly:submitSearchResults:]`
in the Notes app:

- cancels any running search operation;
- builds an `ICSearchUserInput` from the text, the filter tokens and `ICMacDeviceKeyboardLanguage()`;
- starts an `ICSearchQueryOperation` with `performNLSearch:YES`.

Results come back through the operation's `foundItemsHandler(items, isTopHit, operation)`.
The app maps each Spotlight item to its note with
`ICSearchIndexer objectsDictionaryForSearchableItems:inContexts:` and appends the results in
batches. The batch size, `-[ICMSearchNoteListArrayController batchSize]`, is
`ceil(visible height / min(note row height, attachment row height))`.

## The query operation

`-[ICSearchQueryOperation main]` (NotesShared) runs these steps in order:

1. Build the filter-token query and read the ranking strategy
   (`ICSearchRankingStrategySwitch currentStrategy`).
2. `performPrefixAndFuzzyAndSubstringQueries`: run the default query. If the search has more than
   two non-space characters and the internal setting
   `internalSettings.search.enableFuzzySearch` is on, also run a fuzzy query and a per-word
   substring query.
3. If `performNLSearch` is set, run the natural-language query from
   `nlSearchQueryWithSearchString:queryString:rankingQueries:highlightString:`.
4. `performSpellCheckerAPIQueryIfNeeded` and `performRelatedWordQueriesIfNeeded`, each gated by
   its own internal setting.
5. Merge the results. Attachment hits record their note (`ic_relatedModernNoteUniqueIdentifier`),
   and `retrieveNotesOfFoundAttachmentsForSearchResults:` adds notes that weren't found
   directly.
6. Sort with `sortDescriptorsForRankingStrategy:`, split with
   `ic_arrayBySplittingIntoTwoArraysWithMaxPrefixCount:2`, and call the handler twice: once
   with the first two results and `isTopHit = YES`, once with the rest.

None of the internal settings in steps 2 and 4 is registered with a default anywhere in the
bundle or frameworks, and none is set on a normal Mac, so those passes don't run.

## The default query

`-[ICSearchQueryOperation createPrefixMatchingQuery]` builds two strings:

- a prefix query from `prefixMatchingQueryStringForSearchString:enableSpellCheckSPI:YES languageForSpellchecking:<keyboard language> expandedTokens:`;
- a substring query from `substringMatchingQueryStringForSearchString:`.

With `useSearchSuggestions` and `ICSearchConfiguration.sharedConfiguration.searchSubstringMatchingEnabled`,
both go into an `ICSearchSuggestionsQuery`
(`initWithSearchString:additionalLiteralSearchString:searchTokens:filterQueries:…`).

The substring string comes from
`+[ICSearchQueryParser queryStringForSearchString:queryFields:matchType:3]`. That method passes
the entire search string to `_queryStringForSingleTokenString:queryFields:matchType:queryFlags:`
as a single token, with the flags `cwdt`. The clause template for match type 3 is
`%@="*%@*"%@`, so the result is `field="*<search>*"cwdt` for each field.

The rules this query produces (first word anywhere, later words as prefixes, order free) come
from Spotlight's handling of the `t` flag. They were established with `mdfind` on plain files and
then matched against Notes.app; see [validation.md](validation.md).

How Spotlight splits indexed text into tokens wasn't recovered from code. Its tokenizer lives in
the indexing daemon rather than in anything Notes loads. The rules in
[how-it-works.md](how-it-works.md#how-text-is-split-into-searchable-words) were measured instead:
417 queries against a synthetic corpus, compared with `mdfind`.

### Query construction details

From `ICSearchQueryParser` and `ICSpotlightUtilities`:

| Item | Value |
| --- | --- |
| Clause templates by match type | `0` `%@="%@"%@`, `1` `%@="%@*"%@`, `2` `%@="%@*"%@f%@` with `%3.3d` of 66, `3` `%@="*%@*"%@` |
| Joining | fields with ` \|\| `, each token wrapped in `(%@)`, tokens with ` && ` |
| Default flags | `cwd` for per-word clauses, `cwdt` for the literal substring query |
| Escaping | `\` `"` `'` `*` are backslash-escaped, in that order |
| Query fields | `_ICItemDisplayName`, `kMDItemAuthors`, `kMDItemTextContent`, `kMDItemPhotosSceneClassificationLabels`, `kMDItemPhotosSceneClassificationSynonyms` |
| Filter tokens | `%@ = 1` on `_ICItemHasAttachments`, `_ICItemHasChecklists`, `_ICItemHasDrawings`, `_ICItemHasScannedDocuments`, `_ICItemHasICItemHasTags`, `_ICItemIsLocked` or `_ICItemIsShared`, joined with ` && `, then `(%@) && (%@)` with the text query |

### Spell-checker expansion

`ICSearchQueryTokenizer` splits the search into words and treats the connector characters
``_!~`@#$%^&*-+();:={}[],.<>?\/"'`` as joining a word into one unexpandable segment. Each
remaining word is passed to `spellCheckerGuessesForSearchString:inRange:language:`, which:

1. asks the spell server whether the word is misspelled;
2. if so, requests contextual guesses;
3. keeps guesses while there are at most 15 and each scores within 30 points of the best;
4. drops guesses shorter than the typed word.

The number of variants per word is capped at `floor((64 − 1 − 3) / word count)`. 64 is
`rankingQueryLimit`, and 1 and 3 are the highlighting and sorting descriptor groups.

The code is real and is called with `enableSpellCheckSPI:YES`. It made no difference in the app:
see the second mistake below.

## Spotlight's fuzzy matcher

Notes' fuzzy clauses end in `f066`. In `SpotlightIndex`, fuzzy terms are resolved from the term
trie (`term_trie_resolve_fuzzy_transition`, `evaluateFuzzyQuery`), and each candidate is checked
by `fuzzy_matches` in `MetadataUtilities`, which calls `lev_ukkonen_accepts`:

```
n      = number of ICU primary collation elements in the pattern
budget = min(3, floor(n × (100 − 66) / 100))
accept = Levenshtein(pattern, candidate) ≤ budget
prefix = also try candidate prefixes of length n − budget … n + budget
```

Substitutions, insertions and deletions cost one each; a transposition costs two. The extra
bookkeeping in `lev_ukkonen_accepts` records edit positions for highlighting and doesn't affect
acceptance.

The fuzzy lab confirmed the formula: no word Spotlight accepted was rejected by it. Spotlight
sometimes returns fewer matches, because it caps fuzzy candidates per query, and the cap depends
on the index: the same typo dropped in a 295-file index matched in a 13-file one.

## Ranking

`ICRankingQueriesDefinition` creates `ICRankingQueryDescriptor`s from three field lists in
`ICSpotlightUtilities`:

| List | Fields |
| --- | --- |
| generic highlighting | `[[Authors]]` |
| word-specific highlighting | `[[DisplayName, TextContent]]` |
| sorting | `[[DisplayName], [Authors], [TextContent]]` |

A descriptor's ranking query, from `-[ICRankingQueryDescriptor rankingQuery]`, ORs a clause for
every word across its fields. The clause type comes from `rankingQueryForQueryField:tokenString:`:

| Ranking query type | Template |
| --- | --- |
| 0 | `"word*"` |
| 1 | `"word*"…f066` |
| 2 | `"*word*"` |

Spotlight reports which ranking queries each item matched, as `ic_relevance`.
`rankingScoreForSearchableItem:` sums `rankingScoreForSearchResultType:` over the matched sorting
descriptors.

The weights below come from that method's decompilation, together with a three-entry table of
doubles it reads (8, 0.125, 1/256):

| Field (bit) | type 0, note / attachment | type 1 | type 2 |
| --- | --- | --- | --- |
| title (1) | 32 / 4 | 0.5 / 1/16 | 1/64 / 1/512 |
| text (2) | 16 / 2 | 0.25 / 1/32 | 1/128 / 1/1024 |
| authors (4) | 8 / 8 | 0.125 / 0.125 | 1/256 / 1/256 |

`bucketOfTimeInterval:` is `max(2, trunc(ln(seconds) × 1.28617347 − 6.14189718))`.

### Sort strategies

From `+[ICSortableSearchableItem sortDescriptorsForRankingStrategy:]`. `defaultStrategy` returns 0.

| Strategy | Keys (↓ descending) |
| --- | --- |
| 0 | isExactTitleMatch ↓, searchResultType ↑, isPrefixMatch ↓, modificationDate ↓, creationDate ↓, rankingScore ↓, relevanceBitField |
| 1 | searchResultType ↑, rankingScore ↓, isPrefixMatch ↓, relevanceBitField ↓, modificationDate ↓, creationDate ↓ |
| 2 | searchResultType ↑, isPrefixMatch ↓, modificationDateBucket ↑, rankingScore ↓, relevanceBitField ↓, modificationDate ↓, creationDate ↓ |

`isPrefixMatch` is filled in lazily (`lazilyInitializeHighlightInfoAndIsPrefixMatchIfNecessary`).
In the app, results that matched only inside a word were consistently mixed by date with prefix
matches, which suggests the flag isn't set yet when sorting happens.

## Natural language

`+[ICSearchQueryOperation nlpParser]` creates a parser with the private `NLSearchParserCreate`
from NLP.framework. Its grammar was not recovered. Three date phrases were measured in the app
(`yesterday`, `last month`, `from july`); notes-search extends the same calendar pattern to the
others.

## Two conclusions the app disproved

**"Substring matching is switched off."** The fuzzy-and-substring pass in step 2 is gated by an
internal setting that is never on, and the first version of this project concluded from that
that Notes matches only word prefixes. In the app, `ack` finds *back*. The matching happens
somewhere else: the literal substring query inside the default suggestions query, which the gated
pass has nothing to do with.

**"Typos are corrected by the spell checker."** The prefix query requests spell-checker guesses,
and the system spell checker returns them when asked directly (`recpie` → *recipe*). Yet none of
the misspelled searches tried in Notes, in English or Portuguese, returned anything. Whatever reaches Spotlight
from that path, it doesn't add results.

Both were caught by running the same searches in Notes.app, and both are now covered by tests.
