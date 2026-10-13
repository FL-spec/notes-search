"""Ranking, recovered from NotesShared's ICRankingQueryDescriptor / ICRankingQueriesDefinition /
ICSortableSearchableItem (macOS 26). See docs/REVERSE_ENGINEERING.md for evidence."""

from __future__ import annotations

import math
from enum import IntEnum

from .query import MatchType


class Field(IntEnum):
    """displayedMatchedFields bits, from -[ICRankingQueryDescriptor initWithQueryFields:...]."""

    TITLE = 1  # _ICItemDisplayName / kMDItemDisplayName
    BODY = 2  # kMDItemTextContent
    AUTHORS = 4  # kMDItemAuthors / kMDItemAuthorEmailAddresses


class ResultType(IntEnum):
    """ic_searchResultType. 0 sorts first; Notes lists notes above attachments."""

    NOTE = 0
    ATTACHMENT = 1


# -[ICRankingQueryDescriptor rankingScoreForSearchResultType:]
# WEIGHTS[field][ranking query type][search result type]; types not listed score 0.
# Ranking query types, from -[ICRankingQueryDescriptor rankingQueryForQueryField:tokenString:]:
#   0 prefix   field="token*"flags
#   1 fuzzy    field="token*"flagsf066
#   2 substring field="*token*"flags
# Authors ignore the result type.
WEIGHTS: dict[Field, dict[int, dict[int, float]]] = {
    Field.TITLE: {
        0: {0: 32.0, 1: 4.0},
        1: {0: 0.5, 1: 1 / 16},
        2: {0: 1 / 64, 1: 1 / 512},
    },
    Field.BODY: {
        0: {0: 16.0, 1: 2.0},
        1: {0: 0.25, 1: 1 / 32},
        2: {0: 1 / 128, 1: 1 / 1024},
    },
    Field.AUTHORS: {
        0: {0: 8.0, 1: 8.0},
        1: {0: 0.125, 1: 0.125},
        2: {0: 1 / 256, 1: 1 / 256},
    },
}

# Ranking query type -> the match type its per-field ranking query uses.
RANKING_MATCH = {0: MatchType.PREFIX, 1: MatchType.FUZZY, 2: MatchType.SUBSTRING}


def ranking_query_type(match_type: MatchType) -> int:
    """Ranking type a query pass's ICRankingQueriesDefinition uses.

    The prefix, spelling and related-word passes construct their definitions with type 0
    (observed); fuzzy and substring passes are mapped to 1 and 2 (inferred).
    """
    return {MatchType.FUZZY: 1, MatchType.SUBSTRING: 2}.get(match_type, 0)


def field_score(field: Field, ranking_type: int, result_type: ResultType) -> float:
    return WEIGHTS[field][ranking_type].get(int(result_type), 0.0)


def bucket_of_time_interval(seconds: float) -> int:
    """+[ICRankingQueriesDefinition bucketOfTimeInterval:]

    Logarithmic age bucket: max(2, trunc(ln(seconds) * 1.28617347 - 6.14189718)).
    Roughly: under ~20 min -> 2, an hour -> 4, a day -> 8, a week -> 10, a year -> 16.
    """
    if seconds > 0:
        bucket = math.trunc(math.log(seconds) * 1.28617347 - 6.14189718)
        if bucket > 2:
            return bucket
    return 2


class Strategy(IntEnum):
    """+[ICSortableSearchableItem sortDescriptorsForRankingStrategy:].

    +[ICSearchRankingStrategySwitch defaultStrategy] returns 0.
    """

    RECENCY = 0
    SCORE = 1
    BUCKETED = 2


# (key, ascending) pairs, in Apple's order.
SORT_KEYS: dict[Strategy, list[tuple[str, bool]]] = {
    Strategy.RECENCY: [
        ("is_exact_title_match", False),
        ("search_result_type", True),
        ("is_prefix_match", False),
        ("modification_date", False),
        ("creation_date", False),
        ("ranking_score", False),
        ("relevance_bit_field", False),
    ],
    Strategy.SCORE: [
        ("search_result_type", True),
        ("ranking_score", False),
        ("is_prefix_match", False),
        ("relevance_bit_field", False),
        ("modification_date", False),
        ("creation_date", False),
    ],
    Strategy.BUCKETED: [
        ("search_result_type", True),
        ("is_prefix_match", False),
        ("modification_date_bucket", True),
        ("ranking_score", False),
        ("relevance_bit_field", False),
        ("modification_date", False),
        ("creation_date", False),
    ],
}
