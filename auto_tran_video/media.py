from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import requests
from yt_dlp import YoutubeDL


@dataclass(frozen=True)
class DownloadedMedia:
    path: Path
    title: str
    video_id: str
    uploader: str | None = None
    duration: float | None = None
    webpage_url: str | None = None


@dataclass(frozen=True)
class DownloadedSubtitle:
    path: Path
    title: str
    video_id: str
    language: str
    ext: str
    automatic: bool
    uploader: str | None = None
    duration: float | None = None
    webpage_url: str | None = None


@dataclass(frozen=True)
class BilibiliVideo:
    url: str
    title: str
    video_id: str
    uploader: str | None = None


def extract_audio(source: Path, target_wav: Path) -> None:
    source = source.resolve()
    target_wav = target_wav.resolve()
    target_wav.parent.mkdir(parents=True, exist_ok=True)
    command = [
        find_ffmpeg(),
        "-y",
        "-i",
        str(source),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-f",
        "wav",
        str(target_wav),
    ]
    subprocess.run(command, check=True)


def transcode_audio(source: Path, target: Path, audio_format: Literal["m4a", "wav", "mp3"]) -> None:
    source = source.resolve()
    target = target.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    command = [
        find_ffmpeg(),
        "-y",
        "-i",
        str(source),
        "-vn",
    ]
    if audio_format == "m4a":
        command.extend(["-codec:a", "aac", "-b:a", "128k", "-f", "ipod"])
    elif audio_format == "wav":
        command.extend(["-ac", "1", "-ar", "16000", "-f", "wav"])
    elif audio_format == "mp3":
        command.extend(["-codec:a", "libmp3lame", "-b:a", "128k", "-f", "mp3"])
    else:
        raise ValueError(f"Unsupported audio format: {audio_format}")
    command.append(str(target))
    subprocess.run(command, check=True)


def split_audio(audio_path: Path, parts_dir: Path, *, chunk_seconds: int, force: bool = False) -> list[Path]:
    parts_dir.mkdir(parents=True, exist_ok=True)
    existing = sorted(parts_dir.glob("chunk_*.wav"))
    if existing and not force:
        return existing

    if force:
        for path in parts_dir.glob("chunk_*.wav"):
            path.unlink()

    pattern = parts_dir / "chunk_%05d.wav"
    command = [
        find_ffmpeg(),
        "-y",
        "-i",
        str(audio_path.resolve()),
        "-f",
        "segment",
        "-segment_time",
        str(chunk_seconds),
        "-reset_timestamps",
        "1",
        "-acodec",
        "pcm_s16le",
        str(pattern.resolve()),
    ]
    subprocess.run(command, check=True)
    chunks = sorted(parts_dir.glob("chunk_*.wav"))
    if not chunks:
        raise RuntimeError(f"No audio chunks were created in {parts_dir}")
    return chunks


def find_ffmpeg() -> str:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        return ffmpeg

    try:
        import imageio_ffmpeg
    except ImportError as exc:
        raise RuntimeError("ffmpeg not found. Install ffmpeg or run: pip install imageio-ffmpeg") from exc

    return imageio_ffmpeg.get_ffmpeg_exe()


def download_bilibili_audio(
    url: str,
    cache_dir: Path,
    *,
    cookies: str | None = None,
    cookies_from_browser: str | None = None,
) -> DownloadedMedia:
    return download_url_audio(url, cache_dir, cookies=cookies, cookies_from_browser=cookies_from_browser)


def download_url_audio(
    url: str,
    cache_dir: Path,
    *,
    cookies: str | None = None,
    cookies_from_browser: str | None = None,
) -> DownloadedMedia:
    cache_dir.mkdir(parents=True, exist_ok=True)
    options: dict[str, Any] = {
        "format": "bestaudio[ext=m4a]/bestaudio/best",
        "outtmpl": str(cache_dir / "%(extractor_key)s_%(id)s.%(ext)s"),
        "noplaylist": True,
        "quiet": False,
        "no_warnings": False,
        "ffmpeg_location": find_ffmpeg(),
    }
    if cookies:
        options["cookiefile"] = cookies
    if cookies_from_browser:
        options["cookiesfrombrowser"] = (cookies_from_browser,)

    try:
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
            path = Path(ydl.prepare_filename(info)).resolve()
    except Exception as exc:
        raise RuntimeError(
            "Failed to download audio. If the video requires login or cookies, "
            "pass cookies or close the browser before using cookies-from-browser."
        ) from exc

    return DownloadedMedia(
        path=path,
        title=info.get("title") or info.get("id") or "online-video",
        video_id=info.get("id") or "unknown",
        uploader=info.get("uploader"),
        duration=info.get("duration"),
        webpage_url=info.get("webpage_url") or url,
    )


