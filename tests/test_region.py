import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ow174.catalog.regions import localize, region_of  # noqa: E402


def entry(amount, text):
    return {"+0x0": amount * 10000, "+0x8": amount * 10000, "+0x10": 643, "+0x18": text, "+0x40": text}


class RegionTests(unittest.TestCase):
    def test_us_prices_and_currency(self):
        out = localize({"+0x0": [entry(1199, "1199,00 RUB")]}, "US")["+0x0"][0]
        self.assertEqual((out["+0x10"], out["+0x18"], out["+0x40"]), (840, "$19.99", "$19.99"))
        self.assertEqual((out["+0x0"], out["+0x8"]), (199900, 199900))

    def test_eu_uses_decimal_comma_and_euro(self):
        out = localize(entry(119, "119,00 RUB"), "EU")
        self.assertEqual((out["+0x10"], out["+0x18"]), (978, "1,99 \u20ac"))

    def test_ru_keeps_capture(self):
        original = entry(299, "299,00 RUB")
        self.assertEqual(localize(original, "RU"), original)

    def test_country_follows_region_and_unknown_falls_back_to_us(self):
        self.assertEqual(localize({"+0xF8": "RUS"}, "GB"), {"+0xF8": "GBR"})
        self.assertEqual(region_of("nope").code, "US")

    def test_input_is_not_mutated(self):
        original = entry(119, "119,00 RUB")
        localize(original, "US")
        self.assertEqual(original["+0x10"], 643)

    def test_store_message_currency_code_follows_region(self):
        store = {"+0x78": 643, "+0x80": [{"+0x0": [entry(119, "119,00 RUB")]}]}
        self.assertEqual(localize(store, "US")["+0x78"], 840)
        self.assertEqual(localize(store, "RU")["+0x78"], 643)

    def test_no_rub_left_in_any_captured_store_for_non_ru_regions(self):
        templates = json.loads((ROOT / "data" / "retail_templates.json").read_text(encoding="utf-8"))
        for code in ("US", "EU", "GB"):
            text = json.dumps(localize(templates["server"], code), ensure_ascii=False)
            self.assertNotIn("RUB", text, code)
            self.assertNotIn('"RUS"', text, code)
            self.assertNotIn('"+0x10": 643,', text, code)
            self.assertNotIn('"+0x78": 643,', text, code)


if __name__ == "__main__":
    unittest.main()
