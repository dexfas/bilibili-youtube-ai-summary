from __future__ import annotations

import json
import shutil
import subprocess
import threading
import traceback
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .config import AppConfig
from .llm import translate_titles_with_ollama
from .media import download_url_audio, download_url_subtitle, download_url_video, slugify, stable_job_dir, transcode_audio, write_text
from .processing import ProcessOptions, ProcessingStopped, process_one_video, process_subtitle_transcript, summarize_existing
from .text import to_simplified

PRESETS: dict[str, dict[str, Any]] = {
    "fastest": {"asr_model_size": "tiny", "asr_device": "cpu", "asr_compute_type": "int8", "asr_beam_size": 1},
    "balanced": {"asr_model_size": "small", "asr_device": "cpu", "asr_compute_type": "int8", "asr_beam_size": 1},
    "quality": {"asr_model_size": "medium", "asr_device": "cpu", "asr_compute_type": "int8", "asr_beam_size": 3},
}

ACTIVE_ITEM_STATUSES = {
    "starting",
    "checking_subtitles",
    "using_subtitles",
    "downloading",
    "downloading_video",
    "saving_audio",
    "extracting_audio",
    "transcribing",
    "translating",
    "summarizing",
    "stopping",
}
FINAL_ITEM_STATUSES = {"done", "failed", "cancelled"}


class BrowserSettings(BaseModel):
    workflow: Literal["transcribe", "summarize", "english_cn", "audio_only", "video_download"] = "summarize"
    audio_format: Literal["m4a", "wav", "mp3"] = "m4a"
    speed_preset: Literal["fastest", "balanced", "quality"] = "balanced"
    concurrency_preset: Literal["stable", "fast", "custom"] = "stable"
    download_concurrency: int = Field(default=1, ge=1, le=5)
    asr_concurrency: int = Field(default=1, ge=1, le=2)
    ollama_concurrency: int = Field(default=1, ge=1, le=2)
    asr_model_size: str = "small"
    asr_device: str = "cpu"
    asr_compute_type: str = "int8"
    asr_language: str | None = "zh"
    asr_beam_size: int = Field(default=1, ge=1, le=10)
    chunk_minutes: float = Field(default=10.0, ge=1.0, le=120.0)
    force: bool = False
    clean_cache: bool = False
    summary_model: str = "qwen-summary:1.5b"
    translate_model: str = "qwen3:8b"
    title_translate_model: str = "qwen3.5:2b"
    translate_to: str | None = None
    cookies: str | None = None
    cookies_from_browser: str | None = None
    output_dir: str | None = None
    cache_dir: str | None = None
    batch_name: str | None = None
    auto_scan: bool = True
    auto_select_new: bool = True
    prefer_subtitles: bool = True
    simplified_chinese: bool = True


class BrowserVideoInput(BaseModel):
    url: str
    title: str | None = None
    id: str | None = None
    duration: str | None = None


class JobSubmitRequest(BaseModel):
    urls: list[str] = Field(default_factory=list)
    items: list[BrowserVideoInput] = Field(default_factory=list)
    settings: BrowserSettings | None = None


class PathRequest(BaseModel):
    path: str


class TitleTranslateRequest(BaseModel):
    titles: list[str] = Field(default_factory=list)
    target_language: str = "Chinese"
    model: str | None = None


class RetryItemRequest(BaseModel):
    url: str


