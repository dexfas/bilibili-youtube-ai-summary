from __future__ import annotations

import html
import json
import re
import shutil
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .asr import (
    TranscriptSegment,
    offset_segments,
    segments_from_json,
    segments_to_json,
    segments_to_srt,
    segments_to_text,
    transcribe_audio,
)
from .llm import summarize_with_ollama, translate_with_ollama
from .media import extract_audio, split_audio, write_text
from .text import to_simplified

ProgressCallback = Callable[..., None]
StopCheck = Callable[[], bool]


class ProcessingStopped(RuntimeError):
    """Raised when a browser job asks the current item to stop."""


@dataclass(frozen=True)
class ProcessOptions:
    asr_model_size: str
    asr_device: str
    asr_compute_type: str
    asr_language: str | None
    asr_beam_size: int
    chunk_minutes: float
    force: bool
    clean_cache: bool
    summarize: bool
    translate_to: str | None
    english_cn: bool
    ollama_base_url: str
    summary_model: str
    translate_model: str
    simplified_chinese: bool = True


def process_one_video(
    *,
    source_path: Path,
    job_dir: Path,
    metadata: dict,
    options: ProcessOptions,
    cache_path: Path | None = None,
    progress: ProgressCallback | None = None,
    should_stop: StopCheck | None = None,
    asr_gate: Any | None = None,
    llm_gate: Any | None = None,
) -> Path:
    _ensure_not_stopped(should_stop)
    job_dir.mkdir(parents=True, exist_ok=True)
    state = _load_state(job_dir)
    state["metadata"] = metadata
    state["options"] = _state_options(options)
    write_text(job_dir / "metadata.json", json.dumps(metadata, ensure_ascii=False, indent=2))
    _write_state(job_dir, state)

    transcript_path = job_dir / "transcript.txt"
    if _is_done(transcript_path) and not options.force:
        print(f"Skipping transcription, found {transcript_path}.")
        transcript = transcript_path.read_text(encoding="utf-8")
        if options.simplified_chinese:
            transcript = to_simplified(transcript)
            write_text(transcript_path, transcript)
    else:
        emit_progress(progress, "transcribing", "正在转写音频", percent=30)
        transcript = _transcribe_with_resume(
            source_path=source_path,
            job_dir=job_dir,
            options=options,
            state=state,
            progress=progress,
            should_stop=should_stop,
            asr_gate=asr_gate,
        )
        state["transcript_source"] = "asr"
        _write_state(job_dir, state)

    _postprocess_text(
        transcript=transcript,
        job_dir=job_dir,
        state=state,
        options=options,
        progress=progress,
        should_stop=should_stop,
        llm_gate=llm_gate,
    )

    _ensure_not_stopped(should_stop)
    if options.clean_cache and cache_path and cache_path.exists():
        cache_path.unlink()
        state["cache_cleaned"] = str(cache_path)
        _write_state(job_dir, state)

    return job_dir


def process_subtitle_transcript(
    *,
    subtitle_path: Path,
    job_dir: Path,
    metadata: dict,
    options: ProcessOptions,
    subtitle_language: str | None = None,
    subtitle_ext: str | None = None,
    progress: ProgressCallback | None = None,
    should_stop: StopCheck | None = None,
    llm_gate: Any | None = None,
) -> Path:
    _ensure_not_stopped(should_stop)
    job_dir.mkdir(parents=True, exist_ok=True)
    state = _load_state(job_dir)
    state["metadata"] = metadata
    state["options"] = _state_options(options)
    state["transcript_source"] = "subtitle"
    state["subtitle"] = {
        "path": str(subtitle_path),
        "language": subtitle_language,
        "ext": subtitle_ext or subtitle_path.suffix.lstrip("."),
    }
    write_text(job_dir / "metadata.json", json.dumps(metadata, ensure_ascii=False, indent=2))
    _write_state(job_dir, state)

    transcript_path = job_dir / "transcript.txt"
    if _is_done(transcript_path) and not options.force:
        transcript = transcript_path.read_text(encoding="utf-8")
        if options.simplified_chinese:
            transcript = to_simplified(transcript)
            write_text(transcript_path, transcript)
    else:
        emit_progress(progress, "using_subtitles", "正在读取已有字幕", percent=28)
        segments = parse_subtitle_file(subtitle_path)
        if not segments:
            raise RuntimeError(f"No subtitle text was parsed from {subtitle_path}.")
        if options.simplified_chinese:
            segments = simplify_segments(segments)
        transcript = _write_transcript_outputs(job_dir, state, segments)
        raw_target = job_dir / f"source_subtitle.{subtitle_ext or subtitle_path.suffix.lstrip('.') or 'txt'}"
        if subtitle_path.resolve() != raw_target.resolve():
            shutil.copy2(subtitle_path, raw_target)
        _mark_output(state, "source_subtitle", raw_target)
        _write_state(job_dir, state)
        print(f"Wrote transcript from subtitles ({len(segments)} segments).")

    _postprocess_text(
        transcript=transcript,
        job_dir=job_dir,
        state=state,
        options=options,
        progress=progress,
        should_stop=should_stop,
        llm_gate=llm_gate,
    )
    return job_dir


