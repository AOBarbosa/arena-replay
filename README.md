# ArenaReplay

Instant replay for beach courts (footvolley, beach volleyball, beach tennis).
A camera records the court continuously; when a player fires the trigger, the
system builds a clip with the previous ~30 seconds plus a few seconds after the
play. Clips are stored on disk, registered in PostgreSQL and exposed by a JSON API
(later consumed by a Next.js app where students watch and download their plays).

Architecture, responsibilities and rules: [CLAUDE.md](CLAUDE.md).

```
 camera (RTSP) ──► capture ──► data/segments/<court>/*.ts   (rolling buffer, N minutes)
                                        │
 trigger (key) ──► clipper ─────────────┘──► data/clips/<court>/…/<id>.mp4 + .jpg
                      │
                      └──► PostgreSQL (clips) ◄── API (/api/v1, /dev) ◄── browser / Next.js
```

Three Python processes on the host (capture, clipper, API) plus PostgreSQL in Docker.
They talk only through the filesystem and the database.

**Contents:** [Requirements](#requirements) · [Installation (Ubuntu)](#installation-ubuntu) ·
[Configuration](#configuration) · [Phone camera (IP Webcam)](#phone-camera-ip-webcam) ·
[Running](#running) · [Using it](#using-it) · [Monitoring](#monitoring) ·
[API](#api) · [Testing](#testing) · [Troubleshooting](#troubleshooting) ·
[Development on macOS](#development-on-macos) · [Project layout](#project-layout)

## Requirements

- **Ubuntu 24.04 LTS** in an **Xorg session** (the global keyboard trigger does not
  work on Wayland). macOS works for development only.
- Python 3.12, ffmpeg, Docker with the `docker compose` plugin.
- Intel CPU with Quick Sync (optional, for hardware re-encoding via VAAPI).

## Installation (Ubuntu)

### 1. Xorg session

On the login screen, click your user, then the **gear icon** (bottom right) and
choose **"Ubuntu on Xorg"**. Check after logging in:

```bash
echo $XDG_SESSION_TYPE     # must print: x11
```

### 2. System packages

```bash
sudo apt update
sudo apt install -y git ffmpeg python3.12-venv python3.12-dev build-essential \
                    docker.io docker-compose-v2
sudo usermod -aG docker $USER      # use docker without sudo
```

**Log out and back in** so the `docker` group applies. (`build-essential` and
`python3.12-dev` are needed because pynput compiles a dependency on Linux.)

Optional, for hardware re-encoding (`clip.encoder: h264_vaapi`):

```bash
sudo apt install -y vainfo intel-media-va-driver-non-free
vainfo        # look for "VAProfileH264... : VAEntrypointEncSlice"
```

### 3. Project

```bash
git clone https://github.com/AOBarbosa/arena-replay.git
cd arena-replay

python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev]"

cp .env.example .env
sed -i 's/change-this-password/YOUR_PASSWORD/g' .env     # replaces it in all 3 places
cp config.example.yaml config.yaml
```

### 4. Database

```bash
docker compose up -d --wait db     # PostgreSQL 16 on 127.0.0.1:${POSTGRES_PORT}
.venv/bin/alembic upgrade head     # creates the tables
.venv/bin/pytest -q                # everything should pass
```

The container restarts on its own when the PC boots (`restart: unless-stopped`).
If port 5432 is taken (another PostgreSQL on the machine: `sudo ss -ltnp | grep 5432`),
change `POSTGRES_PORT` and the port in both URLs in `.env`.

The test database (`<POSTGRES_DB>_test`) is created automatically **only on the
volume's first start**. If the volume already existed:

```bash
docker compose exec db sh -c 'createdb -U "$POSTGRES_USER" "${POSTGRES_DB}_test"'
```

## Configuration

Two files, validated at startup (a clear error is printed if anything is wrong):

- **`.env`**: secrets (`POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`,
  `POSTGRES_PORT`, `DATABASE_URL`, `TEST_DATABASE_URL`). Never committed.
- **`config.yaml`**: everything else. Relative paths are resolved from the file's
  folder. Another file can be used with `REPLAY_CONFIG=/path/config.yaml`.

| Key | Default | Meaning |
|---|---|---|
| `timezone` | `America/Fortaleza` | Used for display, download names and the API's `?date=`. The database stores UTC |
| `courts[].id` | `court1` | `[a-z0-9_-]`, used in file names and in the API |
| `courts[].name` | `Court 1` | Display name |
| `courts[].stream_url` | fake camera | RTSP (or HTTP) URL of the camera |
| `courts[].trigger_key` | `f9` | pynput key name (`f9`, `space`, `enter`…) or one character. Prefer a key nobody types, like `f9` |
| `capture.segment_s` | `2` | Target segment length (real cuts happen at keyframes) |
| `capture.buffer_minutes` | `10` | History kept per court |
| `capture.latency_offset_s` | `1.0` | Stream delay compensation (see [calibration](#calibrating-the-latency)) |
| `capture.audio` | `true` | `false` drops audio |
| `capture.stall_timeout_s` | `15` | No new segment for this long = restart ffmpeg |
| `capture.reconnect_min_s` / `reconnect_max_s` | `1` / `30` | Reconnection backoff |
| `clip.duration_s` / `post_roll_s` | `30` / `3` | Seconds before / after the trigger |
| `clip.encoder` | `copy` | `copy`, `libx264`, `h264_vaapi`, `h264_videotoolbox` (see below) |
| `trigger.debounce_s` | `5` | Repeated triggers for the same court within this interval are ignored |
| `paths.*` | `data/…` | Segments, clips, logs and heartbeat folders |
| `api.host` / `api.port` | `127.0.0.1` / `8000` | Use `0.0.0.0` to reach the API from other devices on the network |
| `api.cors_origins` | `http://localhost:3000` | Origins allowed to call the API (future Next.js app) |

### `clip.encoder` trade-off

| encoder | cost | start of the clip |
|---|---|---|
| `copy` (default) | almost free | snaps to the keyframe at or before the requested time (up to one GOP earlier) |
| `libx264` | heavy on the i3 CPU | exact |
| `h264_vaapi` | Intel Quick Sync (Ubuntu) | exact |
| `h264_videotoolbox` | Apple hardware (development only) | exact |

With `copy`, a camera that sends a keyframe every 1–2 s gives clips within 1–2 s of
the requested window, which is usually enough.

### Multiple courts

Add more entries to `courts`, each with its own `id`, `stream_url` and
`trigger_key`. Every service handles all courts; no other change is needed.

## Phone camera (IP Webcam)

Until the outdoor PoE camera arrives, an Android phone with the **IP Webcam** app
works as the camera. Menu names can vary slightly between app versions.

1. Install **IP Webcam** from the Play Store. Put the phone and the PC on the **same
   network** (5 GHz Wi-Fi is better than 2.4 GHz). Keep the phone **charging**.
2. In the app's **Video preferences**:
   - **Video resolution**: 1280×720 (1920×1080 if the Wi-Fi is strong).
   - **FPS limit**: 30.
   - If the app offers a keyframe / I-frame interval, set it to 1–2 s: shorter
     segments give more precise clips with `encoder: copy`.
3. Keep the phone awake: in **Power management**, enable the option that keeps the
   device awake while streaming (wake lock), and in Android's settings disable
   **battery optimization** for IP Webcam. A locked or sleeping phone drops the stream.
4. Optionally set a login/password under **Local broadcasting**.
5. Tap **Start server**. The app shows an address like `http://192.168.0.50:8080`.
6. Give the phone a **fixed IP** (DHCP reservation in the router), or the URL will
   change after a reboot.
7. Test from the PC, using the **RTSP H.264** stream, not the MJPEG one:

   ```bash
   ffplay -rtsp_transport tcp rtsp://192.168.0.50:8080/h264_ulaw.sdp
   # with login: rtsp://user:password@192.168.0.50:8080/h264_ulaw.sdp
   ```

8. Put that URL in `config.yaml`:

   ```yaml
   courts:
     - id: court1
       name: Court 1
       stream_url: rtsp://192.168.0.50:8080/h264_ulaw.sdp
       trigger_key: f9
   ```

9. Restart the capture service. Check the real segment length (it follows the
   phone's keyframe interval):

   ```bash
   for f in $(ls data/segments/court1/*.ts | tail -5); do
     ffprobe -v error -show_entries format=duration -of csv=p=0 "$f"; done
   ```

Switching to the final IP camera later only means changing `stream_url`
(configure it for H.264 with a 1–2 s GOP).

### Calibrating the latency

The stream arrives slightly late (typically 0.5–2 s), so the moment you press the
trigger is already a bit "in the past" of the video. `capture.latency_offset_s`
shifts the clip window to compensate:

1. Point the camera at a clock with seconds (e.g. a phone stopwatch) or at the PC screen.
2. Press the trigger exactly when the clock shows a round second (e.g. `:00`).
3. In the clip, the trigger moment is `post_roll_s` (3 s) before the end. If the
   clock there shows `:58.8`, the stream is 1.2 s late: set `latency_offset_s: 1.2`.

## Running

### Option A: everything in one terminal (`run_all.sh`)

```bash
scripts/run_all.sh                    # real cameras from config.yaml
scripts/run_all.sh --fake-camera      # + a test stream per court (no phone needed)
scripts/run_all.sh --stdin            # trigger with Enter in this terminal (SSH, no keyboard access)
```

It starts the database, applies migrations, starts capture, clipper and API, and
prints the test page URL. All logs appear in the terminal (and in `data/logs/`).
**Ctrl+C** stops everything cleanly. If one service dies, everything stops, so the
problem is noticed.

### Option B: systemd services (unattended, restart on failure)

For the court PC, install the three services as systemd **user** units. They start
with the desktop session and restart automatically 5 s after a failure:

```bash
scripts/install_services.sh             # install / update and start
scripts/install_services.sh --uninstall # stop and remove
```

```bash
systemctl --user status 'arena-replay-*'
systemctl --user restart arena-replay-capture      # after editing config.yaml
systemctl --user stop arena-replay-capture arena-replay-clipper arena-replay-api
journalctl --user -u 'arena-replay-*' -f            # live logs (also in data/logs/)
```

They are user units (not system units) because the keyboard trigger needs the
logged-in Xorg session; the clipper is tied to `graphical-session.target`.
For the PC to be fully unattended, enable **automatic login** (Settings → Users)
and keep Docker enabled at boot (`sudo systemctl enable docker`, the default).
After `git pull`, run `scripts/install_services.sh` again to apply changes.

Do not run `run_all.sh` while the services are running under systemd (the script
refuses to).

### Option C: one terminal per service

```bash
scripts/fake_camera.sh court1              # only when testing without a camera
.venv/bin/python -m replay.capture
.venv/bin/python -m replay.clipper         # add --stdin to trigger from the terminal
.venv/bin/python -m replay.api
```

## Using it

1. Open the test page: <http://127.0.0.1:8000/dev>.
2. Wait until the buffer holds at least `clip.duration_s` (30 s) of video after starting.
3. Press the trigger key (from any window). The clipper logs `clip … queued` and,
   ~7 s later, `clip … ready`.
4. The clip appears on the page within 5 s: click to play (seeking works), or download.

Clip files: `data/clips/<court_id>/<YYYY>/<MM>/<DD>/<clip_id>.mp4` (+ `.jpg`), with
the date in UTC.

How a clip is built: the trigger only enqueues a job (it never waits). The worker
protects the needed segments from cleanup with a lease, waits for the post-roll
and for the last segment to be closed, concatenates the segments without
re-encoding (or re-encodes, per `clip.encoder`), writes an MP4 with `+faststart`
and a thumbnail, and registers the clip as `ready` (or `failed`, with the reason).
If the camera was offline during part of the window, the clip is built with what
exists and a warning is logged.

### Clip preview with failures (development)

```bash
.venv/bin/python -m replay.devtools.preview --watch --open
```

Writes `data/clips/preview.html` with the latest clips in **every** status,
including failed ones with their error, and opens it from disk.

## Monitoring

### Live status

Capture and clipper write a heartbeat every 5 s to `data/status/<service>.json`.
`GET /api/v1/status` combines them with a database check, and the test page shows
it as a status bar (refreshed every 3 s; hover a chip for the last error):

- services: `running`; `stopped` (clean shutdown); `down` (no heartbeat for 15 s:
  crash, `kill -9`, frozen); `unknown` (never ran on this machine)
- per court: `recording`, `connecting` or `reconnecting` (camera offline), age of
  the newest segment, reconnect count, last error
- clipper: queue, clips ready/failed since start, last failure
- overall `level`: `error` (database or a service down), `warning` (a camera is
  not recording, or a clip failed in the last 10 minutes) or `ok`, with `problems`

```bash
curl -s localhost:8000/api/v1/status | python3 -m json.tool
```

### Log files

Each service writes to `data/logs/<service>.log` (`capture`, `clipper`, `api`),
rotated at midnight and kept for 14 days:

```bash
tail -F data/logs/*.log
tail -F data/logs/*.log | grep -E "WARNING|ERROR"      # only problems
```

`LOG_LEVEL=DEBUG` shows more detail. HTTP requests are logged only when they fail.

## API

Interactive docs: <http://127.0.0.1:8000/api/v1/docs>. All times in JSON are UTC
(ISO 8601).

| Endpoint | Description |
|---|---|
| `GET /api/v1/health` | `{"status": "ok", "database": true}` |
| `GET /api/v1/status` | Live system status (see [Monitoring](#monitoring)) |
| `GET /api/v1/courts` | Courts |
| `GET /api/v1/clips?court_id=&date=&limit=&cursor=` | Ready clips, newest first. `date`: local day `YYYY-MM-DD`; `limit` 1–100 (default 20); pass `next_cursor` as `cursor` for the next page |
| `GET /api/v1/clips/{id}` | One clip in any status (poll until `ready`) |
| `GET /api/v1/clips/{id}/video` | MP4 with HTTP Range (seeking) |
| `GET /api/v1/clips/{id}/thumbnail` | JPEG |
| `GET /api/v1/clips/{id}/download` | MP4 as attachment, named `court1_2026-09-28_12-00-00.mp4` (local time) |

**Frontend contract:** [`docs/openapi.json`](docs/openapi.json) is committed, and a
test fails if the API changes without it. After an intentional change:

```bash
.venv/bin/python -m replay.api --export-openapi docs/openapi.json
```

## Testing

```bash
.venv/bin/pytest -q                                   # database tests are skipped if Postgres is down
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

The suite covers configuration, repository (against the test database), segment
selection and gaps, ffmpeg commands and real clip building, the worker end to end,
triggers and debounce, storage, the API (Range, pagination, CORS, status) and heartbeats.

### Fake camera

```bash
scripts/fake_camera.sh court1          # rtsp://127.0.0.1:8554/court1
scripts/fake_camera.sh court1 5        # keyframe every 5 s, like some phones
ffplay -rtsp_transport tcp rtsp://127.0.0.1:8554/court1
```

It runs a `mediamtx` container (RTSP server, `fakecam` compose profile) and publishes
a test pattern with a beep, in H.264 + μ-law like IP Webcam. On Ubuntu the video
shows a clock, useful to check clip timing.

### Manual checks

| Scenario | How | Expected |
|---|---|---|
| Clip | trigger once | `ready` in ~7 s; the clip ends ~3 s after the trigger |
| Debounce | trigger twice quickly | the second one is logged as `trigger ignored (debounce)` |
| Camera drop | stop the camera for 10 s | court `reconnecting` on the status bar; recording resumes; a clip over the outage logs the missing seconds |
| Crash | `kill -9` the clipper | `clipper down` after ~15 s (under systemd it comes back by itself) |
| Buffer | `buffer_minutes: 1` | `data/segments/court1` stays around 30 files |

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `pynput is unavailable`, or the key does nothing | Wayland session or SSH: use "Ubuntu on Xorg", or `--stdin` |
| `permission denied … docker.sock` | Log out and in again after `usermod -aG docker` |
| `database unavailable` | `docker compose ps`; `docker compose up -d --wait db` |
| `address already in use` on 5432 | Another PostgreSQL: change `POSTGRES_PORT` and the URLs in `.env` |
| Capture loops on `404 Not Found` | Wrong stream path, or the fake camera is not running |
| Capture loops on `Connection refused` / timeouts | Phone app stopped, phone asleep, wrong IP or different network |
| Court keeps `reconnecting` every few minutes | Weak Wi-Fi or the phone sleeping: check power settings, prefer 5 GHz |
| Clip `failed: no segments recorded` | Trigger before the buffer existed, or the camera was offline for the whole window |
| Clips start a few seconds early | `encoder: copy` with a long keyframe interval: set a 1–2 s GOP or use `h264_vaapi` |
| The play is cut at the end of the clip | Increase `capture.latency_offset_s` (see calibration) |
| `h264_vaapi` fails | Check `vainfo`; the user must be in the `render` group (`sudo usermod -aG render $USER`) |
| The page shows `API unreachable` | API not running, or a different `api.port` |

## Development on macOS

Everything runs on macOS for development, except the production specifics:

- Install with `brew install python@3.12 ffmpeg` and Docker Desktop.
- The keyboard trigger needs **Input Monitoring** permission for the terminal
  (System Settings → Privacy & Security); or use `--stdin`.
- Hardware re-encoding uses `h264_videotoolbox`. Homebrew's ffmpeg has no
  `drawtext`, so the fake camera has no clock overlay.
- systemd services are Ubuntu-only; use `scripts/run_all.sh`.

## Project layout

```
replay/
  config.py            YAML + .env loading and validation
  models.py            domain dataclasses (Court, Clip, Segment)
  buffer.py            segment naming and leases (capture ↔ clipper contract)
  status.py            service heartbeats (capture/clipper ↔ API contract)
  logs.py              console + rotated file logging
  capture/             ffmpeg supervisor, buffer cleanup
  triggers/            trigger interface, keyboard (pynput), stdin
  clipper/             segment selection, clip builder, job worker
  storage/             ClipStorage interface, local disk implementation
  db/                  engine, ORM tables, repository (the only SQL)
  api/                 FastAPI app, schemas, system status, dev page
  devtools/            clip preview page (development)
migrations/            Alembic
deploy/systemd/        systemd user units
scripts/               run_all.sh, install_services.sh, fake_camera.sh
docs/openapi.json      API contract for the frontend
tests/
```
