"""
wake_word.py — Detector de wake word "Bob" para el VoiceChat híbrido.

Pensado para insertarse después del STT (Whisper-Groq u otro) en un pipeline
de audio en tiempo real. Trabaja sobre el TEXTO transcrito, no toca audio.

Capas de detección:
  1. Normalización (lower, sin acentos, sin puntuación, whitespace colapsado).
  2. Match exacto en token 0  → wake al inicio ("Bob, ...").
  3. Match exacto en token 1 si token 0 es prefijo conocido
     ("oye/hola/hey/ey + bob").
  4. Fuzzy match (rapidfuzz si está, sino difflib) sobre token 0 y, con
     prefijo, sobre token 1 → tolera errores típicos de Whisper:
     bob/pop/bog/bobby/bobi/vob/etc.

Anti-falsos-positivos:
  - SOLO se acepta la wake word en los primeros 2 tokens. Si "bob" aparece
    enterrado en una oración larga ("Le dije a Bob ayer") → se rechaza.
  - Score de confianza penaliza utterances largas (narrativas, no comandos).
  - Cooldown anti-doble-disparo: mismo texto exacto dentro de N segundos
    no vuelve a disparar.

Devuelve un WakeWordResult con: detected, confidence, payload, reason.
"""

from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Sequence

try:
    from rapidfuzz import fuzz as _rapidfuzz   # ~10x más rápido que difflib
    _HAS_RAPIDFUZZ = True
except ImportError:
    _HAS_RAPIDFUZZ = False


__all__ = ["WakeWordDetector", "WakeWordResult"]


# ────────────────────────────────────────────────────────────────
# Variantes fonéticas de "Bob" que Whisper suele equivocar.
# ────────────────────────────────────────────────────────────────
_DEFAULT_BOB_VARIANTS: tuple[str, ...] = (
    "bob",                                 # canónica
    "bo", "bop", "bog", "bok", "bod",      # truncamientos / sust. una letra
    "bobs", "boby", "bobi", "bobby", "bobo",
    "pop", "pob", "pov",                   # confusión p/b
    "vob", "vop", "vov",                   # confusión b/v
    "bombo", "boppe",                      # alargamientos
)

# Palabras que pueden anteceder al wake word como vocativo.
_DEFAULT_PREFIXES: tuple[str, ...] = (
    "oye", "oiga", "hola", "hey", "ey", "hi", "hello",
    "che", "escucha", "mira", "amigo", "robot",
    "asistente",
)


# ────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────
def _normalize(text: str) -> str:
    """lower + sin acentos + sin puntuación + whitespace colapsado."""
    if not text:
        return ""
    t = text.lower().strip()
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _fuzzy(a: str, b: str) -> int:
    """0..100 fuzzy match (rapidfuzz si está disponible, sino difflib)."""
    if not a or not b:
        return 0
    if _HAS_RAPIDFUZZ:
        return int(_rapidfuzz.ratio(a, b))
    return int(SequenceMatcher(None, a, b).ratio() * 100)


# ────────────────────────────────────────────────────────────────
# Resultado
# ────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class WakeWordResult:
    detected: bool                         # ¿se gatilla el wake?
    confidence: float                      # 0.0–1.0
    matched_text: str = ""                 # qué tokens consumió el match
    normalized_input: str = ""             # input ya normalizado
    payload: str = ""                      # texto del comando, tras el wake
    reason: str = ""                       # debug: "exact-token0", "fuzzy-82", etc.

    def __bool__(self) -> bool:
        return self.detected