class JobManager:
    def __init__(self, config: AppConfig):
        self.config = config
        self.root = config.output_dir / "browser_jobs"
        self.jobs_path = self.root / "jobs.json"
        self.settings_path = self.root / "settings.json"
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.worker_started = False
        self.jobs: list[dict[str, Any]] = self._load_jobs()
        self.settings = self._load_settings()
        self._reset_interrupted_jobs()

    def start_worker(self) -> None:
        with self.lock:
            if self.worker_started:
                return
            self.worker_started = True
        thread = threading.Thread(target=self._worker_loop, name="browser-job-scheduler", daemon=True)
        thread.start()

    def get_settings(self) -> dict[str, Any]:
        with self.lock:
            return deepcopy(self.settings)

    def save_settings(self, settings: BrowserSettings) -> dict[str, Any]:
        with self.lock:
            self.settings = _model_dump(settings)
            self._persist_settings_locked()
            return deepcopy(self.settings)

    def submit(
        self,
        urls: list[str],
        settings: BrowserSettings | None,
        items: list[BrowserVideoInput] | None = None,
    ) -> dict[str, Any]:
        inputs = _dedupe_inputs(urls, items or [])
        if not inputs:
            raise ValueError("No video URLs provided.")

        effective_settings = _model_dump(settings) if settings else self.get_settings()
        now = _now()
        job_id = uuid.uuid4().hex[:12]
        batch_dir = self._build_batch_dir(effective_settings, job_id)
        batch_dir.mkdir(parents=True, exist_ok=True)
        job = {
            "id": job_id,
            "type": "browser_batch",
            "status": "queued",
            "created_at": now,
            "updated_at": now,
            "started_at": None,
            "finished_at": None,
            "cancel_requested": False,
            "pause_requested": False,
            "paused_at": None,
            "active_item_id": None,
            "active_item_ids": [],
            "settings": effective_settings,
            "batch_dir": str(batch_dir),
            "counts": {"total": len(inputs), "done": 0, "failed": 0, "cancelled": 0},
            "items": [
                {
                    "url": item["url"],
                    "status": "queued",
                    "title": item.get("title"),
                    "id": item.get("id"),
                    "duration": item.get("duration"),
                    "output": None,
                    "output_dir": None,
                    "error": None,
                    "error_stage": None,
                    "stop_requested": False,
                    "progress": _progress("queued", "等待处理", percent=0),
                    "artifact_paths": {},
                    "transcript_source": "unknown",
                    "updated_at": now,
                }
                for item in inputs
            ],
        }
        with self.lock:
            self.jobs.append(job)
            self._persist_jobs_locked()
            result = deepcopy(job)
        self.wake.set()
        return result

    def resummarize(self, job_id: str) -> dict[str, Any]:
        with self.lock:
            source_job = self._find_job_locked(job_id)
            source_items = [item for item in source_job["items"] if item.get("output")]
            if not source_items:
                raise ValueError("No completed output folders were found for this job.")
            settings = deepcopy(source_job.get("settings") or self.settings)
            settings["workflow"] = "summarize"
            settings["force"] = True
            new_id = uuid.uuid4().hex[:12]
            now = _now()
            job = {
                "id": new_id,
                "type": "resummarize",
                "status": "queued",
                "created_at": now,
                "updated_at": now,
                "started_at": None,
                "finished_at": None,
                "cancel_requested": False,
                "pause_requested": False,
                "paused_at": None,
                "active_item_id": None,
                "active_item_ids": [],
                "settings": settings,
                "source_job_id": job_id,
                "batch_dir": source_job.get("batch_dir"),
                "counts": {"total": len(source_items), "done": 0, "failed": 0, "cancelled": 0},
                "items": [
                    {
                        "url": item.get("url"),
                        "status": "queued",
                        "title": item.get("title"),
                        "id": item.get("id"),
                        "duration": item.get("duration"),
                        "output": item.get("output"),
                        "output_dir": item.get("output_dir") or item.get("output"),
                        "error": None,
                        "error_stage": None,
                        "stop_requested": False,
                        "progress": _progress("queued", "等待重新总结", percent=0),
                        "artifact_paths": item.get("artifact_paths", {}),
                        "transcript_source": item.get("transcript_source", "unknown"),
                        "updated_at": now,
                    }
                    for item in source_items
                ],
            }
            self.jobs.append(job)
            self._persist_jobs_locked()
            result = deepcopy(job)
        self.wake.set()
        return result

    def list_jobs(self) -> list[dict[str, Any]]:
        with self.lock:
            return deepcopy(list(reversed(self.jobs)))

    def get_job(self, job_id: str) -> dict[str, Any]:
        with self.lock:
            return deepcopy(self._find_job_locked(job_id))

    def pause(self, job_id: str) -> dict[str, Any]:
        with self.lock:
            job = self._find_job_locked(job_id)
            if job["status"] in {"done", "failed", "cancelled"}:
                return deepcopy(job)
            job["pause_requested"] = True
            if job["status"] == "queued":
                job["status"] = "paused"
                job["paused_at"] = _now()
            elif job["status"] == "running":
                job["status"] = "pausing"
            self._touch_locked(job)
            self._persist_jobs_locked()
            result = deepcopy(job)
        self.wake.set()
        return result

    def resume(self, job_id: str) -> dict[str, Any]:
        with self.lock:
            job = self._find_job_locked(job_id)
            job["pause_requested"] = False
            job["paused_at"] = None
            if job["status"] == "paused":
                job["status"] = "queued"
            elif job["status"] == "pausing":
                job["status"] = "running"
            self._touch_locked(job)
            self._persist_jobs_locked()
            result = deepcopy(job)
        self.wake.set()
        return result

    def stop_current(self, job_id: str) -> dict[str, Any]:
        with self.lock:
            job = self._find_job_locked(job_id)
            active_indexes = self._active_indexes_locked(job)
            if not active_indexes:
                return deepcopy(job)
            index = active_indexes[0]
            item = job["items"][index]
            item["stop_requested"] = True
            item["status"] = "stopping"
            item["progress"] = _progress("stopping", "已请求停止，等待当前阶段结束", percent=item.get("progress", {}).get("percent"))
            item["updated_at"] = _now()
            self._refresh_active_locked(job)
            self._touch_locked(job)
            self._persist_jobs_locked()
            result = deepcopy(job)
        self.wake.set()
        return result

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self.lock:
            job = self._find_job_locked(job_id)
            job["cancel_requested"] = True
            if job["status"] == "queued":
                job["status"] = "cancelled"
                job["finished_at"] = _now()
            for item in job["items"]:
                if item["status"] == "queued":
                    item["status"] = "cancelled"
                    item["progress"] = _progress("cancelled", "已取消", percent=100)
                    item["updated_at"] = _now()
            self._refresh_counts_locked(job)
            self._refresh_active_locked(job)
            self._touch_locked(job)
            self._persist_jobs_locked()
            result = deepcopy(job)
        self.wake.set()
        return result

    def retry_item(self, job_id: str, url: str) -> dict[str, Any]:
        cleaned_url = url.strip()
        if not cleaned_url:
            raise ValueError("Missing item URL.")
        with self.lock:
            job = self._find_job_locked(job_id)
            item = next((candidate for candidate in job.get("items", []) if candidate.get("url") == cleaned_url), None)
            if not item:
                raise ValueError("No item with this URL was found in the job.")
            if item.get("status") not in {"failed", "cancelled"}:
                raise ValueError("Only failed or cancelled items can be retried.")
            settings = deepcopy(job.get("settings") or self.settings)
            settings["force"] = True
            job["settings"] = settings
            job["status"] = "queued"
            job["finished_at"] = None
            job["cancel_requested"] = False
            job["pause_requested"] = False
            job["paused_at"] = None
            item["status"] = "queued"
            item["error"] = None
            item["error_stage"] = None
            item["stop_requested"] = False
            item["progress"] = _progress("queued", "等待重试", percent=0)
            item["artifact_paths"] = {}
            item["transcript_source"] = "unknown"
            item["updated_at"] = _now()
            self._refresh_counts_locked(job)
            self._refresh_active_locked(job)
            self._touch_locked(job)
            self._persist_jobs_locked()
            result = deepcopy(job)
        self.wake.set()
        return result

    def _worker_loop(self) -> None:
        while True:
            job_id = self._next_job_id()
            if not job_id:
                self.wake.wait(timeout=1.0)
                self.wake.clear()
                continue
            self._run_job(job_id)

    def _run_job(self, job_id: str) -> None:
        self._update_job(job_id, status="running", started_at=_now(), finished_at=None)
        job_snapshot = self.get_job(job_id)
        settings = BrowserSettings(**job_snapshot["settings"])
        limits = _concurrency_limits(settings)
        download_gate = threading.BoundedSemaphore(limits["download"])
        asr_gate = threading.BoundedSemaphore(limits["asr"])
        llm_gate = threading.BoundedSemaphore(limits["ollama"])
        max_workers = max(limits.values())
        futures: dict[Any, int] = {}

        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix=f"job-{job_id}") as executor:
            while True:
                if self._is_cancel_requested(job_id):
                    self._cancel_remaining(job_id)
                    break

                if not futures and self._next_queued_index(job_id) is None:
                    break

                if self._is_pause_requested(job_id) and not futures:
                    self._set_job_paused(job_id)
                    while self._is_pause_requested(job_id) and not self._is_cancel_requested(job_id):
                        self.wake.wait(timeout=0.5)
                        self.wake.clear()
                    if self._is_cancel_requested(job_id):
                        self._cancel_remaining(job_id)
                        break
                    self._update_job(job_id, status="running", paused_at=None)

                while not self._is_cancel_requested(job_id) and not self._is_pause_requested(job_id) and len(futures) < max_workers:
                    next_index = self._next_queued_index(job_id)
                    if next_index is None:
                        break
                    self._update_item_progress(job_id, next_index, status="starting", message="等待可用资源", percent=1)
                    future = executor.submit(
                        self._run_item_guarded,
                        job_id,
                        next_index,
                        settings,
                        job_snapshot.get("batch_dir"),
                        download_gate,
                        asr_gate,
                        llm_gate,
                        job_snapshot.get("type"),
                    )
                    futures[future] = next_index

                if not futures:
                    if self._next_queued_index(job_id) is None:
                        break
                    self.wake.wait(timeout=0.25)
                    self.wake.clear()
                    continue

                done, _ = wait(list(futures.keys()), timeout=0.25, return_when=FIRST_COMPLETED)
                for future in done:
                    futures.pop(future, None)
                    future.result()

        self._finish_job(job_id)

    def _run_item_guarded(
        self,
        job_id: str,
        index: int,
        settings: BrowserSettings,
        batch_dir: str | None,
        download_gate: threading.BoundedSemaphore,
        asr_gate: threading.BoundedSemaphore,
        llm_gate: threading.BoundedSemaphore,
        job_type: str | None,
    ) -> None:
        try:
            item = self._get_item_snapshot(job_id, index)
            if job_type == "resummarize":
                self._resummarize_item(job_id, index, item, settings, llm_gate)
            else:
                self._process_item(job_id, index, item["url"], settings, batch_dir, download_gate, asr_gate, llm_gate)
        except ProcessingStopped as exc:
            self._update_item_progress(
                job_id,
                index,
                status="cancelled",
                message=str(exc),
                percent=100,
                error=None,
                error_stage="stopped",
            )
        except Exception as exc:
            self._update_item_progress(
                job_id,
                index,
                status="failed",
                message=str(exc),
                percent=100,
                error=str(exc),
                error_stage=self._get_item_status(job_id, index),
                traceback=traceback.format_exc(),
            )

    def _process_item(
        self,
        job_id: str,
        index: int,
        url: str,
        settings: BrowserSettings,
        batch_dir: str | None,
        download_gate: threading.BoundedSemaphore,
        asr_gate: threading.BoundedSemaphore,
        llm_gate: threading.BoundedSemaphore,
    ) -> None:
        output_dir = Path(batch_dir) if batch_dir else Path(settings.output_dir or self.config.output_dir)
        cache_dir = Path(settings.cache_dir) if settings.cache_dir else self.config.cache_dir
        if settings.workflow == "video_download":
            self._download_video_item(job_id, index, url, settings, output_dir, cache_dir, download_gate)
            return

        self._raise_if_stopped(job_id, index)
        if settings.prefer_subtitles and settings.workflow != "audio_only":
            self._update_item_progress(job_id, index, status="checking_subtitles", message="正在查找已有字幕", percent=4, error=None)
            with download_gate:
                self._raise_if_stopped(job_id, index)
                subtitle = download_url_subtitle(
                    url,
                    cache_dir / "subtitles",
                    preferred_languages=_subtitle_languages(settings),
                    cookies=_blank_to_none(settings.cookies),
                    cookies_from_browser=_blank_to_none(settings.cookies_from_browser),
                )
            self._raise_if_stopped(job_id, index)
            if subtitle:
                job_dir = stable_job_dir(output_dir, f"{subtitle.video_id}_{subtitle.title}")
                metadata = {
                    "source_type": _source_type_from_url(url),
                    "source": url,
                    "title": subtitle.title,
                    "id": subtitle.video_id,
                    "uploader": subtitle.uploader,
                    "duration": subtitle.duration,
                    "webpage_url": subtitle.webpage_url,
                    "subtitle_language": subtitle.language,
                    "subtitle_ext": subtitle.ext,
                    "subtitle_automatic": subtitle.automatic,
                    "downloaded_subtitle": str(subtitle.path),
                }
                self._update_item_progress(
                    job_id,
                    index,
                    status="using_subtitles",
                    message=f"已找到字幕：{subtitle.language}",
                    percent=24,
                    title=subtitle.title,
                    id=subtitle.video_id,
                    duration=_format_duration(subtitle.duration) or self._get_item_snapshot(job_id, index).get("duration"),
                    output=str(job_dir),
                    output_dir=str(job_dir),
                    transcript_source="auto_subtitle" if subtitle.automatic else "manual_subtitle",
                )
                process_subtitle_transcript(
                    subtitle_path=subtitle.path,
                    job_dir=job_dir,
                    metadata=metadata,
                    options=_settings_to_options(settings, self.config),
                    subtitle_language=subtitle.language,
                    subtitle_ext=subtitle.ext,
                    progress=lambda status, **extra: self._update_item_progress(job_id, index, status=status, **extra),
                    should_stop=lambda: self._item_stop_requested(job_id, index),
                    llm_gate=llm_gate,
                )
                self._update_item_progress(
                    job_id,
                    index,
                    status="done",
                    message="已使用字幕处理完成",
                    percent=100,
                    output=str(job_dir),
                    output_dir=str(job_dir),
                    error=None,
                    artifact_paths=_collect_artifacts(job_dir),
                    transcript_source="auto_subtitle" if subtitle.automatic else "manual_subtitle",
                )
                return

        self._update_item_progress(job_id, index, status="downloading", message="正在下载音频", percent=8, error=None)
        with download_gate:
            self._raise_if_stopped(job_id, index)
            media = download_url_audio(
                url,
                cache_dir,
                cookies=_blank_to_none(settings.cookies),
                cookies_from_browser=_blank_to_none(settings.cookies_from_browser),
            )
        self._raise_if_stopped(job_id, index)

        job_dir = stable_job_dir(output_dir, f"{media.video_id}_{media.title}")
        metadata = {
            "source_type": _source_type_from_url(url),
            "source": url,
            "title": media.title,
            "id": media.video_id,
            "uploader": media.uploader,
            "duration": media.duration,
            "webpage_url": media.webpage_url,
            "downloaded_audio": str(media.path),
        }
        write_text(job_dir / "metadata.json", json.dumps(metadata, ensure_ascii=False, indent=2))
        self._update_item_progress(
            job_id,
            index,
            status="saving_audio" if settings.workflow == "audio_only" else "transcribing",
            message="正在保存音频" if settings.workflow == "audio_only" else "正在准备转写",
            percent=20,
            title=media.title,
            id=media.video_id,
            duration=_format_duration(media.duration) or self._get_item_snapshot(job_id, index).get("duration"),
            output=str(job_dir),
            output_dir=str(job_dir),
            transcript_source="unknown" if settings.workflow == "audio_only" else "asr",
        )

        if settings.workflow == "audio_only":
            self._raise_if_stopped(job_id, index)
            audio_path = self._save_audio_artifact(media.path, job_dir, settings.audio_format)
            self._update_item_progress(
                job_id,
                index,
                status="done",
                message=f"已保存音频：{audio_path.name}",
                percent=100,
                output=str(job_dir),
                output_dir=str(job_dir),
                error=None,
                artifact_paths={"audio": str(audio_path)},
            )
            return

        process_one_video(
            source_path=media.path,
            job_dir=job_dir,
            metadata=metadata,
            options=_settings_to_options(settings, self.config),
            cache_path=media.path,
            progress=lambda status, **extra: self._update_item_progress(job_id, index, status=status, **extra),
            should_stop=lambda: self._item_stop_requested(job_id, index),
            asr_gate=asr_gate,
            llm_gate=llm_gate,
        )
        self._update_item_progress(
            job_id,
            index,
            status="done",
            message="处理完成",
            percent=100,
            output=str(job_dir),
            output_dir=str(job_dir),
            error=None,
            artifact_paths=_collect_artifacts(job_dir),
            transcript_source="asr",
        )

    def _download_video_item(
        self,
        job_id: str,
        index: int,
        url: str,
        settings: BrowserSettings,
        output_dir: Path,
        cache_dir: Path,
        download_gate: threading.BoundedSemaphore,
    ) -> None:
        self._raise_if_stopped(job_id, index)
        self._update_item_progress(job_id, index, status="downloading_video", message="正在下载视频", percent=8, error=None)
        with download_gate:
            self._raise_if_stopped(job_id, index)
            media = download_url_video(
                url,
                cache_dir / "videos",
                cookies=_blank_to_none(settings.cookies),
                cookies_from_browser=_blank_to_none(settings.cookies_from_browser),
            )
        self._raise_if_stopped(job_id, index)

        job_dir = stable_job_dir(output_dir, f"{media.video_id}_{media.title}")
        video_path = job_dir / "video.mp4"
        job_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(media.path, video_path)
        metadata = {
            "source_type": f"{_source_type_from_url(url)}_video",
            "source": url,
            "title": media.title,
            "id": media.video_id,
            "uploader": media.uploader,
            "duration": media.duration,
            "webpage_url": media.webpage_url,
            "downloaded_video": str(media.path),
        }
        write_text(job_dir / "metadata.json", json.dumps(metadata, ensure_ascii=False, indent=2))
        self._update_item_progress(
            job_id,
            index,
            status="done",
            message="视频下载完成",
            percent=100,
            title=media.title,
            id=media.video_id,
            duration=_format_duration(media.duration) or self._get_item_snapshot(job_id, index).get("duration"),
            output=str(job_dir),
            output_dir=str(job_dir),
            error=None,
            artifact_paths={"video": str(video_path)},
        )

    def _save_audio_artifact(self, source: Path, job_dir: Path, audio_format: str) -> Path:
        if audio_format == "m4a":
            target = job_dir / "audio.m4a"
            if source.suffix.lower() == ".m4a":
                shutil.copy2(source, target)
            else:
                transcode_audio(source, target, audio_format="m4a")
            return target
        if audio_format in {"wav", "mp3"}:
            target = job_dir / f"audio.{audio_format}"
            transcode_audio(source, target, audio_format=audio_format)  # type: ignore[arg-type]
            return target
        raise RuntimeError(f"Unsupported audio format: {audio_format}")

    def _resummarize_item(
        self,
        job_id: str,
        index: int,
        item: dict[str, Any],
        settings: BrowserSettings,
        llm_gate: threading.BoundedSemaphore,
    ) -> None:
        output = item.get("output")
        if not output:
            raise RuntimeError("This item has no output folder to summarize.")
        self._raise_if_stopped(job_id, index)
        self._update_item_progress(job_id, index, status="summarizing", message="正在重新总结", percent=88, error=None)
        summarize_existing(
            path=Path(output),
            options=_settings_to_options(settings, self.config),
            progress=lambda status, **extra: self._update_item_progress(job_id, index, status=status, **extra),
            should_stop=lambda: self._item_stop_requested(job_id, index),
            llm_gate=llm_gate,
        )
        self._update_item_progress(
            job_id,
            index,
            status="done",
            message="重新总结完成",
            percent=100,
            output=output,
            output_dir=output,
            error=None,
            artifact_paths=_collect_artifacts(Path(output)),
        )

    def _next_job_id(self) -> str | None:
        with self.lock:
            for job in self.jobs:
                if job["status"] == "queued" and not job.get("cancel_requested") and not job.get("pause_requested"):
                    return job["id"]
            return None

    def _next_queued_index(self, job_id: str) -> int | None:
        with self.lock:
            job = self._find_job_locked(job_id)
            for index, item in enumerate(job["items"]):
                if item["status"] == "queued":
                    return index
            return None

    def _is_cancel_requested(self, job_id: str) -> bool:
        with self.lock:
            return bool(self._find_job_locked(job_id).get("cancel_requested"))

    def _is_pause_requested(self, job_id: str) -> bool:
        with self.lock:
            return bool(self._find_job_locked(job_id).get("pause_requested"))

    def _item_stop_requested(self, job_id: str, index: int) -> bool:
        with self.lock:
            job = self._find_job_locked(job_id)
            if job.get("cancel_requested"):
                return True
            try:
                item = job["items"][index]
            except IndexError:
                return True
            return bool(item.get("stop_requested"))

    def _raise_if_stopped(self, job_id: str, index: int) -> None:
        if self._item_stop_requested(job_id, index):
            raise ProcessingStopped("任务已停止")

    def _get_item_status(self, job_id: str, index: int) -> str | None:
        with self.lock:
            job = self._find_job_locked(job_id)
            try:
                return job["items"][index].get("status")
            except IndexError:
                return None

    def _get_item_snapshot(self, job_id: str, index: int) -> dict[str, Any]:
        with self.lock:
            return deepcopy(self._find_job_locked(job_id)["items"][index])

    def _set_job_paused(self, job_id: str) -> None:
        with self.lock:
            job = self._find_job_locked(job_id)
            if job.get("pause_requested") and job["status"] != "paused":
                job["status"] = "paused"
                job["paused_at"] = _now()
                self._touch_locked(job)
                self._persist_jobs_locked()

    def _cancel_remaining(self, job_id: str) -> None:
        with self.lock:
            job = self._find_job_locked(job_id)
            for item in job["items"]:
                if item["status"] == "queued":
                    item["status"] = "cancelled"
                    item["progress"] = _progress("cancelled", "已取消", percent=100)
                    item["updated_at"] = _now()
            self._refresh_counts_locked(job)
            self._refresh_active_locked(job)
            self._touch_locked(job)
            self._persist_jobs_locked()

    def _finish_job(self, job_id: str) -> None:
        with self.lock:
            job = self._find_job_locked(job_id)
            self._refresh_counts_locked(job)
            self._refresh_active_locked(job)
            statuses = {item["status"] for item in job["items"]}
            if statuses == {"cancelled"}:
                job["status"] = "cancelled"
            elif any(status == "failed" for status in statuses):
                job["status"] = "failed"
            elif any(status not in FINAL_ITEM_STATUSES for status in statuses):
                job["status"] = "running"
                return
            else:
                job["status"] = "done"
            job["pause_requested"] = False
            job["finished_at"] = _now()
            self._touch_locked(job)
            self._persist_jobs_locked()

    def _update_job(self, job_id: str, **fields: Any) -> None:
        with self.lock:
            job = self._find_job_locked(job_id)
            job.update(fields)
            self._touch_locked(job)
            self._persist_jobs_locked()

    def _update_item(self, job_id: str, index: int, **fields: Any) -> None:
        with self.lock:
            job = self._find_job_locked(job_id)
            item = job["items"][index]
            item.update(fields)
            item["updated_at"] = _now()
            self._refresh_counts_locked(job)
            self._refresh_active_locked(job)
            self._touch_locked(job)
            self._persist_jobs_locked()

    def _update_item_progress(
        self,
        job_id: str,
        index: int,
        *,
        status: str,
        message: str | None = None,
        percent: int | None = None,
        chunk_index: int | None = None,
        chunk_total: int | None = None,
        artifact_paths: dict[str, str] | None = None,
        **fields: Any,
    ) -> None:
        progress = _progress(
            status,
            message or status,
            percent=percent,
            chunk_index=chunk_index,
            chunk_total=chunk_total,
        )
        if artifact_paths is not None:
            fields["artifact_paths"] = artifact_paths
        self._update_item(job_id, index, status=status, progress=progress, **fields)

    def _find_job_locked(self, job_id: str) -> dict[str, Any]:
        for job in self.jobs:
            if job["id"] == job_id:
                return job
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")

    def _active_indexes_locked(self, job: dict[str, Any]) -> list[int]:
        return [index for index, item in enumerate(job.get("items", [])) if item.get("status") in ACTIVE_ITEM_STATUSES]

    def _refresh_active_locked(self, job: dict[str, Any]) -> None:
        active = self._active_indexes_locked(job)
        job["active_item_ids"] = [job["items"][index].get("id") or job["items"][index].get("url") for index in active]
        job["active_item_id"] = job["active_item_ids"][0] if job["active_item_ids"] else None

    def _refresh_counts_locked(self, job: dict[str, Any]) -> None:
        items = job["items"]
        job["counts"] = {
            "total": len(items),
            "done": sum(1 for item in items if item["status"] == "done"),
            "failed": sum(1 for item in items if item["status"] == "failed"),
            "cancelled": sum(1 for item in items if item["status"] == "cancelled"),
        }

    def _touch_locked(self, job: dict[str, Any]) -> None:
        job["updated_at"] = _now()

    def _load_jobs(self) -> list[dict[str, Any]]:
        if not self.jobs_path.exists() or self.jobs_path.stat().st_size == 0:
            return []
        return json.loads(self.jobs_path.read_text(encoding="utf-8"))

    def _load_settings(self) -> dict[str, Any]:
        if self.settings_path.exists() and self.settings_path.stat().st_size > 0:
            stored = json.loads(self.settings_path.read_text(encoding="utf-8"))
            return _model_dump(BrowserSettings(**stored))
        return _model_dump(_default_settings(self.config))

    def _build_batch_dir(self, settings: dict[str, Any], job_id: str) -> Path:
        output_dir = Path(settings.get("output_dir") or self.config.output_dir)
        batch_name = _blank_to_none(settings.get("batch_name"))
        if batch_name:
            folder = slugify(batch_name)
        else:
            folder = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{job_id}"
        return output_dir / "browser_batches" / folder

    def _reset_interrupted_jobs(self) -> None:
        changed = False
        with self.lock:
            for job in self.jobs:
                job.setdefault("pause_requested", False)
                job.setdefault("paused_at", None)
                job.setdefault("active_item_id", None)
                job.setdefault("active_item_ids", [])
                if job["status"] in {"running", "pausing"}:
                    job["status"] = "queued"
                    job["started_at"] = None
                    job["finished_at"] = None
                    job["cancel_requested"] = False
                    job["pause_requested"] = False
                    changed = True
                for item in job.get("items", []):
                    item.setdefault("duration", None)
                    item.setdefault("output_dir", item.get("output"))
                    item.setdefault("stop_requested", False)
                    item.setdefault("artifact_paths", {})
                    item.setdefault("transcript_source", "unknown")
                    if item.get("status") in ACTIVE_ITEM_STATUSES:
                        item["status"] = "queued"
                        item["error"] = None
                        item["stop_requested"] = False
                        changed = True
                self._refresh_counts_locked(job)
                self._refresh_active_locked(job)
            if changed:
                self._persist_jobs_locked()

    def _persist_jobs_locked(self) -> None:
        write_text(self.jobs_path, json.dumps(self.jobs, ensure_ascii=False, indent=2))

    def _persist_settings_locked(self) -> None:
        write_text(self.settings_path, json.dumps(self.settings, ensure_ascii=False, indent=2))


