"""Entrypoint: ``python -m jchick`` or ``jchick``."""
from __future__ import annotations

import asyncio
import logging
import os
import sys

from .app import run
from .config import Config


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("JCHICK_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        cfg = Config.from_env()
    except ValueError as e:
        print(f"jchick: config error: {e}", file=sys.stderr)
        return 2
    try:
        asyncio.run(run(cfg))
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
