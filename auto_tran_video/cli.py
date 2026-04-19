from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import AppConfig, load_config
from .media import (
    download_url_audio,
    download_url_subtitle,
    fetch_bilibili_user_videos,
    slugify,
    stable_job_dir,
    write_text,
)
from .processing import ProcessOptions, process_one_video, process_subtitle_transcript, summarize_existing


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="auto-tran-video",
        description="Local and online video speech-to-text with optional Ollama summary.",
    )
    parser.add_argument("--config", default="config.yaml", help="Config file path.")
    parser.add_argument("--output", help="Output directory. Overrides config.")
    parser.add_argument("--cache", help="Cache directory. Overrides config.")
    parser.add_argument("--model-size", help="faster-whisper model size, e.g. small, medium, large-v3.")
    parser.add_argument("--device", help="auto, cpu, or cuda.")
    parser.add_argument("--compute-type", help="int8, float16, or float32.")
    parser.add_argument("--language", help="ASR language code. Use zh for Chinese or en for English.")
    parser.add_argument("--beam-size", type=int, help="ASR beam size. 1 is faster, 5 is more accurate.")
    parser.add_argument("--chunk-minutes", type=float, default=10.0, help="Audio chunk size for resumable ASR.")
    parser.add_argument("--force", action="store_true", help="Regenerate outputs even if non-empty files exist.")
    parser.add_argument("--clean-cache", action="store_true", help="Delete downloaded cache audio after success.")
    parser.add_argument("--summarize", action="store_true", help="Ask local Ollama to summarize transcript.")
    parser.add_argument("--translate-to", help="Translate transcript with local Ollama, e.g. Chinese or English.")
    parser.add_argument("--english-cn", action="store_true", help="English video workflow: English transcript, Chinese translation, Chinese summary.")
    parser.add_argument("--summary-model", help="Ollama model for summaries, e.g. qwen-summary:1.5b.")
    parser.add_argument("--translate-model", help="Ollama model for translation, e.g. qwen3:8b.")
    parser.add_argument("--ollama-model", help="Compatibility alias for --summary-model.")
    parser.add_argument("--no-subtitles", action="store_true", help="Do not use existing subtitles; always run ASR.")

    subparsers = parser.add_subparsers(dest="command", required=True)

    local = subparsers.add_parser("local", help="Transcribe a local video/audio file.")
    local.add_argument("path", help="Local video/audio file path.")
    local.add_argument("--title", help="Optional output folder title.")

    bilibili = subparsers.add_parser("bilibili", help="Transcribe one online video URL.")
    bilibili.add_argument("url", help="Bilibili or YouTube video URL.")
    bilibili.add_argument("--cookies", help="Optional cookies.txt for videos that require login.")
    bilibili.add_argument("--cookies-from-browser", help="Read cookies from browser, e.g. chrome, edge, firefox.")

    batch = subparsers.add_parser("batch", help="Transcribe multiple online video URLs.")
    batch.add_argument("urls", nargs="*", help="Bilibili or YouTube video URLs.")
    batch.add_argument("--file", help="Text file with one or more online video URLs.")
    batch.add_argument("--cookies", help="Optional cookies.txt for videos that require login.")
    batch.add_argument("--cookies-from-browser", help="Read cookies from browser, e.g. chrome, edge, firefox.")

    up = subparsers.add_parser("up", help="Transcribe a Bilibili uploader's videos.")
    up.add_argument("url", help="Bilibili uploader space URL.")
    up.add_argument("--limit", type=int, default=20, help="Maximum videos to process.")
    up.add_argument("--cookies", help="Optional cookies.txt for videos that require login.")
    up.add_argument("--cookies-from-browser", help="Read cookies from browser, e.g. chrome, edge, firefox.")

    summarize = subparsers.add_parser("summarize", help="Summarize an existing transcript.txt or output folder.")
    summarize.add_argument("path", help="Path to transcript.txt or an output folder containing transcript.txt.")

    serve = subparsers.add_parser("serve", help="Start the local browser helper API.")
    serve.add_argument("--host", default="127.0.0.1", help="HTTP host. Keep 127.0.0.1 for local-only access.")
    serve.add_argument("--port", type=int, default=8765, help="HTTP port.")

    return parser


def main() -> None:
    args = build_parser().parse_args(_normalize_args(sys.argv[1:]))
    config = _merge_config(load_config(Path(args.config)), args)

    if args.command == "serve":
        from .server import run_server

        run_server(config=config, host=args.host, port=args.port)
        return

    if args.command == "summarize":
        options = _build_process_options(config, args)
        summarize_existing(path=Path(args.path), options=options)
        return

    options = _build_process_options(config, args)

    if args.command == "local":
        source = Path(args.path).expanduser().resolve()
        if not source.exists():
            raise SystemExit(f"File not found: {source}")
        job_dir = stable_job_dir(config.output_dir, args.title or source.stem)
        metadata = {"source_type": "local", "source": str(source), "title": args.title or source.stem}
        print(f"Output: {job_dir}")
        process_one_video(source_path=source, job_dir=job_dir, metadata=metadata, options=options)
        return

    if args.command == "bilibili":
        _process_online_url(
            url=args.url,
            source_type="bilibili",
            output_dir=config.output_dir,
            config=config,
            options=options,
            cookies=args.cookies,
            cookies_from_browser=args.cookies_from_browser,
            prefer_subtitles=not args.no_subtitles,
        )
        return

    if args.command == "batch":
        _process_batch(args=args, config=config, options=options)
        return

    if args.command == "up":
        _process_up(args=args, config=config, options=options)
        return

    raise SystemExit("Unknown command.")


