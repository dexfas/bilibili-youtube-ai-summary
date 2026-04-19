from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class AppConfig:
    asr_model_size: str = "small"
    asr_device: str = "cpu"
    asr_compute_type: str = "int8"
    asr_language: str | None = "zh"
    asr_beam_size: int = 1
    ollama_base_url: str = "http://localhost:11434"
    summary_model: str = "qwen-summary:1.5b"
    translate_model: str = "qwen3:8b"
    cache_dir: Path = Path("cache")
    output_dir: Path = Path("output")


def load_config(path: Path) -> AppConfig:
    if not path.exists():
        return AppConfig()

    with path.open("r", encoding="utf-8") as file:
        raw = yaml.safe_load(file) or {}

    asr = raw.get("asr", {})
    ollama = raw.get("ollama", {})
    paths = raw.get("paths", {})

    return AppConfig(
        asr_model_size=asr.get("model_size", "small"),
        asr_device=asr.get("device", "cpu"),
        asr_compute_type=asr.get("compute_type", "int8"),
        asr_language=asr.get("language", "zh"),
        asr_beam_size=asr.get("beam_size", 1),
        ollama_base_url=ollama.get("base_url", "http://localhost:11434"),
        summary_model=ollama.get("summary_model", ollama.get("model", "qwen-summary:1.5b")),
        translate_model=ollama.get("translate_model", "qwen3:8b"),
        cache_dir=Path(paths.get("cache_dir", "cache")),
        output_dir=Path(paths.get("output_dir", "output")),
    )