def create_app(config: AppConfig) -> FastAPI:
    app = FastAPI(title="auto-tran-video local service")
    manager = JobManager(config)
    app.state.manager = manager
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "https://www.bilibili.com",
            "https://search.bilibili.com",
            "https://www.youtube.com",
            "https://m.youtube.com",
        ],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.on_event("startup")
    def _startup() -> None:
        manager.start_worker()

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "service": "auto-tran-video", "version": 9}

    @app.get("/api/settings")
    def get_settings() -> dict[str, Any]:
        return {"settings": manager.get_settings(), "presets": PRESETS}

    @app.get("/api/ollama/models")
    def ollama_models() -> dict[str, Any]:
        return list_ollama_models(config)

    @app.post("/api/translate-titles")
    def translate_titles(payload: TitleTranslateRequest) -> dict[str, Any]:
        titles = _unique_titles(payload.titles)
        if not titles:
            return {"ok": True, "items": []}
        settings = manager.get_settings()
        model = _blank_to_none(payload.model) or _blank_to_none(settings.get("title_translate_model")) or "qwen3.5:2b"
        try:
            items = translate_titles_with_ollama(
                titles,
                target_language=payload.target_language or "Chinese",
                base_url=config.ollama_base_url,
                model=model,
            )
            if (payload.target_language or "").strip().lower() in {"chinese", "zh", "simplified chinese"}:
                items = [
                    {"source": item["source"], "translation": to_simplified(item["translation"])}
                    for item in items
                ]
            return {"ok": True, "items": items}
        except Exception as exc:
            return {"ok": False, "items": [], "error": str(exc)}

    @app.post("/api/settings")
    def save_settings(settings: BrowserSettings) -> dict[str, Any]:
        return {"settings": manager.save_settings(settings), "presets": PRESETS}

    @app.post("/api/output-dir/pick")
    def pick_output_dir() -> dict[str, Any]:
        return pick_folder()

    @app.post("/api/open-path")
    def open_path(payload: PathRequest) -> dict[str, Any]:
        path = Path(payload.path).expanduser().resolve()
        if not path.exists():
            existing_parent = next((parent for parent in path.parents if parent.exists()), None)
            if not existing_parent:
                raise HTTPException(status_code=404, detail=f"Path not found: {path}")
            subprocess.Popen(["explorer", str(existing_parent)])
            return {"ok": True, "path": str(existing_parent), "requested_path": str(path), "fallback": True}
        target = path if path.is_dir() else path.parent
        subprocess.Popen(["explorer", str(target)])
        return {"ok": True, "path": str(target), "fallback": False}

    @app.post("/api/jobs")
    def submit_job(payload: JobSubmitRequest) -> dict[str, Any]:
        try:
            job = manager.submit(payload.urls, payload.settings, payload.items)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"job": job}

    @app.get("/api/jobs")
    def list_jobs() -> dict[str, Any]:
        return {"jobs": manager.list_jobs()}

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, Any]:
        return {"job": manager.get_job(job_id)}

    @app.post("/api/jobs/{job_id}/pause")
    def pause_job(job_id: str) -> dict[str, Any]:
        return {"job": manager.pause(job_id)}

    @app.post("/api/jobs/{job_id}/resume")
    def resume_job(job_id: str) -> dict[str, Any]:
        return {"job": manager.resume(job_id)}

    @app.post("/api/jobs/{job_id}/stop-current")
    def stop_current_job(job_id: str) -> dict[str, Any]:
        return {"job": manager.stop_current(job_id)}

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str) -> dict[str, Any]:
        return {"job": manager.cancel(job_id)}

    @app.post("/api/jobs/{job_id}/retry-item")
    def retry_job_item(job_id: str, payload: RetryItemRequest) -> dict[str, Any]:
        try:
            return {"job": manager.retry_item(job_id, payload.url)}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/jobs/{job_id}/resummarize")
    def resummarize_job(job_id: str) -> dict[str, Any]:
        try:
            return {"job": manager.resummarize(job_id)}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return app


