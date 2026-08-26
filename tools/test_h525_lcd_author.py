"""Offline regressions for the H525 container-wide alphabet authoring rule."""
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _paths
import hconfig
import h525_lcd_author as lcd
import verify_525_semantics as semantics


DIGIT_7 = (
    "......",
    ".####.",
    "....#.",
    "....#.",
    "...#..",
    "...#..",
    "......",
    "......",
)

HASH_MARK = (
    ".......",
    "..#.#..",
    ".#####.",
    "..#.#..",
    ".#####.",
    "..#.#..",
    ".......",
    ".......",
)


class AlphabetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        raw = _paths.SAMPLE_EZHEX.read_bytes()
        cls.blob = hconfig.split_container(raw)[2]
        cls.sections = semantics.section_offsets(cls.blob)
        cls.document = hconfig.symbolise(hconfig.decompile(raw, _paths.SAMPLE_EZHEX.name))

    def test_owner_alphabet_is_one_to_one_and_code_63_is_hash(self) -> None:
        alphabet = lcd.alphabet_map()
        self.assertEqual(len(alphabet), 66)
        self.assertEqual(alphabet[63], "#")
        self.assertEqual(lcd.encode_text("OF 5", alphabet), bytes((3, 4, 13, 64)))

    def test_known_h525_digit_7_shape_is_pinned(self) -> None:
        self.assertEqual(lcd.glyph_shape_key(DIGIT_7), "8:7e61864cab4a327a")
        self.assertEqual(lcd.encode_literal_glyph(DIGIT_7)[0], 6)

    def test_new_character_is_appended_after_the_global_alphabet(self) -> None:
        document = copy.deepcopy(self.document)
        alphabet = lcd.alphabet_map()
        code, region = lcd.install_character_glyph(
            self.blob,
            document,
            self.sections,
            font_index=4,
            character="7",
            rows=DIGIT_7,
            alphabet=alphabet,
            region_id="test_digit_7",
        )
        self.assertEqual(code, 67)
        self.assertEqual(alphabet[67], "7")
        self.assertEqual(region["kind"], "opaque")
        font = next(
            item for item in document["blob"]["regions"]
            if item["kind"] == "font_set" and item["height"] == 8
        )
        self.assertEqual(len(font["targets"]), 67)
        self.assertEqual(font["targets"][66], {"to": "test_digit_7"})

    def test_null_slot_may_only_be_filled_with_its_existing_character(self) -> None:
        document = copy.deepcopy(self.document)
        alphabet = lcd.alphabet_map()
        code, _region = lcd.install_character_glyph(
            self.blob,
            document,
            self.sections,
            font_index=4,
            character="#",
            rows=HASH_MARK,
            alphabet=alphabet,
            region_id="test_hash",
        )
        self.assertEqual(code, 63)
        font = next(
            item for item in document["blob"]["regions"]
            if item["kind"] == "font_set" and item["height"] == 8
        )
        self.assertEqual(len(font["targets"]), 66)
        self.assertEqual(font["targets"][62], {"to": "test_hash"})

    def test_live_existing_glyph_is_never_replaced(self) -> None:
        with self.assertRaisesRegex(ValueError, "already has a live glyph"):
            lcd.install_character_glyph(
                self.blob,
                copy.deepcopy(self.document),
                self.sections,
                font_index=0,
                character="#",
                rows=(".......",) * 11,
                alphabet=lcd.alphabet_map(),
                region_id="must_not_replace_hash",
            )

    def test_bad_dimensions_and_unknown_text_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid glyph dimensions"):
            lcd.encode_literal_glyph(("..", "."))
        with self.assertRaisesRegex(ValueError, "not in this container alphabet"):
            lcd.encode_text("7", lcd.alphabet_map())


if __name__ == "__main__":
    unittest.main()
