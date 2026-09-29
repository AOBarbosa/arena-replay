"""API service: `python -m replay.api [--export-openapi FILE]`."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import uvicorn

from replay.api.app import create_app
from replay.config import ConfigError, load_settings
from replay.db.repository import Repository
from replay.logs import setup_logging
from replay.storage.local import LocalClipStorage

log = logging.getLogger("replay.api")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m replay.api")
    parser.add_argument(
        "--export-openapi",
        type=Path,
        metavar="FILE",
        help="write the OpenAPI schema to FILE and exit (for the frontend)",
    )
    args = parser.parse_args(argv)

    setup_logging("api")
    try:
        settings = load_settings()
    except ConfigError as exc:
        log.error("%s", exc)
        return 2
    config = settings.app
    repo = Repository.from_url(settings.env.database_url)
    app = create_app(config, repo, LocalClipStorage(config.paths.clips_dir))

    if args.export_openapi:
        args.export_openapi.write_text(json.dumps(app.openapi(), indent=2) + "\n")
        log.info("OpenAPI schema written to %s", args.export_openapi)
        return 0

    log.info("API on http://%s:%d (test page: /dev)", config.api.host, config.api.port)
    try:
        uvicorn.run(app, host=config.api.host, port=config.api.port, log_config=None)
    finally:
        repo.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