def run_server(*, config: AppConfig, host: str, port: int) -> None:
    import uvicorn

    app = create_app(config)
    uvicorn.run(app, host=host, port=port)


def list_ollama_models(config: AppConfig) -> dict[str, Any]:
    try:
        response = requests.get(f"{config.ollama_base_url.rstrip('/')}/api/tags", timeout=5)
        response.raise_for_status()
        payload = response.json()
        models = sorted({item.get("name") for item in payload.get("models", []) if item.get("name")})
        return {"ok": True, "models": models}
    except Exception as exc:
        return {"ok": False, "models": [], "error": str(exc)}


def pick_folder() -> dict[str, Any]:
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selected = filedialog.askdirectory(title="选择 auto-tran-video 输出目录")
        root.destroy()
    except Exception as exc:
        return {"ok": False, "path": None, "error": str(exc)}

    if not selected:
        return {"ok": False, "path": None, "cancelled": True}
    return {"ok": True, "path": selected}


def _settings_to_options(settings: BrowserSettings, config: AppConfig) -> ProcessOptions:
    summarize = settings.workflow in {"summarize", "english_cn"}
    translate_to = _blank_to_none(settings.translate_to)
    language = settings.asr_language
    if settings.workflow == "english_cn":
        language = "en"
        translate_to = "Chinese"

    return ProcessOptions(
        asr_model_size=_blank_to_none(settings.asr_model_size) or config.asr_model_size,
        asr_device=_blank_to_none(settings.asr_device) or config.asr_device,
        asr_compute_type=_blank_to_none(settings.asr_compute_type) or config.asr_compute_type,
        asr_language=_blank_to_none(language),
        asr_beam_size=settings.asr_beam_size or config.asr_beam_size,
        chunk_minutes=settings.chunk_minutes,
        force=settings.force,
        clean_cache=settings.clean_cache,
        summarize=summarize,
        translate_to=translate_to,
        english_cn=settings.workflow == "english_cn",
        ollama_base_url=config.ollama_base_url,
        summary_model=_blank_to_none(settings.summary_model) or config.summary_model,
        translate_model=_blank_to_none(settings.translate_model) or config.translate_model,
        simplified_chinese=settings.simplified_chinese,
    )


