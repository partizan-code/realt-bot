import logging
import sys


def setup_logger():
    """Настройка основного логгера"""
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)  # общий уровень логирования

    # Форматтер
    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    # Handler для консоли — все уровни
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.INFO)

    # Handler для файла — только ERROR
    file_handler = logging.FileHandler("error.log")
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.ERROR)

    # Добавляем handlers к логгеру
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    return logger
