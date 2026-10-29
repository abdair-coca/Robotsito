"""
salud.py — ¿Cada parte de Bob está bien? (Fase S, S1).

Un chequeo es un nombre y una función revisar() -> Resultado. El Vigilante corre todos
los chequeos cada PERIODO_S en un hilo y llama a al_cambiar(nombre, resultado) una vez
por cambio: cuando una parte se cae y cuando vuelve. Arranca optimista: en la primera
pasada solo avisa de las partes caídas. Un chequeo que lanza cuenta como caído.

marcar_caida(nombre, detalle) baja una parte sin esperar a la próxima pasada (p. ej. un
error al usarla); la próxima pasada que la vea bien la devuelve.

Reparar (S2): si una parte caída tiene reparar(), el Vigilante la llama en otro hilo
(un arreglo puede tardar, p. ej. escanear la red) y no la repite mientras corre. Entre
intentos espera REPARAR_ESPERA_S, el doble tras cada fallo, hasta REPARAR_ESPERA_MAX_S.
Tras REPARAR_INTENTOS sin que la parte vuelva, se rinde: llama a al_rendirse(nombre,
resultado) una vez, la parte queda en rotas() y Bob sigue sin ella. Si vuelve sola,
deja de estar rota.

Las fábricas arman el chequeo de cada parte sin tocar su código: reciben el objeto (o
una función que lo lee) y miran su estado. Las recetas (reparar_*) son de S2.
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
REPARAR_ESPERA_S = 5.0
REPARAR_ESPERA_MAX_S = 120.0
REPARAR_INTENTOS = 5


@dataclass(frozen=True)
class Resultado:
    ok: bool
    detalle: str = ''


@dataclass(frozen=True)
class Chequeo:
    nombre: str
    revisar: Callable[[], Resultado]
    reparar: Optional[Callable[[], None]] = None


def _en_hilo(nombre: str, tarea: Callable[[], None]) -> None:
    threading.Thread(target=tarea, daemon=True, name=f'reparar-{nombre}').start()


def _correr(chequeo: Chequeo) -> Resultado:
    try:
        r = chequeo.revisar()
    except Exception as e:
        return Resultado(False, f'el chequeo falló: {e.__class__.__name__}: {e}')
    return r if isinstance(r, Resultado) else Resultado(False, f'el chequeo devolvió {r!r}')


class Vigilante:
    def __init__(self, chequeos: Iterable[Chequeo],
                 al_cambiar: Optional[Callable[[str, Resultado], None]] = None,
                 periodo_s: float = PERIODO_S,
                 al_rendirse: Optional[Callable[[str, Resultado], None]] = None,
                 reloj: Callable[[], float] = time.monotonic,
                 lanzar: Callable[[str, Callable[[], None]], None] = _en_hilo,
                 al_intentar: Optional[Callable[[str, int, Optional[Exception]], None]] = None
                 ) -> None:
        """al_intentar(nombre, intento, error) tras cada arreglo; error es None si no lanzó
        (eso no quiere decir que la parte volvió: lo dice la próxima pasada)."""
        self._chequeos = {c.nombre: c for c in chequeos}
        self._al_cambiar = al_cambiar
        self._al_rendirse = al_rendirse
        self._al_intentar = al_intentar
        self._periodo_s = periodo_s
        self._reloj = reloj
        self._lanzar = lanzar
        self._estado: dict[str, Resultado] = {}
        # nombre -> intentos, próximo intento, si hay uno corriendo y si se rindió
        self._reparacion: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._detener = threading.Event()

    def estado(self) -> dict[str, Resultado]:
        with self._lock:
            return dict(self._estado)

    def rotas(self) -> set[str]:
        with self._lock:
            return {n for n, rep in self._reparacion.items() if rep['rendida']}

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
            if r.ok:
                self._reparacion.pop(nombre, None)
        if cambio:
            self._avisar(self._al_cambiar, nombre, r, 'del cambio de')
        if not r.ok:
            self._quizas_reparar(nombre, r)

    def _quizas_reparar(self, nombre: str, r: Resultado) -> None:
        chequeo = self._chequeos.get(nombre)
        if chequeo is None or chequeo.reparar is None:
            return
        ahora = self._reloj()
        with self._lock:
            rep = self._reparacion.setdefault(
                nombre, {'intentos': 0, 'proximo': ahora, 'en_curso': False, 'rendida': False})
            if rep['en_curso'] or rep['rendida'] or ahora < rep['proximo']:
                return
            rendirse = rep['intentos'] >= REPARAR_INTENTOS
            if rendirse:
                rep['rendida'] = True
            else:
                rep['intentos'] += 1
                rep['en_curso'] = True
                rep['proximo'] = ahora + min(REPARAR_ESPERA_S * 2 ** (rep['intentos'] - 1),
                                             REPARAR_ESPERA_MAX_S)
        if rendirse:
            self._avisar(self._al_rendirse, nombre, r, 'de que me rendí con')
            return

        intento = rep['intentos']

        def tarea() -> None:
            error = None
            try:
                chequeo.reparar()
            except Exception as e:
                error = e
                errores.aviso('SALUD', f'Falló el intento de reparar {nombre}.', e)
            finally:
                with self._lock:
                    rep['en_curso'] = False
            if self._al_intentar is not None:
                try:
                    self._al_intentar(nombre, intento, error)
                except Exception as e:
                    errores.aviso('SALUD', f'Falló el aviso del arreglo de {nombre}.', e)

        self._lanzar(nombre, tarea)

    @staticmethod
    def _avisar(funcion, nombre: str, r: Resultado, que: str) -> None:
        if funcion is None:
            return
        try:
            funcion(nombre, r)
        except Exception as e:
            errores.aviso('SALUD', f'Falló el aviso {que} {nombre}.', e)

    def _loop(self) -> None:
        while not self._detener.is_set():
            self.revisar_todo()
            self._detener.wait(self._periodo_s)


# ── Fábricas de chequeos ────────────────────────────────────────────────────────

def chequeo_camara(frame_actual: Callable[[], object], max_s: float = CAMARA_MAX_S,
                   reloj: Callable[[], float] = time.monotonic,
                   reparar: Optional[Callable[[], None]] = None) -> Chequeo:
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

    return Chequeo('camara', revisar, reparar)


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


def chequeo_audio(audio_io, reparar: Optional[Callable[[], None]] = None) -> Chequeo:
    """AudioIO (shared/voicechatLap) baja `connected` cuando falla un envío o la lectura.
    Mientras está caído, el pipeline ya usa el micrófono y el parlante de la laptop."""
    def revisar() -> Resultado:
        if audio_io.connected:
            return Resultado(True)
        return Resultado(False, 'sin conexión de audio con el DevKit: uso la laptop')

    return Chequeo('audio', revisar, reparar)


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


def chequeo_hilo(nombre_hilo: str, loop: Callable[[], None],
                 vivos: Optional[Callable[[], set]] = None) -> Chequeo:
    """Un hilo que debe correr siempre; si muere, reparar() lanza su loop de nuevo con el
    mismo nombre. Solo sirve para loops que terminan al cerrar Bob: el Vigilante se
    cierra antes, para no relanzarlos durante el cierre."""
    c = chequeo_hilos((nombre_hilo,), nombre=f'hilo-{nombre_hilo}', vivos=vivos)

    def reparar() -> None:
        threading.Thread(target=loop, daemon=True, name=nombre_hilo).start()

    return Chequeo(c.nombre, c.revisar, reparar)


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


# ── Recetas (S2) ────────────────────────────────────────────────────────────────

def _ip_actual(clave: str, resolver: Optional[Callable[[], dict]]) -> Optional[str]:
    """IP de una placa según discovery (cache si responde; si no, escaneo de la red).
    clave: IP_ESPCAM, CONTROL_IP o ESP32_IP (discovery.ROLE_CONFIG_KEY)."""
    if resolver is None:
        from hardware import discovery
        resolver = discovery.resolve
    return resolver().get(clave)


def reparar_camara(lector, resolver: Optional[Callable[[], dict]] = None) -> Callable[[], None]:
    """El LectorStream ya reconecta solo, pero siempre a la misma IP: si la CAM cambió de
    IP (DHCP), le pone la nueva y su próximo reintento va ahí."""
    def reparar() -> None:
        ip = _ip_actual('IP_ESPCAM', resolver)
        if ip and ip != lector.host:
            errores.aviso('CÁMARA', f'Cambió de IP ({lector.host} → {ip}): me conecto a la nueva.')
            lector.host = ip

    return reparar


def reparar_audio(audio_io, resolver: Optional[Callable[[], dict]] = None,
                  timeout_s: float = 3.0) -> Callable[[], None]:
    """Cierra los sockets muertos y vuelve a conectar (a la IP nueva si cambió)."""
    def reparar() -> None:
        ip = _ip_actual('ESP32_IP', resolver)
        audio_io.close()
        if ip:
            audio_io.ip = ip
        audio_io.connect(timeout_s=timeout_s)

    return reparar


def avisar_consola(nombre: str, r: Resultado) -> None:
    """al_cambiar por defecto: la consola dice qué parte se cayó o volvió (S3 suma voz,
    OLED y bitácora)."""
    parte = nombre.upper()
    if r.ok:
        errores.aviso(parte, 'Volvió a funcionar.', r.detalle)
    else:
        errores.aviso(parte, 'Dejó de funcionar.', r.detalle)


def avisar_rendido(nombre: str, r: Resultado) -> None:
    errores.error(nombre.upper(), f'No pude repararla tras {REPARAR_INTENTOS} intentos: '
                  'sigo sin ella.', r.detalle)
