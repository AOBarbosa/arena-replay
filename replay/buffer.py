"""Buffer file contract, shared between capture and clipper.

Segments: `<segments_dir>/<court_id>/<court_id>_YYYYmmdd_HHMMSS.ts`, named with the
UTC time the segment was opened (ffmpeg runs with TZ=UTC).

Leases: `<segments_dir>/<court_id>/.leases/<job_id>.json`. While a lease exists,
cleanup does not delete segments that overlap its window.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from replay.models import Segment

log = logging.getLogger(__name__)

SEGMENT_EXT = ".ts"
SEGMENT_TIME_FORMAT = "%Y%m%d_%H%M%S"
LEASES_DIRNAME = ".leases"


@dataclass(frozen=True, slots=True)
class Lease:
    job_id: str
    court_id: str
    start_at: datetime
    end_at: datetime
    path: Path


def court_dir(segments_dir: Path, court_id: str) -> Path:
    return segments_dir / court_id


def segment_output_pattern(segments_dir: Path, court_id: str) -> Path:
    """Output pattern for ffmpeg's `-strftime 1`."""
    return court_dir(segments_dir, court_id) / f"{court_id}_%Y%m%d_%H%M%S{SEGMENT_EXT}"


def parse_segment_start(name: str, court_id: str) -> datetime | None:
    """UTC start time parsed from the file name, or None if it is not a segment."""
    match = re.fullmatch(rf"{re.escape(court_id)}_(\d{{8}}_\d{{6}})\.ts", name)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), SEGMENT_TIME_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        return None


def list_segments(segments_dir: Path, court_id: str) -> list[Segment]:
    """Segments in chronological order. Each one ends where the next one starts;
    the last one has `end_at=None` (it may still be recording)."""
    directory = court_dir(segments_dir, court_id)
    found: list[tuple[datetime, Path]] = []
    try:
        entries = list(os.scandir(directory))
    except FileNotFoundError:
        return []
    for entry in entries:
        if not entry.is_file():
            continue
        start = parse_segment_start(entry.name, court_id)
        if start is not None:
            found.append((start, Path(entry.path)))
    found.sort()
    segments: list[Segment] = []
    for i, (start, path) in enumerate(found):
        end = found[i + 1][0] if i + 1 < len(found) else None
        segments.append(Segment(court_id=court_id, path=path, start_at=start, end_at=end))
    return segments


def latest_segment_name(segments_dir: Path, court_id: str) -> str | None:
    """Name of the newest segment (names sort chronologically)."""
    latest: str | None = None
    try:
        entries = list(os.scandir(court_dir(segments_dir, court_id)))
    except FileNotFoundError:
        return None
    for entry in entries:
        if parse_segment_start(entry.name, court_id) and (latest is None or entry.name > latest):
            latest = entry.name
    return latest


# --- leases ----------------------------------------------------------------


def leases_dir(segments_dir: Path, court_id: str) -> Path:
    return court_dir(segments_dir, court_id) / LEASES_DIRNAME


def write_lease(
    segments_dir: Path, court_id: str, job_id: str, start_at: datetime, end_at: datetime
) -> Lease:
    """Writes the lease atomically (temporary file + rename)."""
    directory = leases_dir(segments_dir, court_id)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{job_id}.json"
    tmp = directory / f".{job_id}.tmp"
    payload = {
        "job_id": job_id,
        "court_id": court_id,
        "start_at": start_at.astimezone(UTC).isoformat(),
        "end_at": end_at.astimezone(UTC).isoformat(),
    }
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(path)
    return Lease(job_id, court_id, start_at, end_at, path)


def remove_lease(lease: Lease) -> None:
    lease.path.unlink(missing_ok=True)


def read_active_leases(
    segments_dir: Path, court_id: str, now: datetime, max_age: timedelta
) -> list[Lease]:
    """Active leases. Leases that are too old (stuck or dead job) are deleted."""
    directory = leases_dir(segments_dir, court_id)
    if not directory.is_dir():
        return []
    leases: list[Lease] = []
    for path in directory.glob("*.json"):
        try:
            age = now - datetime.fromtimestamp(path.stat().st_mtime, UTC)
            if age > max_age:
                log.warning("[%s] removed expired lease: %s", court_id, path.name)
                path.unlink(missing_ok=True)
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            leases.append(
                Lease(
                    job_id=data["job_id"],
                    court_id=data["court_id"],
                    start_at=datetime.fromisoformat(data["start_at"]),
                    end_at=datetime.fromisoformat(data["end_at"]),
                    path=path,
                )
            )
        except FileNotFoundError:
            continue  # removed by the worker between glob and read
        except (OSError, ValueError, KeyError) as exc:
            log.warning("[%s] ignoring unreadable lease (%s): %s", court_id, path.name, exc)
    return leases