def download_bilibili_video(
    url: str,
    target_dir: Path,
    *,
    cookies: str | None = None,
    cookies_from_browser: str | None = None,
) -> DownloadedMedia:
    return download_url_video(url, target_dir, cookies=cookies, cookies_from_browser=cookies_from_browser)


def download_url_video(
    url: str,
    target_dir: Path,
    *,
    cookies: str | None = None,
    cookies_from_browser: str | None = None,
) -> DownloadedMedia:
    target_dir.mkdir(parents=True, exist_ok=True)
    options: dict[str, Any] = {
        "format": "bv*+ba/bestvideo+bestaudio/best",
        "outtmpl": str(target_dir / "%(extractor_key)s_%(id)s.%(ext)s"),
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": False,
        "no_warnings": False,
        "ffmpeg_location": find_ffmpeg(),
    }
    if cookies:
        options["cookiefile"] = cookies
    if cookies_from_browser:
        options["cookiesfrombrowser"] = (cookies_from_browser,)

    try:
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
    except Exception as exc:
        raise RuntimeError(
            "Failed to download video. If the video requires login or cookies, "
            "pass cookies in the browser helper settings."
        ) from exc

    video_id = info.get("id") or "unknown"
    extractor_key = info.get("extractor_key")
    path = _find_downloaded_file(target_dir, video_id, preferred_suffix=".mp4", extractor_key=extractor_key)
    return DownloadedMedia(
        path=path,
        title=info.get("title") or video_id or "online-video",
        video_id=video_id,
        uploader=info.get("uploader"),
        duration=info.get("duration"),
        webpage_url=info.get("webpage_url") or url,
    )


def download_url_subtitle(
    url: str,
    target_dir: Path,
    *,
    preferred_languages: list[str],
    cookies: str | None = None,
    cookies_from_browser: str | None = None,
) -> DownloadedSubtitle | None:
    target_dir.mkdir(parents=True, exist_ok=True)
    options: dict[str, Any] = {
        "skip_download": True,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
    }
    if cookies:
        options["cookiefile"] = cookies
    if cookies_from_browser:
        options["cookiesfrombrowser"] = (cookies_from_browser,)

    try:
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception:
        return None
    if not info:
        return None

    subtitle = _choose_subtitle(info, preferred_languages)
    if not subtitle:
        return None

    language, entry, automatic = subtitle
    ext = _subtitle_ext(entry)
    video_id = info.get("id") or "unknown"
    extractor_key = info.get("extractor_key") or "Video"
    target = target_dir / f"{slugify(extractor_key)}_{slugify(video_id)}.{slugify(language)}.{ext}"

    try:
        if entry.get("data") is not None:
            data = entry["data"]
            if isinstance(data, bytes):
                target.write_bytes(data)
            else:
                write_text(target, str(data))
        else:
            subtitle_url = entry.get("url")
            if not subtitle_url:
                return None
            headers = dict(entry.get("http_headers") or info.get("http_headers") or {})
            headers.setdefault(
                "User-Agent",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
            )
            response = requests.get(subtitle_url, headers=headers, timeout=30)
            response.raise_for_status()
            target.write_bytes(response.content)
    except Exception:
        return None

    if not target.exists() or target.stat().st_size == 0:
        return None

    return DownloadedSubtitle(
        path=target.resolve(),
        title=info.get("title") or video_id or "online-video",
        video_id=video_id,
        language=language,
        ext=ext,
        automatic=automatic,
        uploader=info.get("uploader"),
        duration=info.get("duration"),
        webpage_url=info.get("webpage_url") or url,
    )


def _find_downloaded_file(target_dir: Path, video_id: str, *, preferred_suffix: str, extractor_key: str | None = None) -> Path:
    candidates = []
    if extractor_key:
        candidates.append(target_dir / f"{extractor_key}_{video_id}{preferred_suffix}")
    candidates.append(target_dir / f"{video_id}{preferred_suffix}")
    for preferred in candidates:
        if preferred.exists():
            return preferred.resolve()

    patterns = [f"*_{video_id}.*", f"{video_id}.*"]
    matches = sorted(path for pattern in patterns for path in target_dir.glob(pattern) if path.is_file())
    if not matches:
        raise RuntimeError(f"Downloaded file was not found for {video_id}.")
    mp4_matches = [path for path in matches if path.suffix.lower() == preferred_suffix.lower()]
    return (mp4_matches[0] if mp4_matches else matches[0]).resolve()


