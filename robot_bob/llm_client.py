"""
Cliente LLM multi-backend para Bob: Groq, Ollama, Gemini.
Cache LRU de respuestas para evitar tokens quemados en preguntas repetitivas.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from typing import Dict, Iterator, List, Optional

from groq import Groq
from rich.console import Console

from config import (
    GROQ_API_KEY, GROQ_LLM_MODEL, GROQ_STT_MODEL,
    TEMPERATURE, MAX_TOKENS, MAX_RETRIES,
    LLM_BACKEND, OLLAMA_BASE_URL, OLLAMA_MODEL,
    GEMINI_BASE_URL, GEMINI_MODEL, GEMINI_API_KEY,
)

console = Console()

# Cache global entre instancias (opcional, pero ahorra memoria)
_CACHE: OrderedDict = OrderedDict()
_CACHE_MAX = 64
_CACHE_TTL_S = 300       # 5 min para streaming
_CACHE_TTL_CHAT_S = 600  # 10 min para chat() no-streaming


class LLMClient:
    def __init__(self):
        self._client = Groq(api_key=GROQ_API_KEY)
        self._llm = self._client
        self._llm_extra: Dict = {}
        self._usando_ollama = False
        self._llm_model = GROQ_LLM_MODEL

        if LLM_BACKEND == "gemini":
            from openai import OpenAI
            self._llm = OpenAI(base_url=GEMINI_BASE_URL, api_key=GEMINI_API_KEY)
            self._llm_model = GEMINI_MODEL
            try:
                self._llm.models.list()
                self._llm_extra = {"reasoning_effort": "none"}
                console.print(f"[dim][llm] Gemini OK -> {self._llm_model}[/]")
            except Exception as e:
                console.print(f"[yellow][llm] Gemini no responde ({e}); fallback a Groq[/]")
                self._llm = self._client
                self._llm_model = GROQ_LLM_MODEL
        elif LLM_BACKEND == "ollama":
            from openai import OpenAI
            self._llm = OpenAI(base_url=OLLAMA_BASE_URL, api_key="ollama")
            self._llm_model = OLLAMA_MODEL
            try:
                self._llm.models.list()
                self._usando_ollama = True
                console.print(f"[dim][llm] Ollama OK -> {self._llm_model}[/]")
            except Exception as e:
                console.print(f"[yellow][llm] Ollama no responde ({e}); fallback a Groq[/]")
                self._llm = self._client
                self._llm_model = GROQ_LLM_MODEL
        else:
            console.print(f"[dim][llm] backend Groq -> {self._llm_model}[/]")

    @property
    def usando_ollama(self) -> bool:
        return self._usando_ollama

    @property
    def model(self) -> str:
        return self._llm_model

    @property
    def extra_params(self) -> Dict:
        return self._llm_extra

    @property
    def raw_client(self):
        return self._llm

    @property
    def is_groq(self) -> bool:
        return LLM_BACKEND not in ("ollama", "gemini")

    # ── Cache LRU ───────────────────────────────────────────────────────────

    @staticmethod
    def _req_hash(**kw) -> str:
        raw = json.dumps(kw, sort_keys=True, default=str)
        return hashlib.md5(raw.encode()).hexdigest()

    def _cache_get(self, key: str, ttl: float):
        if key not in _CACHE:
            return None
        val, ts = _CACHE[key]
        if time.monotonic() - ts > ttl:
            del _CACHE[key]
            return None
        _CACHE.move_to_end(key)
        return val

    def _cache_set(self, key: str, val) -> None:
        _CACHE[key] = (val, time.monotonic())
        while len(_CACHE) > _CACHE_MAX:
            _CACHE.popitem(last=False)

    def _build_kwargs(self, messages, **overrides) -> dict:
        kwargs = dict(
            model=overrides.get('model', self._llm_model),
            messages=messages,
            temperature=overrides.get('temperature', TEMPERATURE),
            max_tokens=overrides.get('max_tokens', MAX_TOKENS),
        )
        extra = overrides.get('extra') or self._llm_extra
        if extra:
            kwargs['extra'] = extra
        return kwargs

    # ── LLM calls ──────────────────────────────────────────────────────────

    def stream_chat(self, messages: List[Dict], **overrides) -> Iterator[str]:
        kwargs = self._build_kwargs(messages, **overrides)
        kwargs['stream'] = True
        ckey = self._req_hash(**kwargs)
        cached = self._cache_get(ckey, _CACHE_TTL_S)
        if cached is not None:
            console.print(f'[dim][llm] cache hit (stream, {len(cached)} chars)[/]')
            yield cached
            return

        try:
            stream = self._llm.chat.completions.create(**kwargs)
        except Exception as e:
            console.print(f'[red][voice] LLM error: {e}[/]')
            return

        buff: list[str] = []
        for chunk in stream:
            delta = (chunk.choices[0].delta.content or '') if chunk.choices else ''
            buff.append(delta)
            yield delta

        full = ''.join(buff)
        if full.strip():
            self._cache_set(ckey, full)

    def chat(self, messages: List[Dict], **overrides) -> Optional[str]:
        temp = overrides.get('temperature', 0.8)
        mtok = overrides.get('max_tokens', 60)
        extra = overrides.get('extra') or self._llm_extra
        model = overrides.get('model', self._llm_model)
        ckey = self._req_hash(model=model, messages=messages,
                              temperature=temp, max_tokens=mtok, extra=extra)
        cached = self._cache_get(ckey, _CACHE_TTL_CHAT_S)
        if cached is not None:
            console.print(f'[dim][llm] cache hit (chat, {len(cached)} chars)[/]')
            return cached

        try:
            resp = self._llm.chat.completions.create(
                model=model, messages=messages,
                temperature=temp, max_tokens=mtok,
                stream=False, **(extra or {}),
            )
            txt = (resp.choices[0].message.content or '').strip() or None
            if txt:
                self._cache_set(ckey, txt)
            return txt
        except Exception as e:
            console.print(f'[dim][llm] chat error: {e}[/]')
            return None

    def stt(self, wav_bytes: bytes) -> str:
        for attempt in range(MAX_RETRIES + 1):
            try:
                resp = self._client.audio.transcriptions.create(
                    file=('audio.wav', wav_bytes, 'audio/wav'),
                    model=GROQ_STT_MODEL,
                    language='es',
                    prompt=None,
                    temperature=0.0,
                    response_format='json',
                )
                return resp.text.strip()
            except Exception as e:
                if attempt == MAX_RETRIES:
                    console.print(f'[red][voice] STT error: {e}[/]')
                    return ''
                time.sleep(0.5)
        return ''

    def warmup(self) -> None:
        def _w():
            try:
                list(self._client.models.list(timeout=3.0))
            except Exception:
                pass
        threading.Thread(target=_w, daemon=True).start()
