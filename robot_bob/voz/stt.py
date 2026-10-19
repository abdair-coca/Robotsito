"""
stt.py — Filtros de la transcripción de Whisper (Fase R1, decisión R-D2).

Whisper inventa texto con silencio o ruido ("Gracias.", "Conectado."), repite el
prompt de sesgo ("Potosí, Bolivia, UATF.") y a veces entra en bucle ("17, 17, 17").
Con `response_format='verbose_json'` cada segmento trae su confianza
(`avg_logprob`) y su `compression_ratio`; con eso se descarta lo que no es voz y se
marca lo dudoso para que Bob pida repetir en vez de adivinar.

Calibración (EXP en .dev/PLAN.md, Fase R, 2026-10-17, Groq whisper-large-v3-turbo,
34 grabaciones + 5 de silencio/ruido): frases bien transcritas avg_logprob de
−0.12 a −0.45; alucinaciones con silencio o ruido de −0.77 a −1.06. Groq devuelve
`no_speech_prob` siempre en 0, así que no se usa.
"""
from __future__ import annotations

import re
import unicodedata
from collections import deque
from dataclasses import dataclass
from typing import List, Optional

# Calibración (2026-10-17): ruido real −1.01 a −1.06; frases cortas reales −0.13 a −0.78
# ("Ok, ponlo." −0.78 era una orden y se descartaba con −0.7).
LOGPROB_RUIDO = -0.9       # frase corta (≤ PALABRAS_CORTA) por debajo → ruido, se descarta
LOGPROB_DUDOSA = -0.9      # frase más larga por debajo → dudosa: Bob pide repetir
# Eco del prompt: "Conversación en español." salió con −0.82 (eco real); "Modo ingeniero."
# con −0.48 era la orden del dueño. Con poca confianza o frase larga, es eco.
ECO_CONF_MAX = -0.6
ECO_PALABRAS_MIN = 6
COMPRESION_MAX = 2.4       # segmento muy repetitivo (bucle de Whisper) → fuera
PALABRAS_CORTA = 3
# Frases que Whisper suele inventar con silencio. Solo se descartan con confianza
# baja: un "gracias" real se transcribe con confianza alta.
ALUCINACIONES = {
    'gracias', 'muchas gracias', 'gracias por ver el video', 'gracias por ver',
    'conectado', 'subtitulos realizados por la comunidad de amara org',
    'no', 'si', 'ya', 'eh', 'ah', 'mmm', 'chau', 'ok', 'okay', 'a', 'y', 'the',
}
LOGPROB_ALUCINACION = -0.5
PROMPT_MAX_CHARS = 850     # Whisper solo mira ~224 tokens del prompt


@dataclass
class Transcripcion:
    texto: str                    # '' si se descartó
    crudo: str                    # lo que devolvió Whisper, para los logs
    confianza: Optional[float]    # avg_logprob ponderado por duración (None si no vino)
    dudosa: bool = False
    descartado: str = ''          # '' o el motivo: ruido, alucinacion, eco_prompt, bucle


def palabras(texto: str) -> List[str]:
    """Minúsculas, sin tildes ni signos."""
    t = unicodedata.normalize('NFD', (texto or '').lower())
    t = ''.join(c for c in t if unicodedata.category(c) != 'Mn')
    return re.sub(r'[^a-zñ0-9 ]', ' ', t).split()


def quitar_bucles(texto: str, repeticiones: int = 3) -> str:
    """Corta los bucles de Whisper: un grupo de 1–3 palabras repetido `repeticiones`
    veces seguidas o más queda una sola vez ("17, 17, 17, 17, 18" → "17, 18")."""
    tokens = texto.split()
    salida: List[str] = []
    i = 0
    while i < len(tokens):
        cortado = False
        for n in (1, 2, 3):
            grupo = [palabras(t) for t in tokens[i:i + n]]
            if len(grupo) < n or not all(grupo):
                continue
            k = 1
            while palabras(' '.join(tokens[i + k * n:i + (k + 1) * n])) == palabras(' '.join(tokens[i:i + n])):
                k += 1
            if k >= repeticiones:
                salida.extend(tokens[i + (k - 1) * n:i + k * n])
                i += k * n
                cortado = True
                break
        if not cortado:
            salida.append(tokens[i])
            i += 1
    return ' '.join(salida)


def es_eco_prompt(texto: str, prompt: str) -> bool:
    """True si el texto es un pedazo del prompt (Whisper lo repite con silencio)."""
    t, p = palabras(texto), palabras(prompt)
    if len(t) < 2 or len(t) > len(p):
        return False
    return ' ' + ' '.join(t) + ' ' in ' ' + ' '.join(p) + ' '


def _segmentos(resp) -> list:
    if isinstance(resp, dict):
        return resp.get('segments') or []
    return getattr(resp, 'segments', None) or []


def _campo(seg, nombre, defecto=None):
    return seg.get(nombre, defecto) if isinstance(seg, dict) else getattr(seg, nombre, defecto)