def _default_settings(config: AppConfig) -> BrowserSettings:
    return BrowserSettings(
        asr_model_size=config.asr_model_size,
        asr_device=config.asr_device,
        asr_compute_type=config.asr_compute_type,
        asr_language=config.asr_language,
        asr_beam_size=config.asr_beam_size,
        summary_model=config.summary_model,
        translate_model=config.translate_model,
        title_translate_model="qwen3.5:2b",
        output_dir=str(config.output_dir),
        cache_dir=str(config.cache_dir),
        simplified_chinese=True,
    )


def _dedupe_inputs(urls: list[str], items: list[BrowserVideoInput]) -> list[dict[str, str | None]]:
    merged: list[dict[str, str | None]] = []
    for item in items:
        if isinstance(item, dict):
            merged.append(
                {
                    "url": item.get("url"),
                    "title": item.get("title"),
                    "id": item.get("id"),
                    "duration": item.get("duration"),
                }
            )
            continue
        merged.append(
            {
                "url": item.url,
                "title": item.title,
                "id": item.id,
                "duration": item.duration,
            }
        )
    for url in urls:
        merged.append({"url": url, "title": None, "id": None, "duration": None})

    unique: list[dict[str, str | None]] = []
    seen: set[str] = set()
    for item in merged:
        cleaned = (item.get("url") or "").strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        item["url"] = cleaned
        unique.append(item)
    return unique