def summarize_existing(
    *,
    path: Path,
    options: ProcessOptions,
    progress: ProgressCallback | None = None,
    should_stop: StopCheck | None = None,
    llm_gate: Any | None = None,
) -> Path:
    _ensure_not_stopped(should_stop)
    transcript_path = resolve_transcript_path(path)
    job_dir = transcript_path.parent
    summary_path = job_dir / "summary.md"
    if _is_done(summary_path) and not options.force:
        print(f"Skipping summary, found {summary_path}.")
        return summary_path

    transcript = transcript_path.read_text(encoding="utf-8")
    if options.simplified_chinese:
        transcript = to_simplified(transcript)
        write_text(transcript_path, transcript)
    emit_progress(progress, "summarizing", "正在重新总结", percent=88)
    print(f"Summarizing existing transcript with Ollama model: {options.summary_model}")
    with _gate(llm_gate):
        _ensure_not_stopped(should_stop)
        summary = summarize_with_ollama(
            transcript,
            base_url=options.ollama_base_url,
            model=options.summary_model,
        )
    if options.simplified_chinese:
        summary = to_simplified(summary)
    write_text(summary_path, summary)
    state = _load_state(job_dir)
    _mark_output(state, "summary", summary_path)
    _write_state(job_dir, state)
    print(f"Wrote {summary_path}.")
    return summary_path


def resolve_transcript_path(path: Path) -> Path:
    path = path.expanduser().resolve()
    if path.is_dir():
        path = path / "transcript.txt"
    if not path.exists():
        raise SystemExit(f"Transcript not found: {path}")
    if path.stat().st_size == 0:
        raise SystemExit(f"Transcript is empty: {path}")
    return path


def language_suffix(language: str) -> str:
    normalized = language.strip().lower()
    if normalized in {"zh", "cn", "chinese", "simplified chinese"}:
        return "zh"
    if normalized in {"en", "english"}:
        return "en"
    return normalized.replace(" ", "-") or "translation"


def parse_subtitle_file(path: Path) -> list[TranscriptSegment]:
    suffix = path.suffix.lower().lstrip(".")
    text = path.read_text(encoding="utf-8-sig", errors="ignore")
    if suffix in {"json", "json3"} or text.lstrip().startswith(("{", "[")):
        return _parse_json_subtitle(text)
    if suffix == "srt":
        return _parse_timed_text(text)
    if suffix in {"vtt", "srv3", "ttml", "xml", "ass"}:
        return _parse_timed_text(text)
    return _parse_timed_text(text)


