"""
foto.py — Modo foto de Bob ("Bob, ponte para la foto").

  "ponte para la foto"              → pose por defecto (MUY_FELIZ)
  "ponte triste para la foto"       → esa cara, con la pose de cabeza que le queda
  "haz una pose de enojado"         → igual: cualquier cara de CARAS

Bob cuenta "tres, dos, uno", pone la cara y deja la cabeza quieta FOTO_SEGUNDOS
para que salga bien la foto; después vuelve a la charla. Las caras son estados del
OLED (oled_ojos.py ESTADOS), así que todas están en _VALID_EMOS.

run_foto(vp, cara) recibe el VoicePipeline en curso, como show.run_show.
"""

import random
import re
import time
from typing import Optional

from nucleo.show import _Cabeza

try:
    from config import FOTO_SEGUNDOS
except Exception:
    FOTO_SEGUNDOS = 5.0

CARA_POR_DEFECTO = 'MUY_FELIZ'

# ── Detección del comando ───────────────────────────────────────────────────────
# Tiene que pedir posar: "¿te gustan las fotos?" es charla, no una pose.
_RE_FOTO = re.compile(
    r"\bpara (la|una|el|mi|nuestra) (foto|fotito|selfie)s?\b"
    r"|\b(posa|pos[aá]|p[oó]sale)\b"
    r"|\b(haz|hac[eé]|hazme|haceme) (una|tu) pose\b"
    r"|\b(t[oó]m\w*|s[aá]c\w*) (una |la )?(foto|fotito|selfie)\b",
    re.IGNORECASE)

# (cara del OLED, palabras que la piden). Orden: "muy triste" antes que "triste".
CARAS = (
    ('MUY_TRISTE',  r'muy trist\w*'),
    ('MUY_FELIZ',   r'muy feliz|muy content\w*|sonr[ií]\w*|sonriente'),
    ('FELIZ',       r'feliz|content[oa]s?|alegre'),
    ('TRISTE',      r'trist\w*'),
    ('ENOJADO',     r'enojad\w*|enfadad\w*|brav[oa]|molest[oa]|furios[oa]'),
    ('SORPRENDIDO', r'sorprendid\w*|asombrad\w*'),
    ('ASUSTADO',    r'asustad\w*|miedo'),
    ('CURIOSO',     r'curios[oa]'),
    ('TRAVIESO',    r'travies[oa]|p[ií]car[oa]'),
    ('AMOR',        r'enamorad\w*|amor|cari[ñn]os[oa]'),
    ('ORGULLOSO',   r'orgullos[oa]'),
    ('EMOCIONADO',  r'emocionad\w*'),
    ('AVERGONZADO', r'avergonzad\w*|t[ií]mid[oa]|verg[uü]enza'),
    ('CONFUNDIDO',  r'confundid\w*'),
    ('SOSPECHANDO', r'sospech\w*|desconfiad\w*'),
    ('PENSANDO',    r'pensativ[oa]|pensando'),
)
_RE_CARAS = [(cara, re.compile(rf'\b(?:{palabras})\b', re.IGNORECASE))
             for cara, palabras in CARAS]

# Pose de cabeza por cara: (Δpan, Δtilt) desde la posición de inicio.
# Tilt menor = mira arriba. Chico: con movimientos fuertes la fuente se cae.
_POSES = {
    'MUY_FELIZ':   (0, -4),     # mentón arriba
    'FELIZ':       (0, -3),
    'ORGULLOSO':   (0, -8),     # pecho afuera
    'EMOCIONADO':  (0, -6),
    'SORPRENDIDO': (0, -8),
    'CURIOSO':     (10, 0),     # cabeza ladeada
    'TRAVIESO':    (-10, -3),
    'AMOR':        (8, -2),
    'CONFUNDIDO':  (12, 0),
    'SOSPECHANDO': (-8, 4),
    'PENSANDO':    (6, -6),     # mirando arriba, como pensando
    'TRISTE':      (-6, 10),    # mirando abajo
    'MUY_TRISTE':  (-6, 14),
    'AVERGONZADO': (-8, 10),
    'ASUSTADO':    (0, 6),      # se encoge
    'ENOJADO':     (0, 4),      # mira fijo, de frente
}

