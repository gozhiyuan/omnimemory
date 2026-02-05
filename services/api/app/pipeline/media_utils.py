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
        # Use escaped comma for ffmpeg filter parsing (no shell quoting here).
        vf = f"select=gt(scene\\,{scene_threshold}),showinfo"
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
        detail = ""
        if isinstance(exc, subprocess.CalledProcessError):
            stderr = (exc.stderr or "").strip()
            if stderr:
                detail = f" | stderr: {stderr[:500]}"
        raise MediaToolError(f"ffmpeg keyframe extraction failed: {exc}{detail}") from exc

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


def _get_duration_sec(path: str) -> Optional[float]:
    try:
        metadata = probe_media(path)
    except MediaToolError:
        return None
    duration = (metadata.get("format") or {}).get("duration")
    return parse_fraction(duration) if isinstance(duration, str) else None


def detect_speech_segments(
    input_path: str,
    *,
    silence_db: float,
    min_silence_sec: float,
    padding_sec: float,
    min_segment_sec: float,
    duration_sec: Optional[float],
) -> list[tuple[float, float]]:
    if duration_sec is None:
        duration_sec = _get_duration_sec(input_path)
    if duration_sec is None:
        return []

    args = [
        "ffmpeg",
        "-i",
        input_path,
        "-af",
        f"silencedetect=noise={silence_db}dB:d={min_silence_sec}",
        "-f",
        "null",
        "-",
    ]
    try:
        result = _run_command(args)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise MediaToolError(f"ffmpeg silencedetect failed: {exc}") from exc

    silences: list[tuple[float, float]] = []
    current_start: Optional[float] = None
    for line in result.stderr.splitlines():
        if "silence_start" in line:
            match = re.search(r"silence_start:\s*([0-9.]+)", line)
            if match:
                current_start = float(match.group(1))
        elif "silence_end" in line:
            match = re.search(r"silence_end:\s*([0-9.]+)", line)
            if match:
                end = float(match.group(1))
                start = current_start if current_start is not None else max(0.0, end - min_silence_sec)
                silences.append((start, end))
                current_start = None

    if current_start is not None:
        silences.append((current_start, duration_sec))

    silences.sort()
    speech_segments: list[tuple[float, float]] = []
    cursor = 0.0
    for start, end in silences:
        if start > cursor:
            speech_segments.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < duration_sec:
        speech_segments.append((cursor, duration_sec))

    if padding_sec > 0:
        padded: list[tuple[float, float]] = []
        for start, end in speech_segments:
            padded_start = max(0.0, start - padding_sec)
            padded_end = min(duration_sec, end + padding_sec)
            padded.append((padded_start, padded_end))
        speech_segments = padded

    merged: list[tuple[float, float]] = []
    for start, end in speech_segments:
        if not merged:
            merged.append((start, end))
            continue
        prev_start, prev_end = merged[-1]
        if start <= prev_end + 0.05:
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))

    filtered = [
        (start, end) for start, end in merged if (end - start) >= min_segment_sec
    ]
    return filtered


def extract_audio_segment(
    input_path: str,
    output_path: str,
    *,
    start_sec: float,
    duration_sec: float,
    sample_rate_hz: int,
    channels: int,
) -> None:
    args = [
        "ffmpeg",
        "-y",
        "-ss",
        f"{start_sec:.3f}",
        "-t",
        f"{duration_sec:.3f}",
        "-i",
        input_path,
        "-vn",
        "-ac",
        str(channels),
        "-ar",
        str(sample_rate_hz),
        "-c:a",
        "pcm_s16le",
        output_path,
    ]
    try:
        _run_command(args)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise MediaToolError(f"ffmpeg audio segment extraction failed: {exc}") from exc


def segment_audio_with_vad(
    input_path: str,
    output_dir: str,
    *,
    chunk_duration_sec: int,
    sample_rate_hz: int,
    channels: int,
    vad_enabled: bool,
    vad_silence_db: float,
    vad_min_silence_sec: float,
    vad_padding_sec: float,
    vad_min_segment_sec: float,
    duration_sec: Optional[float],
) -> list[dict[str, float | str]]:
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    if not vad_enabled:
        chunk_paths = segment_audio(
            input_path,
            output_dir,
            chunk_duration_sec=chunk_duration_sec,
            sample_rate_hz=sample_rate_hz,
            channels=channels,
        )
        chunk_infos: list[dict[str, float | str]] = []
        for idx, chunk_path in enumerate(chunk_paths):
            start = idx * chunk_duration_sec
            end = (idx + 1) * chunk_duration_sec
            if isinstance(duration_sec, (int, float)):
                end = min(duration_sec, end)
            chunk_infos.append(
                {
                    "path": chunk_path,
                    "start_ms": start * 1000,
                    "end_ms": end * 1000,
                }
            )
        return chunk_infos

    speech_segments = detect_speech_segments(
        input_path,
        silence_db=vad_silence_db,
        min_silence_sec=vad_min_silence_sec,
        padding_sec=vad_padding_sec,
        min_segment_sec=vad_min_segment_sec,
        duration_sec=duration_sec,
    )
    if not speech_segments:
        return segment_audio_with_vad(
            input_path,
            output_dir,
            chunk_duration_sec=chunk_duration_sec,
            sample_rate_hz=sample_rate_hz,
            channels=channels,
            vad_enabled=False,
            vad_silence_db=vad_silence_db,
            vad_min_silence_sec=vad_min_silence_sec,
            vad_padding_sec=vad_padding_sec,
            vad_min_segment_sec=vad_min_segment_sec,
            duration_sec=duration_sec,
        )

    chunk_infos: list[dict[str, float | str]] = []
    for seg_idx, (seg_start, seg_end) in enumerate(speech_segments):
        seg_duration = max(0.0, seg_end - seg_start)
        if seg_duration <= 0:
            continue
        segment_dir = Path(output_dir) / f"speech_{seg_idx:05d}"
        segment_dir.mkdir(parents=True, exist_ok=True)
        segment_path = segment_dir / "segment.wav"
        extract_audio_segment(
            input_path,
            str(segment_path),
            start_sec=seg_start,
            duration_sec=seg_duration,
            sample_rate_hz=sample_rate_hz,
            channels=channels,
        )

        if seg_duration <= chunk_duration_sec:
            chunk_infos.append(
                {
                    "path": str(segment_path),
                    "start_ms": seg_start * 1000,
                    "end_ms": seg_end * 1000,
                }
            )
            continue

        chunk_paths = segment_audio(
            str(segment_path),
            str(segment_dir),
            chunk_duration_sec=chunk_duration_sec,
            sample_rate_hz=sample_rate_hz,
            channels=channels,
        )
        for idx, chunk_path in enumerate(chunk_paths):
            start = seg_start + idx * chunk_duration_sec
            end = min(seg_end, seg_start + (idx + 1) * chunk_duration_sec)
            chunk_infos.append(
                {
                    "path": chunk_path,
                    "start_ms": start * 1000,
                    "end_ms": end * 1000,
                }
            )

    return chunk_infos


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
