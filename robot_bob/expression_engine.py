"""
expression_engine.py — Capa de expresividad del Robot Bob.

Centraliza qué emoción del OLED se muestra en cada momento del sistema.
Pensado para trabajar con el firmware nuevo (Oled1_3/oled_ojos_prove.py)
que soporta 21 emociones. Si el firmware actual no soporta una emoción,
la firmware la ignora silenciosamente — no se rompe nada.

Concepto clave: PULSE
  Emite una emoción por N ms y después restaura el OLED que correspondería
  al estado actual de la StateMachine. Usa un hilo daemon — no bloquea.

Ejemplo:
    pulse_emotion(serial_mgr, sm, 'SORPRENDIDO', 800)
    # → muestra SORPRENDIDO por 800 ms, después vuelve al estado natural
"""

from __future__ import annotations

import threading
import time
from typing import Optional

from state_machine import RobotState

# ── Mapeo: estado de la StateMachine → comando OLED por defecto ─────────────
# Coincide con _OLED_STATE de state_machine.py — duplicado a propósito acá
# para que expression_engine no dependa de leerlo desde el otro módulo.
_DEFAULT_OLED: dict[RobotState, str] = {
    RobotState.IDLE:              'ESPERANDO',
    RobotState.PRESENCE:          'SIGUIENDO',
    RobotState.LISTENING:         'ESCUCHANDO',
    RobotState.THINKING:          'PENSANDO',
    RobotState.SPEAKING:          'HABLANDO',
    RobotState.CONVERSATION_IDLE: 'ESPERANDO',
}


# ═══════════════════════════════════════════════════════════════════════════
# CATÁLOGO DE EMOCIONES POR SITUACIÓN
# ═══════════════════════════════════════════════════════════════════════════
# Si querés tunear cómo se siente Bob en cada momento, editá acá.

EMO_WAKE_DETECTED   = 'CURIOSO'      # cuando se escucha "Bob"
EMO_GREETING_PLAYED = 'TRAVIESO'     # cuando dice un saludo divertido
EMO_AUTO_OPENER     = 'CURIOSO'      # cuando arranca él solo
EMO_STT_FAIL        = 'CONFUNDIDO'   # cuando no entiende lo que dijiste
EMO_HAPPY_RESPONSE  = 'FELIZ'        # cuando responde con contenido alegre
EMO_GOODBYE         = 'TRISTE'       # cuando alguien se despide
EMO_DOBLE_TOMA      = 'SORPRENDIDO'  # cara nueva tras larga ausencia
EMO_LLM_ERROR       = 'CONFUNDIDO'   # cuando Groq falla
EMO_LOVE_DETECTED   = 'AMOR'         # cuando alguien dice algo cariñoso

# Duraciones por defecto (ms) — ajustadas para feria
PULSE_FAST = 500
PULSE_NORM = 900
PULSE_SLOW = 1500


# ═══════════════════════════════════════════════════════════════════════════
# PULSE: emoción momentánea con retorno automático al estado base
# ═══════════════════════════════════════════════════════════════════════════

def pulse_emotion(serial_mgr, sm, emotion: str,
                  duration_ms: int = PULSE_NORM,
                  return_to: Optional[str] = None) -> None:
    """
    Muestra `emotion` por `duration_ms`, después restaura el OLED del
    estado actual de `sm` (o `return_to` explícito si se da).

    No bloquea — corre en hilo daemon.
    Si el estado del state_machine CAMBIÓ durante el pulse, NO restaura:
    la transición ya mandó su propio comando OLED (e.g. un tag [EMO:X]
    del LLM durante SPEAKING) y pisarlo arruinaría la expresión vigente.
    """
    pulse_sequence(serial_mgr, sm, [(emotion, duration_ms)], return_to=return_to)


