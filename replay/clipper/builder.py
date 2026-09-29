"""Builds a clip from segments: concat, trim, MP4 with faststart and a thumbnail.

Trade-off of `encoder: copy` (default): no re-encoding, so it is fast and light on
the i3, but the start snaps to the keyframe at or before the requested time (up to
one GOP earlier). Re-encoding (libx264, h264_vaapi, h264_videotoolbox) cuts exactly
at the requested frame, at the cost of CPU/GPU time.
Gaps in the buffer are not filled: the clip jumps from one side of the gap to the other.
"""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from replay.clipper.segment_index import WindowSelection
from replay.config import ClipConfig, Encoder

log = logging.getLogger(__name__)

FFMPEG_BASE = ["ffmpeg", "-y", "-hide_banner", "-nostdin", "-loglevel", "error"]
BUILD_TIMEOUT_S = 180
PROBE_TIMEOUT_S = 15
THUMBNAIL_WIDTH = 640


class BuildError(Exception):
    """ffmpeg/ffprobe failed."""


@dataclass(frozen=True, slots=True)
class BuiltClip:
    video: Path
    thumbnail: Path | None
    duration_s: float
    # Stream times (segment-name clock) actually covered by the clip
    start_at: datetime
    end_at: datetime


# --- command builders (pure) --------------------------------------------------


def build_concat_list(paths: Sequence[Path]) -> str:
    """Content of an ffconcat file listing the segments in order."""
    lines = ["ffconcat version 1.0"]
    for path in paths:
        escaped = str(path.resolve()).replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    return "\n".join(lines) + "\n"


def video_codec_args(encoder: Encoder) -> list[str]:
    match encoder:
        case Encoder.COPY:
            return ["-c:v", "copy"]
        case Encoder.LIBX264:
            return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p"]
        case Encoder.H264_VAAPI:
            return ["-vf", "format=nv12,hwupload", "-c:v", "h264_vaapi", "-qp", "24"]
        case Encoder.H264_VIDEOTOOLBOX:
            return ["-c:v", "h264_videotoolbox", "-b:v", "6M", "-pix_fmt", "yuv420p"]


def build_clip_command(
    concat_file: Path,
    output: Path,
    *,
    offset_s: float,
    duration_s: float,
    encoder: Encoder,
    vaapi_device: str,
) -> list[str]:
    cmd = list(FFMPEG_BASE)
    if encoder is Encoder.H264_VAAPI:
        cmd += ["-vaapi_device", vaapi_device]
    cmd += ["-f", "concat", "-safe", "0"]
    if offset_s > 0:
        # Input seeking: fast; exact when re-encoding, keyframe-aligned with copy
        cmd += ["-ss", f"{offset_s:.3f}"]
    cmd += ["-i", str(concat_file), "-t", f"{duration_s:.3f}"]
    cmd += ["-map", "0:v:0", "-map", "0:a:0?"]
    cmd += video_codec_args(encoder)
    cmd += ["-c:a", "copy"]
    if encoder is Encoder.COPY:
        cmd += ["-avoid_negative_ts", "make_zero"]
    cmd += ["-movflags", "+faststart", str(output)]
    return cmd


def build_thumbnail_command(video: Path, output: Path, at_s: float) -> list[str]:
    return [
        *FFMPEG_BASE,
        "-ss", f"{max(at_s, 0):.3f}",
        "-i", str(video),
        "-frames:v", "1",
        "-vf", f"scale={THUMBNAIL_WIDTH}:-2",
        "-q:v", "3",
        str(output),
    ]  # fmt: skip


def build_probe_command(path: Path) -> list[str]:
    return [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]  # fmt: skip


# --- execution --------------------------------------------------------------


def run_command(cmd: Sequence[str], timeout_s: float) -> str:
    """Runs ffmpeg/ffprobe; returns stdout or raises BuildError with the stderr tail."""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, errors="replace", timeout=timeout_s, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise BuildError(f"{cmd[0]} timed out after {timeout_s:.0f} s") from exc
    except OSError as exc:
        raise BuildError(f"could not run {cmd[0]}: {exc}") from exc
    if result.returncode != 0:
        tail = " | ".join(result.stderr.strip().splitlines()[-5:])
        raise BuildError(f"{cmd[0]} exited with code {result.returncode}: {tail}")
    return result.stdout


def probe_duration(path: Path) -> float | None:
    """Media duration in seconds, or None if it cannot be read (e.g. truncated file)."""
    try:
        output = run_command(build_probe_command(path), PROBE_TIMEOUT_S).strip()
        return float(output) if output and output != "N/A" else None
    except (BuildError, ValueError) as exc:
        log.debug("could not probe %s: %s", path.name, exc)
        return None


def build_clip(
    selection: WindowSelection,
    window_start: datetime,
    window_end: datetime,
    work_dir: Path,
    clip: ClipConfig,
    thumbnail_at_s: float,
) -> BuiltClip:
    """Produces clip.mp4 and thumb.jpg in `work_dir`. The thumbnail is optional:
    if it fails, the clip is still returned with `thumbnail=None`."""
    work_dir.mkdir(parents=True, exist_ok=True)
    concat_file = work_dir / "segments.ffconcat"
    concat_file.write_text(build_concat_list([s.path for s in selection.segments]))

    start_at = max(window_start, selection.start_at)
    offset_s = (start_at - selection.start_at).total_seconds()
    wanted_s = (window_end - start_at).total_seconds()
    video = work_dir / "clip.mp4"
    run_command(
        build_clip_command(
            concat_file,
            video,
            offset_s=offset_s,
            duration_s=wanted_s,
            encoder=clip.encoder,
            vaapi_device=clip.vaapi_device,
        ),
        BUILD_TIMEOUT_S,
    )
    duration_s = probe_duration(video)
    if duration_s is None or duration_s <= 0:
        raise BuildError("the generated clip has no readable duration")

    thumbnail: Path | None = work_dir / "thumb.jpg"
    try:
        run_command(
            build_thumbnail_command(video, thumbnail, min(thumbnail_at_s, duration_s - 0.1)),
            PROBE_TIMEOUT_S,
        )
    except BuildError as exc:
        log.warning("thumbnail failed, keeping the clip without it: %s", exc)
        thumbnail = None

    return BuiltClip(
        video=video,
        thumbnail=thumbnail,
        duration_s=duration_s,
        start_at=start_at,
        end_at=start_at + timedelta(seconds=duration_s),
    )
