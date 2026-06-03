"""
logger.py — shared logger with file + console handlers.
"""

import logging
from datetime import datetime
from pathlib import Path


def get_logger(log_dir: Path) -> logging.Logger:
    log_dir.mkdir(exist_ok=True)
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    fmt  = "%(asctime)s %(levelname)-8s %(message)s"
    dfmt = "%H:%M:%S"

    fh = logging.FileHandler(log_dir / f"run_{ts}.log", encoding="utf-8")
    fh.setFormatter(logging.Formatter(fmt, dfmt))
    fh.setLevel(logging.DEBUG)

    try:
        import colorlog
        ch = colorlog.StreamHandler()
        ch.setFormatter(colorlog.ColoredFormatter("%(log_color)s" + fmt, dfmt))
    except ImportError:
        ch = logging.StreamHandler()
        ch.setFormatter(logging.Formatter(fmt, dfmt))
    ch.setLevel(logging.INFO)

    logger = logging.getLogger("news_scraper")
    logger.setLevel(logging.DEBUG)
    if not logger.handlers:
        logger.addHandler(fh)
        logger.addHandler(ch)
    return logger
