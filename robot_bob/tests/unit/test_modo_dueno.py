"""
test_modo_dueno.py — Comandos de dueño (V5a), sin red, cámara ni micrófono.

Puerta de voz (dueño / otra voz / frase corta / sin perfil) y "Bob, te presento
a X" (persona nueva, ya conocida, el propio dueño, sin cara), también pasando
por el ciclo de la charla. Incluye dos regresiones de identidad diferida: no usar
la cara de la charla anterior y no perder el nombre que la persona dijo antes de
que Bob la identificara. Usa memory.db temporal.

  python tests/unit/test_modo_dueno.py
"""
import os
import shutil
import sys
import tempfile
import threading
import types

import numpy as np

_BOB = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, _BOB)

from voz import pipeline as vp   # noqa: E402
from agente import modo_dueno as md       # noqa: E402
from memoria.memory import Memoria    # noqa: E402

# R1: el LLM adelantado se prueba en test_especulacion.py; aquí no debe pedir al real.
vp.JEV_EN_PARALELO = False

fallos = 0


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    global fallos
    fallos += 0 if ok else 1
    print(f"[{'OK' if ok else 'FALLA'}] {nombre} {detalle}", flush=True)


class FakeSM:
    mood = 0.0
    stt_fail_streak = 0
    positive_streak = 0
    en_conversacion = False
    scan_request = None

    def __init__(self):
        self.ev_hablando = threading.Event()
        self.ev_escuchando = threading.Event()
        self.ev_pensando = threading.Event()
        self.bailando = threading.Event()

    def estado_interno_prompt(self):
        return ''

    def __getattr__(self, n):
        return lambda *a, **k: None


class FakeMgr:
    def __getattr__(self, n):
        return lambda *a, **k: None


class FakeFace:
    """La cara que 've' Bob: se cambia con .cara = (emb, edad) o (None, None)."""
    listo = True

    def __init__(self):
        self.cara = (None, None)

    def analizar(self, frame):
        return self.cara


def vec(seed: int, dim: int = 256) -> np.ndarray:
    v = np.random.default_rng(seed).standard_normal(dim).astype(np.float32)
    return v / np.linalg.norm(v)


def cerca(base, seed):
    r = base + vec(seed, len(base)) * 0.3
    return r / np.linalg.norm(r)


def construir(mem, face):
    v = vp.VoicePipeline(FakeMgr(), FakeSM(), face_id=face, memoria=mem,
                         get_frame=lambda: 'frame', voice_id=types.SimpleNamespace(listo=True))
    v._reset_identidad()
    v.dichas = []
    v._hablar = lambda texto, emo=None: v.dichas.append(texto)
    return v


