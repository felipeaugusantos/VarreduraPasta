import unittest

from app.version_reader import _format_version


class FormatVersionTests(unittest.TestCase):
    def test_parts_below_ten_are_zero_padded(self):
        ms_value = (48 << 16) | 2
        ls_value = (30 << 16) | 74
        self.assertEqual(_format_version(ms_value, ls_value), "48.02.30.74")

    def test_parts_of_ten_or_more_are_not_padded(self):
        ms_value = (10 << 16) | 100
        ls_value = (1000 << 16) | 65535
        self.assertEqual(_format_version(ms_value, ls_value), "10.100.1000.65535")

    def test_zero_version(self):
        self.assertEqual(_format_version(0, 0), "00.00.00.00")


if __name__ == "__main__":
    unittest.main()
