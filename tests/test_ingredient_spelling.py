"""Spelling correction and gibberish rejection for typed ingredients; no network or database."""

import unittest

from ingredient_parser import NAMES_BY_PLAIN, VOCABULARY, WORDS_BY_PLAIN, looks_like_word, parse_ingredients


class SpellingTests(unittest.TestCase):
    def test_plain_keyboard_spelling_maps_to_known_names(self):
        known, unknown, _, invalid, corrected = parse_ingredients(
            "kelem, yag, gobelek, qirmizi biber, cugundur, et, kere yagi, zeytun yagi"
        )
        self.assertEqual(known, ["Kələm", "Yağ", "Göbələk", "Qırmızı bibər", "Çuğundur",
                                 "Ət", "Kərə yağı", "Zeytun yağı"])
        self.assertEqual(unknown + invalid, [])
        self.assertIn("Kelem → Kələm", corrected)
        self.assertIn("Yag → Yağ", corrected)

    def test_single_typo_is_fixed_only_for_longer_names(self):
        known, _, _, _, corrected = parse_ingredients("pomidr, sekerr")
        self.assertEqual(known, ["Pomidor", "Şəkər"])
        self.assertIn("Pomidr → Pomidor", corrected)
        # Short words are not guessed: «kiwi» is not silently turned into another fruit.
        self.assertEqual(parse_ingredients("bul")[1], ["Bul"])

    def test_unknown_real_names_still_ask_with_known_words_fixed(self):
        known, unknown, _, invalid, _ = parse_ingredients("Teheng yarpagi, Spirulina, Kətan toxumu")
        self.assertEqual(known, [])
        self.assertEqual(unknown, ["Teheng yarpağı", "Spirulina", "Kətan toxumu"])
        self.assertEqual(invalid, [])

    def test_keyboard_mash_is_rejected_without_confirmation(self):
        known, unknown, _, invalid, _ = parse_ingredients("hjhfbskajfbsej, qwerty, asdasd, abab, zxcvb")
        self.assertEqual(known + unknown, [])
        self.assertEqual(invalid, ["hjhfbskajfbsej", "qwerty", "asdasd", "abab", "zxcvb"])

    def test_real_words_are_not_treated_as_gibberish(self):
        for word in ("Kinoa", "Hummus", "Bəhməz", "Tortilla", "Smetana", "Bonbon", "Çia toxumu"):
            self.assertTrue(looks_like_word(word), word)

    def test_vocabulary_has_no_ambiguous_plain_spellings(self):
        self.assertNotIn(None, NAMES_BY_PLAIN.values())
        self.assertNotIn(None, WORDS_BY_PLAIN.values())
        self.assertEqual(len(VOCABULARY), len(set(VOCABULARY)))


if __name__ == "__main__":
    unittest.main()
