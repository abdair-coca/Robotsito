"""
Soliloquio / actitud de Bob: frases espontaneas cuando esta solo (IDLE/PRESENCE).
Tambien incluye el loop de recordatorios (P9, reusa el camino de soliloquio).
"""

from __future__ import annotations

import random
import threading
import time
from collections import deque
from datetime import datetime
from typing import Optional

from rich.console import Console

from config import (
    SOLILOQUIO_ENABLED, SOLO_IDLE_MIN_S, PRESENCE_NUDGE_S,
    P_SOLILOQUIO, SOLILOQUIO_COOLDOWN_S, SOLILOQUIO_SETTLE_MS,
    BANCO_SOLILOQUIO, SOLILOQUIO_USA_LLM, SOLILOQUIO_LLM_RATIO,
    SOLILOQUIO_LLM_MAX_TOK, SOLILOQUIO_MAX_CARNADAS,
    SOLILOQUIO_LLM_MODEL, MUSICA_ENABLED, RECORDATORIOS_ENABLED,
)
from audio_helpers import extract_emo_tag

console = Console()

SOLILOQUIO_LLM_PROMPT = (
    "Eres Bob, un robot sociable en una feria de la Universidad Autonoma Tomas "
    "Frias (UATF), en Potosi, Bolivia. Ahora estas SOLO, nadie te habla.\n"
    "Di UNA sola frase corta (maximo 12 palabras) que dirias en voz alta para ti "
    "mismo: un pensamiento, una queja liviana, una curiosidad o una broma con "
    "actitud. Tono de companero de la facu, boliviano, divertido.\n"
    "La frase DEBE empezar con un tag de emocion entre corchetes: [EMO:FELIZ], "
    "[EMO:CURIOSO], [EMO:TRAVIESO], [EMO:PENSANDO], [EMO:TRISTE], [EMO:MUY_FELIZ], "
    "[EMO:CONFUNDIDO] o [EMO:AMOR].\n"
    "Texto plano hablable: sin comillas, sin markdown, sin emojis, sin acotaciones. "
    "Solo la frase con su tag."
)


