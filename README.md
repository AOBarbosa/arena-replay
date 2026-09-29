# ArenaReplay

Instant replay for beach courts: a camera records continuously and, when the
trigger fires, the system builds a clip with the previous ~30 s.
Architecture and rules in [CLAUDE.md](CLAUDE.md).

> Work in progress. Done: **stage 1 — config, database and fake camera; stage 2 — capture.**
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

## Tests and lint

```bash
.venv/bin/pytest            # database tests are skipped if Postgres is not running
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```
