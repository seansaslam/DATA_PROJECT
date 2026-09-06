#!/usr/bin/env python
"""Build and load the three source databases from the command line.

    python seed_cli.py            # create schemas, load everything
    python seed_cli.py --drop     # drop first, then load
"""
import os
import sys
import time

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django  # noqa: E402

django.setup()

from core import registry                       # noqa: E402
from core.models import ensure_defaults         # noqa: E402
from etl import runner, watermark               # noqa: E402
from systems import seeder                      # noqa: E402


def main() -> int:
    ensure_defaults()
    started = time.time()
    if "--drop" in sys.argv:
        for key in reversed(list(registry.SOURCE_SYSTEMS)):
            seeder.drop_schema(key, print)
    seeder.seed_all(log=print, progress=print)
    for key in registry.SOURCE_SYSTEMS:
        watermark.clear(key)
    print("Watermarks cleared. Every system will baseline on its next read.")
    # Recreated tables come back untracked; re-arm whatever is still recorded
    # as CT or CDC so the app and the databases agree.
    runner.rearm_all(print)
    print(f"done in {time.time() - started:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