_LISTO = ('¡Pose de foto!', '¡Listo para la foto!', '¡Mi mejor lado!')
_CARA_DICHA = {
    'MUY_FELIZ': 'con mi mejor sonrisa', 'FELIZ': 'feliz', 'TRISTE': 'triste',
    'MUY_TRISTE': 'bien triste', 'ENOJADO': 'enojado', 'SORPRENDIDO': 'sorprendido',
    'ASUSTADO': 'asustado', 'CURIOSO': 'curioso', 'TRAVIESO': 'travieso',
    'AMOR': 'enamorado', 'ORGULLOSO': 'orgulloso', 'EMOCIONADO': 'emocionado',
    'AVERGONZADO': 'avergonzado', 'CONFUNDIDO': 'confundido',
    'SOSPECHANDO': 'sospechando', 'PENSANDO': 'pensativo',
}
_SALIO = ('¡Salió genial!', '¡Quedé lindo!', '¡Esa va para el álbum!')


def es_comando_foto(texto: str) -> bool:
    return bool(_RE_FOTO.search(texto or ""))


def cara_pedida(texto: str) -> Optional[str]:
    """La cara que nombra el texto ('ponte triste…' → 'TRISTE'), o None si no nombra ninguna."""
    for cara, patron in _RE_CARAS:
        if patron.search(texto or ""):
            return cara
    return None


def cara_foto(texto: str, ruta: Optional[str] = None) -> Optional[str]:
    """La cara para posar si el turno pide una foto, o None si no es una foto.
    JEV decide con ruta 'foto'; sin JEV (o si lo tomó como charla), palabras clave."""
    if ruta == 'foto' or (ruta in (None, 'charla') and es_comando_foto(texto)):
        return cara_pedida(texto) or CARA_POR_DEFECTO
    return None


def _home() -> tuple:
    """Donde la cabeza mira a las caras (TILT_HOME de la feria)."""
    try:
        from vision.facial_tracker import PAN_HOME, TILT_HOME
        return (float(PAN_HOME), float(TILT_HOME))
    except Exception:
        return (90.0, 55.0)


# ── Pose ────────────────────────────────────────────────────────────────────────

def run_foto(vp, cara: str = CARA_POR_DEFECTO) -> None:
    """Cuenta, posa con `cara` y la sostiene FOTO_SEGUNDOS con la cabeza quieta.
    Las teclas D/J (vp._cortado) cortan la pose antes."""
    sm, serial, hablar = vp._sm, vp._serial, vp._hablar
    if cara not in _POSES:
        cara = CARA_POR_DEFECTO

    sm.show_activo.set()     # congela BehaviorEngine: la cabeza no sigue a nadie
    sm.oled_ocupar()         # sin muecas encima de la cara de la foto
    try:
        base = _home()
        cabeza = _Cabeza(serial, base)
        serial.cmd_servo(*base)
        serial.cmd_estado(cara)
        hablar(f'{random.choice(_LISTO)} Me pongo {_CARA_DICHA[cara]}. '
               'Tres, dos, uno...', cara)
        d_pan, d_tilt = _POSES[cara]
        cabeza.ir((base[0] + d_pan, base[1] + d_tilt))
        serial.cmd_estado(cara)            # tras hablar: que el OLED quede en la cara
        fin = time.monotonic() + FOTO_SEGUNDOS
        cortado = getattr(vp, '_cortado', lambda: False)
        while time.monotonic() < fin and not cortado():
            time.sleep(0.1)
        cabeza.ir(base)
        hablar(random.choice(_SALIO), 'MUY_FELIZ')
    finally:
        sm.oled_liberar()
        sm.show_activo.clear()
