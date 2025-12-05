#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
uf_logging.py
===============================================================================
Central logging helper for the Unusual Finds Discogs → Shopify pipeline.

- Sets up a single file-based logger under:
      ~/.discogs_to_shopify/logs/run_YYYYMMDD_HHMMSS.txt

- Intended to be called ONCE at program startup (from the GUI script).

Other modules (label_ocr, discogs_client, ebay_search, pricing, etc.) can
just use the standard Python logging API:

    import logging
    logger = logging.getLogger(__name__)
    logger.info("something...")

No module should call logging.basicConfig; this module owns that.
===============================================================================
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Optional


def setup_logging(level: int = logging.DEBUG, log_root: Optional[str] = None) -> str:
    """
    Configure global logging, *replacing* any existing handlers.

    Returns:
        The path to the log file being used.
    """
    root_logger = logging.getLogger()

    # Always take over: remove any existing handlers that might have been
    # created by early logging calls (e.g., in imported modules).
    for h in list(root_logger.handlers):
        root_logger.removeHandler(h)

    if not log_root:
        log_root = os.path.expanduser("~/.discogs_to_shopify/logs")

    os.makedirs(log_root, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = os.path.join(log_root, f"run_{ts}.txt")

    # File handler: capture everything
    file_handler = logging.FileHandler(log_file, mode="w", encoding="utf-8")
    file_handler.setLevel(level)
    file_fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    file_handler.setFormatter(file_fmt)

    # Console handler: show INFO and above (for now)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_fmt = logging.Formatter("%(levelname)s: %(name)s: %(message)s")
    console_handler.setFormatter(console_fmt)

    root_logger.setLevel(level)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    root_logger.info("Logging initialized. Log file: %s", log_file)
    return log_file


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """
    Convenience wrapper to get a logger for a module.
    """
    return logging.getLogger(name if name is not None else __name__)


if __name__ == "__main__":
    # Simple manual test
    lf = setup_logging()
    log = get_logger(__name__)
    log.info("Test log entry written to %s", lf)
    print("Log file:", lf)
