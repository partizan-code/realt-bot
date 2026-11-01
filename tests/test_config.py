import os
import runpy
import unittest
from unittest.mock import patch

import config


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.environment = {
            "TOKEN": "test-token",
            "ADMIN_ID": "123",
            **{f"{city.upper()}_CH_ID": str(-1000 - i) for i, city in enumerate(config.CITIES)},
        }
        self.dotenv = patch("config.load_dotenv")
        self.dotenv.start()
        self.addCleanup(self.dotenv.stop)
        for name in ("TOKEN", "ADMIN_ID", "CITIES_CH_IDS"):
            original = getattr(config, name)
            self.addCleanup(setattr, config, name, original)

    def test_import_does_not_load_environment(self):
        with patch("dotenv.load_dotenv") as load:
            namespace = runpy.run_path(str(config.PROJECT_ROOT / "config.py"))
        load.assert_not_called()
        self.assertIsNone(namespace["TOKEN"])
        self.assertEqual(namespace["CITIES_CH_IDS"], {})

    def test_missing_configuration_lists_variable_names(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "TOKEN, ADMIN_ID, MINSK_CH_ID"):
                config.configure()

    def test_invalid_id_does_not_disclose_value(self):
        self.environment["ADMIN_ID"] = "private-invalid-value"
        with patch.dict(os.environ, self.environment, clear=True):
            with self.assertRaises(ValueError) as caught:
                config.configure()
        self.assertIn("ADMIN_ID", str(caught.exception))
        self.assertNotIn("private-invalid-value", str(caught.exception))

    def test_valid_configuration_maps_all_cities(self):
        with patch.dict(os.environ, self.environment, clear=True):
            config.configure()
        self.assertEqual(config.TOKEN, "test-token")
        self.assertEqual(config.ADMIN_ID, 123)
        self.assertEqual(config.CITIES_CH_IDS, dict(zip(config.CITIES, range(-1000, -1006, -1))))


if __name__ == "__main__":
    unittest.main()
