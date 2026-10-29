"""
avisos_salud.py — Cómo cuenta Bob lo que ve el vigilante (Fase S, S3).

Cada cambio de una parte (se cae, vuelve, me rindo) y cada intento de arreglo deja una
línea en logs/salud.jsonl. Los cambios salen también en la consola, salvo la red, que ya
la anuncia el pipeline (O2).

Las partes de FRASES, además, las dice Bob por el parlante con la cara de su tag [EMO]
(decir_soliloquio: anti-eco, OLED y vuelta al estado). Solo habla cuando está libre (sin
charla, sin música, sin estar hablando ya); mientras tanto la frase espera. Si una parte
se cae y vuelve antes de que Bob pueda decirlo, no dice nada: ya pasó. La red no se dice
por voz (O-D3).

FRASES es un borrador: los textos los corrige Abdair.
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Callable, Optional

import errores
import rutas
from nucleo import salud

# Por parte: qué dice Bob cuando se cae, cuando vuelve y cuando se rinde.
FRASES = {
    'audio': {
        'cayo':  '[EMO:CONFUNDIDO] Se me cortó el audio del cuerpo. Mientras lo arreglo, te hablo por la laptop.',
        'volvio': '[EMO:FELIZ] Ya recuperé mi voz y mi oído.',
        'rota':  '[EMO:TRISTE] No pude reconectar mi audio. Sigo hablando por la laptop.',
    },
    'serial': {
        'cayo':  '[EMO:CONFUNDIDO] Perdí contacto con mi cuerpo. Estoy intentando reconectarme.',
        'volvio': '[EMO:FELIZ] ¡Ya siento mi cuerpo otra vez!',
    },
    'memoria': {
        'cayo':  '[EMO:ASUSTADO] Tengo un problema con mi memoria. Puede que por ahora no recuerde nombres.',
        'volvio': '[EMO:FELIZ] Mi memoria volvió.',
    },
    'hilos': {
        'cayo':  '[EMO:CONFUNDIDO] Se me trabó una parte de la cabeza. Ya la reinicio.',
        'volvio': '[EMO:FELIZ] Listo, ya estoy completo otra vez.',
        'rota':  '[EMO:TRISTE] Una parte de mi cabeza no arranca. Sigo como puedo.',
    },
    # S6: Bob arregla su propio código (agente/autoarreglo.py).
    'autoarreglo': {
        'empiezo':   '[EMO:PENSANDO] Encontré un error en mi código. Voy a intentar arreglarlo.',
        'listo':     '[EMO:ORGULLOSO] Me arreglé. Me reinicio un momento para aplicarlo.',
        'revertido': '[EMO:AVERGONZADO] Mi arreglo no funcionó, así que lo deshice.',
        'propuesta': '[EMO:PENSANDO] Encontré un error en mi código. Dejé anotado cómo arreglarlo.',
    },
}

# Cómo se nombra cada parte en la barra del CLI.
ETIQUETAS = {
    'serial': 'cuerpo (DevKit)', 'audio': 'audio del DevKit',
    'red': 'internet', 'ollama': 'Ollama', 'memoria': 'memoria',
    'hilo-voice-pipeline': 'charla', 'hilo-wake-monitor': 'escucha de "Bob"',
    'hilo-soliloquio': 'soliloquio', 'hilo-recordatorios': 'recordatorios',
    'hilo-baile-monitor': 'baile',
}
SIN_CONSOLA = {'red'}
RUTA_LOG = os.path.join(rutas.LOGS_DIR, 'salud.jsonl')
ESPERA_S = 1.0   # cada cuánto mira si Bob está libre para decir una frase pendiente


def etiqueta(nombre: str) -> str:
    return ETIQUETAS.get(nombre, nombre)


def _frases(nombre: str) -> dict:
    return FRASES.get('hilos' if nombre.startswith('hilo-') else nombre, {})


class Avisador:
    def __init__(self, decir: Optional[Callable[[str], None]] = None,
                 libre: Callable[[], bool] = lambda: True,
                 ruta_log: Optional[str] = RUTA_LOG,
                 reloj: Callable[[], float] = time.time) -> None:
        self._decir = decir
        self._libre = libre
        self._ruta_log = ruta_log
        self._reloj = reloj
        self._pendientes: dict[str, tuple[str, str]] = {}   # nombre -> (evento, frase)
        self._lock = threading.Lock()
        self._detener = threading.Event()

    # Para el Vigilante (al_cambiar, al_rendirse, al_intentar).
    def al_cambiar(self, nombre: str, r: salud.Resultado) -> None:
        evento = 'volvio' if r.ok else 'cayo'
        self._registrar(nombre, evento, r.detalle)
        if nombre not in SIN_CONSOLA:
            salud.avisar_consola(nombre, r)
        with self._lock:
            if r.ok and self._pendientes.get(nombre, ('',))[0] in ('cayo', 'rota'):
                del self._pendientes[nombre]     # se cayó y volvió sin que Bob lo dijera
                return
        self._pendiente(nombre, evento)

    def al_rendirse(self, nombre: str, r: salud.Resultado) -> None:
        self._registrar(nombre, 'rota', r.detalle)
        salud.avisar_rendido(nombre, r)
        self._pendiente(nombre, 'rota')

    def al_intentar(self, nombre: str, intento: int, error: Optional[Exception]) -> None:
        self._registrar(nombre, 'arreglo', '' if error is None else f'{error.__class__.__name__}: {error}',
                        intento=intento, lanzo=error is not None)

    def avisar(self, nombre: str, evento: str) -> None:
        """Una frase de FRASES[nombre][evento] que no viene del Vigilante (p. ej. el
        autoarreglo). Solo a la voz: la consola y el registro los lleva quien avisa."""
        self._pendiente(nombre, evento)

    def pendientes(self) -> list[str]:
        with self._lock:
            return [frase for _, frase in self._pendientes.values()]

    def entregar(self) -> bool:
        """Dice una frase pendiente si Bob está libre. True si dijo una."""
        if self._decir is None or not self._libre():
            return False
        with self._lock:
            if not self._pendientes:
                return False
            nombre = next(iter(self._pendientes))
            _, frase = self._pendientes.pop(nombre)
        self._decir(frase)
        return True

    def iniciar(self) -> None:
        threading.Thread(target=self._loop, daemon=True, name='avisos-salud').start()

    def cerrar(self) -> None:
        self._detener.set()

    def _loop(self) -> None:
        while not self._detener.wait(ESPERA_S):
            try:
                self.entregar()
            except Exception as e:
                errores.aviso('SALUD', 'Falló una frase de aviso; sigo como siempre.', e)

    def _pendiente(self, nombre: str, evento: str) -> None:
        frase = _frases(nombre).get(evento)
        if frase is None or self._decir is None:
            return
        with self._lock:
            self._pendientes.pop(nombre, None)      # la última noticia reemplaza a la vieja
            self._pendientes[nombre] = (evento, frase)

    def _registrar(self, nombre: str, evento: str, detalle: str, **extra) -> None:
        if self._ruta_log is None:
            return
        linea = {'t': round(self._reloj(), 3), 'parte': nombre, 'evento': evento,
                 'detalle': detalle, **extra}
        try:
            os.makedirs(os.path.dirname(self._ruta_log), exist_ok=True)
            with self._lock, open(self._ruta_log, 'a', encoding='utf-8') as f:
                f.write(json.dumps(linea, ensure_ascii=False) + '\n')
        except OSError as e:
            errores.aviso('SALUD', 'No pude escribir la bitácora de salud.', e)
