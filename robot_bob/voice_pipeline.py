"""
voice_pipeline.py — Orquestador del pipeline de voz de Bob.
Delega a modulos especializados y coordina el ciclo de conversacion.
"""

from __future__ import annotations

import json
import os
import random
import re
import subprocess
import sys
import queue
import threading
import time
from typing import Optional

import imageio_ffmpeg
from rich.console import Console

_VOICECHAT_DIR = os.path.join(os.path.dirname(__file__), '..', 'shared', 'voicechatLap')
if _VOICECHAT_DIR not in sys.path:
    sys.path.append(os.path.abspath(_VOICECHAT_DIR))

from config import (
    USE_ROBOT_MIC, FRAME_MS,
    BARGE_IN_ENABLED, BARGE_IN_SUSTAINED_MS, BARGE_IN_SETTLE_MS, BARGE_IN_RMS_U8,
    WAKE_CANONICAL, WAKE_PREFIXES, WAKE_MIN_CONF, WAKE_FUZZY_THR,
    WAKE_COOLDOWN_S, WAKE_MAX_UTTR_CHARS, WAKE_SCAN_TOKENS,
    MAX_HIST_MSGS, MAX_FRASES_TURNO,
    SYSTEM_PROMPT, SYSTEM_PROMPT_LOCAL,
    MEMORIA_ENABLED, RECORDATORIOS_ENABLED, MUSICA_ENABLED,
    STT_PROMPT,
)
from wake_word import WakeWordDetector
from assistant import contexto_asistente
from music import (parse_music_command, ejecutar as ejecutar_musica,
                   esta_sonando as sonando_musica)
from show import es_comando_show, run_show
from reminders import parse_recordatorio, ReminderStore
from expression_engine import (
    pulse_emotion, react_to_user_text, react_to_bob_text,
    mood_delta_for_user_text, is_love,
    EMO_WAKE_DETECTED, EMO_GREETING_PLAYED, EMO_AUTO_OPENER,
    EMO_STT_FAIL,
    PULSE_FAST,
)
from audio_helpers import (
    extract_emo_tag, ensure_emo_tag,
    is_happy, intent_giro, extraer_nombre,
    is_exit, is_goodbye, synthesize_mp3, split_sentence,
)
from llm_client import LLMClient
from tts_engine import TTSEngine
from recorder import Recorder
from wake_monitor import WakeMonitor
from soliloquy import Soliloquio

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
console = Console()

WAKE_GREETINGS = [
    '!Hola bola!', '?Que pasa calabaza?', '!Aqui estoy!',
    '!Dime, dime!', '!Si dime!', '?Que tranza compadre?',
    '!Te escucho!', '?Que hubo que hay?', '!A la orden!',
    '?Que onda banana?', '?Que tal lechuga?',
    '!Aqui Bob, reportandose!',
]

AUTO_OPENERS = [
    '!Hola! Soy Bob, y tu como te llamas?',
    '!Buenas! De que carrera eres?',
    '!Hey! Que te trae por la feria?',
    '!Hola! Te estaba mirando, charlamos?',
    '!Buenas! Como va tu dia?',
    '!Eh, hola! Te cuento un chiste?',
    '!Hola! Vienes a ver robots o a ver robots?',
    '!Saludos! Te puedo preguntar algo?',
    '!Hola! Me aburria, hablamos un rato?',
    '!Buenas! Sabias que llevo aqui horas? Cuentame algo.',
    '!Hola! Eres de Potosi o de visita?',
    '!Hey! Que opinas de los robots conversacionales?',
]



# ── VoicePipeline ──────────────────────────────────────────────────────────────

