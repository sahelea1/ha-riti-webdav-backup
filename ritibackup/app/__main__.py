"""RitiBackup entry point: build config, start scheduler, serve the ingress UI."""

from __future__ import annotations

import asyncio
import logging
import os

from aiohttp import web

from .config import Config, State
from .jobs import JobManager
from .scheduler import Scheduler
from .server import build_app

INGRESS_HOST = "0.0.0.0"
INGRESS_PORT = int(os.environ.get("RITI_PORT", "8099"))


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


async def _amain() -> None:
    cfg = Config.load()
    setup_logging(cfg.log_level)
    log = logging.getLogger("ritibackup")
    enabled = [b["label"] for b in cfg.backends_summary() if b["enabled"]]
    log.info(
        "RitiBackup starting. Enabled backends: %s",
        ", ".join(enabled) if enabled else "(none configured yet)",
    )

    missing = cfg.configured()
    if missing:
        log.warning(
            "Not fully configured yet (missing: %s). The UI will guide you.",
            ", ".join(missing),
        )

    state = State.load()
    jobs = JobManager(cfg, state)
    sched = Scheduler(cfg, state, jobs)
    app = build_app(cfg, state, jobs, sched)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, INGRESS_HOST, INGRESS_PORT)
    await site.start()
    log.info("Web UI listening on %s:%s (via Ingress)", INGRESS_HOST, INGRESS_PORT)

    sched.start()

    # Run forever
    try:
        await asyncio.Event().wait()
    finally:
        await sched.stop()
        await runner.cleanup()


def main() -> None:
    try:
        asyncio.run(_amain())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
