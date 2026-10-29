"""
salud.py — ¿Cada parte de Bob está bien? (Fase S, S1).

Un chequeo es un nombre y una función revisar() -> Resultado. El Vigilante corre todos
los chequeos cada PERIODO_S en un hilo y llama a al_cambiar(nombre, resultado) una vez
por cambio: cuando una parte se cae y cuando vuelve. Arranca optimista: en la primera
pasada solo avisa de las partes caídas. Un chequeo que lanza cuenta como caído.

marcar_caida(nombre, detalle) baja una parte sin esperar a la próxima pasada (p. ej. un
error al usarla); la próxima pasada que la vea bien la devuelve.

Las fábricas arman el chequeo de cada parte sin tocar su código: reciben el objeto (o
una función que lo lee) y miran su estado. Los arreglos de cada parte son de S2.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

import errores

PERIODO_S = 10.0
CAMARA_MAX_S = 5.0   # sin un frame nuevo en este tiempo, la cámara está caída
HILOS_VOZ = ('voice-pipeline', 'wake-monitor', 'soliloquio', 'recordatorios', 'baile-monitor')


@dataclass(frozen=True)
class Resultado:
    ok: bool
    detalle: str = ''


@dataclass(frozen=True)
class Chequeo:
    nombre: str
    revisar: Callable[[], Resultado]


def _correr(chequeo: Chequeo) -> Resultado:
    try:
        r = chequeo.revisar()
    except Exception as e:
        return Resultado(False, f'el chequeo falló: {e.__class__.__name__}: {e}')
    return r if isinstance(r, Resultado) else Resultado(False, f'el chequeo devolvió {r!r}')


class Vigilante:
    def __init__(self, chequeos: Iterable[Chequeo],
                 al_cambiar: Optional[Callable[[str, Resultado], None]] = None,
                 periodo_s: float = PERIODO_S) -> None:
        self._chequeos = {c.nombre: c for c in chequeos}
        self._al_cambiar = al_cambiar
        self._periodo_s = periodo_s
        self._estado: dict[str, Resultado] = {}
        self._lock = threading.Lock()
        self._detener = threading.Event()

    def estado(self) -> dict[str, Resultado]:
        with self._lock:
            return dict(self._estado)

    def revisar_todo(self) -> dict[str, Resultado]:
        """Una pasada ahora. Devuelve el estado de todas las partes."""
        for nombre, chequeo in self._chequeos.items():
            self._poner(nombre, _correr(chequeo))
        return self.estado()

    def marcar_caida(self, nombre: str, detalle: str = '') -> None:
        self._poner(nombre, Resultado(False, detalle))

    def iniciar(self) -> None:
        threading.Thread(target=self._loop, daemon=True, name='vigilante-salud').start()

    def cerrar(self) -> None:
        self._detener.set()

    def _poner(self, nombre: str, r: Resultado) -> None:
        with self._lock:
            antes = self._estado.get(nombre)
            self._estado[nombre] = r
            # Sin estado previo la parte se supone bien: solo se avisa si arranca caída.
            cambio = (r.ok is False) if antes is None else (antes.ok != r.ok)
        if cambio and self._al_cambiar is not None:
            try:
                self._al_cambiar(nombre, r)
            except Exception as e:
                errores.aviso('SALUD', f'Falló el aviso del cambio de {nombre}.', e)

    def _loop(self) -> None:
        while not self._detener.is_set():
            self.revisar_todo()
            self._detener.wait(self._periodo_s)


# ── Fábricas de chequeos ────────────────────────────────────────────────────────

def chequeo_camara(frame_actual: Callable[[], object], max_s: float = CAMARA_MAX_S,
                   reloj: Callable[[], float] = time.monotonic) -> Chequeo:
    """frame_actual devuelve el último frame recibido, sin copiarlo (p. ej.
    `lambda: tracker._stream._frame`): el lector lo reemplaza por un objeto nuevo con
    cada JPEG, así que el mismo objeto durante max_s significa video congelado."""
    visto = {'frame': None, 't': reloj()}

    def revisar() -> Resultado:
        frame, ahora = frame_actual(), reloj()
        if frame is not None and frame is not visto['frame']:
            visto['frame'], visto['t'] = frame, ahora
        edad = ahora - visto['t']
        if edad > max_s:
            return Resultado(False, f'sin frames nuevos hace {edad:.0f} s')
        return Resultado(True)

    return Chequeo('camara', revisar)


def chequeo_serial(serial_mgr) -> Chequeo:
    """SerialManager deja _modo='none' mientras reconecta. El simulado no tiene _modo."""
    def revisar() -> Resultado:
        modo = getattr(serial_mgr, '_modo', None)
        if modo is None:
            return Resultado(True, 'simulado')
        if modo == 'none':
            return Resultado(False, 'sin conexión con el DevKit')
        return Resultado(True, f'por {modo}')

    return Chequeo('serial', revisar)


def chequeo_audio(audio_io) -> Chequeo:
    """AudioIO (shared/voicechatLap) baja `connected` cuando falla un envío o la lectura."""
    def revisar() -> Resultado:
        if audio_io.connected:
            return Resultado(True)
        return Resultado(False, 'sin conexión de audio con el DevKit')

    return Chequeo('audio', revisar)


def chequeo_red(monitor) -> Chequeo:
    """Lee hardware.red.MonitorRed, que ya sondea por su cuenta (O1)."""
    def revisar() -> Resultado:
        return Resultado(True) if monitor.en_linea else Resultado(False, 'sin internet')

    return Chequeo('red', revisar)


def chequeo_ollama(estado: Optional[Callable[[], tuple[bool, str]]] = None) -> Chequeo:
    def revisar() -> Resultado:
        if estado is None:
            from hardware.preflight import estado_ollama
            return Resultado(*estado_ollama())
        return Resultado(*estado())

    return Chequeo('ollama', revisar)


def chequeo_hilos(nombres: Iterable[str] = HILOS_VOZ, nombre: str = 'hilos',
                  vivos: Optional[Callable[[], set]] = None) -> Chequeo:
    nombres = tuple(nombres)

    def revisar() -> Resultado:
        corriendo = vivos() if vivos else {t.name for t in threading.enumerate()}
        faltan = [n for n in nombres if n not in corriendo]
        if faltan:
            return Resultado(False, f'no corre: {", ".join(faltan)}')
        return Resultado(True)

    return Chequeo(nombre, revisar)


def chequeo_memoria(ruta: Optional[str] = None) -> Chequeo:
    """Abre memory.db en solo lectura (mode=ro no crea el archivo si falta)."""
    def revisar() -> Resultado:
        if ruta is None:
            import rutas
            destino = rutas.DB_MEMORIA
        else:
            destino = ruta
        con = sqlite3.connect(f'{Path(destino).resolve().as_uri()}?mode=ro', uri=True,
                              timeout=2.0)
        try:
            con.execute('SELECT count(*) FROM sqlite_master').fetchone()
        finally:
            con.close()
        return Resultado(True)

    return Chequeo('memoria', revisar)
