import unittest

from midas_cn.universe.symbols import normalize_symbol, normalize_symbols


class SymbolNormalizationTest(unittest.TestCase):
    def test_normalize_symbol_infers_exchange(self):
        self.assertEqual(normalize_symbol("600519"), "600519.SH")
        self.assertEqual(normalize_symbol("300750"), "300750.SZ")
        self.assertEqual(normalize_symbol("830799"), "830799.BJ")
        self.assertEqual(normalize_symbol("920200"), "920200.BJ")

    def test_normalize_symbols_deduplicates(self):
        self.assertEqual(
            normalize_symbols(["600519", "600519.SH", "300750"]),
            ["600519.SH", "300750.SZ"],
        )

    def test_normalize_symbol_rejects_unknown_prefix(self):
        with self.assertRaises(ValueError):
            normalize_symbol("ABC")


if __name__ == "__main__":
    unittest.main()