def pulse_sequence(serial_mgr, sm, steps: list,
                   return_to: Optional[str] = None) -> None:
    """
    Reproduce una secuencia de emociones [(emocion, duration_ms), ...] en orden
    y después restaura el OLED del estado base. No bloquea (hilo daemon).

    Ejemplo — insulto con recuperación rápida:
        pulse_sequence(mgr, sm, [('TRISTE', 400), ('CONFUNDIDO', 800)])
    """
    if not steps:
        return

    def _worker():
        estado_inicial = sm.estado
        for emo, ms in steps:
            try:
                serial_mgr.cmd_estado(emo)
            except Exception:
                return
            time.sleep(ms / 1000.0)
        # Si hubo una transición de estado durante la secuencia, esa transición
        # ya actualizó el OLED — no pisarla con nuestro restore.
        if sm.estado != estado_inicial and return_to is None:
            return
        target = return_to if return_to is not None else \
            _DEFAULT_OLED.get(sm.estado, 'ESPERANDO')
        try:
            serial_mgr.cmd_estado(target)
        except Exception:
            pass

    threading.Thread(target=_worker, daemon=True, name='emo-pulse').start()


# ═══════════════════════════════════════════════════════════════════════════
# DETECTORES DE CONTENIDO — para que Bob reaccione a lo que dicen
# ═══════════════════════════════════════════════════════════════════════════

_HAPPY_WORDS = {
    'bien', 'genial', 'excelente', 'perfecto', 'fantástico', 'increíble',
    'buenísimo', 'maravilloso', 'feliz', 'contento', 'alegre', 'super',
    'súper', 'chévere', 'bárbaro', 'me gusta', 'me encanta',
}
_LOVE_WORDS = {
    'te quiero', 'te amo', 'me caes bien', 'eres genial', 'sos genial',
    'me agradas', 'precioso', 'lindo', 'hermoso', 'guapo', 'tierno',
}
_GOODBYE_WORDS = {
    'adiós', 'adios', 'chao', 'chau', 'hasta luego', 'bye', 'nos vemos',
    'me voy', 'hasta pronto', 'hasta la próxima',
}
_CONFUSED_WORDS = {
    'no entiendo', 'no entendí', 'no sé', 'no se', 'qué dijiste',
    'no comprendo', 'cómo', 'eh', 'qué', 'mande',
}
_INSULT_WORDS = {
    'tonto', 'estúpido', 'estupido', 'feo', 'inútil', 'inutil', 'basura',
    'idiota', 'cállate', 'callate', 'aburrido', 'malo eres', 'no sirves',
    'apágate', 'apagate',
}
_LAUGH_WORDS = {
    'jaja', 'jeje', 'jiji', 'jsjs', 'lol', 'qué risa', 'que risa',
    'gracioso', 'chistoso', 'me hiciste reír', 'me hiciste reir',
}
_CONCERN_WORDS = {
    'estás triste', 'estas triste', 'qué pasa', 'que pasa', 'estás bien',
    'estas bien', 'qué tienes', 'que tienes', 'te pasa algo', 'estás enojado',
    'estas enojado', 'qué te pasa', 'que te pasa',
}
_ABSURD_CUTE_WORDS = {
    'tienes hambre', 'tienes novia', 'tienes novio', 'cuántos años tienes',
    'cuantos años tienes', 'tienes frío', 'tienes frio', 'tienes sueño',
    'tienes calor', 'duermes', 'qué comes', 'que comes', 'tienes familia',
    'tienes mamá', 'tienes mama', 'tienes papá', 'tienes papa',
    'tienes hermanos', 'te enamoras', 'tienes amigos', 'vas al baño',
}
_DEEP_QUESTION_WORDS = {
    'sentido de la vida', 'existe dios', 'qué es el amor', 'que es el amor',
    'eres consciente', 'tienes alma', 'piensas de verdad', 'sientes de verdad',
    'qué es la muerte', 'que es la muerte', 'libre albedrío', 'libre albedrio',
    'qué es la felicidad', 'que es la felicidad', 'eres real', 'estás vivo',
    'estas vivo', 'qué es la conciencia', 'que es la conciencia',
    'sueñan los robots', 'puedes sentir',
}


def is_happy(text: str) -> bool:
    t = text.lower()
    return any(w in t for w in _HAPPY_WORDS)


def is_love(text: str) -> bool:
    t = text.lower()
    return any(w in t for w in _LOVE_WORDS)


def is_goodbye(text: str) -> bool:
    t = text.lower().strip(' .,!?¿¡')
    return any(w == t or w in t for w in _GOODBYE_WORDS)


