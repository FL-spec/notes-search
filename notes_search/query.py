"""Spotlight query-string builder, modelled on Apple Notes' ICSearchQueryParser.

Reverse-engineered behaviour (NotesShared / NotesSupport, macOS 26):

* The search string is escaped by ``+[ICSpotlightUtilities stringByEscapingSearchString:]``,
  which backslash-escapes ``\\``, ``"``, ``'`` and ``*`` (in that order).
* Each token becomes one clause per query field, built from one of four templates
  selected by the match type:

  ====  =========  =========================
  type  name       template
  ====  =========  =========================
  0     exact      ``field="token"flags``
  1     prefix     ``field="token*"flags``
  2     fuzzy      ``field="token*"flags`` + ``f066``
  3     substring  ``field="*token*"flags``
  ====  =========  =========================

* A token's clauses are joined with `` || `` and wrapped as ``(...)``; tokens are
  joined with `` && `` - every word must match, in any field.
* Default fields (``+[ICSpotlightUtilities queryFields]``): title, authors, body text,
  and the photo scene-classification labels and synonyms.
* Default flags are ``cwd``: case-, word- and diacritic-insensitive.
"""

from __future__ import annotations

from enum import IntEnum
from typing import Iterable, Sequence

# +[ICSpotlightUtilities queryFields], in Apple's order.
QUERY_FIELDS: tuple[str, ...] = (
    "_ICItemDisplayName",
    "kMDItemAuthors",
    "kMDItemTextContent",
    "kMDItemPhotosSceneClassificationLabels",
    "kMDItemPhotosSceneClassificationSynonyms",
)

DEFAULT_FLAGS = "cwd"

# The fuzzy template appends "f" + "%3.3d" % 66 to the flags.
FUZZY_SUFFIX = "f%03d" % 66


class MatchType(IntEnum):
    EXACT = 0
    PREFIX = 1
    FUZZY = 2
    SUBSTRING = 3


def escape_search_string(text: str) -> str:
    """Mirror ICSpotlightUtilities.stringByEscapingSearchString:."""
    for raw, escaped in (("\\", "\\\\"), ('"', '\\"'), ("'", "\\'"), ("*", "\\*")):
        text = text.replace(raw, escaped)
    return text


# +[ICSearchQueryTokenizer connectorCharacterSet]
CONNECTORS = frozenset("_!~`@#$%^&*-+();:={}[],.<>?\\/\"'")

# Scope tokens (ICSearchToken.subQueryString is "%@ = 1" on these indexed attributes).
# Token subqueries are wrapped as "(%@)" and joined with " && ", then combined with the text
# query as "(%@) && (%@)" by -[ICSearchQueryOperation jointQueryWithSuggestions:].
TOKEN_ATTRIBUTES: dict[str, str] = {
    "attachments": "_ICItemHasAttachments",
    "checklists": "_ICItemHasChecklists",
    "drawings": "_ICItemHasDrawings",
    "scans": "_ICItemHasScannedDocuments",
    "tags": "_ICItemHasICItemHasTags",  # sic, Apple's attribute name
    "locked": "_ICItemIsLocked",
    "shared": "_ICItemIsShared",
}


def tokenize(search_string: str) -> list[str]:
    """Split on whitespace, as the parser does before building per-token clauses."""
    return search_string.split()


def query_tokens(search_string: str) -> list[tuple[str, bool]]:
    """Mirror ICSearchQueryTokenizer: words, with connector-joined words kept together.

    Returns (token, expandable). Leading/trailing connector characters are dividers and are
    dropped; a token that still contains connectors (``e-mail``, ``don't``) is combined into
    one literal segment and is not spell-expanded.
    """
    out = []
    for chunk in search_string.split():
        token = chunk.strip("".join(CONNECTORS))
        if token:
            out.append((token, not any(c in CONNECTORS for c in token)))
    return out


def token_subquery(name: str) -> str:
    return f"{TOKEN_ATTRIBUTES[name]} = 1"


def joint_query(text_query: str, tokens: Sequence[str] = ()) -> str:
    """-[ICSearchQueryOperation jointQueryWithSuggestions:]."""
    if not tokens:
        return text_query
    tokens_query = " && ".join(f"({token_subquery(t)})" for t in tokens)
    return f"({tokens_query}) && ({text_query})" if text_query else tokens_query


# +[ICSpotlightUtilities rankingQueryLimit]
RANKING_QUERY_LIMIT = 64


def max_variants_per_token(token_count: int) -> int:
    """+[ICRankingQueriesDefinition maxCountOfVariantsForCountOfTokens:].

    floor((rankingQueryLimit - genericHighlightGroups - sortingGroups)
          / wordSpecificHighlightGroups / tokenCount) = floor((64 - 1 - 3) / 1 / n), min 1.
    """
    if token_count <= 0:
        return 0
    return max(1, (RANKING_QUERY_LIMIT - 1 - 3) // token_count)


def clause_for_token(
    token: str,
    match_type: MatchType,
    fields: Sequence[str] = QUERY_FIELDS,
    flags: str = DEFAULT_FLAGS,
) -> str:
    """One token across every field, joined with ' || ' and wrapped in parentheses."""
    token = escape_search_string(token)
    parts = []
    for field in fields:
        if match_type is MatchType.EXACT:
            parts.append(f'{field}="{token}"{flags}')
        elif match_type is MatchType.PREFIX:
            parts.append(f'{field}="{token}*"{flags}')
        elif match_type is MatchType.FUZZY:
            parts.append(f'{field}="{token}*"{flags}{FUZZY_SUFFIX}')
        else:
            parts.append(f'{field}="*{token}*"{flags}')
    return "(" + " || ".join(parts) + ")"


def query_string(
    search_string: str,
    match_type: MatchType = MatchType.PREFIX,
    fields: Sequence[str] = QUERY_FIELDS,
    flags: str = DEFAULT_FLAGS,
    expanded_tokens: Iterable[Iterable[str]] | None = None,
) -> str:
    """Build the full Spotlight query for a search string.

    ``expanded_tokens`` mirrors ``queryStringForExpandedTokens:``: each entry is the list of
    variants for one typed word (the word plus spell-checker alternatives). Variants of the
    same word are OR-ed; words are AND-ed.
    """
    if expanded_tokens is None:
        groups = [[t] for t in tokenize(search_string)]
    else:
        groups = [list(g) for g in expanded_tokens]
    clauses = []
    for variants in groups:
        if not variants:
            continue
        inner = [clause_for_token(v, match_type, fields, flags) for v in variants]
        clauses.append(inner[0] if len(inner) == 1 else "(" + " || ".join(inner) + ")")
    return " && ".join(clauses)


def literal_substring_query(
    search_string: str, fields: Sequence[str] = QUERY_FIELDS, flags: str = "cwdt"
) -> str:
    """The query Notes actually runs by default.

    +[ICSearchQueryOperation substringMatchingQueryStringForSearchString:] calls
    +[ICSearchQueryParser queryStringForSearchString:queryFields:matchType:3], which passes the
    whole search string as ONE token with flags ``cwdt`` (the ``t`` makes Spotlight tokenize it).
    """
    s = escape_search_string(search_string.strip())
    if not s:
        return ""
    return "(" + " || ".join(f'{f}="*{s}*"{flags}' for f in fields) + ")"