def _transcribe_with_resume(
    *,
    source_path: Path,
    job_dir: Path,
    options: ProcessOptions,
    state: dict,
    progress: ProgressCallback | None = None,
    should_stop: StopCheck | None = None,
    asr_gate: Any | None = None,
) -> str:
    audio_path = job_dir / "audio.wav"
    if not _is_done(audio_path) or options.force:
        _ensure_not_stopped(should_stop)
        print("Extracting audio...")
        emit_progress(progress, "extracting_audio", "正在提取音频", percent=20)
        extract_audio(source_path, audio_path)
        _mark_output(state, "audio", audio_path)
        _write_state(job_dir, state)

    _ensure_not_stopped(should_stop)
    parts_dir = job_dir / "parts"
    chunk_seconds = max(60, int(options.chunk_minutes * 60))
    chunk_paths = split_audio(audio_path, parts_dir, chunk_seconds=chunk_seconds, force=options.force)
    chunk_total = len(chunk_paths)

    all_segments: list[TranscriptSegment] = []
    chunk_states: list[dict] = []
    for index, chunk_path in enumerate(chunk_paths):
        _ensure_not_stopped(should_stop)
        stem = chunk_path.stem
        part_json = parts_dir / f"{stem}.json"
        part_txt = parts_dir / f"{stem}.txt"
        part_srt = parts_dir / f"{stem}.srt"
        offset = index * chunk_seconds

        if _is_done(part_json) and not options.force:
            segments = segments_from_json(part_json.read_text(encoding="utf-8"))
            if options.simplified_chinese:
                segments = simplify_segments(segments)
            status = "skipped"
            print(f"Skipping chunk {index + 1}/{chunk_total}, found {part_json.name}.")
        else:
            print(f"Transcribing chunk {index + 1}/{chunk_total}...")
            emit_progress(
                progress,
                "transcribing",
                f"正在转写第 {index + 1}/{chunk_total} 段",
                percent=30 + int((index / max(1, chunk_total)) * 40),
                chunk_index=index + 1,
                chunk_total=chunk_total,
            )
            with _gate(asr_gate):
                _ensure_not_stopped(should_stop)
                raw_segments = transcribe_audio(
                    chunk_path,
                    model_size=options.asr_model_size,
                    device=options.asr_device,
                    compute_type=options.asr_compute_type,
                    language=options.asr_language,
                    beam_size=options.asr_beam_size,
                )
            segments = offset_segments(raw_segments, offset)
            if options.simplified_chinese:
                segments = simplify_segments(segments)
            write_text(part_json, segments_to_json(segments))
            write_text(part_txt, segments_to_text(segments))
            write_text(part_srt, segments_to_srt(segments))
            status = "done"

        all_segments.extend(segments)
        chunk_states.append(
            {
                "index": index,
                "path": str(chunk_path),
                "offset_seconds": offset,
                "segments": len(segments),
                "status": status,
            }
        )
        state["chunks"] = chunk_states
        _write_state(job_dir, state)

    _ensure_not_stopped(should_stop)
    all_segments.sort(key=lambda segment: (segment.start, segment.end))
    return _write_transcript_outputs(job_dir, state, all_segments, simplify=options.simplified_chinese)


def _postprocess_text(
    *,
    transcript: str,
    job_dir: Path,
    state: dict,
    options: ProcessOptions,
    progress: ProgressCallback | None,
    should_stop: StopCheck | None,
    llm_gate: Any | None,
) -> None:
    _ensure_not_stopped(should_stop)
    translation_path: Path | None = None
    if options.translate_to:
        translation_path = job_dir / f"translation.{language_suffix(options.translate_to)}.md"
        if _is_done(translation_path) and not options.force:
            print(f"Skipping translation, found {translation_path}.")
        else:
            emit_progress(progress, "translating", "正在翻译转写稿", percent=75)
            print(f"Translating transcript to {options.translate_to} with Ollama model: {options.translate_model}")
            with _gate(llm_gate):
                _ensure_not_stopped(should_stop)
                translation = translate_with_ollama(
                    transcript,
                    target_language=options.translate_to,
                    base_url=options.ollama_base_url,
                    model=options.translate_model,
                )
            if options.simplified_chinese and language_suffix(options.translate_to) == "zh":
                translation = to_simplified(translation)
            write_text(translation_path, translation)
            _mark_output(state, "translation", translation_path)
            _write_state(job_dir, state)
            print(f"Wrote {translation_path}.")

    _ensure_not_stopped(should_stop)
    if options.summarize:
        summary_path = job_dir / "summary.md"
        if _is_done(summary_path) and not options.force:
            print(f"Skipping summary, found {summary_path}.")
        else:
            emit_progress(progress, "summarizing", "正在生成总结", percent=88)
            summary_source = transcript
            if translation_path and _is_done(translation_path) and language_suffix(options.translate_to or "") == "zh":
                summary_source = translation_path.read_text(encoding="utf-8")
            print(f"Summarizing with Ollama model: {options.summary_model}")
            with _gate(llm_gate):
                _ensure_not_stopped(should_stop)
                summary = summarize_with_ollama(
                    summary_source,
                    base_url=options.ollama_base_url,
                    model=options.summary_model,
                )
            if options.simplified_chinese:
                summary = to_simplified(summary)
            write_text(summary_path, summary)
            _mark_output(state, "summary", summary_path)
            _write_state(job_dir, state)
            print(f"Wrote {summary_path}.")


