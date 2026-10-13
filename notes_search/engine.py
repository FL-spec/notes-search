"""Search pipeline modelled on Apple Notes (macOS 26), verified side by side with Notes.app.

Default query (what Notes actually matches with):
    -[ICSearchQueryOperation createPrefixMatchingQuery] builds an ICSearchSuggestionsQuery and,
    with ICSearchConfiguration.searchSubstringMatchingEnabled, the literal substring query
    ``field="*<whole search string>*"cwdt`` over the query fields. Spotlight's ``t`` flag
    tokenizes the string: the FIRST word matches anywhere inside a word, every LATER word
    must match from the start of a word, word order is free, and all words must be found in
    the same field. Notes' text-content field includes the title line.

Then, as in -[ICSearchQueryOperation main]:
    - natural-language pass (date phrases; our parser stands in for NLSearchParser)
    - optional Apple-internal passes: fuzzy+substring, spelling API, related words
    - spell-server expansion of misspelled words (code path exists; measured to add nothing
      in Notes.app, so opt-in)
    - attachment hits pull in their parent notes
    - sort by ranking strategy; the first two results are Top Hits; deliver in batches.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Iterator, Sequence

from . import macos, nl
from .query import (
    MatchType,
    joint_query,
    literal_substring_query,
    max_variants_per_token,
    query_string,
    query_tokens,
)
from .ranking import (
    RANKING_MATCH,
    SORT_KEYS,
    Field,
    ResultType,
    Strategy,
    bucket_of_time_interval,
    field_score,
    ranking_query_type,
)
from .sources import Document
from .text import fold, search_terms, token_matches, words

_EPOCH = datetime.fromtimestamp(0, tz=timezone.utc)

# -[ICSearchQueryOperation main] splits the sorted results with
# ic_arrayBySplittingIntoTwoArraysWithMaxPrefixCount:2 and reports the prefix as top hits.
TOP_HIT_COUNT = 2


@dataclass
class Item:
    """One searchable item: a note, or an attachment of a note (indexed separately)."""

    doc: Document
    result_type: ResultType
    title: str
    body: str
    authors: list[str]
    labels: list[str]

    def fields(self) -> dict[Field | None, list[str]]:
        """Indexed fields. Notes' kMDItemTextContent includes the title line."""
        text = f"{self.title}\n{self.body}" if self.body else self.title
        return {
            Field.TITLE: [self.title],
            Field.BODY: [text],
            Field.AUTHORS: self.authors,
            None: self.labels,  # photo scene labels: queried, not ranked
        }

    def values(self) -> list[str]:
        return [v for vs in self.fields().values() for v in vs]


@dataclass
class Result:
    item: Item
    match_type: MatchType
    source: str = "default"  # default / prefix / fuzzy / substring / nl / spelling / related / parent / scope
    ranking_score: float = 0.0
    relevance_bit_field: int = 0
    is_exact_title_match: bool = False
    # Lazily computed by Notes (needsLazyInitialization) and not yet set when results are
    # sorted, so it never separates results in practice (measured: substring-only hits are
    # interleaved with prefix hits by date). ``has_prefix_match`` keeps the real value.
    is_prefix_match: bool = False
    has_prefix_match: bool = False
    is_top_hit: bool = False
    snippet: str = ""

    @property
    def search_result_type(self) -> int:
        return int(self.item.result_type)

    @property
    def modification_date(self) -> datetime:
        return self.item.doc.modified or _EPOCH

    @property
    def creation_date(self) -> datetime:
        return self.item.doc.created or self.item.doc.modified or _EPOCH

    @property
    def modification_date_bucket(self) -> int:
        age = (datetime.now(timezone.utc) - self.modification_date).total_seconds()
        return bucket_of_time_interval(age)

    def to_json(self) -> dict:
        return {
            "id": self.item.doc.id,
            "title": self.item.title,
            "type": self.item.result_type.name.lower(),
            "note": self.item.doc.title if self.item.result_type is ResultType.ATTACHMENT else None,
            "folder": self.item.doc.folder,
            "modified": self.item.doc.modified.isoformat() if self.item.doc.modified else None,
            "found_by": self.source,
            "score": self.ranking_score,
            "relevance_bits": self.relevance_bit_field,
            "exact_title": self.is_exact_title_match,
            "prefix_match": self.has_prefix_match,
            "top_hit": self.is_top_hit,
            "snippet": self.snippet,
        }


