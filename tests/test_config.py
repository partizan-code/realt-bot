import os
import unittest
from unittest.mock import patch

import main


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.environment = {
            "TOKEN": "test-token",
            "ADMIN_ID": "123",
            **{f"{city.upper()}_CH_ID": str(-1000 - i) for i, city in enumerate(main.CITIES)},
        }
        self.dotenv = patch("main.load_dotenv")
        self.dotenv.start()
        self.addCleanup(self.dotenv.stop)
        for name in ("TOKEN", "ADMIN_ID", "cities_ch_ids"):
            original = getattr(main, name)
            self.addCleanup(setattr, main, name, original)

    def test_missing_configuration_lists_variable_names(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "TOKEN, ADMIN_ID, MINSK_CH_ID"):
                main.configure()

    def test_invalid_id_does_not_disclose_value(self):
        self.environment["ADMIN_ID"] = "private-invalid-value"
        with patch.dict(os.environ, self.environment, clear=True):
            with self.assertRaises(ValueError) as caught:
                main.configure()
        self.assertIn("ADMIN_ID", str(caught.exception))
        self.assertNotIn("private-invalid-value", str(caught.exception))

    def test_valid_configuration_maps_all_cities(self):
        with patch.dict(os.environ, self.environment, clear=True):
            main.configure()
        self.assertEqual(main.TOKEN, "test-token")
        self.assertEqual(main.ADMIN_ID, 123)
        self.assertEqual(main.cities_ch_ids, dict(zip(main.CITIES, range(-1000, -1006, -1))))


if __name__ == "__main__":
    unittest.main()
