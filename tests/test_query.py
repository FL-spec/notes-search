import unittest

from notes_search.query import (
    MatchType,
    clause_for_token,
    escape_search_string,
    query_string,
)

ONE_FIELD = ("kMDItemTextContent",)


class EscapeTests(unittest.TestCase):
    def test_escapes_backslash_quotes_and_star(self):
        self.assertEqual(escape_search_string("a\\b\"c'd*e"), "a\\\\b\\\"c\\'d\\*e")

    def test_backslash_escaped_first(self):
        # A quote's escape backslash must not itself be re-escaped.
        self.assertEqual(escape_search_string('"'), '\\"')


class ClauseTests(unittest.TestCase):
    def test_templates_per_match_type(self):
        self.assertEqual(clause_for_token("cat", MatchType.EXACT, ONE_FIELD), '(kMDItemTextContent="cat"cwd)')
        self.assertEqual(
            clause_for_token("cat", MatchType.PREFIX, ONE_FIELD), '(kMDItemTextContent="cat*"cwd)'
        )
        self.assertEqual(
            clause_for_token("cat", MatchType.FUZZY, ONE_FIELD), '(kMDItemTextContent="cat*"cwdf066)'
        )
        self.assertEqual(
            clause_for_token("cat", MatchType.SUBSTRING, ONE_FIELD), '(kMDItemTextContent="*cat*"cwd)'
        )

    def test_fields_or_joined(self):
        clause = clause_for_token("x", MatchType.PREFIX, ("a", "b"))
        self.assertEqual(clause, '(a="x*"cwd || b="x*"cwd)')


class QueryStringTests(unittest.TestCase):
    def test_words_and_joined(self):
        q = query_string("red car", fields=ONE_FIELD)
        self.assertEqual(q, '(kMDItemTextContent="red*"cwd) && (kMDItemTextContent="car*"cwd)')

    def test_expanded_variants_or_joined(self):
        q = query_string("", fields=ONE_FIELD, expanded_tokens=[["teh", "the"]])
        self.assertEqual(q, '((kMDItemTextContent="teh*"cwd) || (kMDItemTextContent="the*"cwd))')

    def test_default_fields_include_title_and_photo_labels(self):
        q = query_string("dog")
        for field in ("_ICItemDisplayName", "kMDItemTextContent", "kMDItemPhotosSceneClassificationLabels"):
            self.assertIn(field, q)


if __name__ == "__main__":
    unittest.main()