def _process_online_url(
    *,
    url: str,
    source_type: str,
    output_dir: Path,
    config: AppConfig,
    options: ProcessOptions,
    cookies: str | None,
    cookies_from_browser: str | None,
    prefer_subtitles: bool,
    uploader: str | None = None,
) -> dict[str, Any]:
    if prefer_subtitles:
        print("Checking existing subtitles...")
        subtitle = download_url_subtitle(
            url,
            config.cache_dir / "subtitles",
            preferred_languages=_subtitle_languages_for_options(options),
            cookies=cookies,
            cookies_from_browser=cookies_from_browser,
        )
        if subtitle:
            job_dir = stable_job_dir(output_dir, f"{subtitle.video_id}_{subtitle.title}")
            metadata = {
                "source_type": source_type,
                "source": url,
                "title": subtitle.title,
                "id": subtitle.video_id,
                "uploader": subtitle.uploader or uploader,
                "duration": subtitle.duration,
                "webpage_url": subtitle.webpage_url,
                "subtitle_language": subtitle.language,
                "subtitle_ext": subtitle.ext,
                "subtitle_automatic": subtitle.automatic,
                "downloaded_subtitle": str(subtitle.path),
            }
            print(f"Output: {job_dir}")
            print(f"Using existing subtitle: {subtitle.language} ({'auto' if subtitle.automatic else 'manual'})")
            process_subtitle_transcript(
                subtitle_path=subtitle.path,
                job_dir=job_dir,
                metadata=metadata,
                options=options,
                subtitle_language=subtitle.language,
                subtitle_ext=subtitle.ext,
            )
            return {
                "url": url,
                "status": "done",
                "output": str(job_dir),
                "id": subtitle.video_id,
                "title": subtitle.title,
                "transcript_source": "subtitle",
            }

    media = download_url_audio(
        url,
        config.cache_dir,
        cookies=cookies,
        cookies_from_browser=cookies_from_browser,
    )
    job_dir = stable_job_dir(output_dir, f"{media.video_id}_{media.title}")
    metadata = {
        "source_type": source_type,
        "source": url,
        "title": media.title,
        "id": media.video_id,
        "uploader": media.uploader or uploader,
        "duration": media.duration,
        "webpage_url": media.webpage_url,
        "downloaded_audio": str(media.path),
    }
    print(f"Output: {job_dir}")
    process_one_video(source_path=media.path, job_dir=job_dir, metadata=metadata, options=options, cache_path=media.path)
    return {
        "url": url,
        "status": "done",
        "output": str(job_dir),
        "id": media.video_id,
        "title": media.title,
        "transcript_source": "asr",
    }