class VoicePipeline:
    def __init__(self, serial_mgr, state_machine, audio_io=None,
                 face_id=None, memoria=None, get_frame=None):
        self._serial = serial_mgr
        self._sm     = state_machine
        self._audio_io = audio_io
        self._face_id  = face_id
        self._memoria  = memoria
        self._get_frame = get_frame

        self._convo_persona = None
        self._convo_emb = None
        self._convo_edad = None
        self._persona_nombre = None
        self._nombre_pendiente = None
        self._system_prompt_actual = SYSTEM_PROMPT

        self._llm = LLMClient()
        self._system_prompt_base = SYSTEM_PROMPT_LOCAL if self._llm.usando_ollama else SYSTEM_PROMPT
        self._system_prompt_actual = self._system_prompt_base

        self._convo: list[dict[str, str]] = []
        self._detener = threading.Event()
        self._muted = threading.Event()
        self._recordatorios = ReminderStore()
        self._t_baile_inicio = 0.0
        self._baile_hint = False

        self._wake = WakeWordDetector(
            wake_word=WAKE_CANONICAL,
            prefixes=WAKE_PREFIXES,
            min_confidence=WAKE_MIN_CONF,
            fuzzy_threshold=WAKE_FUZZY_THR,
            cooldown_s=WAKE_COOLDOWN_S,
            max_utterance_chars=WAKE_MAX_UTTR_CHARS,
            max_scan_tokens=WAKE_SCAN_TOKENS,
        )

        self._tts = TTSEngine(self._audio_io, self._detener)
        self._grabador = Recorder(self._audio_io, self._detener, self._serial, self._sm)

        self._wake_monitor = WakeMonitor(
            self._serial, self._sm, self._wake, self._transcribir,
            self._detener, self._muted, self._audio_io)
        self._soliloquio = Soliloquio(
            self._serial, self._sm, self._llm, self._hablar,
            self._detener, self._muted)

        self._warmup()
        self._hilo = threading.Thread(target=self._loop, daemon=True, name='voice-pipeline')
        self._hilo.start()

    # ── API publica ────────────────────────────────────────────────────────────

    def cerrar(self) -> None:
        self._detener.set()
        self._sm.ev_escuchando.set()
        self._hilo.join(timeout=3.0)

    # ── Loop principal ────────────────────────────────────────────────────────

    def _loop(self) -> None:
        while not self._detener.is_set():
            self._sm.ev_escuchando.wait(timeout=0.5)
            if self._detener.is_set():
                break
            if not self._sm.ev_escuchando.is_set():
                continue
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

    def _identificar(self) -> None:
        self._convo = []
        self._convo_persona = None
        self._convo_emb = None
        self._convo_edad = None
        self._persona_nombre = None
        self._nombre_pendiente = None
        self._system_prompt_actual = self._system_prompt_base
        if not self._memoria_activa() or not self._face_id.listo:
            return
        RETRY_S = 2.5
        emb, edad = None, None
        t0 = time.monotonic()
        while time.monotonic() - t0 < RETRY_S:
            try:
                emb, edad = self._face_id.analizar(self._get_frame())
            except Exception as e:
                console.print(f'[dim][memoria] analisis fallo: {e}[/]')
                break
            if emb is not None:
                break
            if self._detener.is_set():
                break
            time.sleep(0.2)
        self._convo_emb = emb
        self._convo_edad = edad
        if emb is None:
            return
        m = self._memoria.reconocer(emb)
        if m:
            pid, nombre, score = m
            self._convo_persona = pid
            self._persona_nombre = nombre
            self._memoria.marcar_visto(pid)
            console.print(f'[bold magenta][memoria][/] reconocido: {nombre} (score {score:.2f})')
            self._system_prompt_actual = (
                self._system_prompt_base + '\n\n═══ MEMORIA (ya conoces a esta persona) ═══\n'
                + self._memoria.contexto(pid)
                + '\nSaludala con calidez por su nombre y, si viene al caso, menciona algo '
                  'que recuerdes de ella. No repitas que sos un robot de feria.'
                  '\nAdapta tu cercania a esa relacion: con un amigo cercano se confianzudo, '
                  'carinoso y bromista; con un conocido reciente, cordial y un poco mas medido.')
        else:
            console.print('[bold magenta][memoria][/] persona desconocida')
            self._system_prompt_actual = (
                self._system_prompt_base + '\n\n═══ MEMORIA ═══\nNo conoces a esta persona todavia. '
                'En algun momento de la charla preguntale su nombre con naturalidad.')

    def _opener_memoria(self):
        pid = self._convo_persona
        if pid is None:
            return None
        eps = self._memoria.episodios(pid, 3)
        p = self._memoria.persona(pid)
        temas = (p[4] if p else '') or ''
        if not eps and not temas:
            return None
        nombre = self._persona_nombre or 'esta persona'
        nivel, _, _ = self._memoria.nivel_relacion(pid)
        recuerdos = '; '.join(t for t, _ in eps) if eps else ''
        ctx = (f"Vas a saludar a {nombre} ({nivel}). "
               f"Temas favoritos suyos: {temas or 'ninguno registrado'}. "
               f"Recuerdos de charlas pasadas: {recuerdos or 'ninguno'}.")
        sys_p = (
            "Eres Bob, un robot sociable de feria. Vas a saludar a alguien que YA "
            "conoces. Saluda retomando con calidez UNO de sus temas o recuerdos de "
            "una charla anterior, como un amigo que retoma la conversacion "
            "(ej: 'la ultima vez me hablabas de tu robot, como va eso?'). "
            "UNA sola frase corta y hablable, que empiece con un tag de emocion "
            "[EMO:FELIZ], [EMO:CURIOSO], [EMO:TRAVIESO] o [EMO:AMOR]. "
            "Sin comillas, sin markdown, sin emojis.")
        txt = self._llm.chat(
            messages=[{'role': 'system', 'content': sys_p},
                      {'role': 'user', 'content': ctx}],
            temperature=0.8, max_tokens=60)
        if not txt:
            console.print(f'[dim][P7] opener memoria fallo[/]')
            return None
        emo, clean = extract_emo_tag(txt)
        if not clean or not any(c.isalnum() for c in clean):
            return None
        return clean, (emo or 'FELIZ')

    def _resumir_conversacion(self):
        turns = [m for m in self._convo if m['role'] in ('user', 'assistant')]
        if not turns:
            return None, None, None
        transcript = '\n'.join(
            f"{'Usuario' if m['role'] == 'user' else 'Bob'}: {m['content']}"
            for m in turns[-12:])
        sys_p = ('Sos un extractor de memoria. Resumi la charla entre Bob (robot) y una '
                 'persona. Devolve SOLO un JSON, sin markdown: '
                 '{"resumen": "una frase en pasado de que hablaron", '
                 '"gustos": "gustos/intereses mencionados o cadena vacia", '
                 '"temas": "temas de los que hablaron o cadena vacia"}.')
        txt = self._llm.chat(
            messages=[{'role': 'system', 'content': sys_p},
                      {'role': 'user', 'content': transcript}],
            temperature=0.3, max_tokens=140)
        if not txt:
            return None, None, None
        mt = re.search(r'\{.*\}', txt, re.DOTALL)
        if mt:
            try:
                d = json.loads(mt.group(0))
                res = (d.get('resumen') or '').strip() or None
                gus = (d.get('gustos') or '').strip() or None
                tem = (d.get('temas') or '').strip() or None
                return res, gus, tem
            except Exception as e:
                console.print(f'[dim][memoria] resumen parse fallo: {e}[/]')
        return None, None, None

    def _cerrar_memoria(self) -> None:
        if not self._memoria_activa() or self._convo_emb is None:
            return
        pid = self._convo_persona
        if pid is None:
            pid = self._memoria.registrar(self._nombre_pendiente, self._convo_emb,
                                          self._convo_edad)
            self._convo_persona = pid
            console.print(f'[bold magenta][memoria][/] persona nueva guardada '
                          f'(id={pid}, nombre={self._nombre_pendiente})')
        resumen, gustos, temas = self._resumir_conversacion()
        if resumen:
            self._memoria.agregar_episodio(pid, resumen)
        self._memoria.actualizar(pid, gustos=gustos, temas=temas)
        turns = len([m for m in self._convo if m['role'] == 'user'])
        mood = self._sm.mood
        d_amistad = int(5 + mood * 8 + min(turns, 5) * 1.5)
        d_confianza = int(3 + (5 if self._persona_nombre else 0) + min(turns, 5))
        self._memoria.registrar_interaccion(pid, d_amistad, d_confianza)
        console.print(f'[dim][memoria] id={pid}: recuerdo guardado | '
                      f'amistad {d_amistad:+d}, confianza {d_confianza:+d}[/]')

    def _run_conversation(self, pending_audio: Optional[bytes] = None,
                          pending_text: Optional[str] = None,
                          greeting: bool = False,
                          trigger: Optional[str] = None) -> None:
        NEXT_TURN_TIMEOUT = 6.0
        FIRST_TURN_TIMEOUT = 10.0
        es_primer_turno = True
        n_turnos = 0

        self._identificar()

        if greeting:
            saludo, saludo_emo = None, None
            recall = self._opener_memoria() if self._convo_persona is not None else None
            if recall:
                saludo, saludo_emo = recall
                console.print(f'[bold magenta][P7 opener memoria][/] ({saludo_emo}) {saludo}')
                self._serial.cmd_estado(saludo_emo)
                self._convo.append({'role': 'assistant',
                                    'content': f'[EMO:{saludo_emo}] {saludo}'})
            elif self._persona_nombre:
                n = self._persona_nombre
                saludo = random.choice([
                    f'!Hola {n}! !Que bueno verte de nuevo!',
                    f'!{n}! Como andas?',
                    f'!Mira quien volvio! Que contas, {n}?'])
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
            if not pending_text:
                self._sm.iniciar_escuchando()

        while not self._detener.is_set():
            texto: str = ''
            if pending_text:
                texto = pending_text
                pending_text = None
                console.print(f'[dim][voice] Usando payload del wake: "{texto}"[/]')
                self._sm.iniciar_pensando()
            elif pending_audio:
                audio = pending_audio
                pending_audio = None
                console.print('[dim][voice] Usando audio de barge-in[/]')
                self._sm.iniciar_pensando()
                console.print('[voice] Transcribiendo...')
                texto = self._transcribir(audio)
            else:
                if not es_primer_turno:
                    self._sm.iniciar_escuchando()
                timeout = FIRST_TURN_TIMEOUT if es_primer_turno else NEXT_TURN_TIMEOUT
                console.print(f'[voice] Escuchando ({timeout:.0f}s timeout)...')
                audio = self._grabar(initial_timeout=timeout)
                if audio is None or not audio:
                    console.print('[dim][voice] Silencio prolongado, fin de conversacion[/]')
                    break
                self._sm.iniciar_pensando()
                console.print('[voice] Transcribiendo...')
                texto = self._transcribir(audio)

            if not texto:
                console.print('[dim][voice] Audio sin texto[/]')
                pulse_emotion(self._serial, self._sm, EMO_STT_FAIL, PULSE_FAST)
                self._sm.mood_event(-0.05)
                self._sm.stt_fail_streak += 1
                if self._sm.stt_fail_streak >= 2:
                    self._sm.stt_fail_streak = 0
                    console.print('[dim][voice] 2 fallos STT -> mensaje empatico[/]')
                    self._sm.iniciar_hablando()
                    self._serial.cmd_estado('CONFUNDIDO')
                    self._hablar('Perdon, no te estoy escuchando bien. '
                                 'Acercate un poquito y dime de nuevo, si?', 'CONFUNDIDO')
                    es_primer_turno = False
                    continue
                break

            console.print(f'[bold cyan]Usuario:[/] {texto}')

            if self._memoria_activa():
                nombre = extraer_nombre(texto)
                if nombre and nombre != self._persona_nombre:
                    self._persona_nombre = nombre
                    self._nombre_pendiente = nombre
                    if self._convo_persona is not None:
                        self._memoria.actualizar(self._convo_persona, nombre=nombre)
                    elif self._convo_emb is not None:
                        self._convo_persona = self._memoria.registrar(
                            nombre, self._convo_emb, self._convo_edad)
                    console.print(f'[bold magenta][memoria][/] nombre aprendido: {nombre}')

            react_to_user_text(self._serial, self._sm, texto)
            self._sm.stt_fail_streak = 0
            self._sm.mood_decay()
            delta = mood_delta_for_user_text(texto)
            self._sm.mood_event(delta)
            if is_love(texto):
                self._sm.mood_floor(0.6)
            if delta > 0:
                self._sm.positive_streak += 1
            elif delta < 0:
                self._sm.positive_streak = 0
            n_turnos += 1
            if n_turnos > 4:
                self._sm.mood_event(0.10)
            console.print(f'[dim][mood] {self._sm.mood:+.2f}  '
                          f'racha+{self._sm.positive_streak}[/]')

            ww = self._wake.detect(texto)
            if ww.detected and ww.payload:
                texto = ww.payload
            if not texto.strip():
                es_primer_turno = False
                continue

            intent = intent_giro(texto)
            if intent:
                self._sm.scan_request = intent
                self._sm.iniciar_hablando()
                ack = random.choice(['!Voy para alla!', '!Ya te busco!',
                                     '!Me doy la vuelta!', '!A ver donde estas!'])
                console.print(f'[bold green]Bob[/] [dim](giro:{intent})[/]: {ack}')
                self._hablar(ack, 'TRAVIESO')
                es_primer_turno = False
                continue

            if RECORDATORIOS_ENABLED:
                rec = parse_recordatorio(texto)
                if rec:
                    self._recordatorios.agregar(rec)
                    self._sm.iniciar_hablando()
                    ack = random.choice([
                        f'!Listo! Te aviso {rec.cuando_str}.',
                        f'!Anotado! Te lo recuerdo {rec.cuando_str}.',
                        f'!Hecho! {rec.cuando_str} te aviso, tranqui.'])
                    console.print(f'[bold green]Bob[/] [dim](recordatorio '
                                  f'{rec.cuando_str}: "{rec.que}")[/]: {ack}')
                    self._hablar(ack, 'FELIZ')
                    es_primer_turno = False
                    continue

            if es_comando_show(texto):
                console.print('[bold magenta][show][/] !Modo presentacion!')
                self._sm.iniciar_hablando()
                if run_show(self):
                    break
                es_primer_turno = False
                continue

            if MUSICA_ENABLED:
                mintent = parse_music_command(texto)
                if mintent:
                    self._sm.iniciar_hablando()
                    ack = ejecutar_musica(mintent)
                    console.print(f'[bold green]Bob[/] [dim](musica:{mintent.accion}'
                                  f'{" «"+mintent.query+"»" if mintent.query else ""})[/]: {ack}')
                    if mintent.accion in ('play', 'play_playlist', 'resume'):
                        self._t_baile_inicio = time.monotonic()
                        self._baile_hint = True
                        self._sm.bailando.set()
                        self._hablar(ack, 'MUY_FELIZ')
                        break
                    if mintent.accion == 'pause':
                        self._baile_hint = False
                        self._sm.bailando.clear()
                    self._hablar(ack, 'FELIZ')
                    es_primer_turno = False
                    continue

            if is_exit(texto):
                self._hablar('Hasta luego.')
                break
            if is_goodbye(texto):
                self._hablar('Hasta pronto.')
                break

            console.print('[voice] Pensando...')
            captured = self._stream_and_speak(texto)
            pending_audio = captured
            es_primer_turno = False

        try:
            self._sm.ei_evento_charla(n_turnos, self._sm.mood)
        except Exception:
            pass
        try:
            self._cerrar_memoria()
        except Exception as e:
            console.print(f'[dim][memoria] cierre fallo: {e}[/]')
        self._sm.fin_turno()

    # ── Grabacion ────────────────────────────────────────────────────────────

    def _grabar(self, initial_timeout: float = 8.0) -> Optional[bytes]:
        return self._grabador.grabar(initial_timeout)

    # ── STT ──────────────────────────────────────────────────────────────────

    def _transcribir(self, wav_bytes: bytes) -> str:
        return self._llm.stt(wav_bytes)

    # ── LLM ──────────────────────────────────────────────────────────────────

    def _contexto_recordatorios(self) -> str:
        if not RECORDATORIOS_ENABLED:
            return ""
        vencidos = self._recordatorios.vencidos()
        if not vencidos:
            return ""
        for rec in vencidos:
            console.print(f'[bold magenta][recordatorio->charla][/] {rec.que}')
        lineas = "\n".join(f"- {r.que}" for r in vencidos)
        return ("\n\n═══ RECORDATORIO VENCIDO (ENTREGAR AHORA, OBLIGATORIO) ═══\n"
                "Se cumplio el tiempo de uno o mas recordatorios que el usuario te pidio. "
                "Antes de responder cualquier otra cosa, AVISALE AHORA MISMO con tu "
                "personalidad, de forma clara y directa (no lo omitas, no lo pospongas):\n"
                + lineas)

    def _stream_llm(self, texto: str):
        self._convo.append({'role': 'user', 'content': texto})
        hist = self._convo[-MAX_HIST_MSGS:]
        sys_content = (self._system_prompt_actual
                       + self._sm.estado_interno_prompt()
                       + contexto_asistente(texto)
                       + self._contexto_recordatorios())
        messages = [{'role': 'system', 'content': sys_content}] + hist

        def _hablable(s: str) -> bool:
            return any(c.isalnum() for c in extract_emo_tag(s)[1])

        try:
            stream = self._llm.stream_chat(messages)
        except Exception as e:
            console.print(f'[red][voice] LLM error: {e}[/]')
            return

        buf = ''
        dicho = []
        pendiente = ''
        n_frases = 0
        corte = False
        for delta in stream:
            buf += delta
            while True:
                sent, buf = split_sentence(buf)
                if sent is None:
                    break
                sent = pendiente + sent
                pendiente = ''
                if not _hablable(sent):
                    pendiente = sent
                    continue
                sent = ensure_emo_tag(sent)
                dicho.append(sent)
                yield sent
                n_frases += 1
                if n_frases >= MAX_FRASES_TURNO:
                    corte = True
                    break
            if corte:
                break
        if not corte:
            tail = (pendiente + buf).strip()
            if _hablable(tail):
                sent = ensure_emo_tag(tail)
                dicho.append(sent)
                yield sent
        self._convo.append({'role': 'assistant', 'content': ' '.join(dicho)})

    # ── TTS + Reproduccion ────────────────────────────────────────────────────

    def _hablar(self, texto: str, emo: Optional[str] = None) -> None:
        self._tts._hablar(texto, emo)

    def _reproducir_mp3(self, mp3: bytes) -> Optional[bytes]:
        return self._tts._reproducir_mp3(mp3)

    def _stream_and_speak(self, texto: str) -> Optional[bytes]:
        sent_q: queue.Queue = queue.Queue()
        audio_q: queue.Queue = queue.Queue(maxsize=8)
        captured = [None]

        def llm_worker():
            try:
                for sent in self._stream_llm(texto):
                    sent_q.put(sent)
            finally:
                sent_q.put(None)

        primer_sent_timeout = 45.0 if self._llm.usando_ollama else 10.0

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
                emo, clean = extract_emo_tag(sent)
                if not any(ch.isalnum() for ch in clean):
                    continue
                try:
                    mp3 = synthesize_mp3(clean, emo)
                except Exception as e:
                    console.print(f'[red][voice] TTS error ("{clean[:40]}"): {e}[/]')
                    continue
                audio_q.put((clean, mp3, emo))

        self._sm.iniciar_hablando()
        if self._sm.mood >= 0.6 or self._sm.positive_streak >= 3:
            self._serial.cmd_estado('MUY_FELIZ')

        t_pensando = time.monotonic()
        primera_frase = True
        llm_t = threading.Thread(target=llm_worker, daemon=True)
        tts_t = threading.Thread(target=tts_worker, daemon=True)
        llm_t.start()
        tts_t.start()

        while True:
            try:
                item = audio_q.get(timeout=15.0)
            except queue.Empty:
                break
            if item is None:
                break
            sent_text, mp3, emo = item
            if not mp3:
                continue
            if primera_frase:
                primera_frase = False
                if time.monotonic() - t_pensando > 2.5:
                    self._serial.cmd_estado('MUY_FELIZ')
                    time.sleep(0.20)
            low = sent_text.lower()
            if emo is None and ('jaja' in low or 'jeje' in low):
                emo = 'MUY_FELIZ'
            if emo:
                self._serial.cmd_estado(emo)
                console.print(f'[bold green]Bob[/] [dim]({emo})[/]: {sent_text}')
            else:
                oled = 'FELIZ' if is_happy(sent_text) else 'HABLANDO'
                self._serial.cmd_estado(oled)
                console.print(f'[bold green]Bob:[/] {sent_text}')
                react_to_bob_text(self._serial, self._sm, sent_text)
            c = self._reproducir_mp3(mp3)
            if sent_text.rstrip().endswith('?'):
                self._serial.cmd_estado('CURIOSO')
            if c is not None:
                captured[0] = c
                break

        llm_t.join(timeout=2.0)
        tts_t.join(timeout=2.0)
        return captured[0]

    # ── Warmup ────────────────────────────────────────────────────────────────

    def _warmup(self) -> None:
        self._llm.warmup()
        threading.Thread(
            target=lambda: subprocess.run([FFMPEG, '-version'], capture_output=True, timeout=2),
            daemon=True,
        ).start()

    # ── Monitores de fondo ────────────────────────────────────────────────────

    def iniciar_wake_monitor(self) -> None:
        self._wake_monitor.start()

    def iniciar_soliloquio_monitor(self) -> None:
        self._soliloquio.iniciar_monitor()

    def iniciar_recordatorio_monitor(self) -> None:
        self._soliloquio.iniciar_recordatorio_monitor(self._recordatorios)

    def iniciar_baile_monitor(self) -> None:
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
            if not (self._baile_hint or self._sm.bailando.is_set()):
                continue
            if self._sm.en_conversacion or self._muted.is_set():
                continue
            try:
                sonando = sonando_musica()
                fallos = 0
            except Exception:
                fallos += 1
                if fallos >= 2 and self._sm.bailando.is_set():
                    console.print('[dim][baile] Spotify no responde -> paro el baile[/]')
                    self._sm.bailando.clear()
                    self._baile_hint = False
                continue
            if sonando:
                if not self._sm.bailando.is_set():
                    self._t_baile_inicio = time.monotonic()
                    self._baile_hint = True
                    self._sm.bailando.set()
                    console.print('[dim][baile] musica sonando -> a bailar[/]')
            else:
                if self._sm.bailando.is_set() and \
                        time.monotonic() - self._t_baile_inicio < 4.0:
                    continue
                if self._sm.bailando.is_set():
                    console.print('[dim][baile] la musica paro -> fin del baile[/]')
                self._sm.bailando.clear()
                self._baile_hint = False