@dataclass
class Sections:
    top_hits: list[Result] = field(default_factory=list)
    notes: list[Result] = field(default_factory=list)
    attachments: list[Result] = field(default_factory=list)


@dataclass
class Plan:
    """What a search will run, for --explain."""

    tokens: list[list[str]]
    scope_tokens: list[str]
    filters: nl.Filters
    nl_text: str | None
    queries: list[tuple[str, str]]  # (pass name, Spotlight query)


def items_for(docs: Iterable[Document]) -> list[Item]:
    items = []
    for d in docs:
        items.append(Item(d, ResultType.NOTE, d.title, d.body, d.authors, d.labels))
        for name in d.attachments:
            items.append(Item(d, ResultType.ATTACHMENT, name, "", [], []))
    return items


def cwdt_field_match(tokens: Sequence[str], value: str) -> bool:
    """Spotlight ``field="*w1 w2 … wn*"cwdt`` on one field value (tokens already folded).

    Measured with mdfind and Notes.app: w1 must occur inside one indexed token of the value;
    w2…wn must each be the start of an indexed token; order is free.
    """
    if not tokens or not value or tokens[0] is None:
        return False
    value_words = words(value)
    if not any(tokens[0] in w for w in value_words):
        return False
    return all(any(w.startswith(t) for w in value_words) for t in tokens[1:])


def default_match(item: Item, tokens: Sequence[str]) -> bool:
    """Notes' default query: the cwdt substring query on any one query field."""
    return any(cwdt_field_match(tokens, v) for v in item.values())


def _matches(item: Item, groups: Sequence[Sequence[str]], mt: MatchType) -> bool:
    """Token-clause query: every group must match (' && '), any variant in any field (' || ')."""
    values = item.values()
    return all(any(token_matches(v, val, mt) for v in g for val in values) for g in groups)


def _score(result: Result, groups: Sequence[Sequence[str]], ranking_type: int) -> None:
    """Sum the weights of the sorting descriptors whose ranking query matched.

    A descriptor's ranking query ORs a field clause for every token variant, so a field's
    bit is set when any token variant matches in that field.
    """
    mt = RANKING_MATCH[ranking_type]
    variants = [v for g in groups for v in g]
    score, bits = 0.0, 0
    for fld, values in result.item.fields().items():
        if fld is None:
            continue
        if any(token_matches(v, val, mt) for v in variants for val in values):
            bits |= int(fld)
            score += field_score(fld, ranking_type, result.item.result_type)
    result.ranking_score = score
    result.relevance_bit_field = bits


