"""
voz/pipeline — Pipeline de voz del robot Bob.

Adapta chat.py para integrarse con StateMachine:
  - run() corre en su propio hilo, respeta ev_escuchando para saber cuándo grabar
  - Usa SerialManager en vez de RobotSerial (COM3 ya no se abre aquí)
  - Notifica StateMachine en cada fase: LISTENING → THINKING → SPEAKING → CONV_IDLE
  - Mantiene WakeWordDetector activo siempre (wake word = override en cualquier estado)

Dependencias externas: voicechatLap/config.py y voicechatLap/wake_word.py
(se importan con ruta relativa; ajustar sys.path en main.py si es necesario)
"""

from __future__ import annotations

import io as _io
import json
import os
import random
import re
import sys
import asyncio
import queue
import subprocess
import tempfile
import threading
import time
import wave
from collections import deque
from typing import Optional, List, Dict, Iterator

import numpy as np
import webrtcvad
import edge_tts
import imageio_ffmpeg
import sounddevice as sd
from scipy.signal import butter, sosfilt
from groq import Groq, APIConnectionError
from rich.console import Console

import rutas
from nucleo import puente_cli

# voicechatLap se appendea (no se inserta al frente) para que robot_bob/config.py
# tenga prioridad sobre voicechatLap/config.py al hacer `from config import ...`.
# Esto deja que Abdair edite robot_bob/config.py como la única fuente de verdad
# y que wake_word.py y audio_io.py sigan siendo importables.
_VOICECHAT_DIR = rutas.VOICECHAT_DIR
if _VOICECHAT_DIR not in sys.path:
    sys.path.append(os.path.abspath(_VOICECHAT_DIR))

from config import (
    GROQ_API_KEY,
    USE_ROBOT_SPEAKER, USE_ROBOT_MIC,
    SAMPLE_RATE, FRAME_MS,
    VAD_AGGRESSIVENESS, SILENCE_END_MS, MAX_RECORDING_S, MIN_SPEECH_S,
    PREROLL_FRAMES, NOISE_FLOOR_MIN,
    TRIGGER_MODE, CLIMA_ENABLED, NOTICIAS_ENABLED,
    FRASE_RESPALDO_STT, FRASE_RESPALDO_LLM, JEV_ROUTING_ENABLED, JEV_SIN_WAKE,
    WAKE_CANONICAL, WAKE_PREFIXES, WAKE_MIN_CONF, WAKE_FUZZY_THR,
    WAKE_COOLDOWN_S, WAKE_MAX_UTTR_CHARS,
    GROQ_LLM_MODEL, GROQ_STT_MODEL, TEMPERATURE, MAX_TOKENS, MAX_RETRIES,
    VOICE, TTS_FFMPEG_FILTERS, SENTENCE_MIN_CHARS, TTS_TAIL_S,
    TTS_BACKEND, PIPER_VOICE, PIPER_LENGTH_SCALE, PIPER_PITCH_FACTOR, PIPER_FFMPEG_FILTERS,
    SYSTEM_PROMPT, EXIT_PHRASES, GOODBYE_PHRASES,
    SOLILOQUIO_ENABLED, SOLO_IDLE_MIN_S, PRESENCE_NUDGE_S,
    P_SOLILOQUIO, SOLILOQUIO_COOLDOWN_S, SOLILOQUIO_SETTLE_MS, BANCO_SOLILOQUIO,
    SOLILOQUIO_MAX_CARNADAS,
)

# Gate de energía del wake monitor (silencio → no transcribir).
try:
    from config import WAKE_MIN_LEVEL
except Exception:
    WAKE_MIN_LEVEL = 1.3
try:
    from config import WAKE_WINDOW_S, WAKE_VOICE_LEVEL, WAKE_SILENCE_CUT_S
except Exception:
    WAKE_WINDOW_S, WAKE_VOICE_LEVEL, WAKE_SILENCE_CUT_S = 3.0, 1.0, 0.45

# Cuántos tokens iniciales escanea el detector buscando "Bob" (sensibilidad).
try:
    from config import WAKE_SCAN_TOKENS
except Exception:
    WAKE_SCAN_TOKENS = 2

# Prompt de sesgo para Whisper (mejora el reconocimiento). '' si config viejo.
try:
    from config import STT_PROMPT
except Exception:
    STT_PROMPT = ""

# Memoria persistente (P1).
try:
    from config import MEMORIA_ENABLED
except Exception:
    MEMORIA_ENABLED = False
# Segunda mirada (P1): antes de guardar como nueva a una persona que no reconoció
# (al decir "me llamo X" o al cerrar la charla), Bob toma MIRADA_FRAMES frames más,
# cada MIRADA_PAUSA_S, y vuelve a buscar. Al empezar la charla también, pero solo si
# el primer frame se parece al menos MIRADA_INICIO_MIN a alguien de la base: así no
# demora el saludo de un visitante claramente nuevo.
MIRADA_FRAMES     = 3
MIRADA_PAUSA_S    = 0.2
MIRADA_INICIO_MIN = 0.25
# Registro por turno (perfil feria). Apagado si un config viejo no lo define.
try:
    from config import REGISTRO_TURNOS
except Exception:
    REGISTRO_TURNOS = False
# Voz del dueño (Fase V). Apagado si un config viejo no lo define.
#   VOZ_ID_ENABLED     aprender la voz del dueño (requiere memoria + voice_id)
#   DUENO_ID           persona en memory.db cuya voz se aprende
#   VOZ_CARA_MIN       score facial mínimo para tomar una muestra pasiva
#   VOZ_MIN_S          segundos de voz mínimos por muestra (EXP-1-001: 2 s)
#   VOZ_CONFLICTO_MAX  con ≥ 5 muestras, una frase que puntúa por debajo de esto
#                      contra el promedio no se guarda (la cara manda, V-D6)
try:
    from config import VOZ_ID_ENABLED
except Exception:
    VOZ_ID_ENABLED = False
try:
    from config import DUENO_ID
except Exception:
    DUENO_ID = 1
try:
    from config import VOZ_CARA_MIN, VOZ_MIN_S, VOZ_CONFLICTO_MAX
except Exception:
    VOZ_CARA_MIN, VOZ_MIN_S, VOZ_CONFLICTO_MAX = 0.55, 2.0, 0.30
#   VOZ_UMBRAL         similitud contra el promedio para decir "habla el dueño"
#                      (EXP-1-001: dueño mín 0.476, otra voz máx 0.275; provisional
#                      hasta medir una voz masculina)
#   VOZ_MIN_MUESTRAS   muestras necesarias antes de identificar por voz
#   VOZ_ID_MIN_S       segundos de voz mínimos para identificar (las muestras que se
#                      guardan siguen pidiendo VOZ_MIN_S). EXP-1-001, contra el
#                      promedio: 1.2 s sin errores; 1.0 s sin falsas aceptaciones
#                      pero 3/15 frases del dueño rechazadas. 1.0 s por decisión del
#                      usuario, provisional (los comandos suelen ser cortos)
try:
    from config import VOZ_UMBRAL, VOZ_MIN_MUESTRAS
except Exception:
    VOZ_UMBRAL, VOZ_MIN_MUESTRAS = 0.45, 5
try:
    from config import VOZ_ID_MIN_S
except Exception:
    VOZ_ID_MIN_S = 1.0
# Backend del LLM (charla): "ollama" local o "groq" nube. Import guardado por si
# un config viejo no lo define.
try:
    from config import LLM_BACKEND, OLLAMA_BASE_URL, OLLAMA_MODEL
except Exception:
    LLM_BACKEND, OLLAMA_BASE_URL, OLLAMA_MODEL = "groq", "http://localhost:11434/v1", "qwen2.5:7b"
# Backend Gemini (cloud). Guardado por si un config viejo no lo define.
try:
    from config import GEMINI_BASE_URL, GEMINI_MODEL, GEMINI_API_KEY
except Exception:
    GEMINI_BASE_URL, GEMINI_MODEL, GEMINI_API_KEY = "", "gemini-2.0-flash", ""
# Recorte de historial por turno (ahorro de tokens). Default 10 si falta.
try:
    from config import MAX_HIST_MSGS
except Exception:
    MAX_HIST_MSGS = 10
# Prompt corto y estricto para el LLM local (modelo chico). Si un config viejo
# no lo define, cae al prompt normal.
try:
    from config import SYSTEM_PROMPT_LOCAL
except Exception:
    SYSTEM_PROMPT_LOCAL = SYSTEM_PROMPT
# Fase R1 (R-D1/R-D2). Guardado por si un config viejo no lo define.
#   LLM_ROTACION        modelos de Groq en orden, con LLM_BACKEND = "rotacion";
#                       Ollama (OLLAMA_MODEL) queda de último respaldo
#   STT_FILTROS_ENABLED filtros de alucinación de Whisper (stt.py) y prompt con contexto
#   JEV_EN_PARALELO     el LLM arranca mientras JEV decide la ruta (si las reglas no
#                       ven un comando); si JEV dice que era un comando, se cancela
#   STT_VOCAB_DUENO     vocabulario extra para Whisper con el modo dueño encendido
try:
    from config import LLM_ROTACION
except Exception:
    LLM_ROTACION = ['openai/gpt-oss-20b', 'qwen/qwen3.8-27b', 'openai/gpt-oss-120b']
try:
    from config import STT_FILTROS_ENABLED
except Exception:
    STT_FILTROS_ENABLED = True
STT_TIMEOUT_S = 10.0     # por intento de transcripción (MAX_RETRIES reintentos)
try:
    from config import JEV_EN_PARALELO
except Exception:
    JEV_EN_PARALELO = True
# Fase R2. Guardado por si un config viejo no lo define.
#   AUDIO_CONTINUO_ENABLED  micrófono siempre abierto + Silero VAD (audio_engine.py)
#   STT_ADELANTADO_ENABLED  en charla, transcribir en la pausa corta y cortar ya si
#                           la frase parece completa (si no, esperar la pausa larga)
#   NIVEL_MIN_CHARLA        nivel (RMS %) mínimo para que empiece una frase en charla;
#                           en reposo manda WAKE_MIN_LEVEL (filtra voces lejanas)
try:
    from config import AUDIO_CONTINUO_ENABLED
except Exception:
    AUDIO_CONTINUO_ENABLED = True
try:
    from config import STT_ADELANTADO_ENABLED
except Exception:
    STT_ADELANTADO_ENABLED = True
try:
    from config import NIVEL_MIN_CHARLA
except Exception:
    NIVEL_MIN_CHARLA = 0.2
try:
    from config import TONO_ESCUCHA_ENABLED
except Exception:
    TONO_ESCUCHA_ENABLED = True
try:
    from config import RELLENO_ENABLED, RELLENO_S
except Exception:
    RELLENO_ENABLED, RELLENO_S = True, 1.4

# Fase R3: dictado largo en modo ingeniero (audio_engine.Microfono.dictar).
#   DICTADO_ENABLED       en modo ingeniero, pausas largas, hasta DICTADO_MAX_S y
#                         transcripción por tramos mientras se habla
#   DICTADO_PAUSA_FIN_MS  silencio que termina el dictado ("listo" lo termina ya)
#   STT_VOCAB_INGENIERO   vocabulario técnico para Whisper en modo ingeniero
try:
    from config import DICTADO_ENABLED
except Exception:
    DICTADO_ENABLED = True
try:
    from config import DICTADO_MAX_S, DICTADO_PAUSA_FIN_MS
except Exception:
    DICTADO_MAX_S, DICTADO_PAUSA_FIN_MS = 120.0, 1600.0
try:
    from config import STT_VOCAB_INGENIERO
except Exception:
    STT_VOCAB_INGENIERO = ('ESP32, ESP32-CAM, MicroPython, Arduino, Raspberry Pi, servo MG996R, '
                           'PWM, I2C, UART, SPI, GPIO, MPU6050, giroscopio, acelerómetro, PID, '
                           'OLED, firmware, batería LiPo, regulador, protoboard, Python, ONNX, '
                           'Groq, Claude, Whisper, Silero, Playwright, GitHub, API, LLM, '
                           'Proyecto Génesis. Listo.')
try:
    from config import STT_VOCAB_DUENO
except Exception:
    STT_VOCAB_DUENO = ('Bob, aprende mi voz. Bob, te presento a mi amiga. Noticias de '
                       'tecnología. Modo ingeniero, sal del modo ingeniero. Busca en '
                       'internet. Abdair, Proyecto Génesis.')
# Tope de frases por turno del guard. config.py está gitignoreado (por-máquina);
# si un config viejo no lo define, default a 2.
try:
    from config import MAX_FRASES_TURNO
except Exception:
    MAX_FRASES_TURNO = 2
# P9: recordatorios/temporizadores. Guardado por si un config viejo no lo define.
try:
    from config import RECORDATORIOS_ENABLED
except Exception:
    RECORDATORIOS_ENABLED = True
from wake_word import WakeWordDetector
from asistente.assistant import contexto_asistente   # P9: hora/fecha/clima por inyección
try:
    from config import MUSICA_ENABLED
except Exception:
    MUSICA_ENABLED = False
from asistente.music import (parse_music_command, ejecutar as ejecutar_musica,  # P6: Spotify
                   esta_sonando as sonando_musica,                    # baile
                   intent_desde_jev,                                  # J4: acción por JEV
                   MusicIntent, tema_de)                              # E-B: "¿qué canción?"
# E-B: respuesta de "no" a "¿qué canción quieres que ponga?"
_RE_NO_MUSICA = re.compile(r'\b(?:no|cancela|cancelalo|olvidalo|olvídalo|mejor no)\b', re.IGNORECASE)
from nucleo.show import es_comando_show, run_baile, run_show     # presentación; tecla M baila
# Tecla P: el pedido vale este rato; si no arrancó (Bob ocupado), no salta después.
SHOW_MANUAL_VIGENCIA_S = 30.0
from nucleo.foto import CARA_POR_DEFECTO, cara_foto, es_comando_foto, run_foto   # "ponte para la foto"
from nucleo.modos import ESCENAS, TECLAS as TECLAS_MODO, run_modo   # teclas 1 a 9
from agente import modo_dueno                               # V5: comandos solo para la voz del dueño
from voz import stt                                      # R1: filtros de alucinación de Whisper
from hardware.red import MonitorRed                      # O2: sin internet, Bob no escucha
from llm.llm_router import RouterLLM, Backend       # R1: rotación de LLM por cupo
from voz import audio_engine                             # R2: micrófono continuo + Silero VAD
from asistente.reminders import parse_recordatorio, ReminderStore   # P9: recordatorios
from voz.jev_decision import decidir, EMOCION_RESPALDO   # J2: ruta del turno; J3: emoción
from voz import registro_turnos   # registro por turno (perfil feria)
from nucleo.expression_engine import (
    pulse_emotion, react_to_user_text,
    mood_delta_for_user_text, is_love,
    EMO_WAKE_DETECTED, EMO_GREETING_PLAYED, EMO_AUTO_OPENER,
    EMO_STT_FAIL, EMO_LLM_ERROR,
    PULSE_FAST,
)

# Reexporta lo movido: tests y calibrar_mic usan vp.<nombre>
from voz.pipeline.emocion import (
    _EMO_TAG_RE, _VALID_EMOS, _extract_emo_tag, _marcar_emo, TTS_RATE_BASE, TTS_PITCH_BASE,
    TTS_EMO_PROSODY, _prosody_for_emo,
)
from voz.pipeline.reglas_turno import (
    _RE_HERRAMIENTA, _VERBO_HERRAMIENTA, _RE_ACCION_PROPIA, _RE_NEGACION, _promete_accion,
    _sin_promesas, _GIRO_DER_KW, _GIRO_IZQ_KW, _GIRO_BUSCAR_KW, _GIRO_JEV, _intent_giro,
    _giro_turno, _RE_NOMBRE, _NO_NOMBRE, _extraer_nombre, _RE_APRENDER_VOZ, _pide_aprender_voz,
    _is_exit, _is_goodbye, _es_alucinacion,
)
from voz.pipeline.consola import console
import errores
from voz.pipeline.frases import (
    RELLENOS, FRASE_SIN_ACCION, FRASE_CORTE, REGLA_CHARLA, DESPEDIDAS_JEV, WAKE_GREETINGS,
    AUTO_OPENERS, PREGUNTAS_VOZ, NEXT_TURN_TIMEOUT_VOZ,
)
from voz.pipeline.audio import (
    _LAP_SAMPLE_RATE, _segundos_wav, _mic_device, _VOICE_BANDPASS, _rms_uint8,
    _uint8_to_int16_bytes, _uint8_to_wav,
)
from voz.pipeline.tts import (
    FFMPEG, _SALIDA_SR, _PIPER, _PIPER_LOCK, _piper_audio, _pcm_a_wav, TTS_PCM_ENABLED,
    _edge_tts_bytes, _sintetizar,
)
from voz.pipeline.contexto import (_contexto_asistente_turno, _extra_groq)
from voz.pipeline.texto import (_SENT_RE, _split_sentence)
from voz.pipeline.prefetch import (_Prefetch)


