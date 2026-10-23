import unittest
from harbor.money import parse_minor
from harbor.time_window import in_window


class Baseline(unittest.TestCase):
    def test_legacy_conversion(self):
        self.assertEqual(parse_minor('12.50'), 1250)
        with self.assertRaises(ValueError): parse_minor('-1.00')

    def test_window(self):
        self.assertTrue(in_window('2026-01-01T12:00:00Z', '2026-01-01T00:00:00Z', '2026-01-02T00:00:00Z'))
