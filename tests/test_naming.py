import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import unittest

from simsetlib.naming import ParsedName, device_name, is_managed, parse_name


class NamingTests(unittest.TestCase):
    def test_first_device_has_no_index_suffix(self):
        self.assertEqual(device_name("triton", "iPhone 17 Pro"), "[triton] iPhone 17 Pro")

    def test_extra_devices_get_hash_index(self):
        self.assertEqual(device_name("triton", "iPhone 17 Pro", 2), "[triton] iPhone 17 Pro #2")

    def test_parse_round_trips_with_and_without_index(self):
        self.assertEqual(parse_name("[triton] iPad Pro 13-inch (M5)"),
                         ParsedName("triton", "iPad Pro 13-inch (M5)", 1))
        self.assertEqual(parse_name("[my-app] iPhone 16e #3"), ParsedName("my-app", "iPhone 16e", 3))

    def test_unmanaged_names_parse_to_none(self):
        for name in ["iPhone 17 Pro", "[unclosed iPhone", "triton iPhone", "[] iPhone"]:
            self.assertIsNone(parse_name(name), name)

    def test_is_managed(self):
        self.assertTrue(is_managed("[x] iPhone 17 Pro"))
        self.assertFalse(is_managed("iPhone 17 Pro"))


if __name__ == "__main__":
    unittest.main()