def _write_transcript_outputs(
    job_dir: Path,
    state: dict,
    segments: list[TranscriptSegment],
    *,
    simplify: bool = False,
) -> str:
    segments = sorted(segments, key=lambda segment: (segment.start, segment.end))
    if simplify:
        segments = simplify_segments(segments)
    transcript = segments_to_text(segments)
    if simplify:
        transcript = to_simplified(transcript)
    write_text(job_dir / "transcript.txt", transcript)
    write_text(job_dir / "transcript.srt", segments_to_srt(segments))
    write_text(job_dir / "transcript.json", segments_to_json(segments))
    _mark_output(state, "transcript", job_dir / "transcript.txt")
    _mark_output(state, "srt", job_dir / "transcript.srt")
    _mark_output(state, "json", job_dir / "transcript.json")
    _write_state(job_dir, state)
    print(f"Wrote transcript.txt and transcript.srt ({len(segments)} segments).")
    return transcript


def _parse_json_subtitle(text: str) -> list[TranscriptSegment]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return []

    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict) and isinstance(payload.get("body"), list):
        items = payload["body"]
    elif isinstance(payload, dict) and isinstance(payload.get("events"), list):
        return _parse_youtube_json3(payload["events"])
    elif isinstance(payload, dict) and isinstance(payload.get("segments"), list):
        items = payload["segments"]
    else:
        return []

    segments: list[TranscriptSegment] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        text_value = _clean_subtitle_text(
            item.get("content")
            or item.get("text")
            or item.get("utf8")
            or item.get("line")
            or ""
        )
        if not text_value:
            continue
        start = _float_first(item, ["from", "start", "start_time", "tStartMs"], scale_ms_key="tStartMs")
        end = _float_first(item, ["to", "end", "end_time"])
        if end is None:
            duration = _float_first(item, ["duration", "dur", "dDurationMs"], scale_ms_key="dDurationMs")
            end = (start or 0.0) + (duration or 2.0)
        segments.append(TranscriptSegment(start=float(start or 0.0), end=float(end), text=text_value))
    return _merge_subtitle_segments(segments)


def _parse_youtube_json3(events: list[dict[str, Any]]) -> list[TranscriptSegment]:
    segments: list[TranscriptSegment] = []
    for event in events:
        if not isinstance(event, dict) or "segs" not in event:
            continue
        start = float(event.get("tStartMs") or 0) / 1000.0
        duration = float(event.get("dDurationMs") or 2000) / 1000.0
        parts = []
        for seg in event.get("segs") or []:
            if isinstance(seg, dict):
                parts.append(seg.get("utf8") or "")
        text_value = _clean_subtitle_text("".join(parts))
        if text_value:
            segments.append(TranscriptSegment(start=start, end=start + duration, text=text_value))
    return _merge_subtitle_segments(segments)


def _parse_timed_text(text: str) -> list[TranscriptSegment]:
    text = re.sub(r"^\ufeff", "", text)
    xml_segments = _parse_xml_text_segments(text)
    if xml_segments and "-->" not in text:
        return _merge_subtitle_segments(xml_segments)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</?p[^>]*>", "\n", text, flags=re.I)
    text = re.sub(r"</?span[^>]*>", "", text, flags=re.I)
    blocks = re.split(r"\n\s*\n", text)
    segments: list[TranscriptSegment] = []
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        timing_index = next((index for index, line in enumerate(lines) if "-->" in line), None)
        if timing_index is None:
            segments.extend(_parse_xml_text_segments(block))
            continue
        start, end = _parse_time_range(lines[timing_index])
        if start is None or end is None:
            continue
        text_lines = lines[timing_index + 1 :]
        text_value = _clean_subtitle_text(" ".join(text_lines))
        if text_value:
            segments.append(TranscriptSegment(start=start, end=end, text=text_value))
    return _merge_subtitle_segments(segments)