def _process_up(*, args: argparse.Namespace, config: AppConfig, options: ProcessOptions) -> None:
    try:
        uploader, videos = fetch_bilibili_user_videos(
            args.url,
            limit=args.limit,
            cookies=args.cookies,
            cookies_from_browser=args.cookies_from_browser,
        )
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
    up_dir = stable_job_dir(config.output_dir, uploader)
    print(f"Uploader: {uploader}")
    print(f"Videos: {len(videos)}")

    failures: list[dict] = []
    for index, video in enumerate(videos, start=1):
        print(f"[{index}/{len(videos)}] {video.title}")
        job_dir = stable_job_dir(up_dir, f"{video.video_id}_{video.title}")
        try:
            _process_online_url(
                url=video.url,
                source_type="bilibili_up",
                output_dir=up_dir,
                config=config,
                options=options,
                cookies=args.cookies,
                cookies_from_browser=args.cookies_from_browser,
                prefer_subtitles=not args.no_subtitles,
                uploader=uploader,
            )
        except Exception as exc:
            failures.append({"url": video.url, "title": video.title, "error": str(exc)})
            write_text(
                job_dir / "state.json",
                json.dumps(
                    {
                        "metadata": {"source": video.url, "title": video.title, "uploader": uploader},
                        "error": str(exc),
                        "traceback": traceback.format_exc(),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            )
            print(f"Failed: {exc}")

    batch_state = {
        "uploader": uploader,
        "source": args.url,
        "limit": args.limit,
        "videos": len(videos),
        "failures": failures,
    }
    write_text(up_dir / "batch_state.json", json.dumps(batch_state, ensure_ascii=False, indent=2))
    print(f"Batch done. Failures: {len(failures)}")


def _process_batch(*, args: argparse.Namespace, config: AppConfig, options: ProcessOptions) -> None:
    urls = _collect_batch_urls(args.urls, args.file)
    if not urls:
        raise SystemExit("No online video URLs provided. Put URLs after batch or pass --file urls.txt.")

    print(f"Batch videos: {len(urls)}")
    results: list[dict] = []
    failures: list[dict] = []
    for index, url in enumerate(urls, start=1):
        print(f"[{index}/{len(urls)}] {url}")
        try:
            result = _process_online_url(
                url=url,
                source_type="online_batch",
                output_dir=config.output_dir,
                config=config,
                options=options,
                cookies=args.cookies,
                cookies_from_browser=args.cookies_from_browser,
                prefer_subtitles=not args.no_subtitles,
            )
            results.append(result)
        except Exception as exc:
            failures.append({"url": url, "error": str(exc), "traceback": traceback.format_exc()})
            results.append({"url": url, "status": "failed", "error": str(exc)})
            print(f"Failed: {exc}")

    batch_state = {
        "source_type": "bilibili_batch",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "videos": len(urls),
        "done": sum(1 for item in results if item.get("status") == "done"),
        "failures": failures,
        "results": results,
    }
    state_path = config.output_dir / "batch_state.json"
    write_text(state_path, json.dumps(batch_state, ensure_ascii=False, indent=2))
    print(f"Batch done. Success: {batch_state['done']}. Failures: {len(failures)}")
    print(f"Batch state: {state_path}")


def _collect_batch_urls(urls: list[str], file_path: str | None) -> list[str]:
    collected: list[str] = []
    collected.extend(urls)
    if file_path:
        path = Path(file_path).expanduser()
        if not path.exists():
            raise SystemExit(f"URL file not found: {path}")
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            collected.extend(line.split())

    unique: list[str] = []
    seen: set[str] = set()
    for url in collected:
        url = url.strip()
        if not url or url in seen:
            continue
        seen.add(url)
        unique.append(url)
    return unique


def _merge_config(config: AppConfig, args: argparse.Namespace) -> AppConfig:
    summary_model = args.summary_model or args.ollama_model or config.summary_model
    return AppConfig(
        asr_model_size=args.model_size or config.asr_model_size,
        asr_device=args.device or config.asr_device,
        asr_compute_type=args.compute_type or config.asr_compute_type,
        asr_language=args.language if args.language is not None else config.asr_language,
        asr_beam_size=args.beam_size or config.asr_beam_size,
        ollama_base_url=config.ollama_base_url,
        summary_model=summary_model,
        translate_model=args.translate_model or config.translate_model,
        cache_dir=Path(args.cache or config.cache_dir),
        output_dir=Path(args.output or config.output_dir),
    )


def _build_process_options(config: AppConfig, args: argparse.Namespace) -> ProcessOptions:
    language = config.asr_language
    translate_to = args.translate_to
    summarize = args.summarize
    if args.english_cn:
        language = "en"
        translate_to = translate_to or "Chinese"
        summarize = True

    return ProcessOptions(
        asr_model_size=config.asr_model_size,
        asr_device=config.asr_device,
        asr_compute_type=config.asr_compute_type,
        asr_language=language,
        asr_beam_size=config.asr_beam_size,
        chunk_minutes=args.chunk_minutes,
        force=args.force,
        clean_cache=args.clean_cache,
        summarize=summarize,
        translate_to=translate_to,
        english_cn=args.english_cn,
        ollama_base_url=config.ollama_base_url,
        summary_model=config.summary_model,
        translate_model=config.translate_model,
    )


def _subtitle_languages_for_options(options: ProcessOptions) -> list[str]:
    if options.english_cn or options.asr_language == "en":
        return ["en", "en-US", "en-GB", "English"]
    return [
        "zh-Hans",
        "zh-CN",
        "zh",
        "zh-Hant",
        "zh-TW",
        "zh-HK",
        "Chinese",
        "en",
        "en-US",
    ]


def _normalize_args(argv: list[str]) -> list[str]:
    value_options = {
        "--config",
        "--output",
        "--cache",
        "--model-size",
        "--device",
        "--compute-type",
        "--language",
        "--beam-size",
        "--chunk-minutes",
        "--translate-to",
        "--summary-model",
        "--translate-model",
        "--ollama-model",
    }
    flag_options = {"--force", "--clean-cache", "--summarize", "--english-cn", "--no-subtitles"}
    subcommands = {"local", "bilibili", "batch", "up", "summarize", "serve"}

    try:
        command_index = next(index for index, token in enumerate(argv) if token in subcommands)
    except StopIteration:
        return argv

    before = argv[:command_index]
    after = argv[command_index:]
    moved: list[str] = []
    kept: list[str] = []
    index = 0
    while index < len(after):
        token = after[index]
        option_name = token.split("=", 1)[0]
        if option_name in flag_options:
            moved.append(token)
            index += 1
        elif option_name in value_options:
            moved.append(token)
            if "=" not in token and index + 1 < len(after):
                moved.append(after[index + 1])
                index += 2
            else:
                index += 1
        else:
            kept.append(token)
            index += 1

    return before + moved + kept
