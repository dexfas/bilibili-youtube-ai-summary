from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from faster_whisper import WhisperModel


@dataclass(frozen=True)
class TranscriptSegment:
    start: float
    end: float
    text: str


def transcribe_audio(
    audio_path: Path,
    *,
    model_size: str,
    device: str,
    compute_type: str,
    language: str | None,
    beam_size: int,
) -> list[TranscriptSegment]:
    model = WhisperModel(
        model_size,
        device=device,
        compute_type=compute_type,
        cpu_threads=default_cpu_threads() if device == "cpu" else 0,
        num_workers=1,
    )
    transcribe_options = {
        "language": language or None,
        "vad_filter": True,
        "beam_size": beam_size,
    }
    if (language or "").lower() in {"zh", "chinese", "cn"}:
        transcribe_options["initial_prompt"] = "以下是普通话简体中文视频字幕，请使用简体中文输出。"
    segments, _info = model.transcribe(str(audio_path), **transcribe_options)

    return [
        TranscriptSegment(start=segment.start, end=segment.end, text=segment.text.strip())
        for segment in segments
        if segment.text.strip()
    ]


def default_cpu_threads() -> int:
    configured = os.environ.get("AUTO_TRAN_VIDEO_ASR_CPU_THREADS")
    if configured:
        try:
            return max(1, int(configured))
        except ValueError:
            pass
    return max(1, min(4, (os.cpu_count() or 2) // 2))


def segments_to_text(segments: list[TranscriptSegment]) -> str:
    return "\n".join(segment.text for segment in segments)


def segments_to_srt(segments: list[TranscriptSegment]) -> str:
    blocks: list[str] = []
    for index, segment in enumerate(segments, start=1):
        blocks.append(
            f"{index}\n"
            f"{format_srt_time(segment.start)} --> {format_srt_time(segment.end)}\n"
            f"{segment.text}\n"
        )
    return "\n".join(blocks)


def segments_to_json(segments: list[TranscriptSegment]) -> str:
    payload = [
        {"start": segment.start, "end": segment.end, "text": segment.text}
        for segment in segments
    ]
    return json.dumps(payload, ensure_ascii=False, indent=2)


def segments_from_json(text: str) -> list[TranscriptSegment]:
    payload = json.loads(text)
    return [
        TranscriptSegment(
            start=float(item["start"]),
            end=float(item["end"]),
            text=str(item["text"]).strip(),
        )
        for item in payload
        if str(item.get("text", "")).strip()
    ]


def offset_segments(segments: list[TranscriptSegment], offset_seconds: float) -> list[TranscriptSegment]:
    return [
        TranscriptSegment(
            start=segment.start + offset_seconds,
            end=segment.end + offset_seconds,
            text=segment.text,
        )
        for segment in segments
    ]


def format_srt_time(seconds: float) -> str:
    millis = int(round(seconds * 1000))
    hours, remainder = divmod(millis, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"
