from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from replay.buffer import list_segments
from replay.clipper.builder import (
    BuildError,
    build_clip,
    build_clip_command,
    build_concat_list,
    build_probe_command,
    build_thumbnail_command,
    probe_duration,
    run_command,
)
from replay.clipper.segment_index import candidate_segments, resolve_ends, select_window
from replay.config import ClipConfig, Encoder
from tests.conftest import make_segment_files, requires_ffmpeg

T0 = datetime(2026, 9, 28, 15, 0, 0, tzinfo=UTC)


def at(offset_s: float) -> datetime:
    return T0 + timedelta(seconds=offset_s)


# --- pure command builders -----------------------------------------------------


def test_concat_list_escapes_quotes(tmp_path: Path) -> None:
    content = build_concat_list([tmp_path / "a.ts", tmp_path / "it's.ts"])
    lines = content.splitlines()
    assert lines[0] == "ffconcat version 1.0"
    assert lines[1] == f"file '{(tmp_path / 'a.ts').resolve()}'"
    assert lines[2].endswith("it'\\''s.ts'")


def test_clip_command_copy() -> None:
    cmd = build_clip_command(
        Path("list.ffconcat"),
        Path("out.mp4"),
        offset_s=1.5,
        duration_s=33,
        encoder=Encoder.COPY,
        vaapi_device="/dev/dri/renderD128",
    )
    assert cmd[cmd.index("-f") + 1] == "concat"
    assert cmd.index("-ss") < cmd.index("-i")  # input seeking
    assert cmd[cmd.index("-ss") + 1] == "1.500"
    assert cmd[cmd.index("-t") + 1] == "33.000"
    assert cmd[cmd.index("-c:v") + 1] == "copy"
    assert cmd[cmd.index("-c:a") + 1] == "copy"
    assert "0:a:0?" in cmd
    assert cmd[cmd.index("-movflags") + 1] == "+faststart"
    assert "-vaapi_device" not in cmd
    assert cmd[-1] == "out.mp4"


def test_clip_command_without_offset_has_no_seek() -> None:
    cmd = build_clip_command(
        Path("l"), Path("o.mp4"), offset_s=0, duration_s=30, encoder=Encoder.COPY, vaapi_device=""
    )
    assert "-ss" not in cmd


@pytest.mark.parametrize(
    ("encoder", "codec"),
    [
        (Encoder.LIBX264, "libx264"),
        (Encoder.H264_VAAPI, "h264_vaapi"),
        (Encoder.H264_VIDEOTOOLBOX, "h264_videotoolbox"),
    ],
)
def test_clip_command_reencode(encoder: Encoder, codec: str) -> None:
    cmd = build_clip_command(
        Path("l"), Path("o.mp4"), offset_s=1, duration_s=30, encoder=encoder, vaapi_device="/dev/x"
    )
    assert cmd[cmd.index("-c:v") + 1] == codec
    assert "-avoid_negative_ts" not in cmd
    if encoder is Encoder.H264_VAAPI:
        assert cmd.index("-vaapi_device") < cmd.index("-i")
        assert cmd[cmd.index("-vaapi_device") + 1] == "/dev/x"
        assert "format=nv12,hwupload" in cmd


def test_thumbnail_and_probe_commands() -> None:
    thumb = build_thumbnail_command(Path("clip.mp4"), Path("t.jpg"), at_s=-2)
    assert thumb[thumb.index("-ss") + 1] == "0.000"
    assert thumb[thumb.index("-frames:v") + 1] == "1"
    probe = build_probe_command(Path("x.ts"))
    assert probe[0] == "ffprobe"
    assert probe[-1] == "x.ts"


def test_run_command_reports_stderr() -> None:
    with pytest.raises(BuildError, match="could not run"):
        run_command(["/does/not/exist"], 5)


# --- real ffmpeg -----------------------------------------------------------------


def select(segments_dir: Path, start: datetime, end: datetime):
    candidates = candidate_segments(list_segments(segments_dir, "court1"), start, end)
    return select_window(resolve_ends(candidates, probe_duration), start, end)


@requires_ffmpeg
@pytest.mark.parametrize(("encoder", "tolerance"), [(Encoder.COPY, 2.1), (Encoder.LIBX264, 0.2)])
def test_build_real_clip(tmp_path: Path, encoder: Encoder, tolerance: float) -> None:
    make_segment_files(tmp_path / "seg", "court1", T0, count=20)  # 40 s
    selection = select(tmp_path / "seg", at(5), at(35))
    assert selection is not None and selection.gaps == []

    built = build_clip(selection, at(5), at(35), tmp_path / "work", ClipConfig(encoder=encoder), 27)
    assert built.video.is_file()
    assert built.thumbnail is not None and built.thumbnail.stat().st_size > 0
    assert built.duration_s == pytest.approx(30, abs=tolerance)
    assert built.start_at == at(5)
    # faststart: the 'moov' atom comes before 'mdat'
    head = built.video.read_bytes()[:4096]
    assert b"moov" in head


@requires_ffmpeg
def test_build_real_clip_with_gap(tmp_path: Path) -> None:
    paths = make_segment_files(tmp_path / "seg", "court1", T0, count=20)
    for path in paths[5:10]:  # 10..20 s missing
        path.unlink()
    selection = select(tmp_path / "seg", at(0), at(30))
    assert selection is not None
    assert selection.gap_seconds == pytest.approx(10, abs=0.5)

    built = build_clip(selection, at(0), at(30), tmp_path / "work", ClipConfig(), 20)
    assert built.duration_s == pytest.approx(20, abs=2.1)
    # The clip spans 0-30 s of real time even though 10 s are missing
    assert (built.start_at, built.end_at) == (at(0), at(30))