class Soliloquio:
    def __init__(self, serial, sm, llm_client, hablar_fn,
                 detener_event, muted_event):
        self._serial = serial
        self._sm = sm
        self._llm_client = llm_client
        self._hablar = hablar_fn
        self._detener = detener_event
        self._muted = muted_event
        self._reciente: deque = deque(maxlen=5)

    def iniciar_monitor(self) -> None:
        if not SOLILOQUIO_ENABLED:
            return
        threading.Thread(target=self._loop, daemon=True,
                         name='soliloquio').start()

    def iniciar_recordatorio_monitor(self, recordatorios) -> None:
        if not RECORDATORIOS_ENABLED:
            return
        threading.Thread(
            target=self._recordatorio_loop, daemon=True,
            name='recordatorios', args=(recordatorios,)
        ).start()

    def _loop(self) -> None:
        from state_machine import RobotState
        t_ultimo = 0.0
        cooldown = SOLILOQUIO_COOLDOWN_S
        carnadas = 0
        while not self._detener.is_set():
            time.sleep(1.0)
            if self._detener.is_set():
                break
            est = self._sm.estado
            if est != RobotState.PRESENCE:
                carnadas = 0
            if self._sm.en_conversacion or self._muted.is_set():
                continue
            if self._sm.bailando.is_set():
                continue
            if self._sm.is_asleep():
                continue
            ahora = time.monotonic()
            if ahora - t_ultimo < cooldown:
                continue
            t_en = self._sm.t_en_estado
            if est == RobotState.IDLE and t_en >= SOLO_IDLE_MIN_S:
                categoria = random.choice(('aburrimiento', 'curiosidad', 'actitud'))
            elif est == RobotState.PRESENCE and t_en >= PRESENCE_NUDGE_S:
                if carnadas >= SOLILOQUIO_MAX_CARNADAS:
                    continue
                categoria = 'carnada'
            else:
                continue
            if random.random() > P_SOLILOQUIO:
                continue
            if self._sm.en_conversacion:
                continue
            frase = self._elegir(categoria, est)
            if not frase:
                continue
            self.decir(frase)
            if categoria == 'carnada':
                carnadas += 1
            t_ultimo = time.monotonic()
            cooldown = SOLILOQUIO_COOLDOWN_S * random.uniform(0.8, 1.4)

    def _elegir(self, categoria: str, estado) -> Optional[str]:
        if SOLILOQUIO_USA_LLM and random.random() < SOLILOQUIO_LLM_RATIO:
            frase = self._generar_llm(categoria, estado)
            if frase:
                return frase
        frases = BANCO_SOLILOQUIO.get(categoria)
        if not frases:
            return None
        candidatas = [f for f in frases
                      if extract_emo_tag(f)[1].lower() not in self._reciente]
        return random.choice(candidatas or frases)

    def _generar_llm(self, categoria: str, estado) -> Optional[str]:
        from state_machine import RobotState
        hora = datetime.now().strftime('%H:%M')
        if estado == RobotState.PRESENCE:
            situacion = ("Hay alguien cerca mirandote pero aun no te habla; "
                         "tirale una frase para romper el hielo.")
        else:
            situacion = "No hay nadie a la vista; esperas que llegue gente."
        contexto = (f"Categoria: {categoria}. Son las {hora}. {situacion} "
                    f"Tu animo (de -1 a 1) esta en {self._sm.mood:+.1f}.")
        if self._reciente:
            contexto += (" No repitas ni parafrasees estas frases que ya dijiste: "
                         + " | ".join(self._reciente))
        modelo = (SOLILOQUIO_LLM_MODEL
                  if SOLILOQUIO_LLM_MODEL and self._llm_client.is_groq
                  else self._llm_client.model)
        try:
            resp = self._llm_client.chat(
                [{'role': 'system', 'content': SOLILOQUIO_LLM_PROMPT},
                 {'role': 'user', 'content': contexto}],
                model=modelo,
                temperature=1.0,
                max_tokens=SOLILOQUIO_LLM_MAX_TOK,
            )
            if resp:
                console.print(f'[dim][soliloquio-LLM] generada: {resp}[/]')
            return resp
        except Exception as e:
            console.print(f'[dim][soliloquio] LLM fallo, uso banco: {e}[/]')
            return None

    def decir(self, frase: str) -> None:
        emo, clean = extract_emo_tag(frase)
        if not any(ch.isalnum() for ch in clean):
            return
        self._reciente.append(clean.lower())
        self._muted.set()
        self._sm.oled_ocupar()
        try:
            if emo:
                self._serial.cmd_estado(emo)
            console.print(f'[bold blue][soliloquio][/] [dim]({emo})[/]: {clean}')
            self._hablar(clean, emo)
        except Exception as e:
            console.print(f'[red][soliloquio] error: {e}[/]')
        finally:
            self._sm.oled_liberar()
            self._restaurar_oled()
            time.sleep(SOLILOQUIO_SETTLE_MS / 1000.0)
            self._muted.clear()

    def _restaurar_oled(self) -> None:
        from state_machine import RobotState, _OLED_STATE
        est = self._sm.estado
        if est == RobotState.PRESENCE:
            return
        cmd = _OLED_STATE.get(est)
        if cmd:
            self._serial.cmd_estado(cmd)

    def _recordatorio_loop(self, recordatorios) -> None:
        while not self._detener.is_set():
            time.sleep(1.0)
            if self._detener.is_set():
                break
            if self._sm.en_conversacion or self._muted.is_set():
                continue
            for rec in recordatorios.vencidos():
                frase = f"[EMO:CURIOSO] !Ey! Me pediste que te recuerde: {rec.que}."
                console.print(f'[bold magenta][recordatorio][/] {rec.que}')
                self.decir(frase)
