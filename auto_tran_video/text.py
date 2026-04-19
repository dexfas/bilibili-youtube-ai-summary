from __future__ import annotations

try:
    from opencc import OpenCC
except ImportError:  # pragma: no cover - dependency is listed, fallback keeps old installs usable.
    OpenCC = None  # type: ignore[assignment]

_T2S = OpenCC("t2s") if OpenCC else None


def to_simplified(text: str) -> str:
    if not text or _T2S is None:
        return text
    return _T2S.convert(text)
