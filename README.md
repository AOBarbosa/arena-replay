# ArenaReplay

Instant replay for beach courts: a camera records continuously and, when the
trigger fires, the system builds a clip with the previous ~30 s.
Architecture and rules in [CLAUDE.md](CLAUDE.md).

> Work in progress. Done: **stage 1 — config, database and fake camera; stage 2 — capture;
> stage 3 — trigger and clipper; stage 4 — API.**
> The full README (phone setup, running the services) comes in stage 5.

## Requirements

- Ubuntu 24.04 (production) or macOS (development only)
- Python 3.12, ffmpeg, Docker with the `docker compose` plugin

```bash
# Ubuntu
sudo apt install python3.12-venv ffmpeg
# macOS
brew install python@3.12 ffmpeg
```

## Installation

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev]"

cp .env.example .env              # change the password (in DATABASE_URL and TEST_DATABASE_URL too)
cp config.example.yaml config.yaml

docker compose up -d --wait db    # PostgreSQL 16 on 127.0.0.1:${POSTGRES_PORT}
.venv/bin/alembic upgrade head    # creates the tables
```

If port 5432 is already taken (another Postgres on the machine), change
`POSTGRES_PORT` and the port in both URLs in `.env`.

The test database (`<POSTGRES_DB>_test`) is created automatically, but **only on
the volume's first start**. If the volume already existed, create it by hand:

```bash
docker compose exec db sh -c 'createdb -U "$POSTGRES_USER" "${POSTGRES_DB}_test"'
```

## Fake camera (no phone needed)

```bash
scripts/fake_camera.sh court1      # publishes rtsp://127.0.0.1:8554/court1
ffplay -rtsp_transport tcp rtsp://127.0.0.1:8554/court1
```

The script starts the `mediamtx` container (RTSP server, `fakecam` profile) and
publishes a test pattern with a beeping audio track, in H.264 + μ-law like IP Webcam.
The second argument changes the keyframe interval (e.g. `scripts/fake_camera.sh court1 5`
to mimic a phone with a long GOP).

## Capture

```bash
.venv/bin/python -m replay.capture     # uses config.yaml (or REPLAY_CONFIG=/path.yaml)
```

- One ffmpeg per court records `.ts` segments into `data/segments/<court_id>/`,
  with the **UTC** start time in the name (`court1_20260928_153012.ts`).
- Video is not re-encoded (`-c:v copy`). Audio becomes AAC 48 kHz (IP Webcam's
  μ-law does not fit in MPEG-TS); `capture.audio: false` drops audio.
- If ffmpeg dies or goes `stall_timeout_s` without a new segment, it is
  restarted with exponential backoff (`reconnect_min_s` to `reconnect_max_s`).
- Every 10 s, cleanup deletes segments older than `buffer_minutes`, except those
  protected by *leases* of clips being built
  (`data/segments/<court_id>/.leases/`, ignored after 10 min).
- Segments are cut at keyframes: if the camera emits a keyframe every 4 s,
  segments will be ~4 s long. On the final camera, set a GOP of 1–2 s.

To test reconnection: with capture running, stop `fake_camera.sh` (Ctrl+C),
watch the reconnect warnings in the log and start it again.

## Trigger and clipper

```bash
.venv/bin/python -m replay.clipper           # global keyboard trigger (keys from config.yaml)
.venv/bin/python -m replay.clipper --stdin   # dev: Enter (or a court id) in the terminal
```

- The keyboard trigger uses pynput: on Ubuntu it needs an **Xorg** session (not
  Wayland; pick "Ubuntu on Xorg" on the login screen). On macOS, grant the terminal
  *Input Monitoring* in System Settings → Privacy & Security. Over SSH, use `--stdin`.
- Repeated triggers for the same court within `trigger.debounce_s` are ignored.
- Each trigger enqueues a job (the trigger never waits). The worker writes a lease,
  waits for the post-roll and for the segment covering the end to close, then
  concatenates the segments, writes an MP4 with `+faststart` plus a JPG thumbnail,
  and registers the clip (`processing` → `ready` or `failed`).
- Clips are stored under `data/clips/<court_id>/<YYYY/MM/DD UTC>/<clip_id>.mp4`.
- `capture.latency_offset_s` compensates the stream delay: the clip window is
  shifted by it so the play lands at the end of the clip. Calibrate it by filming a
  clock and comparing with the trigger time.
- Gaps in the buffer (camera offline) do not break the clip: it is built with what
  exists and a warning is logged. With no segments at all, the clip is `failed`.
- On start, clips left in `processing` by a crash are marked `failed`.

### `clip.encoder` trade-off

| encoder | cost | start of the clip |
|---|---|---|
| `copy` (default) | almost free | snaps to the keyframe at or before the requested time (up to one GOP earlier) |
| `libx264` | heavy CPU on the i3 | exact |
| `h264_vaapi` | Intel Quick Sync (Ubuntu) | exact |
| `h264_videotoolbox` | Apple hardware (dev only) | exact |

## Clip preview (development only)

```bash
.venv/bin/python -m replay.devtools.preview --watch --open
```

Writes `data/clips/preview.html` with the latest clips (every status, including
failed ones with their error) and opens it in the browser straight from disk.
With `--watch`, the page is regenerated when clips change and reloads itself
(never while a video is playing). Unlike the API page below, it also shows
`processing` and `failed` clips, which helps debugging.

## API

```bash
.venv/bin/python -m replay.api     # http://127.0.0.1:8000 (api.host / api.port in config.yaml)
```

- Test page: <http://127.0.0.1:8000/dev> (filter by court and day, play, download;
  new clips appear on their own). Disposable: the real UI will be the Next.js app.
- Interactive docs: <http://127.0.0.1:8000/api/v1/docs>
- To reach it from the phone or another PC on the network, set `api.host: 0.0.0.0`.

| Endpoint | Description |
|---|---|
| `GET /api/v1/health` | `{"status": "ok", "database": true}` |
| `GET /api/v1/courts` | Courts |
| `GET /api/v1/clips?court_id=&date=&limit=&cursor=` | Ready clips, newest first. `date` is a local day (YYYY-MM-DD, `timezone` in config); `limit` 1–100 (default 20); pass `next_cursor` as `cursor` for the next page |
| `GET /api/v1/clips/{id}` | One clip in any status (poll until `ready`) |
| `GET /api/v1/clips/{id}/video` | MP4 with HTTP Range (seeking in the player) |
| `GET /api/v1/clips/{id}/thumbnail` | JPEG |
| `GET /api/v1/clips/{id}/download` | MP4 as attachment (`court1_2026-09-28_12-00-00.mp4`, local time) |

All times in JSON are UTC (ISO 8601); convert to local time when displaying.
CORS origins come from `api.cors_origins`.

**Frontend contract:** [`docs/openapi.json`](docs/openapi.json) is committed and a test
fails if the API changes without it. After an intentional change, regenerate it:

```bash
.venv/bin/python -m replay.api --export-openapi docs/openapi.json
```

## Tests and lint

```bash
.venv/bin/pytest            # database tests are skipped if Postgres is not running
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```
