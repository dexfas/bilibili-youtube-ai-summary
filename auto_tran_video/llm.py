from __future__ import annotations

import json

import requests

CHUNK_SUMMARY = "\u672c\u6bb5\u6458\u8981"
KEY_CONTENT = "\u5173\u952e\u5185\u5bb9"
ACTIONABLE_INFO = "\u53ef\u6267\u884c\u4fe1\u606f"
FINAL_SUMMARY = "\u603b\u7ed3"
CHAPTER_FLOW = "\u7ae0\u8282\u8109\u7edc"
KEY_POINTS = "\u5173\u952e\u89c2\u70b9"
ACTION_ADVICE = "\u884c\u52a8\u5efa\u8bae"
REVIEW_SHORT = "\u9002\u5408\u590d\u4e60\u7684\u77ed\u7248"


def summarize_with_ollama(transcript: str, *, base_url: str, model: str) -> str:
    chunks = split_text(transcript, max_chars=2500)
    partials = [
        _ask_ollama(
            base_url=base_url,
            model=model,
            prompt=build_chunk_prompt(chunk, index, len(chunks)),
        )
        for index, chunk in enumerate(chunks, start=1)
    ]

    if len(partials) == 1:
        return partials[0]

    return _ask_ollama(
        base_url=base_url,
        model=model,
        prompt=build_final_prompt("\n\n".join(partials)),
    )


def translate_with_ollama(
    transcript: str,
    *,
    target_language: str,
    base_url: str,
    model: str,
) -> str:
    chunks = split_text(transcript, max_chars=2500)
    translated_chunks = [
        _ask_ollama(
            base_url=base_url,
            model=model,
            prompt=build_translate_prompt(chunk, target_language, index, len(chunks)),
        )
        for index, chunk in enumerate(chunks, start=1)
    ]
    return "\n\n".join(translated_chunks)


def translate_titles_with_ollama(
    titles: list[str],
    *,
    target_language: str,
    base_url: str,
    model: str,
) -> list[dict[str, str]]:
    cleaned = [title.strip() for title in titles if title and title.strip()]
    results: list[dict[str, str]] = []
    for chunk in _chunk_items(cleaned, max_items=8):
        answer = _ask_ollama(
            base_url=base_url,
            model=model,
            prompt=build_title_translate_prompt(chunk, target_language),
            num_ctx=2048,
            num_predict=max(220, min(700, len(chunk) * 80)),
            read_timeout=90,
        )
        results.extend(_parse_title_translation(answer, chunk))
    return results


def split_text(text: str, *, max_chars: int) -> list[str]:
    paragraphs = [line.strip() for line in text.splitlines() if line.strip()]
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for paragraph in paragraphs:
        if current and current_len + len(paragraph) + 1 > max_chars:
            chunks.append("\n".join(current))
            current = []
            current_len = 0
        current.append(paragraph)
        current_len += len(paragraph) + 1

    if current:
        chunks.append("\n".join(current))

    return chunks or [text]


def build_chunk_prompt(chunk: str, index: int, total: int) -> str:
    return f"""You are a careful video-note assistant.

Process transcript part {index}/{total}.
Output Simplified Chinese Markdown only.
Be concise and do not invent facts that are not in the transcript.

Required sections:
## {CHUNK_SUMMARY}
- 3 to 6 bullet points

## {KEY_CONTENT}
- important facts, claims, terms, tools, examples

## {ACTIONABLE_INFO}
- steps, recommendations, conclusions, or todos

Transcript:
{chunk}
"""


def build_final_prompt(partials: str) -> str:
    return f"""Merge these partial notes into one concise Simplified Chinese Markdown note.

Do not invent facts. Preserve important names, data, and claims from the partial notes.

Required sections:
## {FINAL_SUMMARY}
## {CHAPTER_FLOW}
## {KEY_POINTS}
## {ACTION_ADVICE}
## {REVIEW_SHORT}

Partial notes:
{partials}
"""


def build_translate_prompt(chunk: str, target_language: str, index: int, total: int) -> str:
    return f"""You are a professional transcript translator.

Translate part {index}/{total} of this video transcript into {target_language}.

Rules:
- Output only the translation.
- Do not summarize, analyze, explain, or add new facts.
- Keep the meaning faithful and natural.
- Use readable short paragraphs.
- Keep common proper nouns in their standard form.

Transcript:
{chunk}

Translation:
"""


def build_title_translate_prompt(titles: list[str], target_language: str) -> str:
    payload = json.dumps(titles, ensure_ascii=False, indent=2)
    return f"""Translate these video titles into {target_language}.

Rules:
- Output a JSON array only.
- Keep the same order and number of items.
- Each item must have "source" and "translation".
- Do not add explanations or Markdown.
- Use concise Simplified Chinese when the target language is Chinese.
- Keep names, brands, and game titles in their common form.

Titles:
{payload}

JSON:
"""


def _chunk_items(items: list[str], *, max_items: int) -> list[list[str]]:
    return [items[index : index + max_items] for index in range(0, len(items), max_items)]


def _parse_title_translation(answer: str, sources: list[str]) -> list[dict[str, str]]:
    parsed: object | None = None
    text = answer.strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = text[text.find("[") : text.rfind("]") + 1] if "[" in text and "]" in text else ""
        if match:
            try:
                parsed = json.loads(match)
            except json.JSONDecodeError:
                parsed = None

    if isinstance(parsed, dict) and isinstance(parsed.get("items"), list):
        parsed = parsed["items"]

    if isinstance(parsed, list):
        items: list[dict[str, str]] = []
        for index, source in enumerate(sources):
            candidate = parsed[index] if index < len(parsed) else {}
            if isinstance(candidate, dict):
                translation = str(candidate.get("translation") or candidate.get("title") or "").strip()
            else:
                translation = str(candidate or "").strip()
            items.append({"source": source, "translation": translation or source})
        return items

    lines = [line.strip(" -0123456789.、") for line in text.splitlines() if line.strip()]
    return [
        {"source": source, "translation": lines[index] if index < len(lines) and lines[index] else source}
        for index, source in enumerate(sources)
    ]


def _ask_ollama(
    *,
    base_url: str,
    model: str,
    prompt: str,
    num_ctx: int = 4096,
    num_predict: int = 900,
    read_timeout: float | None = None,
) -> str:
    response = requests.post(
        f"{base_url.rstrip('/')}/api/generate",
        json={
            "model": model,
            "prompt": prompt,
            "stream": True,
            "think": False,
            "options": {
                "temperature": 0.2,
                "num_ctx": num_ctx,
                "num_predict": num_predict,
            },
        },
        stream=True,
        timeout=(30, read_timeout),
    )
    response.raise_for_status()

    parts: list[str] = []
    for line in response.iter_lines(decode_unicode=True):
        if not line:
            continue
        payload = json.loads(line)
        parts.append(payload.get("response", ""))
        if payload.get("done"):
            break
    result = "".join(parts).strip()
    if not result:
        raise RuntimeError(f"Ollama returned an empty response for model: {model}")
    return result