def filtrar(resp, prompt: str = '') -> Transcripcion:
    """Aplica los filtros a una respuesta verbose_json (dict u objeto del SDK)."""
    crudo = ((resp.get('text') if isinstance(resp, dict) else getattr(resp, 'text', '')) or '').strip()
    segs = _segmentos(resp)

    texto, peso, suma = crudo, 0.0, 0.0
    if segs:
        partes = []
        for s in segs:
            if (_campo(s, 'compression_ratio') or 0.0) > COMPRESION_MAX:
                continue
            partes.append((_campo(s, 'text', '') or '').strip())
            lp = _campo(s, 'avg_logprob')
            if lp is not None:
                dur = max(float(_campo(s, 'end', 0.0) or 0.0) - float(_campo(s, 'start', 0.0) or 0.0), 0.1)
                peso += dur
                suma += lp * dur
        texto = ' '.join(p for p in partes if p)
        if not texto and crudo:
            return Transcripcion('', crudo, None, descartado='bucle')
    confianza = suma / peso if peso else None
    texto = quitar_bucles(texto).strip()

    def fuera(motivo: str) -> Transcripcion:
        return Transcripcion('', crudo, confianza, descartado=motivo)

    # Si la frase menciona a Bob, el dueño le está hablando: no se descarta por ruido,
    # eco ni alucinación (el "Ok, ponlo." con "Bob" de la charla se perdía así).
    dirigido = any(p.startswith('bob') for p in palabras(texto))
    if not palabras(texto):
        return fuera('ruido') if crudo else Transcripcion('', crudo, confianza)
    eco = prompt and es_eco_prompt(texto, prompt) and (
        confianza is None or confianza < ECO_CONF_MAX or len(palabras(texto)) >= ECO_PALABRAS_MIN)
    if eco and not dirigido:
        return fuera('eco_prompt')
    if confianza is not None:
        if not dirigido and ' '.join(palabras(texto)) in ALUCINACIONES \
                and confianza < LOGPROB_ALUCINACION:
            return fuera('alucinacion')
        if not dirigido and len(palabras(texto)) <= PALABRAS_CORTA and confianza < LOGPROB_RUIDO:
            return fuera('ruido')
        if confianza < LOGPROB_DUDOSA:
            return Transcripcion(texto, crudo, confianza, dudosa=True)
    return Transcripcion(texto, crudo, confianza)


# R3: dictado largo (modo ingeniero). Dichas solas tras una pausa, terminan el
# dictado al instante; al final del dictado, lo descartan entero.
FIN_DICTADO = {'listo', 'listo bob', 'ya listo', 'listo envialo', 'envialo', 'envialo bob',
               'enviar', 'envia', 'mandalo', 'eso es todo', 'eso es todo bob', 'fin',
               'fin del mensaje', 'ya esta', 'ya esta bob'}
CANCELAR_DICTADO = ('borra eso', 'borra todo', 'empieza de nuevo', 'olvidalo', 'cancela',
                    'cancelalo', 'cancelar')


def es_fin_dictado(texto: str) -> bool:
    return ' '.join(palabras(texto)) in FIN_DICTADO


def es_cancelar_dictado(texto: str) -> bool:
    """True si el dictado termina pidiendo borrarlo ("…no, borra eso")."""
    cola = ' '.join(palabras(texto)[-4:])
    return any(cola.endswith(c) for c in CANCELAR_DICTADO)


def unir_dictado(textos: List[str]) -> str:
    """Tramos del dictado en orden, sin vacíos ni la palabra de fin del último."""
    partes = [t.strip() for t in textos if t and t.strip()]
    if partes and es_fin_dictado(partes[-1]):
        partes.pop()
    return ' '.join(partes)


class CupoSTT:
    """Cuenta las transcripciones del último minuto (Groq: 20 por minuto). La charla
    siempre transcribe; lo opcional (escucha en reposo, transcripción adelantada)
    deja `reserva` pedidos libres para que la charla no se quede sin cupo."""

    def __init__(self, por_minuto: int = 20, reloj=None):
        import time
        self._reloj = reloj or time.monotonic
        self._por_minuto = por_minuto
        self._usos: deque = deque()

    def registrar(self) -> None:
        self._usos.append(self._reloj())

    def usados(self) -> int:
        limite = self._reloj() - 60.0
        while self._usos and self._usos[0] < limite:
            self._usos.popleft()
        return len(self._usos)

    def libre(self, reserva: int = 0) -> bool:
        return self.usados() < self._por_minuto - reserva


def armar_prompt(base: str, vocabulario: str = '', contexto: str = '') -> str:
    """Prompt de sesgo para Whisper: vocabulario primero y lo último que dijo Bob al
    final (Whisper lo lee como el texto previo a la frase, lo que más pesa)."""
    contexto = ' '.join((contexto or '').split())
    cuerpo = ' '.join(p for p in (base.strip(), vocabulario.strip()) if p)
    if not contexto:
        return cuerpo[-PROMPT_MAX_CHARS:]
    contexto = contexto[-300:]
    return (cuerpo[:PROMPT_MAX_CHARS - len(contexto) - 1] + ' ' + contexto).strip()