def _choose_subtitle(info: dict[str, Any], preferred_languages: list[str]) -> tuple[str, dict[str, Any], bool] | None:
    manual = info.get("subtitles") or {}
    automatic = info.get("automatic_captions") or {}
    for languages, source, is_auto in [
        (preferred_languages, manual, False),
        (preferred_languages, automatic, True),
        (list(manual.keys()), manual, False),
        (list(automatic.keys()), automatic, True),
    ]:
        for language in languages:
            entries = _find_subtitle_entries(source, language)
            entry = _choose_subtitle_format(entries)
            if entry:
                return language, entry, is_auto
    return None


def _find_subtitle_entries(source: dict[str, Any], language: str) -> list[dict[str, Any]]:
    if language in source:
        return source.get(language) or []
    normalized = _normalize_language(language)
    for candidate, entries in source.items():
        if _normalize_language(candidate) == normalized:
            return entries or []
    for candidate, entries in source.items():
        if _normalize_language(candidate).startswith(normalized) or normalized.startswith(_normalize_language(candidate)):
            return entries or []
    return []


def _choose_subtitle_format(entries: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not entries:
        return None
    preferred_exts = ["json", "json3", "srt", "vtt", "srv3", "ttml", "ass"]
    for ext in preferred_exts:
        for entry in entries:
            if _subtitle_ext(entry) == ext and (entry.get("url") or entry.get("data") is not None):
                return entry
    for entry in entries:
        if entry.get("url") or entry.get("data") is not None:
            return entry
    return None


def _subtitle_ext(entry: dict[str, Any]) -> str:
    ext = str(entry.get("ext") or "").lower().strip(".")
    if ext in {"json", "json3", "srt", "vtt", "srv3", "ttml", "ass"}:
        return ext
    url = str(entry.get("url") or "")
    match = re.search(r"[?&]fmt=([a-z0-9]+)", url)
    if match:
        return match.group(1).lower()
    return ext or "vtt"


def _normalize_language(language: str) -> str:
    value = language.lower().replace("_", "-")
    aliases = {
        "zh-cn": "zh-hans",
        "zh-sg": "zh-hans",
        "zh": "zh-hans",
        "zh-tw": "zh-hant",
        "zh-hk": "zh-hant",
    }
    return aliases.get(value, value)


def fetch_bilibili_user_videos(
    url: str,
    *,
    limit: int,
    cookies: str | None = None,
    cookies_from_browser: str | None = None,
) -> tuple[str, list[BilibiliVideo]]:
    options = {
        "extract_flat": "in_playlist",
        "ignoreerrors": True,
        "playlistend": limit,
        "quiet": False,
        "skip_download": True,
    }
    if cookies:
        options["cookiefile"] = cookies
    if cookies_from_browser:
        options["cookiesfrombrowser"] = (cookies_from_browser,)

    try:
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        raise RuntimeError(
            "Failed to fetch Bilibili uploader videos. Bilibili may have blocked the request; "
            "try again later, pass --cookies, or close the browser before using --cookies-from-browser."
        ) from exc
    if not info:
        raise RuntimeError(
            "Failed to fetch Bilibili uploader videos. Bilibili may have blocked the request; "
            "try again later, or pass --cookies / --cookies-from-browser."
        )

    uploader = info.get("uploader") or info.get("channel") or info.get("title") or "bilibili-up"
    videos: list[BilibiliVideo] = []
    for entry in info.get("entries") or []:
        if not entry:
            continue
        video_id = entry.get("id") or entry.get("url") or "unknown"
        title = entry.get("title") or video_id
        webpage_url = entry.get("webpage_url") or entry.get("url") or ""
        if not webpage_url.startswith("http"):
            webpage_url = f"https://www.bilibili.com/video/{video_id}/"
        videos.append(BilibiliVideo(url=webpage_url, title=title, video_id=video_id, uploader=uploader))

    if not videos:
        raise RuntimeError(
            "No videos were found for this Bilibili uploader. If the space page is blocked, "
            "try --cookies or --cookies-from-browser."
        )

    return uploader, videos[:limit]


def stable_job_dir(output_dir: Path, title: str) -> Path:
    job_dir = output_dir / slugify(title)
    job_dir.mkdir(parents=True, exist_ok=True)
    return job_dir


def make_job_dir(output_dir: Path, title: str) -> Path:
    safe_title = slugify(title)
    job_dir = output_dir / safe_title
    output_dir.mkdir(parents=True, exist_ok=True)
    if not job_dir.exists():
        job_dir.mkdir(parents=True)
        return job_dir
    if job_dir.is_dir() and not (job_dir / "transcript.txt").exists():
        return job_dir

    index = 2
    while True:
        candidate = output_dir / f"{safe_title}-{index}"
        if not candidate.exists():
            candidate.mkdir(parents=True)
            return candidate
        index += 1


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def slugify(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value)
    value = re.sub(r"\s+", " ", value).strip()
    value = value.rstrip(". ")
    return value[:90] or "video"
