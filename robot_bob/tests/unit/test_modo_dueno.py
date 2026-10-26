"""
test_modo_dueno.py — Comandos de dueño (V5a), sin red, cámara ni micrófono.

Puerta de voz (dueño / otra voz / frase corta / sin perfil) y "Bob, te presento
a X" (persona nueva, ya conocida, el propio dueño, sin cara), también pasando
por el ciclo de la charla. Incluye dos regresiones de identidad diferida: no usar
la cara de la charla anterior y no perder el nombre que la persona dijo antes de
que Bob la identificara. Y la segunda mirada (al cerrar y al decir "me llamo X"):
una persona conocida cuyo primer frame salió malo no se duplica. Usa memory.db
temporal.

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


class FakeFaceSecuencia(FakeFace):
    """Devuelve las caras en orden (la primera es la de _identificar) y después
    repite la última."""
    def __init__(self, caras):
        super().__init__()
        self.caras = list(caras)

    def analizar(self, frame):
        return self.caras.pop(0) if len(self.caras) > 1 else self.caras[0]


def vec(seed: int, dim: int = 256) -> np.ndarray:
    v = np.random.default_rng(seed).standard_normal(dim).astype(np.float32)
    return v / np.linalg.norm(v)


def cerca(base, seed):
    r = base + vec(seed, len(base)) * 0.3
    return r / np.linalg.norm(r)


def con_sim(base, sim: float, seed: int) -> np.ndarray:
    """Cara con similitud coseno exacta `sim` contra base (un frame malo de la misma persona)."""
    r = vec(seed, len(base))
    r = r - np.dot(r, base) * base
    r = r / np.linalg.norm(r)
    return (sim * base + np.sqrt(1 - sim * sim) * r).astype(np.float32)


def entre(a, b) -> np.ndarray:
    """Cara a mitad de camino entre a y b (un frame bueno: se parece a las dos)."""
    m = a + b
    return (m / np.linalg.norm(m)).astype(np.float32)


def construir(mem, face):
    v = vp.VoicePipeline(FakeMgr(), FakeSM(), face_id=face, memoria=mem,
                         get_frame=lambda: 'frame', voice_id=types.SimpleNamespace(listo=True))
    v._reset_identidad()
    v.dichas = []
    v._hablar = lambda texto, emo=None: v.dichas.append(texto)
    return v


def charla(mem, caras, texto='hola, ¿cómo estás?', saludo=False):
    """Una charla completa hasta cerrar la memoria. Sin saludo es diferida (JEV):
    identifica antes de la primera respuesta; con saludo, al arrancar.
    caras: lo que ve la cámara en orden; la primera es la de _identificar()."""
    v = construir(mem, FakeFaceSecuencia(caras))
    v._audio_wake = None
    v._decidir_ruta = lambda t: 'charla'
    v._stream_and_speak = lambda t: None
    v._grabar = lambda **k: None
    v._resumir_conversacion = lambda: ('Charlaron un rato.', None, None)
    v._run_conversation(pending_text=texto, greeting=saludo)
    return v


def main() -> None:
    vp.MEMORIA_ENABLED = True
    vp.VOZ_ID_ENABLED = True
    vp.DUENO_ID = 1
    md.MODO_DUENO_ENABLED = True
    md.CARA_ESPERA_S = 0.0
    vp.MIRADA_PAUSA_S = 0.0
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

        # 16) Segunda mirada al cerrar. Ana ya está en la base; el primer frame de la
        #     charla sale malo (0.35 contra su cara guardada: no la reconoce) y los
        #     siguientes son buenos → la charla queda en Ana, sin fila nueva.
        mem7 = Memoria(os.path.join(tmp, 'm7.db'))
        mem7.registrar('Abdair', cara_dueno)
        cara_ana = vec(507, 512)
        ana = mem7.registrar('Ana', cara_ana)
        malo = con_sim(cara_ana, 0.35, 508)
        charla(mem7, [(malo, 30), (entre(malo, cara_ana), 30)])
        check('segunda mirada: no duplica a Ana', mem7.total_personas() == 2,
              f'({mem7.total_personas()} personas)')
        check('segunda mirada: el recuerdo queda en Ana', len(mem7.episodios(ana)) == 1)

        # 17) Persona nueva: se guarda con el promedio de sus caras y no con el primer
        #     frame, así un frame bueno posterior la reconoce mejor que 0.6.
        cara_beto = vec(509, 512)
        charla(mem7, [(con_sim(cara_beto, 0.6, 510), 40), (cara_beto, 40)])
        beto = mem7.reconocer(cara_beto)
        check('persona nueva: una sola fila', mem7.total_personas() == 3,
              f'({mem7.total_personas()} personas)')
        check('persona nueva: guardada con el promedio de sus caras',
              beto is not None and beto[2] > 0.9, f'-> {beto}')

        # 18) Al cerrar pasa otra persona conocida (Abdair) frente a la cámara: no es
        #     la cara de la charla y no cuenta. No se le asigna la charla y la nueva
        #     se guarda con su propia cara.
        cara_caro = vec(511, 512)
        charla(mem7, [(cara_caro, 20), (cara_dueno, 45)])
        caro = mem7.reconocer(cara_caro)
        check('otra persona al cerrar: no se le asigna la charla', len(mem7.episodios(1)) == 0)
        check('otra persona al cerrar: la nueva se guarda con su cara',
              caro is not None and caro[0] != 1 and caro[2] > 0.99, f'-> {caro}')

        # 19) Se fue antes de cerrar (sin cara en los frames nuevos): como antes, se
        #     guarda con la cara de la charla.
        cara_dora = vec(512, 512)
        charla(mem7, [(cara_dora, 33), (None, None)])
        dora = mem7.reconocer(cara_dora)
        check('se fue al cerrar: se guarda con la cara de la charla',
              dora is not None and dora[2] > 0.99, f'-> {dora}')

        # 20) La que reconoce al cerrar no tenía nombre y en la charla dijo "me llamo
        #     Eva": se completa el nombre, sin otra fila.
        cara_eva = vec(513, 512)
        eva = mem7.registrar(None, cara_eva)
        n = mem7.total_personas()
        malo_eva = con_sim(cara_eva, 0.35, 514)
        charla(mem7, [(malo_eva, 28), (entre(malo_eva, cara_eva), 28)], 'Bob, me llamo Eva')
        check('sin nombre + "me llamo Eva": completa el nombre sin otra fila',
              mem7.total_personas() == n and mem7.persona(eva)[1] == 'Eva',
              f'({mem7.total_personas()} personas, nombre {mem7.persona(eva)[1]})')

        # 21) "me llamo Fede" a mitad de charla (identidad al arrancar, con saludo).
        #     Fede ya está en la base y el primer frame sale malo: antes de
        #     registrarlo, Bob vuelve a mirar y sigue como si lo hubiera reconocido.
        cara_fede = vec(515, 512)
        fede = mem7.registrar('Fede', cara_fede)
        n = mem7.total_personas()
        malo_fede = con_sim(cara_fede, 0.35, 516)
        v = charla(mem7, [(malo_fede, 35), (entre(malo_fede, cara_fede), 35)],
                   'me llamo Fede', saludo=True)
        check('"me llamo" + segunda mirada: no duplica a Fede', mem7.total_personas() == n,
              f'({mem7.total_personas()} personas)')
        check('"me llamo" + segunda mirada: la charla sigue con Fede',
              v._convo_persona == fede and 'ya conocés a esta persona' in v._system_prompt_actual)
        check('"me llamo" + segunda mirada: el recuerdo queda en Fede',
              len(mem7.episodios(fede)) == 1)

        # 22) "me llamo Gabi", nueva: se registra al momento con el promedio de sus caras.
        cara_gabi = vec(517, 512)
        charla(mem7, [(con_sim(cara_gabi, 0.6, 518), 22), (cara_gabi, 22)], 'me llamo Gabi',
               saludo=True)
        gabi = mem7.reconocer(cara_gabi)
        check('"me llamo" nueva: guardada con su nombre y el promedio de sus caras',
              gabi is not None and gabi[1] == 'Gabi' and gabi[2] > 0.9, f'-> {gabi}')

        # 23) Guardada con otro nombre (p. ej. mal transcrito): toma el que dice ahora,
        #     igual que si la hubiera reconocido al empezar.
        cara_ale = vec(519, 512)
        ale = mem7.registrar('Halle', cara_ale)
        n = mem7.total_personas()
        malo_ale = con_sim(cara_ale, 0.35, 520)
        charla(mem7, [(malo_ale, 27), (entre(malo_ale, cara_ale), 27)], 'me llamo Ale',
               saludo=True)
        check('"me llamo" con otro nombre guardado: toma el que dice ahora, sin otra fila',
              mem7.total_personas() == n and mem7.persona(ale)[1] == 'Ale',
              f'({mem7.total_personas()} personas, nombre {mem7.persona(ale)[1]})')
        mem7.cerrar()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{'TODO OK' if fallos == 0 else f'{fallos} FALLA(S)'}")
    sys.exit(1 if fallos else 0)


if __name__ == '__main__':
    main()
