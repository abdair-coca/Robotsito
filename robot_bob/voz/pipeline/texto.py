"""Partición del texto del LLM en frases para hablarlas una por una."""
from __future__ import annotations

import re
from typing import Optional
from config import SENTENCE_MIN_CHARS


_SENT_RE = re.compile(r'[.!?¡¿…\n]+[\s"\')\]]*')


def _split_sentence(buf: str) -> tuple[Optional[str], str]:
    if len(buf) < SENTENCE_MIN_CHARS:
        return None, buf
    m = _SENT_RE.search(buf, SENTENCE_MIN_CHARS - 1)
    if m is None:
        return None, buf
    return buf[:m.end()].strip(), buf[m.end():]