# ────────────────────────────────────────────────────────────────
# Detector
# ────────────────────────────────────────────────────────────────
class WakeWordDetector:
    """
    Configurable. Parámetros principales:
      wake_word           palabra canónica ("bob")
      variants            variantes fonéticas (usa default si wake_word=='bob')
      prefixes            saludos/vocativos admitidos antes del wake
      min_confidence      umbral de disparo (default 0.70)
      fuzzy_threshold     umbral fuzzy 0-100 (default 78)
      cooldown_s          mismo input no re-dispara en este intervalo (1.5 s)
      max_utterance_chars utterances mayores son penalizadas (100 chars)
    """

    def __init__(
        self,
        wake_word: str = "bob",
        variants: Sequence[str] | None = None,
        prefixes: Sequence[str] | None = None,
        min_confidence: float = 0.70,
        fuzzy_threshold: int = 78,
        cooldown_s: float = 1.5,
        max_utterance_chars: int = 100,
    ) -> None:
        self.wake_word = _normalize(wake_word)

        if variants is None:
            variants = _DEFAULT_BOB_VARIANTS if self.wake_word == "bob" else (self.wake_word,)
        self.variants = tuple(_normalize(v) for v in variants if v)
        if self.wake_word and self.wake_word not in self.variants:
            self.variants = (self.wake_word,) + self.variants
        self.variant_set = frozenset(self.variants)

        if prefixes is None:
            prefixes = _DEFAULT_PREFIXES
        self.prefixes = tuple(_normalize(p) for p in prefixes if p)
        self.prefix_set = frozenset(self.prefixes)

        self.min_confidence = float(min_confidence)
        self.fuzzy_threshold = int(fuzzy_threshold)
        self.cooldown_s = float(cooldown_s)
        self.max_utterance_chars = int(max_utterance_chars)

        # cooldown state
        self._last_fire = 0.0
        self._last_normalized = ""

    # ─── API principal ──────────────────────────────────────────
    def detect(self, text: str) -> WakeWordResult:
        """Procesa un transcript (parcial o final) y decide si dispara."""
        norm = _normalize(text)
        if not norm:
            return WakeWordResult(False, 0.0, reason="empty")

        # Cooldown: mismo input dentro del intervalo → no re-dispara
        now = time.monotonic()
        if (now - self._last_fire) < self.cooldown_s and norm == self._last_normalized:
            return WakeWordResult(False, 0.0, normalized_input=norm, reason="cooldown")

        tokens = norm.split()
        if not tokens:
            return WakeWordResult(False, 0.0, normalized_input=norm, reason="empty-tokens")

        # ── Capa 1: token 0 es variante EXACTA ──────────────────
        if tokens[0] in self.variant_set:
            return self._build(norm, tokens, idx=0,
                               exact=True, fuzzy=100,
                               reason="exact-token0", now=now)

        # ── Capa 2: token 0 = prefijo y token 1 = variante exacta ─
        if (
            len(tokens) >= 2
            and tokens[0] in self.prefix_set
            and tokens[1] in self.variant_set
        ):
            return self._build(norm, tokens, idx=1,
                               exact=True, fuzzy=100,
                               reason="prefix-exact", now=now)

        # ── Capa 3: fuzzy contra token 0 ────────────────────────
        fz0 = _fuzzy(tokens[0], self.wake_word)
        if fz0 >= self.fuzzy_threshold:
            return self._build(norm, tokens, idx=0,
                               exact=False, fuzzy=fz0,
                               reason=f"fuzzy-token0-{fz0}", now=now)

        # ── Capa 4: prefijo + fuzzy en token 1 ──────────────────
        if len(tokens) >= 2 and tokens[0] in self.prefix_set:
            fz1 = _fuzzy(tokens[1], self.wake_word)
            if fz1 >= self.fuzzy_threshold:
                return self._build(norm, tokens, idx=1,
                                   exact=False, fuzzy=fz1,
                                   reason=f"prefix-fuzzy-{fz1}", now=now)

        return WakeWordResult(False, 0.0,
                              normalized_input=norm, reason="no-match")

    def reset_cooldown(self) -> None:
        """Limpiar cooldown manualmente (ej. tras consumir el comando)."""
        self._last_fire = 0.0
        self._last_normalized = ""

    # ─── Internals ──────────────────────────────────────────────
    def _build(
        self,
        norm: str,
        tokens: list[str],
        *,
        idx: int,
        exact: bool,
        fuzzy: int,
        reason: str,
        now: float,
    ) -> WakeWordResult:
        confidence = self._score(exact=exact, fuzzy=fuzzy,
                                 idx=idx, utterance_len=len(norm))
        if confidence < self.min_confidence:
            return WakeWordResult(False, confidence,
                                  normalized_input=norm,
                                  matched_text=tokens[idx],
                                  reason=f"low-conf:{reason}")

        payload = " ".join(tokens[idx + 1:]).strip()
        matched = " ".join(tokens[: idx + 1])

        # Update cooldown anchor
        self._last_fire = now
        self._last_normalized = norm
        return WakeWordResult(True, confidence,
                              matched_text=matched,
                              normalized_input=norm,
                              payload=payload,
                              reason=reason)

    def _score(self, *, exact: bool, fuzzy: int,
               idx: int, utterance_len: int) -> float:
        """Score = base + bonus_posición + bonus_longitud, clip a [0,1]."""
        base = 1.0 if exact else fuzzy / 100.0
        pos_bonus = 0.10 if idx == 0 else 0.0    # token 0 es ligeramente mejor
        if utterance_len <= 25:
            len_bonus = 0.10
        elif utterance_len <= 60:
            len_bonus = 0.0
        elif utterance_len <= self.max_utterance_chars:
            len_bonus = -0.10
        else:
            len_bonus = -0.25
        score = base + pos_bonus + len_bonus
        if score < 0.0: score = 0.0
        elif score > 1.0: score = 1.0
        return score


# ════════════════════════════════════════════════════════════════
# DEMO
# ════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    det = WakeWordDetector()

    pruebas = [
        # POSITIVOS esperados
        ("Bob",                                    True),
        ("Bob, ¿qué hora es?",                     True),
        ("Oye Bob",                                True),
        ("Oye Bob, dime el clima",                 True),
        ("Hola Bob, ¿estás ahí?",                  True),
        ("Hey Bob",                                True),
        ("Hey, Bob! buenos días",                  True),
        ("Pop",                                    True),   # error Whisper p/b
        ("Oye pop, qué pasa",                      True),
        ("Bobby",                                  True),
        ("Bog cuéntame un chiste",                 True),
        ("Boba dime algo",                         True),   # fuzzy

        # NEGATIVOS esperados
        ("",                                       False),
        ("...",                                    False),
        ("Le dije a Bob ayer",                     False),
        ("Pero qué dijo",                          False),
        ("Hablábamos de Bob esperanza el cantante", False),
        ("Probablemente sí",                       False),
        ("Mañana iré a casa de Bob",               False),
    ]

    print(f"{'INPUT':<45}{'OK?':<6}{'EXP':<6}{'CONF':<7}{'PAYLOAD':<26}REASON")
    print("-" * 110)
    for txt, expected in pruebas:
        det.reset_cooldown()
        r = det.detect(txt)
        ok_flag = "YES" if r.detected else "no"
        exp_flag = "YES" if expected else "no"
        match = "   " if r.detected == expected else "!! "
        in_repr = (txt or "(empty)")[:43]
        pl_repr = r.payload[:24] if r.payload else "-"
        print(f"{match}{in_repr:<42}{ok_flag:<6}{exp_flag:<6}{r.confidence:<7.2f}"
              f"{pl_repr:<26}{r.reason}")
