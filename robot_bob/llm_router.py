"""
llm_router.py — Cliente de chat con rotación por cupo (Fase R, decisión R-D1).

Se usa igual que un cliente OpenAI (`router.chat.completions.create(...)`), así que
el resto de Bob no cambia. Prueba los backends en orden y salta al siguiente cuando
uno no tiene cupo (cabeceras `x-ratelimit-*` de Groq, error 429), falla o no
responde. En streaming el salto ocurre antes del primer chunk: quien consume el
stream no nota el cambio.

Orden típico: Groq gpt-oss-20b → qwen3.8-27b → gpt-oss-120b → Ollama local (sin
cupo; usa el prompt corto para modelos chicos).
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Callable, Dict, List, Optional

# Bloqueos por tipo de falla (segundos), cuando la respuesta no trae cuándo reintentar.
BLOQUEO_429_S = 60.0       # sin cupo y sin retry-after
BLOQUEO_FALLA_S = 20.0     # red, timeout, 5xx
BLOQUEO_RECHAZO_S = 3600.0  # 400/401/403/404: modelo o clave no sirven
# Chars por token para estimar el tamaño de un pedido (español ≈ 3.5).
CHARS_POR_TOKEN = 3.5
# Cada cuánto, como mucho, se precalienta el backend local cuando la nube se agota.
PRECALENTAR_CADA_S = 240.0

_DURACION = re.compile(r'(\d+(?:\.\d+)?)(ms|h|m|s)')


def segundos(valor) -> Optional[float]:
    """'1m30.5s' / '7.66s' / '250ms' / '2' → segundos. None si no se entiende."""
    if valor is None:
        return None
    texto = str(valor).strip()
    try:
        return float(texto)
    except ValueError:
        pass
    partes = _DURACION.findall(texto)
    if not partes:
        return None
    escala = {'h': 3600.0, 'm': 60.0, 's': 1.0, 'ms': 0.001}
    return sum(float(n) * escala[u] for n, u in partes)


def _entero(valor) -> Optional[int]:
    try:
        return int(float(valor))
    except (TypeError, ValueError):
        return None


@dataclass
class Backend:
    nombre: str                 # para los logs ("gpt-oss-20b", "ollama")
    cliente: object             # cliente OpenAI-compatible (Groq, OpenAI)
    modelo: str
    extra: Dict = field(default_factory=dict)   # p. ej. reasoning_effort
    local: bool = False         # sin cupo; usa los reemplazos de prompt locales
    timeout: float = 8.0        # s por operación de red (también entre chunks)
    bloqueado_hasta: float = 0.0
    motivo: str = ''


class _StreamArrancado:
    """Stream cuyo primer chunk ya se leyó: lo devuelve primero y sigue con el resto."""

    def __init__(self, primero, resto, original):
        self._primero = primero
        self._resto = resto
        self._original = original

    def __iter__(self):
        if self._primero is not None:
            yield self._primero
        yield from self._resto

    def close(self) -> None:
        cerrar = getattr(self._original, 'close', None)
        if cerrar:
            cerrar()


class RouterLLM:
    """
    backends: en orden de preferencia.
    reemplazos_locales: {prefijo: reemplazo} del system prompt para backends locales
        (el modelo chico usa SYSTEM_PROMPT_LOCAL en vez del prompt largo).
    log: función para los avisos (console.print en Bob).
    """

    def __init__(self, backends: List[Backend], reemplazos_locales: Optional[Dict[str, str]] = None,
                 log: Callable[[str], None] = print):
        if not backends:
            raise ValueError('RouterLLM necesita al menos un backend')
        self._backends = backends
        self._reemplazos = reemplazos_locales or {}
        self._log = log
        self._lock = threading.Lock()
        self._ultimo_precalentado = 0.0
        self.ultimo: Optional[Backend] = None    # el que respondió el último pedido
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    @property
    def backends(self) -> List[Backend]:
        return list(self._backends)

    def disponibles(self) -> List[Backend]:
        ahora = time.monotonic()
        return [b for b in self._backends if b.bloqueado_hasta <= ahora]

    def tiene_local(self) -> bool:
        return any(b.local for b in self._backends)

    # ── Pedido ────────────────────────────────────────────────────────────────

    def create(self, model=None, messages=(), stream: bool = False, **kw):
        """Como chat.completions.create. `model` se ignora: lo pone cada backend."""
        errores = []
        for b in self._backends:
            if b.bloqueado_hasta > time.monotonic():
                continue
            args = {k: v for k, v in kw.items() if k not in ('reasoning_effort',)}
            args.update(b.extra)
            msgs = self._adaptar(b, messages)
            try:
                resp, headers = self._pedir(b, msgs, stream, args)
                self._leer_cupo(b, headers, msgs, args.get('max_tokens'))
                if stream:
                    resp = self._arrancar(resp)
                if self.ultimo is not b:
                    previo = self.ultimo.nombre if self.ultimo else None
                    if previo:
                        self._log(f'[yellow][llm] ahora responde {b.nombre} (antes {previo})[/]')
                self.ultimo = b
                self._quizas_precalentar()
                return resp
            except Exception as e:
                self._penalizar(b, e)
                errores.append(f'{b.nombre}: {type(e).__name__}')
        raise RuntimeError('ningún LLM disponible (' + '; '.join(errores or ['todos sin cupo']) + ')')

    def _pedir(self, b: Backend, msgs, stream, args):
        comp = b.cliente.chat.completions
        crudo = getattr(comp, 'with_raw_response', None)
        if crudo is not None:
            raw = crudo.create(model=b.modelo, messages=msgs, stream=stream, timeout=b.timeout, **args)
            return raw.parse(), getattr(raw, 'headers', {}) or {}
        return comp.create(model=b.modelo, messages=msgs, stream=stream, timeout=b.timeout, **args), {}

    @staticmethod
    def _arrancar(stream):
        """Lee el primer chunk acá: si el backend falla al empezar, salta el siguiente."""
        it = iter(stream)
        try:
            primero = next(it)
        except StopIteration:
            primero = None
        return _StreamArrancado(primero, it, stream)

    def _adaptar(self, b: Backend, messages):
        if not (b.local and self._reemplazos):
            return list(messages)
        salida = []
        for m in messages:
            if m.get('role') == 'system':
                contenido = m.get('content', '')
                for prefijo, reemplazo in self._reemplazos.items():
                    if prefijo and contenido.startswith(prefijo):
                        contenido = reemplazo + contenido[len(prefijo):]
                        break
                m = dict(m, content=contenido)
            salida.append(m)
        return salida

    # ── Cupo ──────────────────────────────────────────────────────────────────

    def _bloquear(self, b: Backend, s: float, motivo: str) -> None:
        with self._lock:
            b.bloqueado_hasta = max(b.bloqueado_hasta, time.monotonic() + max(s, 1.0))
            b.motivo = motivo
        self._log(f'[yellow][llm] {b.nombre}: {motivo} → pausa {s:.0f} s[/]')

    def _leer_cupo(self, b: Backend, headers, msgs, max_tokens) -> None:
        """Groq informa lo que queda: pedidos del día y tokens del minuto."""
        if b.local or not headers:
            return
        pedidos = _entero(headers.get('x-ratelimit-remaining-requests'))
        if pedidos is not None and pedidos <= 0:
            self._bloquear(b, segundos(headers.get('x-ratelimit-reset-requests')) or BLOQUEO_429_S,
                           'sin pedidos por hoy')
            return
        tokens = _entero(headers.get('x-ratelimit-remaining-tokens'))
        if tokens is not None and tokens < self.estimar_tokens(msgs, max_tokens):
            self._bloquear(b, segundos(headers.get('x-ratelimit-reset-tokens')) or BLOQUEO_429_S,
                           f'quedan {tokens} tokens este minuto')

    @staticmethod
    def estimar_tokens(msgs, max_tokens) -> int:
        chars = sum(len(str(m.get('content', ''))) for m in msgs)
        return int(chars / CHARS_POR_TOKEN) + int(max_tokens or 0)

    def _penalizar(self, b: Backend, e: Exception) -> None:
        estado = getattr(e, 'status_code', None)
        respuesta = getattr(e, 'response', None)
        headers = getattr(respuesta, 'headers', None) or {}
        if estado == 429:
            self._bloquear(b, segundos(headers.get('retry-after')) or BLOQUEO_429_S, 'sin cupo (429)')
        elif estado in (400, 401, 403, 404, 422):
            self._bloquear(b, BLOQUEO_RECHAZO_S, f'rechazado ({estado})')
        else:
            self._bloquear(b, BLOQUEO_FALLA_S, f'falló ({type(e).__name__})')

    def _quizas_precalentar(self) -> None:
        """Con la nube casi agotada, carga el modelo local en la GPU antes de necesitarlo
        (en frío tarda ~14 s; caliente, ~3 s)."""
        local = next((b for b in self._backends if b.local), None)
        if local is None or self.ultimo is local:
            return
        nube_libre = [b for b in self.disponibles() if not b.local]
        ahora = time.monotonic()
        if len(nube_libre) > 1 or ahora - self._ultimo_precalentado < PRECALENTAR_CADA_S:
            return
        self._ultimo_precalentado = ahora

        def _calentar():
            try:
                local.cliente.chat.completions.create(
                    model=local.modelo, messages=[{'role': 'user', 'content': 'hola'}],
                    max_tokens=1, timeout=60)
                self._log(f'[dim][llm] {local.nombre} precalentado de respaldo[/]')
            except Exception:
                pass
        threading.Thread(target=_calentar, daemon=True, name='llm-precalentar').start()
