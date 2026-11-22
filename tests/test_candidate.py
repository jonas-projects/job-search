"""tools/candidate.py: the candidate's name and PDF prefixes come from facts.json, never code."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import candidate  # noqa: E402


def facts(first, last, full=None, **extra):
    f = {"name": {"first": first, "last": last}}
    if full is not None:
        f["name"]["full"] = full
    f.update(extra)
    return f


class CandidateTest(unittest.TestCase):
    def test_prefixes_from_facts(self):
        f = facts("Ada", "Lovelace", "Ada Lovelace")
        self.assertEqual(candidate.first_last(f), ("Ada", "Lovelace"))
        self.assertEqual(candidate.full_name(f), "Ada Lovelace")
        self.assertEqual(candidate.file_prefix(f), "Ada_Lovelace")
        self.assertEqual(candidate.resume_prefix(f), "Ada_Lovelace_Resume_")
        self.assertEqual(candidate.cover_letter_name(f), "Ada_Lovelace_Cover_Letter.pdf")

    def test_full_name_falls_back_to_first_last(self):
        self.assertEqual(candidate.full_name(facts("Ada", "Lovelace", "[FULL_NAME]")), "Ada Lovelace")

    def test_unsafe_characters_become_underscores(self):
        self.assertEqual(candidate.file_prefix(facts("Mary Ann", "O'Neil-Smith")), "Mary_Ann_O_Neil-Smith")

    def test_placeholders_raise_clear_error(self):
        for first, last in (("[FIRST_NAME]", "[LAST_NAME]"), ("", "Lovelace"), ("Ada", None), ("{first}", "x")):
            with self.assertRaises(candidate.CandidateNotSet) as cm:
                candidate.file_prefix(facts(first, last))
            self.assertIn("candidate name not set", str(cm.exception))

    def test_auto_submit_is_opt_in(self):
        self.assertFalse(candidate.auto_submit_opted_in(facts("Ada", "Lovelace")))
        self.assertFalse(candidate.auto_submit_opted_in(facts("Ada", "Lovelace", automation={"auto_submit": "yes"})))
        self.assertTrue(candidate.auto_submit_opted_in(facts("Ada", "Lovelace", automation={"auto_submit": True})))


if __name__ == "__main__":
    unittest.main()