def _parse_xml_text_segments(text: str) -> list[TranscriptSegment]:
    segments: list[TranscriptSegment] = []
    for match in re.finditer(r"<text\b([^>]*)>(.*?)</text>", text, flags=re.I | re.S):
        attrs = match.group(1)
        start = _xml_attr_float(attrs, "start")
        if start is None:
            start = _xml_attr_float(attrs, "t")
        if start is None:
            continue
        duration = _xml_attr_float(attrs, "dur")
        end = _xml_attr_float(attrs, "end")
        if end is None:
            end = start + (duration or 2.0)
        text_value = _clean_subtitle_text(match.group(2))
        if text_value:
            segments.append(TranscriptSegment(start=start, end=end, text=text_value))
    return segments


def _xml_attr_float(attrs: str, name: str) -> float | None:
    match = re.search(rf"\b{name}\s*=\s*(['\"])(.*?)\1", attrs, flags=re.I)
    if not match:
        return None
    try:
        return float(match.group(2))
    except ValueError:
        return None


def _parse_time_range(line: str) -> tuple[float | None, float | None]:
    parts = line.split("-->", 1)
    if len(parts) != 2:
        return None, None
    return _parse_timestamp(parts[0]), _parse_timestamp(parts[1])


def _parse_timestamp(value: str) -> float | None:
    value = value.strip().split()[0].replace(",", ".")
    pieces = value.split(":")
    try:
        if len(pieces) == 3:
            hours, minutes, seconds = pieces
            return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        if len(pieces) == 2:
            minutes, seconds = pieces
            return int(minutes) * 60 + float(seconds)
        return float(value)
    except ValueError:
        return None


def _clean_subtitle_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\{\\[^}]+\}", "", text)
    text = text.replace("\r", "\n")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _merge_subtitle_segments(segments: list[TranscriptSegment]) -> list[TranscriptSegment]:
    cleaned: list[TranscriptSegment] = []
    previous_text = ""
    for segment in sorted(segments, key=lambda item: (item.start, item.end)):
        text = _clean_subtitle_text(segment.text)
        if not text or text == previous_text:
            continue
        cleaned.append(TranscriptSegment(start=segment.start, end=max(segment.end, segment.start + 0.1), text=text))
        previous_text = text
    return cleaned


def _float_first(item: dict[str, Any], keys: list[str], *, scale_ms_key: str | None = None) -> float | None:
    for key in keys:
        if key not in item or item[key] is None:
            continue
        try:
            value = float(item[key])
        except (TypeError, ValueError):
            continue
        if scale_ms_key and key == scale_ms_key:
            return value / 1000.0
        return value
    return None


def _load_state(job_dir: Path) -> dict:
    state_path = job_dir / "state.json"
    if not state_path.exists() or state_path.stat().st_size == 0:
        return {}
    return json.loads(state_path.read_text(encoding="utf-8"))


def _write_state(job_dir: Path, state: dict) -> None:
    write_text(job_dir / "state.json", json.dumps(state, ensure_ascii=False, indent=2))


def _mark_output(state: dict, key: str, path: Path) -> None:
    outputs = state.setdefault("outputs", {})
    outputs[key] = str(path)


def emit_progress(progress: ProgressCallback | None, status: str, message: str, **extra: Any) -> None:
    if not progress:
        return
    progress(status, message=message, **extra)


def simplify_segments(segments: list[TranscriptSegment]) -> list[TranscriptSegment]:
    return [
        TranscriptSegment(start=segment.start, end=segment.end, text=to_simplified(segment.text))
        for segment in segments
    ]


def _state_options(options: ProcessOptions) -> dict:
    return {
        "asr_model_size": options.asr_model_size,
        "asr_device": options.asr_device,
        "asr_compute_type": options.asr_compute_type,
        "asr_language": options.asr_language,
        "asr_beam_size": options.asr_beam_size,
        "chunk_minutes": options.chunk_minutes,
        "summarize": options.summarize,
        "translate_to": options.translate_to,
        "summary_model": options.summary_model,
        "translate_model": options.translate_model,
        "simplified_chinese": options.simplified_chinese,
    }


def _is_done(path: Path) -> bool:
    return path.exists() and path.stat().st_size > 0


def _gate(lock_like: Any | None):
    return lock_like if lock_like is not None else nullcontext()


def _ensure_not_stopped(should_stop: StopCheck | None) -> None:
    if should_stop and should_stop():
        raise ProcessingStopped("任务已停止")