def _snippet(item: Item, tokens: Sequence[str], width: int = 90) -> str:
    text = item.body or item.title
    folded = fold(text)
    hits = [p for p in (folded.find(t) for t in tokens) if p >= 0]
    pos = min(hits, default=0)
    start = max(0, pos - width // 3)
    s = " ".join(text[start:start + width].split())
    return ("…" if start else "") + s + ("…" if start + width < len(text) else "")


def _sort_key(strategy: Strategy):
    keys = SORT_KEYS[strategy]

    def key(r: Result):
        out = []
        for name, ascending in keys:
            v = getattr(r, name)
            if isinstance(v, datetime):
                v = v.timestamp()
            v = float(v)
            out.append(v if ascending else -v)
        return tuple(out)

    return key


def _apply_filters(item: Item, f: nl.Filters, scope: Sequence[str]) -> bool:
    d = item.doc
    if any(t not in d.flags for t in scope):
        return False
    if f.folder and fold(f.folder) not in fold(d.folder):
        return False
    if f.modified_after and (not d.modified or d.modified < f.modified_after):
        return False
    if f.modified_before and (not d.modified or d.modified >= f.modified_before):
        return False
    if f.tags and not all(f"#{t}" in fold(d.body + " " + d.title) for t in f.tags):
        return False
    if f.has_attachments and not d.attachments:
        return False
    return True


class SearchEngine:
    def __init__(
        self,
        docs: Iterable[Document],
        strategy: Strategy = Strategy.RECENCY,
        fuzzy: bool = False,
        natural_language: bool = True,
        spell_expansion: bool = False,
        spell_api: bool = False,
        related_words: bool = False,
        related_max_count: int = 5,
        related_max_distance: float = 1.0,
        language: str | None = None,
    ) -> None:
        self.items = items_for(docs)
        self.strategy = strategy
        self.fuzzy = fuzzy
        self.natural_language = natural_language
        self.spell_expansion = spell_expansion and macos.available()
        self.spell_api = spell_api and macos.available()
        self.related_words = related_words and macos.available()
        self.related_max_count = related_max_count
        self.related_max_distance = related_max_distance
        self.language = language

    # -- planning ----------------------------------------------------------------------

    def _expand(self, text: str) -> tuple[list[list[str]], dict]:
        """Token groups (word + spell-server guesses when enabled) and raw lookups."""
        toks = query_tokens(text)
        expandable = tuple(t for t, e in toks if e)
        services: dict = {"guesses": {}, "lemmas": {}, "neighbors": {}}
        wants_spell = self.spell_expansion or self.spell_api
        if expandable and (wants_spell or self.related_words):
            services = macos.language_services(
                spell=expandable if wants_spell else (),
                lemma=(text,) if self.spell_api else (),
                related=(text,) if self.related_words else (),
                language=self.language,
                max_count=self.related_max_count,
                max_distance=self.related_max_distance,
            )
        cap = max_variants_per_token(len(toks))
        groups = []
        for tok, exp in toks:
            group = [tok]
            guesses = services["guesses"].get(tok) if (exp and self.spell_expansion) else None
            if guesses:
                # Guesses shorter than the typed word are dropped; cap the variant count.
                seen = {fold(tok)}
                for g in guesses:
                    if len(g) >= len(tok) and fold(g) not in seen:
                        seen.add(fold(g))
                        group.append(g)
            groups.append(group[:cap])
        return groups, services

    def plan(self, search: str, scope: Sequence[str] = ()) -> Plan:
        groups, services = self._expand(search)
        nl_text, filters = nl.parse(search) if self.natural_language else (search, nl.Filters())
        queries = [("default", joint_query(literal_substring_query(search), scope))]
        if self.spell_expansion:
            queries.append(("prefix+spelling",
                            joint_query(query_string("", MatchType.PREFIX, expanded_tokens=groups), scope)))
        if self.fuzzy and len("".join(search.split())) > 2:
            queries.append(("fuzzy", joint_query(query_string(search, MatchType.FUZZY), scope)))
            queries.append(("substring", joint_query(query_string(search, MatchType.SUBSTRING), scope)))
        if self.natural_language and filters.active():
            text_q = literal_substring_query(nl_text) if nl_text else "*"
            queries.append(("nl", f"{text_q}  + {filters.describe()}"))
        for name, q in self._extra_queries(search, services):
            queries.append((name, joint_query(query_string(q, MatchType.EXACT), scope)))
        return Plan(groups, list(scope), filters, nl_text if filters.active() else None, queries)

    def _extra_queries(self, search: str, services: dict) -> list[tuple[str, str]]:
        out = []
        if self.spell_api:
            # performSpellCheckerAPIQueryIfNeeded: first guess, then the lemma.
            toks = [t for t, _ in query_tokens(search)]
            guessed = [(services["guesses"].get(t) or [t])[0] for t in toks]
            if guessed != toks:
                out.append(("spelling", " ".join(guessed)))
            lemma = services["lemmas"].get(search)
            if lemma and fold(lemma) != fold(search):
                out.append(("spelling", lemma))
        if self.related_words:
            for n in services["neighbors"].get(search, []) or []:
                out.append(("related", n))
        return out

    # -- execution ---------------------------------------------------------------------

    def search(self, search: str, scope: Sequence[str] = ()) -> list[Result]:
        terms = search_terms(search)
        tokens = [t for t in terms if t]
        groups, services = self._expand(search)
        fgroups = [[fold(v) for v in g] for g in groups]
        rank_groups = [[t] for t in tokens]

        nl_text, filters = nl.parse(search) if self.natural_language else (search, nl.Filters())
        pool = [i for i in self.items if _apply_filters(i, nl.Filters(), scope)]
        found: dict[int, Result] = {}

        def add(item: Item, mt: MatchType, name: str, grps, ranking_type: int = 0) -> None:
            if id(item) not in found:
                r = Result(item, mt, name)
                _score(r, grps, ranking_type)
                found[id(item)] = r

        if terms:
            for item in pool:
                if default_match(item, terms):
                    add(item, MatchType.SUBSTRING, "default", rank_groups)
            if self.spell_expansion:
                for item in pool:
                    if _matches(item, fgroups, MatchType.PREFIX):
                        add(item, MatchType.PREFIX, "prefix", fgroups)
            if self.fuzzy and len("".join(search.split())) > 2:
                for mt in (MatchType.FUZZY, MatchType.SUBSTRING):
                    for item in pool:
                        if _matches(item, rank_groups, mt):
                            add(item, mt, mt.name.lower(), rank_groups, ranking_query_type(mt))
        elif scope:
            # allowEmptySearchString: scope tokens alone list every item that has them.
            for item in pool:
                add(item, MatchType.PREFIX, "scope", [])

        if self.natural_language and filters.active():
            nl_tokens = [t for t in search_terms(nl_text) if t]
            for item in pool:
                if _apply_filters(item, filters, ()) and (not nl_tokens or default_match(item, nl_tokens)):
                    add(item, MatchType.SUBSTRING, "nl", [[t] for t in nl_tokens])

        for name, q in self._extra_queries(search, services):
            q_groups = [[fold(t)] for t in q.split()]
            for item in pool:
                if _matches(item, q_groups, MatchType.EXACT):
                    add(item, MatchType.EXACT, name, q_groups)

        # retrieveNotesOfFoundAttachmentsForSearchResults: add parent notes of attachment hits.
        notes_by_doc = {id(i.doc): i for i in self.items if i.result_type is ResultType.NOTE}
        for r in list(found.values()):
            if r.item.result_type is ResultType.ATTACHMENT:
                parent = notes_by_doc.get(id(r.item.doc))
                if parent is not None:
                    add(parent, r.match_type, "parent", rank_groups)

        results = list(found.values())
        for r in results:
            r.is_exact_title_match = bool(tokens) and fold(r.item.title).strip() == fold(search).strip()
            r.has_prefix_match = bool(tokens) and _matches(r.item, rank_groups, MatchType.PREFIX)
            r.snippet = _snippet(r.item, tokens)
        results.sort(key=_sort_key(self.strategy))
        for r in results[:TOP_HIT_COUNT]:
            r.is_top_hit = True
        return results

    def search_batched(self, search: str, batch_size: int = 25, scope: Sequence[str] = ()) -> Iterator[list[Result]]:
        """Deliver results in fixed-size batches, as Notes' found-items handler does.

        The Mac app sizes a batch as the number of rows that fit on screen:
        ceil(visible height / min(note row height, attachment row height)).
        """
        results = self.search(search, scope)
        for i in range(0, len(results), batch_size):
            yield results[i:i + batch_size]

    @staticmethod
    def sections(results: Sequence[Result]) -> Sections:
        s = Sections()
        for r in results:
            if r.is_top_hit:
                s.top_hits.append(r)
            elif r.item.result_type is ResultType.ATTACHMENT:
                s.attachments.append(r)
            else:
                s.notes.append(r)
        return s