def is_confused(text: str) -> bool:
    t = text.lower()
    return any(w in t for w in _CONFUSED_WORDS)


def is_insult(text: str) -> bool:
    t = text.lower()
    return any(w in t for w in _INSULT_WORDS)


def is_laugh(text: str) -> bool:
    t = text.lower()
    return any(w in t for w in _LAUGH_WORDS)


def is_concern(text: str) -> bool:
    """El usuario pregunta cómo está Bob / muestra preocupación."""
    t = text.lower()
    return any(w in t for w in _CONCERN_WORDS)


def is_absurd_cute(text: str) -> bool:
    """Pregunta absurda/tierna sobre la 'vida' de Bob (hambre, novia, etc.)."""
    t = text.lower()
    return any(w in t for w in _ABSURD_CUTE_WORDS)


def is_deep_question(text: str) -> bool:
    """Pregunta filosófica — amerita cara de PENSANDO sostenida."""
    t = text.lower()
    return any(w in t for w in _DEEP_QUESTION_WORDS)


# ═══════════════════════════════════════════════════════════════════════════
# MOOD DRIFT (InteractiveGoal Fase A) — tabla de deltas por contenido
# ═══════════════════════════════════════════════════════════════════════════

def mood_delta_for_user_text(texto: str) -> float:
    """
    Devuelve el delta de mood que corresponde a lo que dijo el usuario.
    Orden de prioridad: amor > insulto > risa > despedida > confusión > normal.
    """
    if not texto:
        return 0.0
    if is_love(texto):
        return +0.25
    if is_insult(texto):
        return -0.30
    if is_laugh(texto):
        return +0.15
    if is_goodbye(texto):
        return -0.10
    if is_confused(texto):
        return -0.05
    return +0.05  # turno normal: la charla fluye


# ═══════════════════════════════════════════════════════════════════════════
# REACCIÓN AUTOMÁTICA A UN TEXTO DEL USUARIO O DE BOB
# ═══════════════════════════════════════════════════════════════════════════

def react_to_user_text(serial_mgr, sm, texto: str) -> None:
    """
    Pulsa la emoción apropiada según lo que el usuario acaba de decir
    (InteractiveGoal Capa 2 — Instant Reaction). Prioridad de detección:
    amor > insulto > risa > preocupación > despedida > confusión >
    absurdo tierno > pregunta filosófica.
    """
    if not texto:
        return
    if is_love(texto):
        # "te quiero", "eres genial" → corazones
        pulse_emotion(serial_mgr, sm, EMO_LOVE_DETECTED, 1500)
    elif is_insult(texto):
        # Híbrido con recuperación rápida: le afecta breve, lo asume con humor
        pulse_sequence(serial_mgr, sm, [('TRISTE', 400), ('CONFUNDIDO', 800)])
    elif is_laugh(texto):
        # El usuario se ríe → Bob se contagia
        pulse_emotion(serial_mgr, sm, 'FELIZ', 800)
    elif is_concern(texto):
        # "¿estás bien?" → Bob nota el interés
        pulse_emotion(serial_mgr, sm, 'CURIOSO', 700)
    elif is_goodbye(texto):
        pulse_emotion(serial_mgr, sm, EMO_GOODBYE, 900)
    elif is_confused(texto):
        # El usuario no entendió a Bob
        pulse_emotion(serial_mgr, sm, EMO_STT_FAIL, 1200)
    elif is_absurd_cute(texto):
        # "¿tienes hambre?" → picardía
        pulse_emotion(serial_mgr, sm, 'TRAVIESO', 600)
    elif is_deep_question(texto):
        # Pregunta filosófica → cara de pensar sostenida (se funde con el
        # estado THINKING que llega enseguida)
        pulse_emotion(serial_mgr, sm, 'PENSANDO', 1500)


def react_to_bob_text(serial_mgr, sm, texto: str) -> None:
    """Pulsa la emoción apropiada según lo que Bob acaba de decir/leer."""
    if not texto:
        return
    if is_happy(texto):
        pulse_emotion(serial_mgr, sm, EMO_HAPPY_RESPONSE, PULSE_FAST)
