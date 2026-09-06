#!/usr/bin/env python
"""Start the simulator:  python run_app.py

Runs on port 5001 -- the JDE ERP simulator already holds 5000 on this machine.
Set CCT_APP_PORT to move it.

The autoreloader is off, because the live-mode ticker and the background job
workers are threads in this process and a reloader would leave a second copy of
them running.
"""
import socket
import os
import sys


def main() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django
    django.setup()

    from django.conf import settings
    from django.core.management import call_command

    call_command("migrate", run_syncdb=True, verbosity=0)
    from core.models import ensure_defaults
    ensure_defaults()

    port = int(os.environ.get("CCT_APP_PORT", settings.APP_PORT))
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            print(f"port {port} is already in use -- set CCT_APP_PORT to a free port")
            return 1

    print(f"CCT Oil & Cattle source simulator  ->  http://localhost:{port}/")
    call_command("runserver", f"0.0.0.0:{port}", use_reloader=False)


if __name__ == "__main__":
    sys.exit(main())