_SILENCE_FRAMES  = SILENCE_END_MS // FRAME_MS


# ── Helpers de audio ───────────────────────────────────────────────────────────


# ── TTS ────────────────────────────────────────────────────────────────────────


def _hay_comando(texto: str) -> bool:
    """R1: ¿las reglas de siempre ven un comando (giro, recordatorio, show, música,
    salida)? Si sí, no se adelanta el LLM: el turno casi seguro no lo usa."""
    try:
        return bool(_intent_giro(texto)
                    or (RECORDATORIOS_ENABLED and parse_recordatorio(texto))
                    or es_comando_show(texto)
                    or es_comando_foto(texto)
                    or (MUSICA_ENABLED and parse_music_command(texto))
                    or _is_exit(texto) or _is_goodbye(texto))
    except Exception:
        return True


# ── Sentence splitter ──────────────────────────────────────────────────────────


# ── VoicePipeline ──────────────────────────────────────────────────────────────

class VoicePipeline:
    """
    Corre en un hilo daemon. Espera ev_escuchando de StateMachine para grabar.
    Flujo por turno: graba → transcribe → detecta wake/exit → LLM → TTS → reproduce.

    serial_mgr: SerialManager  (para comandos ESTADO sin abrir COM3)
    state_machine: StateMachine (para notificar transiciones)
    """

    def __init__(self, serial_mgr, state_machine, audio_io=None,
                 face_id=None, memoria=None, get_frame=None, voice_id=None):
        """
        audio_io: instancia opcional de AudioIO (voicechatLap.audio_io) ya conectada.
        face_id / memoria / get_frame: para la memoria persistente (P1). face_id da
                  el embedding de la cara, memoria lo busca/guarda, get_frame() devuelve
                  el frame actual de la cámara. Si faltan, la memoria queda desactivada.
        voice_id: VoiceID opcional (Fase V) para aprender la voz del dueño. Si falta
                  o VOZ_ID_ENABLED es False, todo sigue igual que sin voz.
        """
        self._serial = serial_mgr
        self._sm     = state_machine
        self._audio_io = audio_io
        self._face_id  = face_id
        self._memoria  = memoria
        self._get_frame = get_frame
        self._voice_id  = voice_id
        self._modo_ingeniero = False         # V5c: lo prende el dueño, dura la charla
        self._sesion_ing = None              # V5c: Claude Code del modo ingeniero
        self._sesion_web = None              # V5d: Claude Code + Playwright MCP
        self._sesion_esc = None              # B3: Claude Code con Edit y Write (solo turnos del dueño)
        self._contexto_web = None            # V5d: resultado web para el turno siguiente
        self._espera_cancion = False         # E-B: Bob preguntó qué canción poner

        # Estado de identidad de la conversación actual (P1).
        self._convo_persona       = None     # id en la base, o None
        self._convo_face_score    = None     # score facial del reconocimiento
        self._convo_emb           = None     # embedding facial de esta charla
        self._convo_edad          = None
        self._persona_nombre      = None
        self._nombre_pendiente    = None
        self._system_prompt_actual = SYSTEM_PROMPT

        self._client = Groq(api_key=GROQ_API_KEY)   # STT (Whisper) SIEMPRE en Groq

        # Cliente de CHAT: local (Ollama, sin tokens) o Groq. La API
        # chat.completions es OpenAI-compatible en ambos. Si Ollama no responde
        # al arrancar, cae solo a Groq.
        # Params extra por backend para las llamadas al LLM. Gemini 2.5 es modelo
        # "thinking": sin esto se come el max_tokens pensando y corta la respuesta.
        self._llm_extra: Dict = {}
        self._usando_ollama = False
        if LLM_BACKEND == "rotacion":
            # R1: Groq en orden (cada modelo con su cupo) y Ollama de último respaldo.
            from openai import OpenAI
            backends = [Backend(m.split('/')[-1], self._client, m, _extra_groq(m), timeout=8.0)
                        for m in LLM_ROTACION]
            backends.append(Backend('ollama', OpenAI(base_url=OLLAMA_BASE_URL, api_key='ollama'),
                                    OLLAMA_MODEL, local=True, timeout=30.0))
            self._llm = RouterLLM(backends, {SYSTEM_PROMPT: SYSTEM_PROMPT_LOCAL},
                                  log=console.print)
            self._llm_model = 'rotación'
            console.print(f"[dim][llm] rotación → {' → '.join(b.nombre for b in backends)}[/]")
        elif LLM_BACKEND == "gemini":
            from openai import OpenAI
            self._llm = OpenAI(base_url=GEMINI_BASE_URL, api_key=GEMINI_API_KEY)
            self._llm_model = GEMINI_MODEL
            try:
                self._llm.models.list()   # ping a Gemini (valida key + red)
                self._llm_extra = {"reasoning_effort": "none"}   # thinking off
                console.print(f"[dim][llm] Gemini OK → {self._llm_model}[/]")
            except Exception as _e:
                errores.aviso('IA', 'Gemini no responde: uso Groq.', _e)
                self._llm = self._client
                self._llm_model = GROQ_LLM_MODEL
        elif LLM_BACKEND == "ollama":
            from openai import OpenAI
            self._llm = OpenAI(base_url=OLLAMA_BASE_URL, api_key="ollama")
            self._llm_model = OLLAMA_MODEL
            try:
                self._llm.models.list()   # ping a Ollama
                self._usando_ollama = True
                console.print(f"[dim][llm] Ollama OK → {self._llm_model}[/]")
            except Exception as _e:
                errores.aviso('IA', 'Ollama (el modelo local) no responde: uso Groq.', _e)
                self._llm = self._client
                self._llm_model = GROQ_LLM_MODEL
        else:
            self._llm = self._client
            self._llm_model = GROQ_LLM_MODEL
            console.print(f"[dim][llm] backend Groq → {self._llm_model}[/]")

        # GPT-OSS usa parte de max_tokens para razonamiento interno. Con el
        # nivel por defecto puede consumir todo el presupuesto y dejar cero
        # texto hablable; Bob entonces vuelve a escuchar sin responder.
        # `low` conserva razonamiento suficiente para charla y deja espacio
        # para que llegue el contenido que consume el TTS.
        if self._llm_model.startswith('openai/gpt-oss-'):
            self._llm_extra = {'reasoning_effort': 'low'}
        # Backends efectivos (pueden diferir de config si Ollama no respondió).
        console.print(f"[dim][perfil] LLM={self._llm_model} "
                      f"({'ollama' if self._usando_ollama else 'nube'}) · "
                      f"TTS={TTS_BACKEND} · STT=groq · "
                      f"clima={CLIMA_ENABLED} · noticias={NOTICIAS_ENABLED} · musica={MUSICA_ENABLED}[/]")

        # Prompt base según el backend efectivo: solo el modelo local chico usa el
        # prompt corto y estricto (no obedece el largo). Gemini/Groq usan el completo.
        self._system_prompt_base = SYSTEM_PROMPT_LOCAL if self._usando_ollama else SYSTEM_PROMPT
        self._system_prompt_actual = self._system_prompt_base

        self._mic = None                       # R2: micrófono continuo (perezoso)
        self._mic_silenciado = False           # tecla S en la ventana de video
        self._corte = threading.Event()        # teclas D/J: cortar lo que Bob está diciendo
        self._cancelar_escucha = threading.Event()   # tecla P/T o cerrar: la escucha corta ya
        self._corte_escuchar = False           # D: tras el corte escucha ya; J: fin de charla
        self._audio_q = None                   # frases listas de la respuesta en curso
        self._mic_lock = threading.Lock()
        self._cupo_stt = stt.CupoSTT()         # R2: transcripciones del último minuto
        self._red = MonitorRed(al_cambiar=self._al_cambiar_red)   # O2: arranca con el wake monitor
        self._stt_listo = None                # R2: (wav, Future) de la transcripción adelantada
        self._convo: List[Dict[str, str]] = []
        self._detener = threading.Event()
        # Anti-eco: cuando Bob habla solo (soliloquio), el wake monitor NO debe
        # grabar su propia voz. Este flag lo silencia mientras dura el soliloquio.
        self._muted = threading.Event()
        # Anti-repetición de soliloquios: últimas frases dichas (texto sin tag).
        self._soliloquio_reciente: deque = deque(maxlen=5)
        # P9: recordatorios pendientes (creados en charla, disparados en background).
        self._recordatorios = ReminderStore()
        # Baile: momento en que arrancó (gracia antes de que el monitor lo corte,
        # porque Spotify tarda un instante en reportar is_playing tras start_playback).
        self._t_baile_inicio = 0.0
        # Hint: "puede haber música sonando". Lo prende un comando de play; mientras
        # esté prendido el monitor consulta a Spotify para sostener/cortar el baile.
        # Se apaga cuando la música efectivamente paró → el monitor deja de consultar.
        self._baile_hint = False
        # J3: decisión de JEV que el monitor en reposo ya pidió para el primer turno.
        # Se usa solo si el texto coincide; así no se llama a JEV dos veces.
        self._jev_previa = None
        self._emocion_turno = EMOCION_RESPALDO   # J3: emoción de JEV para el turno en curso

        self._wake = WakeWordDetector(
            wake_word=WAKE_CANONICAL,
            prefixes=WAKE_PREFIXES,
            min_confidence=WAKE_MIN_CONF,
            fuzzy_threshold=WAKE_FUZZY_THR,
            cooldown_s=WAKE_COOLDOWN_S,
            max_utterance_chars=WAKE_MAX_UTTR_CHARS,
            max_scan_tokens=WAKE_SCAN_TOKENS,
        )

        # Pygame para reproducción local
        import pygame
        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init(frequency=24000, size=-16, channels=1, buffer=512)
        except Exception as e:
            errores.error('VOZ', 'No arrancó el audio de la laptop (pygame): Bob puede quedarse '
                          'mudo. Revisa el parlante o los audífonos.', e)
        self._pygame = pygame

        self._warmup()
        self._hilo = threading.Thread(target=self._loop, daemon=True, name='voice-pipeline')
        self._hilo.start()

    # ── API pública ────────────────────────────────────────────────────────────

    def cerrar(self) -> None:
        self._detener.set()
        self._red.cerrar()
        self._cancelar_escucha.set()
        self._sm.ev_escuchando.set()  # desbloquea el wait
        self._hilo.join(timeout=3.0)
        modo_dueno.fin_charla(self)   # V5: cierra Claude Code y el navegador si quedaron
        mic = getattr(self, '_mic', None)
        if mic:
            mic.cerrar()              # R2

    def _mic_continuo(self):
        """R2: el micrófono continuo, creado la primera vez que se graba (los tests que
        no graban no tocan el hardware). None si está apagado o no se pudo abrir: se
        graba como antes."""
        if not AUDIO_CONTINUO_ENABLED:
            return None
        if getattr(self, '_mic', None) is None and not getattr(self, '_mic_fallo', False):
            with self._mic_lock:
                if getattr(self, '_mic', None) is None and not getattr(self, '_mic_fallo', False):
                    try:
                        mic = audio_engine.Microfono(audio_engine.SileroVAD(), _mic_device(),
                                                     log=console.print)
                        if not mic.abierto:
                            raise RuntimeError('stream cerrado')
                        mic.silenciado = self._mic_silenciado
                        self._mic = mic
                        console.print('[dim][mic] micrófono continuo + Silero VAD[/]')
                    except Exception as e:
                        self._mic_fallo = True
                        errores.aviso('MICRÓFONO', 'No abrió el micrófono continuo: grabo con el '
                                      'método anterior (escucha un poco más lento).', e)
        return getattr(self, '_mic', None)

    def alternar_microfono(self) -> bool:
        """Tecla S: silencia o reactiva el micrófono; Bob sigue con todo lo demás (ojos,
        cabeza, voz). Silenciado, las escuchas esperan sin cortar la charla. Devuelve True
        si quedó activo."""
        self._mic_silenciado = not getattr(self, '_mic_silenciado', False)
        mic = getattr(self, '_mic', None)
        if mic is not None:
            mic.silenciado = self._mic_silenciado
        console.print('[bold red][mic] SILENCIADO: Bob no escucha (S para activar)[/]'
                      if self._mic_silenciado else '[bold green][mic] ACTIVO: Bob escucha[/]')
        return not self._mic_silenciado

    @property
    def microfono_activo(self) -> bool:
        return not getattr(self, '_mic_silenciado', False)

    @property
    def en_linea(self) -> bool:
        """O2: False sin internet. Sin red Bob no escucha: solo teclas."""
        red = getattr(self, '_red', None)
        return red is None or red.en_linea

    def _al_cambiar_red(self, en_linea: bool) -> None:
        """O-D3: el cambio de red se avisa solo en la consola y el CLI; Bob no lo dice."""
        console.print('[bold green][red] Volvió internet: Bob escucha otra vez[/]' if en_linea
                      else '[bold red][red] SIN INTERNET: Bob no escucha; usa las teclas[/]')

    def cortar_respuesta(self, escuchar: bool) -> None:
        """Teclas D (escuchar=True) y J en la ventana de video: cortan lo que Bob está
        diciendo. D lo pone a escuchar enseguida; J termina la charla. Si Bob no está
        respondiendo ni hablando solo, no hacen nada."""
        from nucleo.state_machine import RobotState
        if not (self._muted.is_set()
                or self._sm.estado in (RobotState.THINKING, RobotState.SPEAKING)):
            return
        self._corte_escuchar = escuchar
        self._corte.set()
        q = getattr(self, '_audio_q', None)
        if q is not None:
            try:
                q.put_nowait(None)       # despierta a _stream_and_speak si espera frase
            except queue.Full:
                pass                     # está reproduciendo: el corte lo para igual
        console.print('[bold yellow][corte] Bob se calla y te escucha (D)[/]' if escuchar
                      else '[bold yellow][corte] Bob se calla (J)[/]')

    def presentar_manual(self) -> bool:
        """Tecla P: Bob hace su presentación (el show de "preséntate") sin pedirla por voz.
        En reposo arranca ya; en una charla va en el próximo turno (si Bob está hablando,
        se calla primero; si está escuchando, al terminar la escucha). Hablando solo o con
        música no hace nada. Durante el show (o una pose de foto), P lo corta.
        True si quedó pedida."""
        return self._pedir_manual('show', 'P', 'presentación manual')

    def foto_manual(self) -> bool:
        """Tecla T: Bob posa para la foto (cara por defecto) sin pedirlo por voz.
        Igual que P: en reposo arranca ya, en una charla va en el próximo turno y
        durante una pose (o el show), T la corta. True si quedó pedida."""
        return self._pedir_manual('foto', 'T', 'pose para la foto')

    def modo_manual(self, tecla: str) -> bool:
        """Teclas 1 a 4: modo expresivo; 5 a 9: frase suelta (modos.py); M, baila. Igual que P y T: en
        reposo arranca ya, en una charla va en el próximo turno y durante un modo (o
        el show, o una pose) lo corta. True si quedó pedido."""
        nombre = TECLAS_MODO.get(tecla)
        if nombre is None:
            return False
        return self._pedir_manual(nombre, tecla, f'modo {nombre}')

    def _pedir_manual(self, accion: str, tecla: str, que: str) -> bool:
        etiqueta = f'[{accion}] {tecla}'
        if self._sm.show_activo.is_set():
            console.print(f'[bold yellow]{etiqueta}: corto lo que está haciendo[/]')
            self.cortar_respuesta(escuchar=True)
            return False
        en_charla = self._sm.en_conversacion
        if self._muted.is_set() and not en_charla:
            console.print(f'[dim]{etiqueta}: Bob está hablando solo; probá en un momento[/]')
            return False
        self._accion_manual = accion
        self._show_manual_t = time.monotonic()
        self._cancelar_escucha.set()                # si está escuchando, deja de esperar
        if en_charla:
            self.cortar_respuesta(escuchar=True)    # si está hablando, se calla
        else:
            self._sm.notificar_wake_word(payload=None, saludar=False, buscar=False)
        console.print(f'[bold magenta][{accion}][/] {tecla}: {que}')
        return True

    def _tomar_manual(self) -> Optional[str]:
        """¿Apretaron P, T, 1 a 9 o M hace poco? 'show', 'foto', el modo o None. Lo
        consume: una tecla, una vez. Va al inicio de cada turno, antes de escuchar:
        así la escucha siguiente no sale cancelada."""
        if not self._detener.is_set():
            self._cancelar_escucha.clear()
        t, self._show_manual_t = getattr(self, '_show_manual_t', 0.0), 0.0
        if t > 0 and time.monotonic() - t < SHOW_MANUAL_VIGENCIA_S:
            return getattr(self, '_accion_manual', 'show')
        return None

    def _cortado(self) -> bool:
        corte = getattr(self, '_corte', None)
        return corte is not None and corte.is_set()

    def _esperar_microfono(self) -> None:
        """Sin micrófono continuo (ESP32 o grabación de antes): silenciado, no se graba."""
        while getattr(self, '_mic_silenciado', False) and not self._detener.is_set():
            time.sleep(0.1)

    # ── Loop principal ────────────────────────────────────────────────────────

    def _loop(self) -> None:
        while not self._detener.is_set():
            # Esperar hasta que StateMachine diga LISTENING
            self._sm.ev_escuchando.wait(timeout=0.5)
            if self._detener.is_set():
                break
            if not self._sm.ev_escuchando.is_set():
                continue

            # Capturar flags pendientes ANTES de arrancar la conversación
            pending_audio = self._sm.pending_audio
            pending_text  = self._sm.pending_text
            greeting      = self._sm.greeting_pending
            trigger       = self._sm.conversation_trigger
            self._sm.pending_audio        = None
            self._sm.pending_text         = None
            self._sm.greeting_pending     = False
            self._sm.conversation_trigger = None

            self._run_conversation(pending_audio=pending_audio,
                                   pending_text=pending_text,
                                   greeting=greeting,
                                   trigger=trigger)

    # ── Memoria persistente (P1) ───────────────────────────────────────────────

    def _memoria_activa(self) -> bool:
        return bool(MEMORIA_ENABLED and self._face_id and self._memoria and self._get_frame)

    # ── Voz del dueño (Fase V) ─────────────────────────────────────────────────
    def _voz_activa(self) -> bool:
        return bool(VOZ_ID_ENABLED and self._memoria_activa()
                    and self._voice_id is not None and self._voice_id.listo)

    def _dueno_por_cara(self) -> bool:
        """La cara de esta charla es la del dueño con score suficiente."""
        return (self._convo_persona == DUENO_ID and self._convo_face_score is not None
                and self._convo_face_score >= VOZ_CARA_MIN)

    def _analizar_voz(self, audio: Optional[bytes]):
        """(embedding, segundos de voz) de la frase, o (None, 0.0)."""
        if not audio:
            return None, 0.0
        try:
            return self._voice_id.analizar(audio)
        except Exception as e:
            console.print(f'[dim][voz] análisis falló: {e}[/]')
            return None, 0.0

    def _verificar_voz_turno(self, analisis) -> Optional[float]:
        """Similitud de la frase contra la voz del dueño, o None si no se puede
        decir (pocas muestras o frase corta)."""
        emb, seg = analisis
        if emb is None or seg < VOZ_ID_MIN_S:
            console.print(f'[dim][voz] frase de {seg:.1f} s: muy corta para reconocer la voz[/]')
            return None
        n = self._memoria.total_voces(DUENO_ID)
        if n < VOZ_MIN_MUESTRAS:
            console.print(f'[dim][voz] {n} muestras: faltan para reconocer la voz[/]')
            return None
        return self._memoria.verificar_voz(DUENO_ID, emb)

    def _aplicar_voz_turno(self) -> None:
        """Antes de responder: con la similitud de voz del turno decide quién
        habla. Sin cara reconocida y con la voz del dueño, Bob lo reconoce por la
        voz. Deja en _marca_voz la nota para el LLM ('' si no hay nada que decir)."""
        self._marca_voz = ''
        sim = self._voz_turno
        if sim is None:
            return
        es_dueno = sim >= VOZ_UMBRAL
        if es_dueno and self._convo_persona is None:
            p = self._memoria.persona(DUENO_ID)
            if p is None:
                return
            self._convo_persona = DUENO_ID
            self._persona_nombre = p[1]
            self._memoria.marcar_visto(DUENO_ID)
            self._system_prompt_actual = self._prompt_conocido(DUENO_ID)
            console.print(f'[bold magenta][voz][/] reconocido por la voz: {p[1]} (sim {sim:.2f})')
        nombre = (self._memoria.persona(DUENO_ID) or (None, 'tu creador'))[1]
        if es_dueno:
            if self._convo_persona == DUENO_ID:
                console.print(f'[dim][voz] habla {nombre} (sim {sim:.2f})[/]')
            else:
                console.print(f'[bold magenta][voz][/] habla {nombre} aunque la cara es de '
                              f'otra persona (sim {sim:.2f})')
            self._marca_voz = (f'\n\n[Por su voz, quien te habla en este turno es {nombre}, '
                               'tu creador.]')
        elif self._convo_persona == DUENO_ID:
            console.print(f'[dim][voz] la voz no es la de {nombre} (sim {sim:.2f})[/]')
            self._marca_voz = (f'\n\n[Por su voz, en este turno NO te habla {nombre}: es otra '
                               f'persona que está con él. No la llames {nombre}.]')

    def _muestra_voz(self, audio: Optional[bytes], origen: str, analisis=None) -> bool:
        """Guarda la frase como muestra de voz del dueño si es larga y no
        contradice a las muestras previas. Devuelve True si la guardó."""
        emb, seg = analisis if analisis is not None else self._analizar_voz(audio)
        if emb is None or seg < VOZ_MIN_S:
            console.print(f'[dim][voz] muestra descartada: {seg:.1f} s de voz[/]')
            return False
        if self._memoria.total_voces(DUENO_ID) >= 5:
            sim = self._memoria.verificar_voz(DUENO_ID, emb)
            if sim is not None and sim < VOZ_CONFLICTO_MAX:
                console.print(f'[dim][voz] la voz no coincide con la cara (sim {sim:.2f}): '
                              'no se guarda[/]')
                return False
        self._memoria.agregar_voz(DUENO_ID, emb, origen)
        console.print(f'[bold magenta][voz][/] muestra {origen} guardada ({seg:.1f} s, '
                      f'total {self._memoria.total_voces(DUENO_ID)})')
        return True

    def _aprender_voz(self) -> None:
        """'Bob, aprende mi voz': solo con la cara del dueño delante. Hace unas
        preguntas abiertas y guarda cada respuesta larga como muestra explícita."""
        if not self._dueno_por_cara():
            console.print('[dim][voz] pedido de aprender voz sin la cara del dueño[/]')
            self._sm.iniciar_hablando()
            self._hablar('Solo aprendo la voz de mi creador, y primero tengo que verle la cara.',
                         'SOSPECHANDO')
            return
        self._sm.iniciar_hablando()
        self._hablar('¡Dale! Te hago unas preguntas y me contestas con calma.', 'FELIZ')
        guardadas = 0
        for pregunta in PREGUNTAS_VOZ:
            if self._detener.is_set():
                return
            self._sm.iniciar_hablando()
            self._hablar(pregunta, 'CURIOSO')
            self._sm.iniciar_escuchando()
            audio = self._grabar(initial_timeout=NEXT_TURN_TIMEOUT_VOZ, invitar=False)
            if audio is None:
                break
            guardadas += self._muestra_voz(audio, 'explicito')
        self._sm.iniciar_hablando()
        if guardadas:
            self._hablar(f'¡Listo! Ya aprendí {guardadas} frases de tu voz.', 'ORGULLOSO')
        else:
            self._hablar('No pude escucharte bien. Probemos otro rato.', 'CONFUNDIDO')

    def _reset_identidad(self) -> None:
        """Estado de identidad limpio: nada de la charla anterior."""
        self._convo = []                     # historial fresco por conversación
        self._convo_persona = None
        self._convo_face_score = None
        self._marca_voz     = ''
        self._convo_emb     = None
        self._convo_edad    = None
        self._persona_nombre = None
        self._nombre_pendiente = None
        self._system_prompt_actual = self._system_prompt_base

    def _identificar(self) -> None:
        """Al arrancar la charla: reconoce la cara y personaliza el prompt/saludo."""
        self._reset_identidad()
        if not self._memoria_activa() or not self._face_id.listo:
            return
        # Al venir de un wake word, Bob puede estar todavía girando hacia la
        # persona y el primer frame no tiene cara. Reintentamos hasta ~2.5 s para
        # darle tiempo a que aparezca antes de decidir que no la conocemos
        # (así te reconoce cuando te encara, en vez de saludarte como desconocido).
        RETRY_S = 2.5
        emb, edad = None, None
        t0 = time.monotonic()
        while time.monotonic() - t0 < RETRY_S:
            try:
                emb, edad = self._face_id.analizar(self._get_frame())
            except Exception as e:
                console.print(f'[dim][memoria] análisis falló: {e}[/]')
                break
            if emb is not None:
                break
            if self._detener.is_set():
                break
            time.sleep(0.2)
        self._convo_emb  = emb
        self._convo_edad = edad
        if emb is None:
            return
        m = self._memoria.reconocer(emb)
        cerca = None if m else self._memoria.mas_parecida(emb)
        otra_vez = ''
        if cerca and cerca[2] >= MIRADA_INICIO_MIN:
            # Se parece a alguien sin llegar al umbral: el primer frame pudo salir
            # movido o de costado (Bob todavía girando). Vuelve a mirar antes de
            # tratarla como desconocida.
            m, cara = self._memoria.segunda_mirada(emb, self._frames_nuevos())
            if m:
                otra_vez = ' al volver a mirar'
            else:
                self._convo_emb = cara
                cerca = self._memoria.mas_parecida(cara)
        if m:
            pid, nombre, score = m
            self._convo_persona  = pid
            self._convo_face_score = score
            self._persona_nombre = nombre
            self._memoria.marcar_visto(pid)
            console.print(f'[bold magenta][memoria][/] reconocido{otra_vez}: {nombre} '
                          f'(score {score:.2f})')
            self._system_prompt_actual = self._prompt_conocido(pid)
        else:
            falta = (f' (más parecida: id={cerca[0]} {cerca[1] or "sin nombre"}, '
                     f'score {cerca[2]:.2f})' if cerca else '')
            console.print(f'[bold magenta][memoria][/] persona desconocida{falta}')
            self._system_prompt_actual = (
                self._system_prompt_base + '\n\n═══ MEMORIA ═══\nNo conocés a esta persona todavía. '
                'En algún momento de la charla preguntale su nombre con naturalidad.')

    def _identificar_diferido(self) -> None:
        """Identidad resuelta a mitad de charla (J3). _identificar() resetea la
        charla; si la persona ya dijo "me llamo X" y la cara no es conocida, el
        nombre se conserva para que al cerrar no se guarde una persona sin nombre."""
        nombre = self._nombre_pendiente
        self._identificar()
        if nombre and self._convo_persona is None:
            self._nombre_pendiente = self._persona_nombre = nombre

    def _prompt_conocido(self, pid: int) -> str:
        """System prompt para una persona que Bob ya conoce (por cara o por voz)."""
        return (self._system_prompt_base + '\n\n═══ MEMORIA (ya conocés a esta persona) ═══\n'
                + self._memoria.contexto(pid)
                + '\nSaludala con calidez por su nombre y, si viene al caso, mencioná algo '
                  'que recuerdes de ella. No repitas que sos un robot de feria.'
                  '\nAdaptá tu cercanía a esa relación: con un amigo cercano sé confianzudo, '
                  'cariñoso y bromista; con un conocido reciente, cordial y un poco más medido.')

    def _opener_memoria(self):
        """
        P7 — Conversación autónoma sobre P1: arma un saludo que RETOMA un tema de
        una charla anterior con esta persona reconocida (ej: "la última vez me
        hablabas de tu robot, ¿cómo va?"). Usa el LLM ya configurado (local o
        Groq). Devuelve (texto_limpio, emo) o None si no hay nada que retomar o
        el LLM falla — el caller cae al saludo normal.
        """
        pid = self._convo_persona
        if pid is None:
            return None
        eps = self._memoria.episodios(pid, 3)
        p   = self._memoria.persona(pid)
        temas = (p[4] if p else '') or ''          # columna temas
        if not eps and not temas:
            return None                            # sin recuerdos → saludo normal
        nombre = self._persona_nombre or 'esta persona'
        nivel, _, _ = self._memoria.nivel_relacion(pid)
        recuerdos = '; '.join(t for t, _ in eps) if eps else ''
        ctx = (f"Vas a saludar a {nombre} ({nivel}). "
               f"Temas favoritos suyos: {temas or 'ninguno registrado'}. "
               f"Recuerdos de charlas pasadas: {recuerdos or 'ninguno'}.")
        sys_p = (
            "Eres Bob, un robot sociable de feria. Vas a saludar a alguien que YA "
            "conoces. Saluda retomando con calidez UNO de sus temas o recuerdos de "
            "una charla anterior, como un amigo que retoma la conversación "
            "(ej: 'la última vez me hablabas de tu robot, ¿cómo va eso?'). "
            "UNA sola frase corta y hablable. Sin etiquetas ni corchetes, sin comillas, "
            "sin markdown, sin emojis.")
        try:
            resp = self._llm.chat.completions.create(
                model=self._llm_model,
                messages=[{'role': 'system', 'content': sys_p},
                          {'role': 'user',   'content': ctx}],
                temperature=0.8, max_tokens=60, **self._llm_extra)
            txt = (resp.choices[0].message.content or '').strip()
        except Exception as e:
            console.print(f'[dim][P7] opener memoria falló: {e}[/]')
            return None
        _, clean = _extract_emo_tag(txt)
        if not clean or not any(c.isalnum() for c in clean):
            return None
        return clean, 'FELIZ'   # J3: el LLM no pone tags; saludo de persona conocida = FELIZ

    def _resumir_conversacion(self):
        """LLM resume la charla → (resumen, gustos, temas). None si falla."""
        turns = [m for m in self._convo if m['role'] in ('user', 'assistant')]
        if not turns:
            return None, None, None
        transcript = '\n'.join(
            f"{'Usuario' if m['role'] == 'user' else 'Bob'}: {m['content']}"
            for m in turns[-12:])
        sys_p = ('Sos un extractor de memoria. Resumí la charla entre Bob (robot) y una '
                 'persona. Devolvé SOLO un JSON, sin markdown: '
                 '{"resumen": "una frase en pasado de qué hablaron", '
                 '"gustos": "gustos/intereses mencionados o cadena vacía", '
                 '"temas": "temas de los que hablaron o cadena vacía"}.')
        try:
            resp = self._llm.chat.completions.create(
                model=self._llm_model,
                messages=[{'role': 'system', 'content': sys_p},
                          {'role': 'user', 'content': transcript}],
                temperature=0.3, max_tokens=140, **self._llm_extra)
            txt = (resp.choices[0].message.content or '').strip()
            mt = re.search(r'\{.*\}', txt, re.DOTALL)
            if mt:
                d = json.loads(mt.group(0))
                res = (d.get('resumen') or '').strip() or None
                gus = (d.get('gustos') or '').strip() or None
                tem = (d.get('temas') or '').strip() or None
                return res, gus, tem
        except Exception as e:
            console.print(f'[dim][memoria] resumen falló: {e}[/]')
        return None, None, None

    def _frames_nuevos(self) -> list:
        """Embeddings de MIRADA_FRAMES frames nuevos de la cámara (None donde no
        hay cara), para la segunda mirada: al empezar, si la cara quedó cerca de
        alguien, y antes de guardar a alguien como nuevo."""
        caras = []
        for i in range(MIRADA_FRAMES):
            if self._detener.is_set():
                break
            if i:
                time.sleep(MIRADA_PAUSA_S)
            try:
                emb, _edad = self._face_id.analizar(self._get_frame())
            except Exception as e:
                console.print(f'[dim][memoria] análisis de la segunda mirada falló: {e}[/]')
                break
            caras.append(emb)
        return caras

    def _cerrar_memoria(self) -> None:
        """Al terminar la charla: registra (si nuevo) y guarda recuerdo + gustos/temas."""
        # Sin cara, solo se guarda si la voz identificó a la persona (Fase V).
        if not self._memoria_activa() or (self._convo_emb is None and self._convo_persona is None):
            return
        pid = self._convo_persona
        if pid is None:
            # La cara de la charla no coincidió con nadie. Antes de guardarla como
            # nueva, Bob vuelve a mirar: si el primer frame salió movido o de
            # costado, una persona conocida se duplicaba.
            m, cara = self._memoria.segunda_mirada(self._convo_emb, self._frames_nuevos())
            if m:
                pid, nombre, score = m
                if self._nombre_pendiente and not nombre:
                    self._memoria.actualizar(pid, nombre=self._nombre_pendiente)
                self._persona_nombre = nombre or self._nombre_pendiente
                self._memoria.marcar_visto(pid)
                console.print(f'[bold magenta][memoria][/] al cerrar la reconocí: '
                              f'{self._persona_nombre or "sin nombre"} (id={pid}, '
                              f'score {score:.2f}); no la guardo como nueva')
            else:
                pid = self._memoria.registrar(self._nombre_pendiente, cara, self._convo_edad)
                console.print(f'[bold magenta][memoria][/] persona nueva guardada '
                              f'(id={pid}, nombre={self._nombre_pendiente})')
            self._convo_persona = pid
        resumen, gustos, temas = self._resumir_conversacion()
        if resumen:
            self._memoria.agregar_episodio(pid, resumen)
        self._memoria.actualizar(pid, gustos=gustos, temas=temas)

        # ── Relación social (P2): la charla sube amistad/confianza ─────────────
        turns = len([m for m in self._convo if m['role'] == 'user'])
        mood  = self._sm.mood                       # ánimo final de la charla
        d_amistad   = int(5 + mood * 8 + min(turns, 5) * 1.5)
        d_confianza = int(3 + (5 if self._persona_nombre else 0) + min(turns, 5))
        self._memoria.registrar_interaccion(pid, d_amistad, d_confianza)
        console.print(f'[dim][memoria] id={pid}: recuerdo guardado | '
                      f'amistad {d_amistad:+d}, confianza {d_confianza:+d}[/]')

    def _run_conversation(self, pending_audio: Optional[bytes] = None,
                          pending_text: Optional[str] = None,
                          greeting: bool = False,
                          trigger: Optional[str] = None) -> None:
        """
        Maneja una conversación completa: turno inicial + turnos sucesivos
        sin requerir wake word entre ellos. Termina cuando:
          - El usuario no responde en NEXT_TURN_TIMEOUT segundos
          - El usuario dice una frase de salida/despedida
          - Se llama a cerrar()

        pending_text: si está presente, se usa como primer turno SIN grabar/transcribir
                      (caso típico: "Bob ¿cómo estás?" — el payload tras el wake).
        greeting:     si True, dice un saludo random ANTES de proceder.
        """
        NEXT_TURN_TIMEOUT = 6.0   # s para esperar respuesta del usuario tras hablar Bob
        FIRST_TURN_TIMEOUT = 10.0  # s para el primer turno tras wake word

        es_primer_turno = True
        n_turnos = 0  # contador de turnos para el bonus de mood (charla fluye)
        ruido_seguido = 0  # R1: frases descartadas como ruido, seguidas
        self._espera_cancion = False   # E-B: la espera de "¿qué canción?" no sobrevive la charla

        # ── Identidad (memoria P1): reconocer la cara y personalizar ──────────
        # Activación por JEV sin saludo (pending_text sin greeting): la identidad se
        # resuelve recién antes de la primera respuesta del LLM. Así una orden
        # (música, recordatorio) no espera a buscar la cara.
        # Tecla P/T: el show o la pose arranca ya; la cara se busca después, antes del primer LLM.
        identificado = (greeting or not pending_text) and not getattr(self, '_show_manual_t', 0.0)
        if identificado:
            self._identificar()
        else:
            # Diferida: mientras tanto, nada de la charla anterior (si no, la
            # cara/nombre de la persona previa se usarían en este primer turno).
            self._reset_identidad()

        # ── Saludo / opener al arrancar conversación ──────────────────────────
        if greeting:
            saludo, saludo_emo = None, None
            # P7: si reconocemos a la persona y hay recuerdos, abrimos retomando
            # un tema de una charla anterior en vez del saludo genérico.
            recall = self._opener_memoria() if self._convo_persona is not None else None
            if recall:
                saludo, saludo_emo = recall
                console.print(f'[bold magenta][P7 opener memoria][/] ({saludo_emo}) {saludo}')
                self._serial.cmd_estado(saludo_emo)
                # Registrar lo dicho para que el LLM continúe el hilo del tema.
                self._convo.append({'role': 'assistant', 'content': saludo})
            elif self._persona_nombre:
                # Persona conocida sin recuerdos útiles → saludo por su nombre.
                n = self._persona_nombre
                saludo = random.choice([
                    f'¡Hola {n}! ¡Qué bueno verte de nuevo!',
                    f'¡{n}! ¿Cómo andás?',
                    f'¡Mirá quién volvió! ¿Qué contás, {n}?'])
                saludo_emo = 'FELIZ'
                console.print(f'[bold magenta][saludo memoria][/] {saludo}')
                pulse_emotion(self._serial, self._sm, 'FELIZ', PULSE_FAST)
            elif trigger == 'auto':
                saludo = random.choice(AUTO_OPENERS)
                console.print(f'[bold magenta][opener auto][/] {saludo}')
                pulse_emotion(self._serial, self._sm, EMO_AUTO_OPENER, PULSE_FAST)
            else:
                saludo = random.choice(WAKE_GREETINGS)
                console.print(f'[bold magenta][saludo wake][/] {saludo}')
                pulse_emotion(self._serial, self._sm, EMO_GREETING_PLAYED, PULSE_FAST)
            self._sm.iniciar_hablando()
            self._hablar(saludo, saludo_emo)
            # Si no hay payload pendiente, volvemos a LISTENING para grabar
            if not pending_text:
                self._sm.iniciar_escuchando()

        while not self._detener.is_set():
            self._cerrar_especulacion()   # R1: lo adelantado del turno anterior, si quedó
            # Corte por tecla: D sigue al turno siguiente (escuchar ya); J cierra la charla.
            if self._cortado():
                self._corte.clear()
                if not self._corte_escuchar:
                    console.print('[dim][corte] fin de conversación[/]')
                    break
            # Tecla P/T/1-9/M: el show, la pose, el modo o el baile va en lugar de este turno,
            # sin escuchar ni JEV.
            manual = self._tomar_manual()
            if manual == 'foto':
                console.print('[bold magenta][foto][/] ¡Pose para la foto (tecla T)!')
                pending_text = pending_audio = None
                self._sm.iniciar_hablando()
                run_foto(self, CARA_POR_DEFECTO)
                es_primer_turno = False
                continue
            if manual in ESCENAS:
                console.print(f'[bold magenta][modo][/] ¡Modo {manual}!')
                pending_text = pending_audio = None
                self._sm.iniciar_hablando()
                run_modo(self, manual)
                es_primer_turno = False
                continue
            if manual == 'bailar':
                console.print('[bold magenta][baile][/] ¡A bailar (tecla M)!')
                pending_text = pending_audio = None
                self._sm.iniciar_hablando()
                if run_baile(self):
                    break     # bailando con música → fin de charla, como tras el show
                es_primer_turno = False
                continue
            if manual == 'show':
                console.print('[bold magenta][show][/] ¡Modo presentación (tecla P)!')
                pending_text = pending_audio = None
                self._sm.iniciar_hablando()
                if run_show(self):
                    break     # cerró bailando con música → fin de charla
                es_primer_turno = False
                continue
            if not (pending_text or pending_audio or self.en_linea):
                # O2: sin internet no hay a quién transcribir: tras la tecla, fin de charla.
                console.print('[dim][red] sin internet: no escucho, fin de conversación[/]')
                break
            # ── 1. Obtener input del turno ─────────────────────────────────────
            texto: str = ''
            audio_turno: Optional[bytes] = None   # audio de la frase (Fase V)
            if pending_text:
                # El wake monitor ya transcribió la frase entera junto al wake.
                # Saltamos grabar + STT para este primer turno.
                texto = pending_text
                pending_text = None
                audio_turno = getattr(self, '_audio_wake', None)   # Fase V
                self._audio_wake = None
                self._stt_dudosa, self._stt_descartado = False, ''   # ya filtrado en reposo
                console.print(f'[dim][voice] Usando payload del wake: "{texto}"[/]')
                self._sm.iniciar_pensando()
            elif pending_audio:
                audio = audio_turno = pending_audio
                pending_audio = None
                console.print('[dim][voice] Usando audio de barge-in[/]')
                self._sm.iniciar_pensando()
                console.print('[voice] Transcribiendo...')
                texto = self._transcribir(audio)
            else:
                if not es_primer_turno:
                    self._sm.iniciar_escuchando()
                timeout = FIRST_TURN_TIMEOUT if es_primer_turno else NEXT_TURN_TIMEOUT
                dictado = DICTADO_ENABLED and self._modo_ingeniero   # R3: prompts largos
                if dictado:
                    timeout = max(timeout, 12.0)   # se piensa antes de hablar
                console.print(f'[voice] Escuchando ({timeout:.0f}s timeout'
                              f'{", dictado" if dictado else ""})...')
                if es_primer_turno or getattr(self, '_hablo_desde_escucha', False):
                    self._tono_escucha()      # R4: "ya te escucho" (no tras un ruido)
                self._hablo_desde_escucha = False
                audio = self._grabar(initial_timeout=timeout, adelantar_stt=True, dictado=dictado)
                if getattr(self, '_show_manual_t', 0.0):
                    self._stt_listo = None
                    continue      # tecla P/T durante la escucha: gana a lo grabado
                if audio is None or not audio:
                    console.print('[dim][voice] Silencio prolongado, fin de conversación[/]')
                    break
                audio_turno = audio
                self._sm.iniciar_pensando()
                console.print('[voice] Transcribiendo...')
                texto = self._transcribir(audio)

            if not texto and getattr(self, '_stt_fallo', False):
                # Groq caído (red/API): no es silencio. Lo decimos y cerramos el
                # turno; el wake monitor vuelve a escuchar.
                errores.error('ESCUCHA', 'Groq no puede transcribir (¿sin internet o caído?): Bob '
                              'dice una frase de respaldo y vuelve a esperar "Bob".')
                self._sm.iniciar_hablando()
                self._hablar(FRASE_RESPALDO_STT, 'CONFUNDIDO')
                break

            if not texto and getattr(self, '_stt_descartado', ''):
                # R1: era ruido o una alucinación de Whisper. No es un turno: se sigue
                # escuchando. Varios seguidos → la charla terminó. E-A: si hubo voz real
                # (≥ 1 s), Bob lo dice: en silencio el dueño cree que lo ignoró.
                ruido_seguido += 1
                if ruido_seguido >= 3:
                    console.print('[dim][voice] solo ruido, fin de conversación[/]')
                    break
                if _segundos_wav(audio_turno) >= 1.0:
                    self._sm.iniciar_hablando()
                    self._hablar('¿Perdón? No te escuché bien.', 'CONFUNDIDO')
                es_primer_turno = False
                continue
            ruido_seguido = 0

            if texto and getattr(self, '_stt_dudosa', False):
                # R1: transcripción con confianza baja → pedir que repita, no adivinar.
                self._sm.stt_fail_streak += 1
                self._sm.iniciar_hablando()
                self._serial.cmd_estado('CONFUNDIDO')
                if self._sm.stt_fail_streak >= 2:
                    self._sm.stt_fail_streak = 0
                    self._hablar('Perdón, no te estoy escuchando bien. '
                                 'Acércate un poquito y dime de nuevo, ¿sí?', 'CONFUNDIDO')
                else:
                    self._hablar(random.choice(['¿Perdón? ¿Me lo repites?',
                                                'No te entendí bien, ¿me lo dices otra vez?',
                                                '¿Cómo? Repítemelo, porfa.']), 'CONFUNDIDO')
                es_primer_turno = False
                continue

            if not texto:
                console.print('[dim][voice] Audio sin texto[/]')
                # Pulso CONFUNDIDO para señalar visualmente que no entendió
                pulse_emotion(self._serial, self._sm, EMO_STT_FAIL, PULSE_FAST)
                self._sm.mood_event(-0.05)  # silencio incomprendido
                self._sm.stt_fail_streak += 1
                if self._sm.stt_fail_streak >= 2:
                    # Fase C: 2 fallos seguidos → Bob lo reconoce con empatía
                    # y da otra oportunidad en vez de cortar la conversación.
                    self._sm.stt_fail_streak = 0
                    console.print('[dim][voice] 2 fallos STT → mensaje empático[/]')
                    self._sm.iniciar_hablando()
                    self._serial.cmd_estado('CONFUNDIDO')
                    self._hablar('Perdón, no te estoy escuchando bien. '
                                 'Acércate un poquito y dime de nuevo, ¿sí?', 'CONFUNDIDO')
                    es_primer_turno = False
                    continue
                break

            with puente_cli.turno('usuario'):            # el CLI lo resalta
                console.print(f'[bold cyan]Usuario:[/] {texto}')

            if self._modo_ingeniero and stt.es_cancelar_dictado(texto):
                # R3: "…no, borra eso" al final de un dictado → no se envía nada.
                self._sm.iniciar_hablando()
                self._hablar('Listo, lo borré. Te escucho.', 'CURIOSO')
                es_primer_turno = False
                continue

            # ── Memoria: ¿se presentó? ("me llamo X") → guardar nombre ─────────
            if self._memoria_activa():
                nombre = _extraer_nombre(texto)
                if nombre and nombre != self._persona_nombre:
                    self._persona_nombre = nombre
                    self._nombre_pendiente = nombre
                    if self._convo_persona is None and self._convo_emb is not None:
                        # Antes de guardarla como nueva, Bob vuelve a mirar: si la cara
                        # del inicio salió mala, quizás ya la conoce. Si es así, sigue
                        # como si la hubiera reconocido al empezar (y toma este nombre).
                        m, cara = self._memoria.segunda_mirada(self._convo_emb,
                                                               self._frames_nuevos())
                        if m:
                            pid, viejo, score = m
                            self._convo_persona, self._convo_face_score = pid, score
                            self._memoria.actualizar(pid, nombre=nombre)
                            self._memoria.marcar_visto(pid)
                            self._system_prompt_actual = self._prompt_conocido(pid)
                            console.print(f'[bold magenta][memoria][/] al volver a mirar la '
                                          f'reconocí: {viejo or "sin nombre"} (id={pid}, '
                                          f'score {score:.2f})')
                        else:
                            # registrar ya para asociar cara↔nombre desde este momento
                            self._convo_persona = self._memoria.registrar(
                                nombre, cara, self._convo_edad)
                    elif self._convo_persona is not None:
                        self._memoria.actualizar(self._convo_persona, nombre=nombre)
                    console.print(f'[bold magenta][memoria][/] nombre aprendido: {nombre}')

            # Reaccionar emocionalmente a lo que dijo el usuario
            # (AMOR / TRISTE / CONFUNDIDO según contenido)
            react_to_user_text(self._serial, self._sm, texto)

            # ── Mood drift (Fase A) + continuidad (Fase C) ─────────────────
            self._sm.stt_fail_streak = 0   # transcripción exitosa
            self._sm.mood_decay()                                  # 5% hacia 0
            delta = mood_delta_for_user_text(texto)
            self._sm.mood_event(delta)
            if is_love(texto):
                self._sm.mood_floor(0.6)   # cariño directo: salto de ánimo
            if delta > 0:
                self._sm.positive_streak += 1
            elif delta < 0:
                self._sm.positive_streak = 0
            n_turnos += 1
            if n_turnos > 4:
                self._sm.mood_event(0.10)  # la conversación fluye — bonus
            console.print(f'[dim][mood] {self._sm.mood:+.2f}  '
                          f'racha+{self._sm.positive_streak}[/]')

            # Limpiar wake word residual si el usuario repitió "Bob"
            ww = self._wake.detect(texto)
            if ww.detected and ww.payload:
                texto = ww.payload

            if not texto.strip():
                es_primer_turno = False
                continue

            # ── Fase V: "Bob, aprende mi voz" / quién habla / muestra pasiva ───
            self._voz_turno = None
            if self._voz_activa():
                if _pide_aprender_voz(texto):
                    if not identificado:       # vino como payload del wake: ver la cara ya
                        self._identificar_diferido()
                        identificado = True
                    self._aprender_voz()
                    es_primer_turno = False
                    continue
                analisis = self._analizar_voz(audio_turno)
                self._voz_turno = self._verificar_voz_turno(analisis)   # antes de sumar la muestra
                if self._dueno_por_cara():
                    self._muestra_voz(audio_turno, 'pasivo', analisis)
                # V5: comandos que solo obedecen a la voz del dueño
                if modo_dueno.atender(self, texto, analisis):
                    # Si el comando fijó con quién se habla, no reidentificar después.
                    identificado = identificado or self._convo_persona is not None
                    es_primer_turno = False
                    continue

            # ── R1: mientras JEV decide, identificar y adelantar el LLM ────────
            # Solo si las reglas no ven un comando; si JEV dice que lo era, se cancela.
            self._ruta_turno = None
            if self._puede_especular(texto, es_primer_turno):
                self._hilo_prep = threading.Thread(
                    target=self._preparar_respuesta, args=(texto, not identificado, True),
                    daemon=True, name='llm-especular')
                self._hilo_prep.start()
                identificado = True

            # ── J2: ruta del turno con JEV (None = parsers de siempre) ─────────
            ruta = self._decidir_ruta(texto)
            self._ruta_turno = ruta

            # ── J3: "vete", "adiós Bob": Bob se despide y termina la charla ─────
            if ruta == 'despedida':
                console.print('[dim][jev] despedida → fin de la charla[/]')
                self._sm.iniciar_hablando()
                self._hablar(random.choice(DESPEDIDAS_JEV), 'FELIZ')
                break
            # ── J3: en medio de la charla, lo que no le habla a Bob no se contesta ─
            if ruta == 'ajeno' and not es_primer_turno:
                console.print('[dim][jev] no le habla a Bob → silencio[/]')
                continue

            # ── J5: giro ("date la vuelta", "a tu derecha"): lo decide JEV ──────
            intent = _giro_turno(texto, ruta)
            if intent:
                self._sm.scan_request = intent
                self._sm.iniciar_hablando()
                ack = random.choice(['¡Voy para allá!', '¡Ya te busco!',
                                     '¡Me doy la vuelta!', '¡A ver dónde estás!'])
                with puente_cli.turno('bob'):
                    console.print(f'[bold green]Bob[/] [dim](giro:{intent})[/]: {ack}')
                self._hablar(ack, 'TRAVIESO')
                es_primer_turno = False
                continue

            # ── P9: crear recordatorio ("recuérdame en 5 min que...") ──────────
            if RECORDATORIOS_ENABLED and ruta in (None, 'recordatorio'):
                rec = parse_recordatorio(texto)
                if rec:
                    self._recordatorios.agregar(rec)
                    self._sm.iniciar_hablando()
                    ack = random.choice([
                        f'¡Listo! Te aviso {rec.cuando_str}.',
                        f'¡Anotado! Te lo recuerdo {rec.cuando_str}.',
                        f'¡Hecho! {rec.cuando_str} te aviso, tranqui.'])
                    with puente_cli.turno('bob'):
                        console.print(f'[bold green]Bob[/] [dim](recordatorio '
                                      f'{rec.cuando_str}: "{rec.que}")[/]: {ack}')
                    self._hablar(ack, 'FELIZ')
                    es_primer_turno = False
                    continue

            # ── FOTO: "ponte (feliz) para la foto" → pose con esa cara ──────────
            cara = cara_foto(texto, ruta)
            if cara:
                console.print(f'[bold magenta][foto][/] pose {cara}')
                self._sm.iniciar_hablando()
                run_foto(self, cara)
                es_primer_turno = False
                continue

            # ── SHOW de presentación: "presentate / qué sabés hacer" ───────────
            # J5: la presentación la decide JEV; sin respuesta, palabras clave.
            if ruta == 'presentacion' or (ruta is None and es_comando_show(texto)):
                console.print('[bold magenta][show][/] ¡Modo presentación!')
                self._sm.iniciar_hablando()
                if run_show(self):
                    break     # cerró bailando con música → fin de charla,
                              # wake monitor queda activo (decir "Bob" lo corta)
                es_primer_turno = False
                continue      # sin música: sigue la charla normal

            # ── P6: control de música (Spotify) ────────────────────────────────
            # E-B: si Bob acababa de preguntar "¿qué canción?", este turno es la respuesta.
            esperaba_cancion, self._espera_cancion = self._espera_cancion, False
            ruta_musica = (esperaba_cancion or ruta is None or ruta == 'musica'
                           or (ruta or '').startswith('musica_'))
            if MUSICA_ENABLED and ruta_musica:
                # modo ingeniero: solo con una palabra de música ("pon un botón" no es Spotify)
                mintent = parse_music_command(texto,
                                              estricto=getattr(self, '_modo_ingeniero', False))
                if ruta and ruta.startswith('musica_'):
                    mintent = intent_desde_jev(ruta, texto, mintent)   # J4: JEV manda la acción
                if mintent is None and esperaba_cancion:
                    if _RE_NO_MUSICA.search(texto):
                        self._sm.iniciar_hablando()
                        self._hablar('Ok, no pongo nada.', 'FELIZ')
                        es_primer_turno = False
                        continue
                    if tema_de(texto) and not _is_exit(texto) and not _is_goodbye(texto):
                        mintent = MusicIntent('play', query=tema_de(texto))
                if mintent is None and ruta == 'musica_play':
                    # E-B: JEV entendió "pon música" pero no hay tema. Antes caía a la
                    # charla y el LLM inventaba que no podía; ahora pregunta.
                    self._sm.iniciar_hablando()
                    self._hablar('¿Qué canción quieres que ponga?', 'CURIOSO')
                    self._espera_cancion = True
                    es_primer_turno = False
                    continue
                if mintent:
                    self._sm.iniciar_hablando()
                    ack = ejecutar_musica(mintent)          # hace la llamada a Spotify
                    with puente_cli.turno('bob'):
                        console.print(f'[bold green]Bob[/] [dim](música:{mintent.accion}'
                                      f'{" «"+mintent.query+"»" if mintent.query else ""})[/]: {ack}')
                    # Baile: si arrancó música, Bob sale de la charla y se pone a
                    # bailar con el wake monitor activo (decir "Bob" lo corta). Si
                    # la pausó, deja de bailar.
                    if mintent.accion in ('play', 'play_playlist', 'resume'):
                        self._t_baile_inicio = time.monotonic()
                        self._baile_hint = True
                        self._sm.musica_sonando.set()
                        self._sm.bailando.set()
                        self._hablar(ack, 'MUY_FELIZ')
                        break          # fin de charla → wake monitor + baile activos
                    if mintent.accion == 'pause':
                        self._baile_hint = False
                        self._sm.musica_sonando.clear()
                        self._sm.bailando.clear()
                    self._hablar(ack, 'FELIZ')
                    es_primer_turno = False
                    continue

            # ── 4. Comandos de salida (cualquier turno) ────────────────────────
            if _is_exit(texto):
                self._hablar('Hasta luego.')
                break
            if _is_goodbye(texto):
                self._hablar('Hasta pronto.')
                break

            # ── 5. LLM + TTS paralelo ──────────────────────────────────────────
            hilo_prep, self._hilo_prep = getattr(self, '_hilo_prep', None), None
            if hilo_prep is not None:
                hilo_prep.join()      # R1: identidad y LLM ya salieron en paralelo con JEV
            else:
                self._preparar_respuesta(texto, not identificado, False)
                identificado = True
            console.print('[voice] Pensando...')
            captured = self._stream_and_speak(texto)

            # Si hubo barge-in, el audio capturado va al siguiente turno
            pending_audio = captured
            es_primer_turno = False

        # ── 6. Fin de conversación ─────────────────────────────────────────────
        self._cerrar_especulacion()
        # P3: la charla ajusta los estados internos (sociabilidad/motivación suben,
        # energía baja, curiosidad se sacia) según cuántos turnos y qué ánimo hubo.
        try:
            self._sm.ei_evento_charla(n_turnos, self._sm.mood)
        except Exception:
            pass
        try:
            self._cerrar_memoria()   # guarda recuerdo + perfil de la persona
        except Exception as e:
            console.print(f'[dim][memoria] cierre falló: {e}[/]')
        modo_dueno.fin_charla(self)  # V5c: el modo ingeniero dura la charla
        # D sobre la frase que cerraba la charla ("Hasta luego", "Poniendo…"): escuchar ya.
        if self._cortado():
            self._corte.clear()
            if self._corte_escuchar:
                self._sm.iniciar_escuchando()
                return
        self._sm.fin_turno()

    # ── R1: respuesta adelantada (JEV en paralelo) ───────────────────────────

    def _puede_especular(self, texto: str, es_primer_turno: bool) -> bool:
        """Adelantar el LLM solo cuando vale la pena y no puede salir mal:
        JEV va a tardar (no hay decisión previa), no es modo ingeniero (Claude),
        no hay recordatorios por entregar (sacarlos de la cola es un efecto) y las
        reglas no ven un comando. A mitad de charla, solo si Bob preguntó algo o le
        dicen "Bob" (lo demás puede ser charla ajena: no gastar cupo)."""
        if not (JEV_EN_PARALELO and JEV_ROUTING_ENABLED):
            return False
        previa = getattr(self, '_jev_previa', None)
        if previa and previa[0] == texto:
            return False
        if modo_dueno.ajustes_ingeniero(self):
            return False
        if RECORDATORIOS_ENABLED and self._recordatorios.hay_vencidos():
            return False
        if _hay_comando(texto):
            return False
        if es_primer_turno:
            return True
        ultimo = next((m['content'] for m in reversed(self._convo) if m['role'] == 'assistant'), '')
        return ultimo.rstrip().endswith('?') or 'bob' in stt.palabras(texto)

    def _preparar_respuesta(self, texto: str, identificar: bool, adelantar: bool) -> None:
        """Lo que va antes del LLM: identidad (si estaba diferida) y quién habla.
        Con adelantar, además lanza el pedido al LLM (_Prefetch)."""
        try:
            if identificar:
                self._identificar_diferido()   # J3: diferido desde el arranque
            if self._voz_activa():
                self._aplicar_voz_turno()     # Fase V: quién habla, después de la cara
            if adelantar:
                llm, kwargs, _ = self._armar_pedido(texto, None, None)
                self._prefetch = _Prefetch(llm, kwargs)
        except Exception as e:
            console.print(f'[dim][llm] no se pudo adelantar: {e}[/]')

    def _cerrar_especulacion(self) -> None:
        hilo, self._hilo_prep = getattr(self, '_hilo_prep', None), None
        if hilo is not None:
            hilo.join(timeout=5.0)
        pre, self._prefetch = getattr(self, '_prefetch', None), None
        if pre is not None:
            pre.close()

    # ── Grabación ────────────────────────────────────────────────────────────

    def _grabar(self, initial_timeout: float = 8.0, invitar: bool = True,
                adelantar_stt: bool = False, reposo: bool = False,
                dictado: bool = False) -> Optional[bytes]:
        """
        Graba con VAD. Despacha a robot (ESP32 TCP) o laptop (sounddevice)
        según el flag USE_ROBOT_MIC y la disponibilidad del AudioIO.
        invitar: si False, no dispara el pulso CURIOSO de invitación (laptop).
        R2 (micrófono continuo): adelantar_stt transcribe en la pausa corta (charla);
        reposo exige el nivel de WAKE_MIN_LEVEL para empezar (voces cercanas).
        R3: dictado (modo ingeniero) tolera pausas largas y transcribe por tramos.
        """
        if USE_ROBOT_MIC and self._audio_io is not None and self._audio_io.connected:
            self._esperar_microfono()
            return self._grabar_robot(initial_timeout)
        mic = self._mic_continuo()
        if mic is not None:
            return self._grabar_continuo(mic, initial_timeout, invitar, adelantar_stt, reposo,
                                         dictado)
        self._esperar_microfono()
        return self._grabar_laptop(initial_timeout, invitar)

    def _grabar_continuo(self, mic, initial_timeout: float, invitar: bool,
                         adelantar_stt: bool, reposo: bool, dictado: bool = False) -> Optional[bytes]:
        """R2: frase desde el micrófono continuo. Si la transcripción salió en la pausa
        (o por tramos, en un dictado), queda en _stt_listo para que _transcribir la use
        sin pedirla otra vez."""
        if dictado and STT_FILTROS_ENABLED:
            frase = mic.dictar(
                transcribir=lambda wav, previo: self._stt_bruto(wav, True, previo or None)[0],
                timeout_inicial=initial_timeout, max_s=DICTADO_MAX_S,
                pausa_fin_ms=DICTADO_PAUSA_FIN_MS, nivel_min=NIVEL_MIN_CHARLA,
                puede_tramo=lambda: self._cupo_stt.libre(reserva=4),
                cancelar=self._cancelar_escucha)
            if frase is None:
                return None
            wav = frase.wav()
            self._stt_listo = (wav, frase.adelantado) if frase.adelantado is not None else None
            console.print(f'[dim][dictado] {frase.segundos_voz:.1f} s de voz[/]')
            return wav
        al_pausa = None
        if (adelantar_stt and STT_ADELANTADO_ENABLED and STT_FILTROS_ENABLED
                and self._cupo_stt.libre(reserva=8)):
            al_pausa = lambda wav: self._stt_bruto(wav, True)   # noqa: E731
        frase = mic.escuchar(
            timeout_inicial=initial_timeout, max_s=MAX_RECORDING_S,
            silencio_fin_ms=SILENCE_END_MS,
            nivel_min=WAKE_MIN_LEVEL if reposo else NIVEL_MIN_CHARLA,
            al_pausa=al_pausa,
            invitar=(lambda: pulse_emotion(self._serial, self._sm, 'CURIOSO', 700)) if invitar else None,
            cancelar=self._detener if reposo else self._cancelar_escucha)   # P/T corta la de charla
        if frase is None:
            return None
        wav = frase.wav()
        if frase.adelantado is not None:
            self._stt_listo = (wav, frase.adelantado)
        return wav

    def _grabar_robot(self, initial_timeout: float) -> Optional[bytes]:
        """
        Graba uint8 @ 8 kHz desde el mic del ESP32 vía AudioIO.
        Misma lógica de VAD + endpointing que la versión laptop.
        """
        self._audio_io.drain_mic()  # tirar audio viejo
        vad_local   = webrtcvad.Vad(VAD_AGGRESSIVENESS)
        u8_per_frame = SAMPLE_RATE * FRAME_MS // 1000  # 240 bytes / frame VAD a 8 kHz

        preroll: deque[bytes] = deque(maxlen=PREROLL_FRAMES)
        recorded: List[bytes] = []
        buffer = bytearray()
        speech_started = False
        silence_streak = 0
        speech_ms      = 0
        waited_ms      = 0
        initial_timeout_ms = int(initial_timeout * 1000)

        while True:
            chunk = self._audio_io.get_mic(timeout=0.5)
            if chunk is None:
                waited_ms += 500
                if not speech_started and waited_ms > initial_timeout_ms:
                    return None
                continue
            buffer.extend(chunk)

            # Slice frames VAD de tamaño fijo
            while len(buffer) >= u8_per_frame:
                frame_u8 = bytes(buffer[:u8_per_frame])
                del buffer[:u8_per_frame]

                # webrtcvad requiere int16 → convertir
                frame_i16 = _uint8_to_int16_bytes(frame_u8)
                try:
                    is_vad = vad_local.is_speech(frame_i16, SAMPLE_RATE)
                except Exception:
                    is_vad = False

                rms = _rms_uint8(frame_u8)

                if not speech_started:
                    preroll.append(frame_u8)
                    waited_ms += FRAME_MS
                    if is_vad and rms > NOISE_FLOOR_MIN:
                        speech_started = True
                        recorded.extend(preroll)
                        speech_ms = FRAME_MS * len(preroll)
                    elif waited_ms > initial_timeout_ms:
                        return None
                else:
                    recorded.append(frame_u8)
                    speech_ms += FRAME_MS
                    if is_vad and rms > NOISE_FLOOR_MIN:
                        silence_streak = 0
                    else:
                        silence_streak += 1
                    if silence_streak >= _SILENCE_FRAMES:
                        if speech_ms < MIN_SPEECH_S * 1000:
                            return None
                        raw = b''.join(recorded)
                        return _uint8_to_wav(raw)
                    if speech_ms >= MAX_RECORDING_S * 1000:
                        raw = b''.join(recorded)
                        return _uint8_to_wav(raw)

    def _grabar_laptop(self, initial_timeout: float = 8.0, invitar: bool = True) -> Optional[bytes]:
        """
        Graba desde micrófono de laptop con VAD + gate adaptativo.
        initial_timeout: segundos máximo de silencio inicial antes de devolver None.
        invitar: pulso CURIOSO a los 3 s sin hablar (Fase D).
        """
        q: queue.Queue = queue.Queue()

        def callback(indata, frames, t, status):
            q.put(bytes(indata))

        frame_bytes = int(_LAP_SAMPLE_RATE * FRAME_MS / 1000) * 2  # int16
        vad_local   = webrtcvad.Vad(VAD_AGGRESSIVENESS)
        initial_timeout_ms = int(initial_timeout * 1000)

        preroll:  deque[bytes] = deque(maxlen=PREROLL_FRAMES)
        recorded: List[bytes]  = []
        speech_started = False
        silence_streak = 0
        speech_ms      = 0
        waited_ms      = 0
        invite_sent    = False  # Fase D: CURIOSO de invitación a los 3 s

        with sd.RawInputStream(device=_mic_device(), samplerate=_LAP_SAMPLE_RATE, channels=1,
                               dtype='int16', blocksize=frame_bytes // 2,
                               callback=callback):
            while True:
                try:
                    chunk = q.get(timeout=1.0)
                except queue.Empty:
                    chunk = None

                if chunk is None:
                    waited_ms += 1000
                    if not speech_started and waited_ms > initial_timeout_ms:
                        return None
                    continue

                # El VAD de webrtcvad necesita frames de 10/20/30 ms exactos
                if len(chunk) < frame_bytes:
                    continue
                frame = chunk[:frame_bytes]

                try:
                    is_vad = vad_local.is_speech(frame, _LAP_SAMPLE_RATE)
                except Exception:
                    is_vad = False

                arr   = np.frombuffer(frame, dtype=np.int16).astype(np.float32)
                level = float(np.sqrt(np.mean(arr * arr))) / 32767.0 * 100.0

                if not speech_started:
                    preroll.append(frame)
                    waited_ms += FRAME_MS
                    # Fase D: a los 3 s sin hablar, Bob "te invita" con ojos curiosos
                    if invitar and not invite_sent and waited_ms > 3000:
                        invite_sent = True
                        pulse_emotion(self._serial, self._sm, 'CURIOSO', 700)
                    if is_vad and level > 1.5:
                        speech_started = True
                        recorded.extend(preroll)
                        speech_ms = FRAME_MS * len(preroll)
                    elif waited_ms > initial_timeout_ms:
                        return None
                else:
                    recorded.append(frame)
                    speech_ms += FRAME_MS

                    if is_vad and level > 1.5:
                        silence_streak = 0
                    else:
                        silence_streak += 1

                    if silence_streak >= _SILENCE_FRAMES:
                        break
                    if speech_ms >= MAX_RECORDING_S * 1000:
                        break

        if speech_ms < MIN_SPEECH_S * 1000:
            return None

        raw = b''.join(recorded)
        buf = _io.BytesIO()
        with wave.open(buf, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(_LAP_SAMPLE_RATE)
            wf.writeframes(raw)
        return buf.getvalue()

    # ── STT ──────────────────────────────────────────────────────────────────

    def _prompt_stt(self, contexto: bool = True, previo: Optional[str] = None) -> str:
        """R1: prompt de sesgo de Whisper. Vocabulario de los comandos y, en charla, lo
        último que dijo Bob (la respuesta del usuario se transcribe con ese contexto).
        R3: en un dictado, `previo` es el texto del tramo anterior; en modo ingeniero
        se suma el vocabulario técnico."""
        if not STT_FILTROS_ENABLED:
            return STT_PROMPT
        vocab = STT_VOCAB_DUENO if modo_dueno.MODO_DUENO_ENABLED else ''
        if getattr(self, '_modo_ingeniero', False):
            vocab = (vocab + ' ' + STT_VOCAB_INGENIERO).strip()
        ultimo = previo or ''
        if not ultimo and contexto and self._convo:
            ultimo = next((m['content'] for m in reversed(self._convo)
                           if m['role'] == 'assistant'), '')
        return stt.armar_prompt(STT_PROMPT, vocab, ultimo)

    def _transcribir(self, wav_bytes: bytes, contexto: bool = True) -> str:
        """Texto de la frase, o '' si no hubo voz o Groq falló (ver _stt_fallo).
        R1: descarta alucinaciones (_stt_descartado) y marca lo dudoso (_stt_dudosa).
        R2: si esa misma grabación ya se transcribió en la pausa, usa ese resultado."""
        t0 = time.monotonic()
        listo, self._stt_listo = getattr(self, '_stt_listo', None), None
        r = None
        if listo is not None and listo[0] is wav_bytes:
            try:
                r = listo[1].result(timeout=20.0)
            except Exception:
                r = None
        if isinstance(r, str):     # R3: dictado (texto unido de sus tramos)
            r = (r, False, False, '' if r else 'ruido')
        if r is None:
            r = self._stt_bruto(wav_bytes, contexto)
        texto, self._stt_fallo, self._stt_dudosa, self._stt_descartado = r
        self._stt_s = round(time.monotonic() - t0, 3)
        return texto

    def _stt_bruto(self, wav_bytes: bytes, contexto: bool = True,
                   previo: Optional[str] = None) -> tuple:
        """(texto, fallo, dudosa, descartado). fallo=True solo si Groq falló (no si el
        audio era silencio). No toca el estado: lo puede correr otro hilo (R2)."""
        prompt = self._prompt_stt(contexto, previo)
        formato = 'verbose_json' if STT_FILTROS_ENABLED else 'json'
        trans = self._client.audio.transcriptions
        crear = getattr(getattr(trans, 'with_raw_response', None), 'create', None)
        for attempt in range(MAX_RETRIES + 1):
            try:
                self._cupo_stt.registrar()
                args = dict(file=('audio.wav', wav_bytes, 'audio/wav'),
                            model=GROQ_STT_MODEL,
                            language='es',
                            # Sesgo de vocabulario: "Bob", los comandos y el contexto.
                            prompt=prompt or None,
                            # temperature=0 → transcripción determinista, menos alucinación.
                            temperature=0.0,
                            response_format=formato,
                            # Un pedido colgado no frena el turno: 2 min de audio
                            # tardan ~1–2 s; el SDK esperaría 60 s por intento.
                            timeout=STT_TIMEOUT_S)
                headers = {}
                if crear is not None:
                    raw = crear(**args)
                    resp, headers = raw.parse(), getattr(raw, 'headers', {}) or {}
                else:
                    resp = trans.create(**args)
                self._avisar_cupo_stt(headers)
                if not STT_FILTROS_ENABLED:
                    return (getattr(resp, 'text', '') or '').strip(), False, False, ''
                r = stt.filtrar(resp.model_dump() if hasattr(resp, 'model_dump') else resp, prompt)
                if r.descartado:
                    console.print(f'[dim][stt] descartado ({r.descartado}, '
                                  f'conf {f"{r.confianza:.2f}" if r.confianza is not None else "?"}): "{r.crudo}"[/]')
                    return '', False, False, r.descartado
                if r.dudosa:
                    console.print(f'[dim][stt] dudosa (conf {r.confianza:.2f}): "{r.texto}"[/]')
                return r.texto, False, r.dudosa, ''
            except Exception as e:
                if attempt == MAX_RETRIES:
                    errores.error('ESCUCHA', f'No pude transcribir lo que dijiste tras {MAX_RETRIES} '
                                  'intentos (¿sin internet?).', e)
                    if isinstance(e, APIConnectionError):
                        self._red.marcar_caida()   # O2: sin esperar al próximo sondeo
                    return '', True, False, ''
                time.sleep(0.5)
        return '', True, False, ''

    def _avisar_cupo_stt(self, headers) -> None:
        """Avisa en el log cuando quedan pocas transcripciones de Groq (2000/día)."""
        try:
            quedan = int(headers.get('x-ratelimit-remaining-requests'))
        except (TypeError, ValueError):
            return
        if quedan in (500, 200, 100, 50) or quedan < 20:
            errores.aviso('ESCUCHA', f'Quedan {quedan} transcripciones de Groq por hoy; al '
                          'llegar a 0, Bob deja de entender.')

    # ── LLM ──────────────────────────────────────────────────────────────────

    def _contexto_recordatorios(self) -> str:
        """P9: si hay recordatorios VENCIDOS, los saca de la cola y devuelve un bloque
        para que Bob los entregue YA dentro de su respuesta. Así, aunque estés en
        plena charla (en_conversacion=True, el loop de fondo se difiere), el
        recordatorio se dispara en el próximo turno en vez de perderse."""
        if not RECORDATORIOS_ENABLED:
            return ""
        vencidos = self._recordatorios.vencidos()
        if not vencidos:
            return ""
        for rec in vencidos:
            console.print(f'[bold magenta][recordatorio→charla][/] {rec.que}')
        lineas = "\n".join(f"- {r.que}" for r in vencidos)
        return ("\n\n═══ RECORDATORIO VENCIDO (ENTREGAR AHORA, OBLIGATORIO) ═══\n"
                "Se cumplió el tiempo de uno o más recordatorios que el usuario te pidió. "
                "Antes de responder cualquier otra cosa, AVISALE AHORA MISMO con tu "
                "personalidad, de forma clara y directa (no lo omitas, no lo pospongas):\n"
                + lineas)

    def _pedir_jev(self, texto: str):
        """Pregunta a JEV por la ruta de `texto` y la imprime. Devuelve la Decision."""
        ultimo = next((m['content'] for m in reversed(self._convo)
                       if m['role'] == 'assistant'), '')
        d = decidir(texto, ultimo)
        origen = 'respaldo' if d.respaldo else 'JEV'
        console.print(f'[dim][jev] ruta={d.ruta} emoción={d.emocion} ({origen}, {d.latencia_s:.2f}s)[/]')
        return d

    def _decidir_ruta(self, texto: str) -> Optional[str]:
        """J2: pide la ruta del turno a JEV. None = JEV no respondió (usar parsers).
        J3: también guarda la emoción del turno (CURIOSO si JEV está apagado).
        Si el monitor en reposo ya la pidió para este mismo texto, la reutiliza."""
        if not JEV_ROUTING_ENABLED:
            self._emocion_turno = EMOCION_RESPALDO
            return None
        previa, self._jev_previa = self._jev_previa, None
        d = previa[1] if previa and previa[0] == texto else self._pedir_jev(texto)
        self._emocion_turno = d.emocion
        return d.ruta

    def _armar_pedido(self, texto: str, ing: Optional[Dict], ruta: Optional[str]):
        """(cliente, kwargs de chat.completions.create, tope de frases) para responder
        `texto`, sin tocar el historial. Lo usan _stream_llm y la especulación (R1)."""
        # Recorte: solo los últimos N mensajes van al LLM (ahorro de tokens). La
        # memoria de largo plazo (P1) ya está en el system prompt, no se pierde.
        hist = (self._convo + [{'role': 'user', 'content': texto}])[-MAX_HIST_MSGS:]
        # P3: inyecta el estado interno actual de Bob (energía/ánimo) para que su
        # tono derive con el tiempo. Se recalcula por turno → refleja el cansancio
        # acumulado dentro de una charla larga.
        # P9: si el turno pide hora/fecha/clima, inyecta datos en vivo al prompt.
        # contexto_asistente devuelve '' cuando no hay intención de asistente.
        # V5c: en modo ingeniero cambian prompt, modelo (nube) y largo; fuera, nada.
        llm, modelo, extra = ((ing['llm'], ing['modelo'], ing['extra']) if ing
                              else (self._llm, self._llm_model, self._llm_extra))
        max_tokens = ing['max_tokens'] if ing else MAX_TOKENS
        max_frases = ing['max_frases'] if ing else MAX_FRASES_TURNO
        sys_content = ((ing['prompt'] if ing else self._system_prompt_actual)
                       + self._sm.estado_interno_prompt()
                       + _contexto_asistente_turno(texto, ruta)
                       + self._contexto_recordatorios()
                       + getattr(self, '_marca_voz', '')    # Fase V: quién habla
                       + REGLA_CHARLA)                       # E-C: sin acciones no hechas
        kwargs = dict(model=modelo,
                      messages=[{'role': 'system', 'content': sys_content}] + hist,
                      temperature=TEMPERATURE, max_tokens=max_tokens, stream=True, **extra)
        return llm, kwargs, max_frases

    def _stream_llm(self, texto: str) -> Iterator[str]:
        ing = modo_dueno.ajustes_ingeniero(self)
        if ing and ing.get('sesion') is not None:      # Claude Code (Pro del dueño)
            self._llm_modelo = 'claude_code'
            self._convo.append({'role': 'user', 'content': texto})
            frases = []
            for frase in modo_dueno.responder_ingeniero(self, texto, ing['sesion']):   # S4
                if isinstance(frase, modo_dueno.Aviso):   # "estoy leyendo…": no es la respuesta
                    yield _marcar_emo(frase, 'PENSANDO')
                    continue
                frases.append(frase)              # sin _sin_promesas: esta sesión sí tiene herramientas
                yield _marcar_emo(frase, self._emocion_turno)
            if frases:
                self._convo.append({'role': 'assistant', 'content': ' '.join(frases)})
                return
            self._convo.pop()
            ing = modo_dueno.ajustes_ingeniero(self, motor='groq')   # respaldo este turno
        llm, kwargs, max_frases = self._armar_pedido(texto, ing, getattr(self, '_ruta_turno', None))
        self._convo.append({'role': 'user', 'content': texto})

        # R1: si el pedido ya salió mientras JEV decidía y es el mismo, se usa ese.
        pre, self._prefetch = getattr(self, '_prefetch', None), None
        stream = None
        if pre is not None:
            if pre.llm is llm and pre.kwargs == kwargs:
                if pre.esperar_inicio(timeout=45.0):
                    stream = pre
                    console.print('[dim][llm] respuesta adelantada (JEV en paralelo)[/]')
                else:
                    errores.error('IA', 'El modelo no respondió: Bob dice una frase de respaldo.',
                                  pre.error)
                    self._llm_fallo = 'respaldo'
                    yield f'[EMO:{EMO_LLM_ERROR}] {FRASE_RESPALDO_LLM}'
                    return
            else:
                pre.close()     # cambió el contexto (ruta, recordatorio…): pedir de nuevo
        if stream is None:
            try:
                stream = llm.chat.completions.create(**kwargs)
            except Exception as e:
                errores.error('IA', 'El modelo no respondió: Bob dice una frase de respaldo.', e)
                # Sin LLM: Bob lo dice en voz en vez de quedarse callado.
                self._llm_fallo = 'respaldo'
                yield f'[EMO:{EMO_LLM_ERROR}] {FRASE_RESPALDO_LLM}'
                return

        self._llm_modelo = getattr(getattr(llm, 'ultimo', None), 'nombre', None)
        # Guard: corta el turno al llegar a MAX_FRASES_TURNO frases y pone en cada
        # frase la emoción de JEV (_marcar_emo). Guardamos en el historial SOLO lo
        # dicho, sin tags (refuerza brevedad y evita que el LLM copie los tags).
        #
        # _split_sentence parte también en ¿/¡, dejando fragmentos sin contenido
        # hablable (solo un tag o un signo de apertura). Esos NO cuentan como
        # frase y se arrastran al fragmento siguiente, para no perder su tag ni
        # truncar la respuesta a media frase.
        def _hablable(s: str) -> bool:
            return any(c.isalnum() for c in _EMO_TAG_RE.sub('', s))

        emo_turno = self._emocion_turno
        buf       = ''
        dicho     = []     # frases efectivamente habladas
        pendiente = ''     # prefijo (tag/signo de apertura) para la próxima frase
        n_frases  = 0
        corte     = False
        fallo     = False   # E-C: el stream se cortó a mitad de la respuesta
        try:
            for chunk in stream:
                delta = (chunk.choices[0].delta.content or '') if chunk.choices else ''
                buf += delta
                while True:
                    sent, buf = _split_sentence(buf)
                    if sent is None:
                        break
                    sent = pendiente + sent
                    pendiente = ''
                    if not _hablable(sent):
                        pendiente = sent        # arrastrar tag/signo al siguiente
                        continue
                    sent = _marcar_emo(_sin_promesas(sent), emo_turno)
                    dicho.append(sent)
                    yield sent
                    n_frases += 1
                    if n_frases >= max_frases:
                        corte = True
                        break
                if corte:
                    break
        except Exception as e:
            # E-C: antes el error se perdía y la respuesta quedaba a medias sin aviso.
            fallo = True
            self._llm_fallo = 'corte'
            errores.aviso('IA', 'La respuesta se cortó a la mitad: Bob dice lo que alcanzó a '
                          'llegar.', e)
        if corte:
            try:
                stream.close()              # no seguir generando server-side
            except Exception:
                pass
        elif fallo:
            frase = _marcar_emo(FRASE_CORTE, emo_turno)
            dicho.append(frase)
            yield frase
        else:
            tail = (pendiente + buf).strip()
            if _hablable(tail):
                sent = _marcar_emo(_sin_promesas(tail), emo_turno)
                dicho.append(sent)
                yield sent
        self._convo.append({'role': 'assistant',
                            'content': ' '.join(_EMO_TAG_RE.sub('', s).strip() for s in dicho)})

    # ── TTS + Reproducción ────────────────────────────────────────────────────

    def _hablar(self, texto: str, emo: Optional[str] = None) -> None:
        mp3 = _sintetizar(texto, emo)
        if not mp3:
            return
        self._reproducir(mp3)

    def _reproducir(self, mp3: bytes) -> Optional[bytes]:
        """Reproduce el audio de Bob (WAV de Piper o mp3 de edge-tts) por el speaker
        del ESP32 (TCP) o pygame local según flag.
        R2: avisa al micrófono continuo para que la voz de Bob no cuente como del usuario."""
        if self._cortado():
            return None                      # D/J: lo que quedaba por decir no se dice
        self._hablo_desde_escucha = True     # R4: el próximo turno arranca con el tono
        mic = getattr(self, '_mic', None)
        if mic is not None:
            mic.bob_habla(True)
        try:
            if USE_ROBOT_SPEAKER and self._audio_io is not None and self._audio_io.connected:
                return self._reproducir_robot(mp3)
            return self._reproducir_laptop(mp3)
        finally:
            if mic is not None:
                mic.bob_habla(False)

    def _reproducir_robot(self, mp3: bytes) -> Optional[bytes]:
        """Convierte mp3 → uint8 @ 8 kHz y lo manda al ESP32 de una vez."""
        u8 = self._audio_a_u8(mp3)
        if not u8:
            return None
        total = len(u8)
        if not self._audio_io.send_audio_header(total):
            return None
        # TODO el body en un sendall. El envío por chunks con pacing dejaba MUDO
        # el 2º playback en adelante (bug de firmware sin resolver: play_mode
        # consume los bytes a ritmo correcto pero el DAC no suena); de golpe el
        # DAC reproduce siempre. TCP bufferea; el firmware consume a 8 kHz.
        t0 = time.monotonic()
        if not self._audio_io.send_audio_body(u8):
            return None
        # sendall vuelve cuando el buffer TCP acepta los bytes, NO cuando terminó
        # de sonar: esperar la duración real del audio (anti-eco / auto-wake).
        restante = total / SAMPLE_RATE - (time.monotonic() - t0)
        if restante > 0:
            self._detener.wait(restante)
        time.sleep(TTS_TAIL_S)
        return None

    @staticmethod
    def _audio_a_u8(mp3: bytes) -> bytes:
        """Convierte mp3 → PCM uint8 mono @ SAMPLE_RATE Hz vía ffmpeg + filtros TTS."""
        proc = subprocess.run(
            [FFMPEG, '-hide_banner', '-loglevel', 'error',
             '-i', 'pipe:0',
             '-af', TTS_FFMPEG_FILTERS,
             '-ar', str(SAMPLE_RATE), '-ac', '1',
             # dither_method triangular al bajar a 8 bits: rompe el ruido de
             # cuantización (grano áspero) y lo vuelve un hiss suave, mucho más
             # tolerable en el DAC de 8 bits del ESP32.
             '-dither_method', 'triangular',
             '-acodec', 'pcm_u8', '-f', 'u8', 'pipe:1'],
            input=mp3, capture_output=True, check=False,
        )
        return proc.stdout

    def _reproducir_laptop(self, mp3: bytes) -> Optional[bytes]:
        """Reproduce con pygame en parlantes de la laptop: WAV (Piper, R4) desde
        memoria; MP3 (edge-tts) por archivo temporal."""
        if mp3[:4] == b'RIFF' and self._reproducir_wav_laptop(mp3):
            return None
        with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as tf:
            tf.write(mp3)
            path = tf.name
        try:
            self._pygame.mixer.music.load(path)
            self._pygame.mixer.music.play()
            while self._pygame.mixer.music.get_busy():
                time.sleep(0.05)
                if self._detener.is_set() or self._cortado():
                    self._pygame.mixer.music.stop()
                    break
        finally:
            try:
                # pygame deja el archivo abierto hasta descargarlo: sin esto el
                # borrado fallaba en silencio y quedaban mp3 en %TEMP% (433 hallados).
                self._pygame.mixer.music.unload()
                os.unlink(path)
            except Exception:
                pass
        return None

    def _tono_escucha(self) -> None:
        """R4: dos notas cortas y suaves (≈120 ms, −18 dBFS) al empezar a escuchar, como
        los asistentes comerciales. Se marca como voz de Bob con un margen corto para
        que el micrófono continuo no la tome como del usuario."""
        mic = getattr(self, '_mic', None)
        # Solo con el micrófono continuo: ya escucha cuando suena el tono (sin él, el
        # stream tardaría 330–780 ms en abrirse y el tono mentiría).
        if not TONO_ESCUCHA_ENABLED or mic is None or (
                USE_ROBOT_SPEAKER and self._audio_io is not None and self._audio_io.connected):
            return
        if getattr(self, '_tono_wav', None) is None:
            t1, t2 = np.arange(int(0.05 * _SALIDA_SR)), np.arange(int(0.07 * _SALIDA_SR))

            def nota(t, hz):
                env = np.minimum(t / (0.005 * _SALIDA_SR), 1.0) * np.exp(-t / (0.025 * _SALIDA_SR))
                return np.sin(2 * np.pi * hz * t / _SALIDA_SR) * env
            x = 0.12 * np.concatenate([nota(t1, 1046.5), nota(t2, 1318.5)])   # do6 → mi6
            self._tono_wav = _pcm_a_wav((x * 32767).astype(np.int16).tobytes(), _SALIDA_SR)
        mic.bob_habla(True)
        try:
            self._reproducir_wav_laptop(self._tono_wav)
        finally:
            mic.bob_habla(False, margen_ms=60)

    def _decir_relleno(self) -> None:
        """R4: la respuesta tarda (Claude, red lenta, respaldo local): Bob muestra que
        está pensando en vez de quedarse mudo."""
        frase = random.choice(RELLENOS)
        cache = getattr(self, '_rellenos_audio', None)
        if cache is None:
            cache = self._rellenos_audio = {}
        audio = cache.get(frase)
        if audio is None:
            audio = cache[frase] = _sintetizar(frase, 'PENSANDO')
        console.print(f'[dim][voice] (relleno) {frase}[/]')
        self._serial.cmd_estado('PENSANDO')
        if audio:
            self._reproducir(audio)

    def _reproducir_wav_laptop(self, wav: bytes) -> bool:
        """R4: reproduce un WAV 16-bit desde memoria. False si no se puede (formato
        distinto al del mixer): se reproduce por el camino de archivo."""
        try:
            with wave.open(_io.BytesIO(wav)) as wf:
                sr, canales_in, ancho = wf.getframerate(), wf.getnchannels(), wf.getsampwidth()
                frames = wf.readframes(wf.getnframes())
            freq, _, canales = self._pygame.mixer.get_init()
            if sr != freq or canales_in != 1 or ancho != 2:
                return False
            x = np.frombuffer(frames, np.int16)
            if canales > 1:                     # el mixer quedó en estéreo (pide mono)
                x = np.repeat(x[:, None], canales, axis=1)
            canal = self._pygame.sndarray.make_sound(np.ascontiguousarray(x)).play()
        except Exception:
            return False
        while canal is not None and canal.get_busy():
            time.sleep(0.02)
            if self._detener.is_set() or self._cortado():
                canal.stop()
                break
        return True

    def _registrar_turno(self, cortado: bool, llm_s, tts_s: float, hasta_audio_s) -> None:
        """Una línea por turno en logs/turnos.jsonl (solo perfil feria). Sin texto."""
        if not REGISTRO_TURNOS:
            return
        registro_turnos.registrar({
            'ruta': getattr(self, '_ruta_turno', None),
            'stt_s': getattr(self, '_stt_s', None),
            'stt_fallo': bool(getattr(self, '_stt_fallo', False)),
            'llm_modelo': self._llm_modelo,
            'llm_fallo': self._llm_fallo,
            'llm_s': llm_s,
            'hasta_audio_s': hasta_audio_s,
            'tts_s': round(tts_s, 3),
            'cortado': cortado,
        })
        self._stt_s = None      # el STT de este turno ya quedó registrado

    def _stream_and_speak(self, texto: str) -> Optional[bytes]:
        """LLM streaming + TTS en paralelo, reproducción en cuanto llega cada frase."""
        if self._cortado():
            return None                  # D/J mientras pensaba: no responde
        self._llm_modelo = self._llm_fallo = None
        t_llm_fin, tts_s, hasta_audio = [None], [0.0], [None]
        sent_q:  queue.Queue = queue.Queue()
        audio_q: queue.Queue = queue.Queue(maxsize=8)
        self._audio_q = audio_q          # cortar_respuesta la despierta
        captured = [None]
        terminado = threading.Event()    # corte: el TTS deja de preparar frases

        def llm_worker():
            # finally: el None de cierre DEBE llegar aunque el LLM explote,
            # si no el tts_worker espera su timeout entero y el turno muere mudo.
            try:
                for sent in self._stream_llm(texto):
                    sent_q.put(sent)
            finally:
                t_llm_fin[0] = round(time.monotonic() - t_pensando, 3)
                sent_q.put(None)

        # Ollama local frío (cargar el modelo a VRAM) tarda más que los 10 s
        # que toleramos con Groq/Gemini → sin esto el primer turno sale mudo.
        # R1: con la rotación, Ollama puede entrar de respaldo en frío.
        con_local = self._usando_ollama or getattr(self._llm, 'tiene_local', lambda: False)()
        primer_sent_timeout = 45.0 if con_local else 10.0

        def tts_worker():
            while True:
                try:
                    sent = sent_q.get(timeout=primer_sent_timeout)
                except queue.Empty:
                    audio_q.put(None)
                    return
                if sent is None:
                    audio_q.put(None)
                    return
                # Extraer el tag [EMO:X] ANTES del TTS para que no se lea
                emo, clean = _extract_emo_tag(sent)
                # Saltar fragmentos sin contenido hablable (solo tag, puntuación,
                # espacios) — edge-tts lanza NoAudioReceived con esos.
                if not any(ch.isalnum() for ch in clean):
                    continue
                if terminado.is_set():
                    return
                try:
                    t_sint = time.monotonic()
                    mp3 = _sintetizar(clean, emo)
                    tts_s[0] += time.monotonic() - t_sint
                except Exception as e:
                    # Una frase mala no debe matar el hilo TTS entero
                    errores.aviso('VOZ', f'No pude decir "{clean[:40]}"; sigo con la frase '
                                  'siguiente.', e)
                    continue
                # Con la cola llena y la respuesta cortada nadie la vacía: no colgarse.
                while not terminado.is_set():
                    try:
                        audio_q.put((clean, mp3, emo), timeout=0.5)
                        break
                    except queue.Full:
                        continue

        self._sm.iniciar_hablando()

        # Fase C: si la conversación viene muy bien (mood alto o racha de 3+
        # turnos positivos), Bob arranca a hablar con cara eufórica. La emoción
        # de JEV la reemplaza cuando empiece la primera frase.
        if self._sm.mood >= 0.6 or self._sm.positive_streak >= 3:
            self._serial.cmd_estado('MUY_FELIZ')

        t_pensando = time.monotonic()  # para detectar LLM lento (Fase D)
        primera_frase = True

        llm_t = threading.Thread(target=llm_worker, daemon=True)
        tts_t = threading.Thread(target=tts_worker, daemon=True)
        llm_t.start()
        tts_t.start()

        relleno = not RELLENO_ENABLED      # R4: ¿ya se dijo (o no se usa)?
        while True:
            try:
                # R4: antes de la 1ª frase, a los RELLENO_S sin respuesta, un "Mmm, a ver…"
                item = audio_q.get(timeout=RELLENO_S if (primera_frase and not relleno) else 15.0)
            except queue.Empty:
                if primera_frase and not relleno:
                    relleno = True
                    if llm_t.is_alive():
                        self._decir_relleno()
                    continue
                break
            if item is None or self._cortado():
                break
            sent_text, mp3, emo = item
            if not mp3:
                continue

            # Fase D: si el LLM tardó >2.5s, flash de "¡ya sé!" antes de hablar
            if primera_frase:
                primera_frase = False
                hasta_audio[0] = round(time.monotonic() - t_pensando, 3)
                if time.monotonic() - t_pensando > 2.5:
                    self._serial.cmd_estado('MUY_FELIZ')
                    time.sleep(0.20)

            # JEV decidió la emoción del turno (ver _marcar_emo)
            self._serial.cmd_estado(emo)
            with puente_cli.turno('bob'):
                console.print(f'[bold green]Bob[/] [dim]({emo})[/]: {sent_text}')

            c = self._reproducir(mp3)

            # Fase D: si la frase fue una pregunta, cara CURIOSA al terminarla
            # (la siguiente frase o la transición a LISTENING la reemplaza)
            if sent_text.rstrip().endswith('?'):
                self._serial.cmd_estado('CURIOSO')

            if c is not None:
                captured[0] = c
                break

        self._audio_q = None
        terminado.set()
        if self._cortado():
            self._registrar_turno(True, t_llm_fin[0], tts_s[0], hasta_audio[0])
            return None                  # cortada: no esperar a que el LLM termine
        llm_t.join(timeout=2.0)
        tts_t.join(timeout=2.0)
        self._registrar_turno(False, t_llm_fin[0], tts_s[0], hasta_audio[0])
        return captured[0]

    # ── Warmup ────────────────────────────────────────────────────────────────

    def _warmup(self) -> None:
        def _w():
            try:
                list(self._client.models.list(timeout=3.0))
            except Exception:
                pass
        threading.Thread(target=_w, daemon=True).start()
        threading.Thread(
            target=lambda: subprocess.run([FFMPEG, '-version'], capture_output=True, timeout=2),
            daemon=True,
        ).start()

    # ── Wake word monitor (siempre activo, corre en hilo separado) ────────────

    def iniciar_wake_monitor(self) -> None:
        """
        Hilo que graba continuamente en modo IDLE/PRESENCE solo para detectar wake word.
        Cuando detecta "Bob" (o JEV dice que le hablan, J3), notifica
        StateMachine.notificar_wake_word().
        Se mantiene activo mientras el robot no esté en conversación.
        Con TRIGGER_MODE = "tecla" no escucha: ENTER en la consola dispara el turno
        (en modo CLI, el ENTER del CLI: ver disparar_turno).
        """
        # R2: abrir el micrófono continuo ya (Silero + stream ≈ 2 s), no en el 1er turno.
        threading.Thread(target=self._mic_continuo, daemon=True, name='mic-abrir').start()
        self._red.iniciar()   # O2: también en modo tecla (el ENTER transcribe)
        if TRIGGER_MODE == "tecla":
            # En modo CLI, msvcrt le robaría las teclas a la consola del CLI.
            if not puente_cli.ACTIVO:
                threading.Thread(target=self._tecla_loop, daemon=True, name='tecla-trigger').start()
            return
        threading.Thread(target=self._wake_monitor_loop, daemon=True, name='wake-monitor').start()

    # ── Soliloquio / actitud (featuresAction.md Fase A) ───────────────────────

    def iniciar_soliloquio_monitor(self) -> None:
        """Hilo que, cuando Bob está libre (IDLE/PRESENCE, fuera de conversación),
        suelta de vez en cuando una frase espontánea del banco local."""
        if not SOLILOQUIO_ENABLED:
            return
        threading.Thread(target=self._soliloquio_loop, daemon=True,
                         name='soliloquio').start()

    # ── P9: monitor de recordatorios (dispara proactivamente) ──────────────────

    def iniciar_recordatorio_monitor(self) -> None:
        """Hilo que cada segundo revisa recordatorios vencidos y los anuncia
        reusando el camino de soliloquio (anti-eco + OLED + habla)."""
        if not RECORDATORIOS_ENABLED:
            return
        threading.Thread(target=self._recordatorio_loop, daemon=True,
                         name='recordatorios').start()

    # ── Baile: monitor que apaga el baile cuando la música para ────────────────

    def iniciar_baile_monitor(self) -> None:
        """Hilo que, mientras suena música, vigila Spotify/YouTube: si ya no suena
        (terminó la canción, la pausaron desde el celular, etc.), limpia los flags de
        música y baile para que Bob vuelva a hablar y a su comportamiento normal."""
        if not MUSICA_ENABLED:
            return
        threading.Thread(target=self._baile_monitor_loop, daemon=True,
                         name='baile-monitor').start()

    def _baile_monitor_loop(self) -> None:
        fallos = 0
        while not self._detener.is_set():
            self._detener.wait(1.5)
            if self._detener.is_set():
                break
            # Solo consultamos a Spotify si hay una sesión de música en curso
            # (hint) o si Bob ya está bailando. Sin esto no se gasta red.
            if not (self._baile_hint or self._sm.bailando.is_set()):
                continue
            # No tocar el baile mientras Bob charla o habla solo (el wake ya limpió
            # el flag al entrar a LISTENING; al terminar la charla acá se re-evalúa).
            if self._sm.en_conversacion or self._muted.is_set():
                continue
            try:
                sonando = sonando_musica()
                fallos = 0
            except Exception:
                # Fail-safe: si Spotify no responde 2 veces seguidas, asumir que
                # la música paró — Bob no debe seguir bailando a ciegas.
                fallos += 1
                if fallos >= 2 and self._sm.musica_sonando.is_set():
                    console.print('[dim][baile] Spotify no responde → paro el baile[/]')
                    self._sm.bailando.clear()
                    self._sm.musica_sonando.clear()
                    self._baile_hint = False
                continue
            if sonando:
                # Música sonando → Bob callado. No re-arranca el baile: baila
                # BAILE_MAX_S por canción pedida (lo corta BehaviorEngine).
                self._sm.musica_sonando.set()
            else:
                # Gracia: Spotify tarda un instante en reportar is_playing tras
                # start_playback; no cortar la música en los primeros segundos.
                if self._sm.musica_sonando.is_set() and \
                        time.monotonic() - self._t_baile_inicio < 4.0:
                    continue
                if self._sm.musica_sonando.is_set():
                    console.print('[dim][baile] la música paró → fin del baile[/]')
                self._sm.bailando.clear()
                self._sm.musica_sonando.clear()
                self._baile_hint = False     # música terminó → dejar de consultar

    def _recordatorio_loop(self) -> None:
        while not self._detener.is_set():
            time.sleep(1.0)
            if self._detener.is_set():
                break
            # Reparto: si hay charla en curso, los recordatorios vencidos los
            # entrega _contexto_recordatorios() inyectándolos en el próximo turno
            # (ver _stream_llm) → NO se pierden aunque sigas hablando. Acá (libre)
            # solo disparamos cuando NO hay charla ni Bob hablando solo.
            if self._sm.en_conversacion or self._muted.is_set():
                continue
            for rec in self._recordatorios.vencidos():
                frase = (f"[EMO:CURIOSO] ¡Ey! Me pediste que te recuerde: {rec.que}.")
                console.print(f'[bold magenta][recordatorio][/] {rec.que}')
                self.decir_soliloquio(frase)

    def _soliloquio_loop(self) -> None:
        from nucleo.state_machine import RobotState
        t_ultimo = 0.0
        cooldown = SOLILOQUIO_COOLDOWN_S
        carnadas = 0                      # frases-carnada disparadas en esta presencia
        while not self._detener.is_set():
            time.sleep(1.0)
            if self._detener.is_set():
                break

            est = self._sm.estado
            # Al dejar PRESENCE (se fue la persona o arrancó charla), resetear el
            # contador de carnadas para la próxima persona.
            if est != RobotState.PRESENCE:
                carnadas = 0

            # No hablar solo si hay una charla en curso o si ya estamos hablando.
            if self._sm.en_conversacion or self._muted.is_set():
                continue
            # Con música sonando Bob está callado (baile o no): nada de
            # frases/carnadas (hablarían encima de la música).
            if self._sm.bailando.is_set() or self._sm.musica_sonando.is_set():
                continue
            # Dormido = callado (igual que las muecas): coherencia con la pose de sueño.
            if self._sm.is_asleep():
                continue
            ahora = time.monotonic()
            if ahora - t_ultimo < cooldown:
                continue

            t_en = self._sm.t_en_estado
            if est == RobotState.IDLE and t_en >= SOLO_IDLE_MIN_S:
                categoria = random.choice(('aburrimiento', 'curiosidad', 'actitud'))
            elif est == RobotState.PRESENCE and t_en >= PRESENCE_NUDGE_S:
                # Carnada acotada: no acosar a quien no se anima a hablar.
                if carnadas >= SOLILOQUIO_MAX_CARNADAS:
                    continue
                categoria = 'carnada'
            else:
                continue

            if random.random() > P_SOLILOQUIO:
                continue
            # Última verificación: pudo entrar una conversación en este segundo.
            if self._sm.en_conversacion:
                continue

            frase = self._elegir_frase_soliloquio(categoria)
            if not frase:
                continue
            self.decir_soliloquio(frase)
            if categoria == 'carnada':
                carnadas += 1
            t_ultimo = time.monotonic()
            # Cadencia con jitter: que no suene metronómico.
            cooldown = SOLILOQUIO_COOLDOWN_S * random.uniform(0.8, 1.4)

    def _elegir_frase_soliloquio(self, categoria: str) -> Optional[str]:
        """Devuelve una frase del banco local para la categoría, sin repetir las recientes."""
        frases = BANCO_SOLILOQUIO.get(categoria)
        if not frases:
            return None
        # Anti-repetición: evitar las frases dichas hace poco.
        candidatas = [f for f in frases
                      if _extract_emo_tag(f)[1].lower() not in self._soliloquio_reciente]
        return random.choice(candidatas or frases)

    def decir_soliloquio(self, frase: str) -> None:
        """Dice UNA frase espontánea: muta el mic (anti-eco), pone el OLED según
        su tag [EMO], habla, restaura el OLED y deja decaer el eco."""
        emo, clean = _extract_emo_tag(frase)
        if not any(ch.isalnum() for ch in clean):
            return
        self._soliloquio_reciente.append(clean.lower())   # anti-repetición
        self._muted.set()
        self._sm.oled_ocupar()          # bloquea muecas mientras Bob habla solo
        try:
            if emo:
                self._serial.cmd_estado(emo)
            with puente_cli.turno('bob'):
                console.print(f'[bold blue][soliloquio][/] [dim]({emo})[/]: {clean}')
            self._hablar(clean, emo)
        except Exception as e:
            errores.aviso('HABLA SOLO', 'Falló una frase suelta de Bob; sigue como siempre.', e)
        finally:
            self._sm.oled_liberar()
            self._restaurar_oled()
            time.sleep(SOLILOQUIO_SETTLE_MS / 1000.0)
            self._muted.clear()
            if self._cortado():          # D/J sobre una frase suelta
                self._corte.clear()
                if self._corte_escuchar:
                    self._sm.iniciar_escuchando()

    def _restaurar_oled(self) -> None:
        """Devuelve el OLED al look del estado actual tras un soliloquio."""
        from nucleo.state_machine import RobotState, _OLED_STATE
        est = self._sm.estado
        # En PRESENCE el FacialTracker manda SIGUIENDO; no lo pisamos.
        if est == RobotState.PRESENCE:
            return
        cmd = _OLED_STATE.get(est)
        if cmd:
            self._serial.cmd_estado(cmd)

    def _wake_monitor_loop(self) -> None:
        from nucleo.state_machine import RobotState
        # J3: sin "Bob", cada frase oída en reposo va a JEV, que decide si le habla.
        escucha_libre = JEV_SIN_WAKE and JEV_ROUTING_ENABLED
        while not self._detener.is_set():
            # Solo monitorear en estados no-conversacionales
            if self._sm.en_conversacion:
                time.sleep(0.2)
                continue

            # Anti-eco: si Bob está hablando solo, no grabar (su voz no debe
            # auto-dispararse como wake word).
            if self._muted.is_set():
                time.sleep(0.2)
                continue

            if not self.en_linea:
                time.sleep(0.5)   # O2: sin internet no se graba ni se llama a Groq
                continue

            if escucha_libre:
                # Frase completa con VAD (no corta a 3 s). Sin pulso de invitación.
                audio = self._grabar(initial_timeout=WAKE_WINDOW_S, invitar=False, reposo=True)
            else:
                audio = self._grabar_wake()
            if audio is None:
                continue
            if self._muted.is_set() or self._sm.en_conversacion:
                continue   # Bob habló o arrancó una charla mientras grababa
            if not self._cupo_stt.libre(reserva=4):
                # R2: cupo de Whisper (20/min) casi lleno: la charla tiene prioridad.
                errores.aviso('ESCUCHA', 'Casi lleno el cupo de Groq de este minuto: mientras nadie '
                              'charla, no transcribo.')
                continue

            texto = self._transcribir(audio, contexto=False)
            if not texto:
                continue
            if _es_alucinacion(texto):
                continue   # silencio/ruido alucinado por Whisper → ignorar
            if getattr(self, '_stt_dudosa', False):
                console.print(f'[dim][wake?] dudosa, ignoro: "{texto}"[/]')
                continue   # R1: en reposo, lo dudoso no despierta a Bob

            ww = self._wake.detect(texto)
            payload = (ww.payload or '').strip() if ww.detected else texto.strip()
            decision = None
            if escucha_libre and payload:
                decision = self._pedir_jev(payload)
                if decision.ruta in ('ajeno', 'despedida') and not ww.detected:
                    console.print(f'[dim][jev] "{texto}" no es para iniciar charla ({decision.ruta}) → ignoro[/]')
                    continue
            # Activa si dijo "Bob" (respaldo) o si JEV dijo a qué ruta va el turno.
            activar = ww.detected or (decision is not None and decision.ruta is not None)
            if not activar:
                console.print(f'[dim][wake?] heard: "{texto}"[/]')
                continue
            if ww.detected:
                if payload:
                    console.print(f'[bold yellow][wake] "{texto}" → payload: "{payload}"[/]')
                else:
                    console.print(f'[bold yellow][wake] "{texto}" detectado![/]')
            else:
                console.print(f'[bold yellow][jev] "{texto}" → le habla a Bob[/]')
            # Pulso emocional inmediato: ojos CURIOSOS al oír su nombre o al entender que le hablan
            pulse_emotion(self._serial, self._sm, EMO_WAKE_DETECTED, PULSE_FAST)
            if decision is not None:
                # El primer turno reutiliza esta decisión (ver _decidir_ruta).
                self._jev_previa = (payload, decision)
            # Fase V: el primer turno llega como texto; su audio va aparte para la voz.
            self._audio_wake = audio
            # Sin "Bob" (JEV) no hay saludo: el turno va directo a la acción.
            self._sm.notificar_wake_word(payload=payload or None, saludar=ww.detected)

    def _tecla_loop(self) -> None:
        """Modo TRIGGER_MODE = "tecla": ENTER en la consola inicia un turno.
        Las teclas presionadas durante una conversación o mientras Bob habla se
        descartan, para que no disparen un turno en cuanto termine."""
        import msvcrt   # solo Windows; el proyecto corre en Windows
        console.print('[dim][tecla] ENTER para hablar con Bob[/]')
        while not self._detener.is_set():
            if not msvcrt.kbhit():
                time.sleep(0.05)
                continue
            if msvcrt.getwch() in ('\r', '\n'):
                self.disparar_turno()

    def disparar_turno(self) -> bool:
        """ENTER en modo tecla (consola o CLI): inicia un turno. Durante una charla o
        mientras Bob habla solo, no hace nada (no dispara un turno al terminar).
        True si lo inició."""
        if TRIGGER_MODE != "tecla" or self._sm.en_conversacion or self._muted.is_set():
            return False
        self._sm.notificar_wake_word(payload=None)
        return True

    def _grabar_wake(self) -> Optional[bytes]:
        """Versión corta para wake monitor: ~3 s, despacha a robot o laptop."""
        if USE_ROBOT_MIC and self._audio_io is not None and self._audio_io.connected:
            self._esperar_microfono()
            return self._grabar_wake_robot()
        mic = self._mic_continuo()
        if mic is not None:     # R2: misma ventana corta, desde el micrófono continuo
            frase = mic.escuchar(timeout_inicial=WAKE_WINDOW_S, max_s=WAKE_WINDOW_S,
                                 silencio_fin_ms=WAKE_SILENCE_CUT_S * 1000,
                                 nivel_min=WAKE_MIN_LEVEL, cancelar=self._detener)
            return frase.wav() if frase is not None else None
        self._esperar_microfono()
        return self._grabar_wake_laptop()

    def _grabar_wake_robot(self) -> Optional[bytes]:
        """Graba hasta WAKE_WINDOW_S desde el mic del ESP32 (uint8). Corte
        anticipado con voz + silencio, igual que la versión laptop."""
        self._audio_io.drain_mic()
        collected = bytearray()
        t0 = time.monotonic()
        t_ultima_voz = 0.0
        while time.monotonic() - t0 < WAKE_WINDOW_S and not self._detener.is_set():
            if self._sm.en_conversacion:
                return None
            chunk = self._audio_io.get_mic(timeout=0.1)
            if chunk:
                collected.extend(chunk)
                ahora = time.monotonic()
                # Escala u8: gate de silencio es 4.0 → voz clara ~2x eso.
                if _rms_uint8(bytes(chunk)) >= 8.0:
                    t_ultima_voz = ahora
                elif t_ultima_voz and ahora - t_ultima_voz >= WAKE_SILENCE_CUT_S:
                    break
        if not collected:
            return None
        raw = bytes(collected)
        if _rms_uint8(raw) < 4.0:        # silencio → no transcribir
            return None
        return _uint8_to_wav(raw)

    def _grabar_wake_laptop(self) -> Optional[bytes]:
        """Graba hasta WAKE_WINDOW_S desde el mic local. Devuelve WAV int16 @ 16 kHz.
        Corte anticipado: si hubo voz y luego WAKE_SILENCE_CUT_S de silencio,
        corta ya y transcribe → el "Bob" dispara sin esperar la ventana entera."""
        q: queue.Queue = queue.Queue()

        def callback(indata, frames, t, status):
            q.put(bytes(indata))

        frame_bytes = int(_LAP_SAMPLE_RATE * FRAME_MS / 1000) * 2
        collected: List[bytes] = []
        t0 = time.monotonic()
        t_ultima_voz = 0.0

        try:
            with sd.RawInputStream(device=_mic_device(), samplerate=_LAP_SAMPLE_RATE, channels=1,
                                   dtype='int16', blocksize=frame_bytes // 2,
                                   callback=callback):
                while time.monotonic() - t0 < WAKE_WINDOW_S and not self._detener.is_set():
                    if self._sm.en_conversacion:
                        return None
                    try:
                        chunk = q.get(timeout=0.1)
                        if len(chunk) >= frame_bytes:
                            collected.append(chunk[:frame_bytes])
                            arr = np.frombuffer(chunk[:frame_bytes], dtype=np.int16)
                            nivel = float(np.sqrt(np.mean(
                                arr.astype(np.float32) ** 2))) / 32767.0 * 100.0
                            ahora = time.monotonic()
                            if nivel >= WAKE_VOICE_LEVEL:
                                t_ultima_voz = ahora
                            elif (t_ultima_voz and
                                  ahora - t_ultima_voz >= WAKE_SILENCE_CUT_S):
                                break     # voz + silencio → transcribir YA
                    except queue.Empty:
                        pass
        except Exception:
            return None

        if not collected:
            return None

        raw = b''.join(collected)
        # Gate de energía: si la ventana es silencio, NO transcribir (Whisper
        # alucina "Gracias." con silencio y quema llamadas a Groq).
        arr = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
        if arr.size:
            level = float(np.sqrt(np.mean(arr * arr))) / 32767.0 * 100.0
            if level < WAKE_MIN_LEVEL:
                return None

        buf = _io.BytesIO()
        with wave.open(buf, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(_LAP_SAMPLE_RATE)
            wf.writeframes(raw)
        return buf.getvalue()