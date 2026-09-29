"""Development-only clip preview: writes a static HTML page listing the latest clips.

    python -m replay.devtools.preview            # generate once and print the path
    python -m replay.devtools.preview --watch    # regenerate as clips change; page auto-reloads

The page opens straight from disk (file://), no server needed. It shows every
status, including failed clips with their error. Disposable: the real consumer
is the API (stage 4).
"""

from __future__ import annotations

import argparse
import html
import logging
import sys
import time
import webbrowser
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from replay.config import ConfigError, load_settings
from replay.db.repository import Repository
from replay.logs import setup_logging
from replay.models import Clip, ClipStatus
from replay.storage.base import ClipStorage
from replay.storage.local import LocalClipStorage

log = logging.getLogger("replay.devtools.preview")

PAGE_NAME = "preview.html"

# Reloads every few seconds, but never while a video is playing
AUTO_RELOAD_JS = """
<script>
setInterval(() => {
  const playing = [...document.querySelectorAll("video")].some((v) => !v.paused);
  if (!playing) location.reload();
}, 4000);
</script>
"""

STYLE = """
:root { color-scheme: light dark; --muted: #888; --card: rgba(127,127,127,.08); }
body { font-family: system-ui, sans-serif; margin: 0 auto; max-width: 1100px; padding: 16px; }
h1 { font-size: 1.3rem; margin: 0 0 4px; }
p.meta { color: var(--muted); margin: 0 0 16px; font-size: .9rem; }
.grid { display: grid; gap: 16px; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); }
.card { background: var(--card); border-radius: 10px; padding: 10px; }
.card video { width: 100%; border-radius: 6px; background: #000; aspect-ratio: 16 / 9; }
.row { display: flex; justify-content: space-between; align-items: center; gap: 8px;
       font-size: .9rem; margin-top: 6px; }
.badge { font-size: .75rem; padding: 2px 8px; border-radius: 99px; color: #fff; }
.ready { background: #2e7d32; }
.processing { background: #ef6c00; }
.failed { background: #c62828; }
.error { color: #c62828; font-size: .85rem; margin-top: 6px; word-break: break-word; }
.small { color: var(--muted); font-size: .8rem; }
a { color: inherit; }
"""


def render_page(
    clips: Sequence[Clip],
    storage: ClipStorage,
    tz: ZoneInfo,
    court_names: dict[str, str],
    generated_at: datetime,
    auto_reload: bool,
) -> str:
    cards = "\n".join(_render_card(c, storage, tz, court_names) for c in clips)
    if not clips:
        cards = "<p>No clips yet. Fire a trigger and regenerate this page.</p>"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Clip preview</title>
<style>{STYLE}</style>
</head>
<body>
<h1>ArenaReplay — clip preview (dev)</h1>
<p class="meta">{len(clips)} latest clip(s) · generated
{generated_at.astimezone(tz):%Y-%m-%d %H:%M:%S} ({tz.key})
{"· auto-reload on" if auto_reload else "· run with --watch to auto-reload"}</p>
<div class="grid">
{cards}
</div>
{AUTO_RELOAD_JS if auto_reload else ""}
</body>
</html>
"""


def _render_card(
    clip: Clip, storage: ClipStorage, tz: ZoneInfo, court_names: dict[str, str]
) -> str:
    esc = html.escape
    court = esc(court_names.get(clip.court_id, clip.court_id))
    when = f"{clip.triggered_at.astimezone(tz):%d/%m %H:%M:%S}"
    status = clip.status.value
    media = ""
    if clip.status is ClipStatus.READY and clip.file_key:
        src = esc(storage.url_for(clip.file_key))
        poster = f' poster="{esc(storage.url_for(clip.thumb_key))}"' if clip.thumb_key else ""
        media = (
            f'<video controls preload="none" src="{src}"{poster}></video>'
            f'<div class="row small"><span>{clip.duration_s or 0:.1f} s</span>'
            f'<a href="{src}" download>download</a></div>'
        )
    error = f'<div class="error">{esc(clip.error)}</div>' if clip.error else ""
    return f"""<div class="card">
  {media}
  <div class="row"><strong>{court}</strong><span class="badge {status}">{status}</span></div>
  <div class="row small"><span>{when}</span><span title="{clip.id}">{str(clip.id)[:8]}</span></div>
  {error}
</div>"""


def generate(
    repo: Repository,
    storage: LocalClipStorage,
    tz: ZoneInfo,
    court_names: dict[str, str],
    limit: int,
    auto_reload: bool,
) -> tuple[Path, list[Clip]]:
    clips = repo.list_recent_clips(limit=limit)
    page = render_page(clips, storage, tz, court_names, datetime.now(tz), auto_reload)
    path = storage.root / PAGE_NAME
    tmp = path.with_suffix(".tmp")
    tmp.write_text(page, encoding="utf-8")
    tmp.replace(path)
    return path, clips


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m replay.devtools.preview")
    parser.add_argument("--watch", action="store_true", help="keep regenerating the page")
    parser.add_argument("--open", action="store_true", help="open the page in the browser")
    parser.add_argument("--limit", type=int, default=30, help="how many clips to show")
    args = parser.parse_args(argv)

    setup_logging("preview")
    try:
        settings = load_settings()
    except ConfigError as exc:
        log.error("%s", exc)
        return 2
    config = settings.app
    repo = Repository.from_url(settings.env.database_url)
    storage = LocalClipStorage(config.paths.clips_dir)
    names = {c.id: c.name for c in config.courts}

    path, clips = generate(repo, storage, config.tz, names, args.limit, args.watch)
    log.info("preview written: %s", path.as_uri())
    if args.open:
        webbrowser.open(path.as_uri())
    if not args.watch:
        return 0

    signature = [(c.id, c.status) for c in clips]
    try:
        while True:
            time.sleep(2)
            clips = repo.list_recent_clips(limit=args.limit)
            new_signature = [(c.id, c.status) for c in clips]
            if new_signature != signature:
                signature = new_signature
                generate(repo, storage, config.tz, names, args.limit, auto_reload=True)
                log.info("preview updated (%d clip(s))", len(clips))
    except KeyboardInterrupt:
        return 0
    finally:
        repo.dispose()


if __name__ == "__main__":
    sys.exit(main())
