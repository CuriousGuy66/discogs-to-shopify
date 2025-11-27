#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
discogs_to_shopify.py

Thin CLI wrapper around the core logic implemented in
discogs_to_shopify_gui_v1_2_3.py.

This lets you run the converter from the command line without launching
the GUI, while keeping a single source of truth for all processing logic.
"""

import logging
import sys
from pathlib import Path
from typing import Optional, List

from discogs_to_shopify_gui_v1_2_3 import (
    parse_args,
    process_file,
    print_run_banner,
)


def main(argv: Optional[List[str]] = None) -> None:
    if argv is None:
        argv = sys.argv[1:]

    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    input_path = Path(args.input)
    if not input_path.exists():
        logging.error("Input file does not exist: %s", input_path)
        sys.exit(1)

    matched_output_path = Path(args.output)
    unmatched_output_path = matched_output_path.with_name(
        matched_output_path.stem + "_unmatched" + matched_output_path.suffix
    )

    try:
        print_run_banner()
        process_file(
            input_path=input_path,
            matched_output_path=matched_output_path,
            unmatched_output_path=unmatched_output_path,
            token=args.token,
            dry_run_limit=args.dry_limit,
            progress_callback=None,
        )
    except Exception as e:
        logging.exception("Fatal error: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
