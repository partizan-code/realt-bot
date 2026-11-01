from contextlib import contextmanager

from loguru import logger


@contextmanager
def capture_logs(level="INFO"):
    """Аналог assertLogs для Loguru с обязательным удалением тестового sink."""
    output = []
    handler = logger.add(output.append, level=level, format="{level}:{name}:{message}")
    try:
        yield output
        if not output:
            raise AssertionError(f"Нет записей Loguru уровня {level} или выше")
    finally:
        logger.remove(handler)
