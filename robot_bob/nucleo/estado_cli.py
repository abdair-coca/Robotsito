"""
estado_cli.py — Estado de Bob para la barra del CLI (bob-cli).

Cada 200 ms toma una foto del estado (FSM, emoción del OLED, cabeza simulada, micrófono, música, baile,
modo ingeniero) y emite {"t": "estado", ...} solo con lo que cambió; la primera foto va
entera. Una foto en vez de avisar en cada cambio: `bailando` y `musica_sonando` se
prenden y apagan en muchos lugares.

Solo corre en modo CLI (BOB_CLI=1).
"""
from __future__ import annotations

import threading
from typing import Dict

from nucleo import puente_cli

PERIODO_S = 0.2


def foto(sm, voice, serial) -> Dict[str, object]:
    """El estado de Bob ahora. Lo que no se pueda leer queda afuera."""
    f: Dict[str, object] = {
        'fsm': sm.estado.name,
        'mic': bool(voice.microfono_activo),
        'musica': sm.musica_sonando.is_set(),
        'baile': sm.bailando.is_set(),
        'ingeniero': bool(getattr(voice, '_modo_ingeniero', False)),
        'red': bool(getattr(voice, 'en_linea', True)),
    }
    # Último estado OLED enviado ('' tras reconectar: todavía no se sabe).
    emocion = getattr(serial, '_ultimo_estado', '')
    if emocion:
        f['emocion'] = emocion
    # Cabeza simulada (bob --alone). Con el ESP32 el serial no tiene posición: no aparece.
    pos = getattr(serial, 'posicion', None)
    if pos:
        f['cabeza'] = {'pan': pos[0], 'tilt': pos[1]}
    return f


def cambios(previa: Dict[str, object], actual: Dict[str, object]) -> Dict[str, object]:
    return {k: v for k, v in actual.items() if previa.get(k) != v}


def iniciar(sm, voice, serial, detener: threading.Event) -> bool:
    """Arranca el hilo (solo con el CLI activo). True si arrancó."""
    if not puente_cli.ACTIVO:
        return False
    threading.Thread(target=_bucle, args=(sm, voice, serial, detener), daemon=True,
                     name='cli-estado').start()
    return True


def _bucle(sm, voice, serial, detener: threading.Event) -> None:
    previa: Dict[str, object] = {}
    while not detener.is_set():
        try:
            actual = foto(sm, voice, serial)
        except Exception:
            actual = previa          # un dato que no se pudo leer no corta la barra
        nuevo = cambios(previa, actual)
        if nuevo:
            puente_cli.emitir({'t': 'estado', **nuevo})
            previa = actual
        detener.wait(PERIODO_S)
