"""ffmpeg/ffprobe helpers for media processing."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional


class MediaToolError(RuntimeError):
    pass


def _run_command(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def probe_media(path: str) -> dict[str, Any]:
    args = [
        "ffprobe",
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        path,
    ]
    try:
        result = _run_command(args)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise MediaToolError(f"ffprobe failed: {exc}") from exc
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise MediaToolError("ffprobe returned invalid JSON") from exc


def parse_fraction(value: str | None) -> Optional[float]:
    if not value or value == "0/0":
        return None
    if "/" in value:
        num, denom = value.split("/", 1)
        try:
            num_f = float(num)
            denom_f = float(denom)
        except ValueError:
            return None
        if denom_f == 0:
            return None
        return num_f / denom_f
    try:
        return float(value)
    except ValueError:
        return None


def parse_iso6709(value: str | None) -> Optional[dict[str, float]]:
    if not value:
        return None
    match = re.match(r"^([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)?/?$", value)
    if not match:
        return None
    lat = float(match.group(1))
    lng = float(match.group(2))
    alt = match.group(3)
    result = {"latitude": lat, "longitude": lng}
    if alt is not None:
        result["altitude"] = float(alt)
    return result


def extract_keyframes(
    input_path: str,
    output_dir: str,
    *,
    mode: str,
    interval_sec: int,
    scene_threshold: float,
    max_frames: int,
) -> tuple[list[dict[str, Any]], str, list[float]]:
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    output_pattern = str(Path(output_dir) / "frame_%05d.jpg")
    if mode == "scene":
        vf = f"select='gt(scene,{scene_threshold})',showinfo"
    else:
        vf = f"fps=1/{interval_sec},showinfo"
    args = [
        "ffmpeg",
        "-i",
        input_path,
        "-vf",
        vf,
        "-vsync",
        "vfr",
        "-q:v",
        "2",
    ]
    if max_frames > 0:
        args.extend(["-frames:v", str(max_frames)])
    args.append(output_pattern)
    try:
        result = _run_command(args)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise MediaToolError(f"ffmpeg keyframe extraction failed: {exc}") from exc

    times = [float(m.group(1)) for m in re.finditer(r"pts_time:([0-9.]+)", result.stderr)]
    files = sorted(Path(output_dir).glob("frame_*.jpg"))
    frames: list[dict[str, Any]] = []
    for idx, file_path in enumerate(files):
        t_sec = times[idx] if idx < len(times) else None
        if t_sec is None and mode != "scene":
            t_sec = float(idx * interval_sec)
        frames.append({"path": str(file_path), "t_sec": t_sec})
    return frames, mode, times


def segment_audio(
    input_path: str,
    output_dir: str,
    *,
    chunk_duration_sec: int,
    sample_rate_hz: int,
    channels: int,
) -> list[str]:
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    output_pattern = str(Path(output_dir) / "chunk_%05d.wav")
    args = [
        "ffmpeg",
        "-i",
        input_path,
        "-vn",
        "-ac",
        str(channels),
        "-ar",
        str(sample_rate_hz),
        "-f",
        "segment",
        "-segment_time",
        str(chunk_duration_sec),
        "-reset_timestamps",
        "1",
        output_pattern,
    ]
    try:
        _run_command(args)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise MediaToolError(f"ffmpeg audio segmentation failed: {exc}") from exc

    return [str(path) for path in sorted(Path(output_dir).glob("chunk_*.wav"))]


def segment_video(
    input_path: str,
    output_dir: str,
    *,
    chunk_duration_sec: int,
    output_ext: str = ".mp4",
) -> list[str]:
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    ext = output_ext if output_ext.startswith(".") else f".{output_ext}"
    output_pattern = str(Path(output_dir) / f"chunk_%05d{ext}")
    args = [
        "ffmpeg",
        "-i",
        input_path,
        "-c",
        "copy",
        "-f",
        "segment",
        "-segment_time",
        str(chunk_duration_sec),
        "-reset_timestamps",
        "1",
        output_pattern,
    ]
    try:
        _run_command(args)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise MediaToolError(f"ffmpeg video segmentation failed: {exc}") from exc

    return [str(path) for path in sorted(Path(output_dir).glob("chunk_*.mp4"))]


def extract_single_frame(
    input_path: str,
    output_path: str,
    *,
    timestamp_sec: float = 0.0,
) -> None:
    args = [
        "ffmpeg",
        "-y",
        "-ss",
        str(timestamp_sec),
        "-i",
        input_path,
        "-frames:v",
        "1",
        "-q:v",
        "2",
        output_path,
    ]
    try:
        _run_command(args)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise MediaToolError(f"ffmpeg frame capture failed: {exc}") from exc


def create_video_preview(
    input_path: str,
    output_path: str,
    *,
    duration_sec: int,
    max_width: int,
    fps: int,
    bitrate_kbps: int,
) -> None:
    args = [
        "ffmpeg",
        "-y",
        "-i",
        input_path,
        "-t",
        str(duration_sec),
        "-vf",
        f"scale='min({max_width},iw)':-2,fps={fps}",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-b:v",
        f"{bitrate_kbps}k",
        "-movflags",
        "+faststart",
        "-pix_fmt",
        "yuv420p",
        output_path,
    ]
    try:
        _run_command(args)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise MediaToolError(f"ffmpeg preview failed: {exc}") from exc
