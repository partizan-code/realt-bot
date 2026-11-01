import io
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from loguru import logger

from logger_config import setup_logger


class LoggingTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(TemporaryDirectory())
        self.path = Path(directory) / "error.log"
        self.console = io.StringIO()
        self.enterContext(patch("config.LOG_FILE", self.path))
        self.enterContext(patch("logger_config.sys.stdout", self.console))
        self.addCleanup(logger.remove)
        setup_logger()

    def test_console_and_error_file_keep_their_levels(self):
        logger.info("Найдено {} объявлений {}", 3, "minsk")
        logger.warning("Фото недоступно")
        logger.error("Не загрузилась страница {}", "gomel")
        console = self.console.getvalue()
        errors = self.path.read_text(encoding="utf-8")
        self.assertIn("Найдено 3 объявлений minsk", console)
        self.assertIn("Фото недоступно", console)
        self.assertIn("Не загрузилась страница gomel", errors)
        self.assertNotIn("Найдено", errors)
        self.assertNotIn("Фото недоступно", errors)
        self.assertNotIn("%s", console)

    def test_repeated_setup_does_not_duplicate_messages(self):
        setup_logger()
        logger.error("unique-message")
        self.assertEqual(self.console.getvalue().count("unique-message"), 1)
        self.assertEqual(self.path.read_text(encoding="utf-8").count("unique-message"), 1)

    def test_global_minimum_level_applies_to_all_sinks(self):
        with patch("config.LOG_LEVEL", "ERROR"):
            setup_logger()
            logger.warning("filtered-warning")
            logger.error("visible-error")
        self.assertNotIn("filtered-warning", self.console.getvalue())
        self.assertIn("visible-error", self.console.getvalue())

    def test_exception_diagnostics_do_not_dump_local_values(self):
        private_value = "private-test-value"
        try:
            raise ValueError("test failure")
        except ValueError:
            logger.exception("Ошибка обработки")
        output = self.path.read_text(encoding="utf-8")
        self.assertIn("ValueError: test failure", output)
        self.assertNotIn(private_value, output)