def _unique_titles(titles: list[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for title in titles:
        cleaned = " ".join(str(title or "").split())
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        unique.append(cleaned)
    return unique[:80]


def _progress(
    stage: str,
    message: str,
    *,
    percent: int | None = None,
    chunk_index: int | None = None,
    chunk_total: int | None = None,
) -> dict[str, Any]:
    return {
        "stage": stage,
        "message": message,
        "percent": percent,
        "chunk_index": chunk_index,
        "chunk_total": chunk_total,
    }


def _collect_artifacts(job_dir: Path) -> dict[str, str]:
    names = {
        "audio": ["audio.m4a", "audio.wav", "audio.mp3"],
        "video": ["video.mp4"],
        "source_subtitle": ["source_subtitle.json", "source_subtitle.json3", "source_subtitle.srt", "source_subtitle.vtt", "source_subtitle.srv3", "source_subtitle.ttml", "source_subtitle.ass"],
        "transcript": ["transcript.txt"],
        "srt": ["transcript.srt"],
        "json": ["transcript.json"],
        "translation": ["translation.zh.md"],
        "summary": ["summary.md"],
    }
    artifacts: dict[str, str] = {}
    for key, candidates in names.items():
        for name in candidates:
            path = job_dir / name
            if path.exists():
                artifacts[key] = str(path)
                break
    return artifacts


def _concurrency_limits(settings: BrowserSettings) -> dict[str, int]:
    if settings.concurrency_preset == "fast":
        return {"download": 3, "asr": 1, "ollama": 1}
    if settings.concurrency_preset == "custom":
        return {
            "download": _clamp(settings.download_concurrency, 1, 5),
            "asr": _clamp(settings.asr_concurrency, 1, 2),
            "ollama": _clamp(settings.ollama_concurrency, 1, 2),
        }
    return {"download": 1, "asr": 1, "ollama": 1}


def _subtitle_languages(settings: BrowserSettings) -> list[str]:
    if settings.workflow == "english_cn" or _blank_to_none(settings.asr_language) == "en":
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


def _source_type_from_url(url: str) -> str:
    lowered = url.lower()
    if "youtube.com" in lowered or "youtu.be" in lowered:
        return "youtube_browser"
    if "bilibili.com" in lowered:
        return "bilibili_browser"
    return "browser_url"


def _format_duration(duration: float | int | None) -> str | None:
    if duration is None:
        return None
    try:
        seconds = int(float(duration))
    except (TypeError, ValueError):
        return None
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _model_dump(model: BaseModel) -> dict[str, Any]:
    return model.model_dump()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, int(value)))