def main() -> None:
    vp.MEMORIA_ENABLED = True
    vp.VOZ_ID_ENABLED = True
    vp.DUENO_ID = 1
    md.MODO_DUENO_ENABLED = True
    md.CARA_ESPERA_S = 0.0
    tmp = tempfile.mkdtemp(prefix='bob_v5a_')
    try:
        # 1) Detección del comando
        for t, nombre in (('Bob, te presento a María', 'María'),
                          ('te presento a mi amiga Gisel', 'Gisel'),
                          ('conoce a Juan', 'Juan'),
                          ('te quiero presentar a pedro', 'Pedro')):
            d = md.detectar(t)
            check(f'detecta: "{t}"', d == ('presentar', {'nombre': nombre}), f'-> {d}')
        for t in ('te presento mi proyecto', 'hoy presento el examen',
                  'te presento a mi amiga', 'conoce a alguien', ''):
            check(f'no detecta: "{t}"', md.detectar(t) is None, f'-> {md.detectar(t)}')

        mem = Memoria(os.path.join(tmp, 'm.db'))
        cara_dueno, cara_gisel = vec(500, 512), vec(501, 512)
        dueno = mem.registrar('Abdair', cara_dueno)
        voz = vec(1)
        voz_dueno = (cerca(voz, 2), 1.6)
        voz_otra = (vec(3), 1.6)
        face = FakeFace()

        # 2) Sin perfil de voz → pide enseñarla primero
        v = construir(mem, face)
        check('sin perfil: verificar', md.verificar(v, voz_dueno) == 'sin_perfil')
        for i in range(vp.VOZ_MIN_MUESTRAS):
            mem.agregar_voz(dueno, cerca(voz, 10 + i), 'explicito')

        # 3) Puerta de voz
        check('puerta: voz del dueño', md.verificar(v, voz_dueno) == 'si')
        check('puerta: otra voz', md.verificar(v, voz_otra) == 'no')
        check('puerta: frase corta', md.verificar(v, (cerca(voz, 4), 0.6)) == 'corta')
        check('puerta: sin audio', md.verificar(v, (None, 0.0)) == 'corta')

        # 4) Apagado → no intercepta
        md.MODO_DUENO_ENABLED = False
        check('apagado: no es comando', not md.atender(v, 'te presento a María', voz_dueno))
        md.MODO_DUENO_ENABLED = True
        check('texto normal: no es comando', not md.atender(v, 'cuéntame un chiste', voz_dueno))

        # 5) Otra voz → rechazo con gracia, sin altas
        face.cara = (vec(502, 512), 25)
        v = construir(mem, face)
        n0 = mem.total_personas()
        check('otra voz: es comando', md.atender(v, 'te presento a María', voz_otra))
        check('otra voz: rechazo con gracia', any(r.split('{')[0][:20] in d
                                                  for r in md.RECHAZOS for d in v.dichas),
              f'-> {v.dichas}')
        check('otra voz: no guarda a nadie', mem.total_personas() == n0)

        # 6) Frase corta → pide repetir, sin altas
        v = construir(mem, face)
        md.atender(v, 'te presento a María', (cerca(voz, 5), 0.5))
        check('voz corta: pide repetir', any('otra vez' in d for d in v.dichas), f'-> {v.dichas}')
        check('voz corta: no guarda a nadie', mem.total_personas() == n0)

        # 7) Dueño + cara nueva → la guarda, la saluda y la charla sigue con ella
        cara_maria = vec(503, 512)
        face.cara = (cara_maria, 22)
        v = construir(mem, face)
        md.atender(v, 'Bob, te presento a María', voz_dueno)
        nueva = mem.reconocer(cara_maria)
        check('dueño: verifica en voz alta', v.dichas[:1] == ['Voz verificada, creador.'])
        check('dueño: guarda a María con su cara', nueva is not None and nueva[1] == 'María')
        p = mem.persona(nueva[0]) if nueva else None
        check('dueño: relación amigo de Abdair', p is not None and p[5] == 'amigo de Abdair')
        check('dueño: arranca con amistad', mem.nivel_relacion(nueva[0])[1] == 20 if nueva else False)
        check('dueño: la saluda por su nombre', any('María' in d for d in v.dichas[1:]))
        check('dueño: la charla sigue con María',
              v._convo_persona == (nueva[0] if nueva else -1) and 'María' in v._system_prompt_actual)

        # 8) Dueño + cara ya conocida con otro nombre → actualiza el nombre
        gis = mem.registrar('Gisela', cara_gisel)
        face.cara = (cara_gisel, 24)
        v = construir(mem, face)
        n1 = mem.total_personas()
        md.atender(v, 'te presento a Gisel', voz_dueno)
        check('conocida: no crea otra persona', mem.total_personas() == n1)
        check('conocida: nombre actualizado', mem.persona(gis)[1] == 'Gisel')
        check('conocida: "ya nos conocemos"', any('ya nos conocemos' in d for d in v.dichas))

        # 9) Dueño + su propia cara → broma, sin altas
        face.cara = (cara_dueno, 24)
        v = construir(mem, face)
        md.atender(v, 'te presento a Pedro', voz_dueno)
        check('su propia cara: broma', any('ese eres tú' in d for d in v.dichas), f'-> {v.dichas}')
        check('su propia cara: no guarda a Pedro', mem.total_personas() == n1)

        # 10) Dueño + nadie delante → lo dice, sin altas
        face.cara = (None, None)
        v = construir(mem, face)
        md.atender(v, 'te presento a Pedro', voz_dueno)
        check('sin cara: lo dice', any('No veo a nadie' in d for d in v.dichas))
        check('sin cara: no guarda a nadie', mem.total_personas() == n1)

        # 11) Por el ciclo de la charla (JEV sin saludo): comando en el primer turno
        cara_luis = vec(504, 512)
        face.cara = (cara_luis, 30)
        v = construir(mem, face)
        tabla = {b'voz-luis-cmd': voz_dueno}
        v._voice_id = types.SimpleNamespace(listo=True, analizar=lambda a: tabla.get(a, (None, 0.0)))
        llamadas = {'identificar': 0, 'llm': 0}
        original_identificar = v._identificar
        v._identificar = lambda: (llamadas.__setitem__('identificar', llamadas['identificar'] + 1),
                                  original_identificar())[1]
        v._decidir_ruta = lambda texto: 'charla'
        v._stream_and_speak = lambda texto: llamadas.__setitem__('llm', llamadas['llm'] + 1)
        v._grabar = lambda **k: None
        v._resumir_conversacion = lambda: (None, None, None)
        v._audio_wake = b'voz-luis-cmd'
        v._run_conversation(pending_text='Bob, te presento a Luis')
        luis = mem.reconocer(cara_luis)
        check('ciclo: Luis guardado', luis is not None and luis[1] == 'Luis')
        check('ciclo: el comando no va al LLM', llamadas['llm'] == 0)
        check('ciclo: no se reidentifica después', llamadas['identificar'] == 0)

        mem.cerrar()

        # 12) Regresión: charla diferida no hereda la cara de la anterior. Perfil
        #     con < 5 muestras: el filtro de conflicto todavía no protege.
        mem5 = Memoria(os.path.join(tmp, 'm5.db'))
        d5 = mem5.registrar('Abdair', cara_dueno)
        face.cara = (None, None)
        v = construir(mem5, face)
        v._convo_persona, v._convo_face_score = d5, 0.9         # charla anterior: el dueño
        tabla2 = {b'voz-extrana': (vec(7), 2.5)}
        v._voice_id = types.SimpleNamespace(listo=True, analizar=lambda a: tabla2.get(a, (None, 0.0)))
        v._identificar = lambda: None
        v._decidir_ruta = lambda texto: 'charla'
        v._stream_and_speak = lambda texto: None
        v._grabar = lambda **k: None
        v._resumir_conversacion = lambda: (None, None, None)
        v._audio_wake = b'voz-extrana'
        v._run_conversation(pending_text='hola, ¿qué tal tu día?')
        check('diferida: no guarda la voz ajena como del dueño', mem5.total_voces(d5) == 0,
              f'({mem5.total_voces(d5)} muestras)')
        mem5.cerrar()

        # 13) Regresión: "Bob, me llamo Carla" con identidad diferida y cara nueva.
        #     _identificar() resetea la charla y borraba el nombre recién dicho: la
        #     persona se guardaba sin nombre al cerrar.
        mem6 = Memoria(os.path.join(tmp, 'm6.db'))
        mem6.registrar('Abdair', cara_dueno)
        cara_carla = vec(505, 512)
        face.cara = (cara_carla, 25)
        v = construir(mem6, face)
        v._audio_wake = None
        v._decidir_ruta = lambda texto: 'charla'
        v._stream_and_speak = lambda texto: None
        v._grabar = lambda **k: None
        v._resumir_conversacion = lambda: (None, None, None)
        v._run_conversation(pending_text='Bob, me llamo Carla')
        carla = mem6.reconocer(cara_carla)
        check('diferida: la persona nueva se guarda con el nombre que dijo',
              carla is not None and carla[1] == 'Carla', f'-> {carla}')
        check('diferida: una sola persona nueva', mem6.total_personas() == 2,
              f'({mem6.total_personas()} personas)')

        # 14) Con cara conocida, el nombre dicho antes no renombra a nadie; y una
        #     charla nueva no hereda el nombre pendiente de la anterior.
        v._reset_identidad()
        v._nombre_pendiente = 'Otra'
        v._identificar_diferido()
        check('diferida con cara conocida: sigue siendo Carla',
              v._convo_persona == carla[0] and v._persona_nombre == 'Carla'
              and v._nombre_pendiente is None)
        v._nombre_pendiente = 'Otra'
        v._identificar()
        check('charla nueva: no hereda el nombre pendiente', v._nombre_pendiente is None)

        # 15) Bajo el umbral: reconocer() no da a nadie y mas_parecida() dice cuánto faltó
        desconocida = vec(506, 512)
        cerca_d = mem6.mas_parecida(desconocida)
        check('más parecida: reconocer no da a nadie', mem6.reconocer(desconocida) is None)
        check('más parecida: igual da la más cercana con su score',
              cerca_d is not None and cerca_d[0] in (1, carla[0]), f'-> {cerca_d}')
        mem6.cerrar()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{'TODO OK' if fallos == 0 else f'{fallos} FALLA(S)'}")
    sys.exit(1 if fallos else 0)


if __name__ == '__main__':
    main()
