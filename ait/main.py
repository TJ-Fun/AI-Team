"""Background worker startup and server entry point."""

from __future__ import annotations

from ait.common import *


def start_background_workers() -> None:
    reconcile_provider_workers()
    threading.Thread(target=watchdog_worker_loop, daemon=True).start()
    threading.Thread(target=ci_poll_worker_loop, daemon=True).start()
    threading.Thread(target=model_refresh_worker_loop, daemon=True).start()


def main() -> None:
    ensure_data()
    get_cpu_percent()
    start_background_workers()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Personal AI Team listening on http://{HOST}:{PORT}", flush=True)
    server.serve_forever()
