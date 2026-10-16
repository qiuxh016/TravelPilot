"""Local OCR adapter for temporary user supplied travel screenshots."""

from functools import lru_cache
from threading import Lock

_OCR_LOCK = Lock()


@lru_cache(maxsize=1)
def _engine():
    from rapidocr import RapidOCR

    return RapidOCR()


def recognize_text(image_bytes: bytes) -> tuple[str, float | None]:
    """Recognize Chinese/English text locally; never sends image bytes to an LLM."""
    with _OCR_LOCK:
        result = _engine()(image_bytes)
    raw_lines = result.txts if result.txts is not None else []
    raw_scores = result.scores if result.scores is not None else []
    lines = [str(text).strip() for text in raw_lines if str(text).strip()]
    scores = [float(score) for score in raw_scores]
    confidence = sum(scores) / len(scores) if scores else None
    return "\n".join(lines)[:20_000], confidence
