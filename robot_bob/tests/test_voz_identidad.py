"""
test_voz_identidad.py — Quién habla según la voz (V4), sin red, cámara ni micrófono.

Combina la cara de la charla con la voz del turno: sin cara, la voz del dueño
lo identifica; con su cara, la voz confirma o avisa que habla otro; con la cara
de otra persona, marca que habla el dueño. Comprueba la nota que recibe el LLM
y que la charla identificada solo por voz se guarde en la memoria. Usa una
memory.db temporal. Si hay grabaciones en exp_voz/, repite con voces reales.

  python tests/test_voz_identidad.py
"""
import glob
import os
import shutil
import sys
import tempfile
import threading
import types

import numpy as np

_BOB = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, _BOB)

import voice_pipeline as vp   # noqa: E402
from memory import Memoria    # noqa: E402

DIR_WAV = os.path.join(_BOB, 'exp_voz')
fallos = 0


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    global fallos
    fallos += 0 if ok else 1
    print(f"[{'OK' if ok else 'FALLA'}] {nombre} {detalle}", flush=True)


class FakeSM:
    mood = 0.0

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


class FakeLLM:
    """Guarda los mensajes que recibe y contesta una frase."""
    def __init__(self):
        self.chat = self
        self.completions = self
        self.mensajes = None

    def create(self, **k):
        self.mensajes = k['messages']
        return iter([types.SimpleNamespace(
            choices=[types.SimpleNamespace(delta=types.SimpleNamespace(content='Hola.'))])])


def vec(seed: int) -> np.ndarray:
    v = np.random.default_rng(seed).standard_normal(256).astype(np.float32)
    return v / np.linalg.norm(v)


def cerca(base, seed):
    r = base + vec(seed) * 0.3
    return r / np.linalg.norm(r)


def construir(mem, pid=None, score=None):
    v = vp.VoicePipeline(FakeMgr(), FakeSM(), face_id=types.SimpleNamespace(listo=False),
                         memoria=mem,
                         get_frame=lambda: None, voice_id=types.SimpleNamespace(listo=True))
    v._identificar()          # estado de charla limpio (sin cámara: no reconoce a nadie)
    v._convo_persona, v._convo_face_score = pid, score
    return v


def turno(v, emb, seg=2.5):
    v._voz_turno = v._verificar_voz_turno((emb, seg))
    v._aplicar_voz_turno()
    return v


def main() -> None:
    vp.MEMORIA_ENABLED = True
    vp.VOZ_ID_ENABLED = True
    vp.DUENO_ID = 1
    tmp = tempfile.mkdtemp(prefix='bob_voz4_')
    try:
        mem = Memoria(os.path.join(tmp, 'm.db'))
        dueno = mem.registrar('Abdair', None)
        otra = mem.registrar('Gisel', None)
        base = vec(1)

        # 1) Pocas muestras: la voz todavía no decide nada
        for i in range(vp.VOZ_MIN_MUESTRAS - 1):
            mem.agregar_voz(dueno, cerca(base, 10 + i))
        v = turno(construir(mem), cerca(base, 50))
        check('con pocas muestras no identifica', v._convo_persona is None and v._marca_voz == '')
        mem.agregar_voz(dueno, cerca(base, 20))

        # 2) Sin cara + voz del dueño → lo reconoce por la voz
        v = turno(construir(mem), cerca(base, 51))
        check('sin cara: la voz identifica al dueño',
              v._convo_persona == dueno and v._persona_nombre == 'Abdair')
        check('sin cara: prompt con su memoria', 'ya conocés a esta persona' in v._system_prompt_actual)
        check('sin cara: nota de voz para el LLM', 'Abdair' in v._marca_voz)

        # 3) Sin cara + otra voz → sigue desconocido
        v = turno(construir(mem), vec(2))
        check('sin cara: otra voz no identifica', v._convo_persona is None and v._marca_voz == '')

        # 4) Frase corta → no decide
        v = turno(construir(mem), cerca(base, 52), seg=1.0)
        check('frase corta: no decide', v._convo_persona is None and v._voz_turno is None)

        # 5) Cara del dueño + su voz → confirma
        v = turno(construir(mem, dueno, 0.8), cerca(base, 53))
        check('cara + voz del dueño: confirma', 'Abdair' in v._marca_voz and 'NO' not in v._marca_voz)

        # 6) Cara del dueño + otra voz → avisa que habla otra persona
        v = turno(construir(mem, dueno, 0.8), vec(3))
        check('cara del dueño + otra voz: habla otra persona', 'NO te habla Abdair' in v._marca_voz)
        check('cara del dueño + otra voz: la identidad de la charla no cambia', v._convo_persona == dueno)

        # 7) Cara de otra persona + voz del dueño → habla el dueño, la charla sigue con ella
        v = turno(construir(mem, otra, 0.8), cerca(base, 54))
        check('cara de otra + voz del dueño: marca al dueño',
              'Abdair' in v._marca_voz and v._convo_persona == otra)

        # 8) La nota llega al system prompt del LLM, y no queda en el historial
        v = turno(construir(mem), cerca(base, 55))
        llm = FakeLLM()
        v._llm, v._llm_model, v._llm_extra = llm, 'fake', {}
        v._recordatorios = types.SimpleNamespace(vencidos=lambda: [], agregar=lambda r: None)
        list(v._stream_llm('hola'))
        sistema = llm.mensajes[0]['content'] if llm.mensajes else ''
        check('LLM: el system prompt trae la nota de voz', v._marca_voz in sistema and v._marca_voz)
        check('LLM: el historial no trae la nota', all('Por su voz' not in m['content']
                                                       for m in v._convo))

        # 9) Charla identificada solo por voz: el recuerdo se guarda
        antes = len(mem.episodios(dueno, 100))
        v._resumir_conversacion = lambda: ('Charlamos por voz sin cámara.', None, None)
        v._cerrar_memoria()
        check('sin cara: el recuerdo se guarda en el dueño',
              len(mem.episodios(dueno, 100)) == antes + 1)
        check('sin cara: no se crea una persona nueva', mem.total_personas() == 2)

        # 10) Sin voz activa no cambia nada (y el monitor no llama a la voz)
        vp.VOZ_ID_ENABLED = False
        check('VOZ_ID_ENABLED=False: inactiva', not construir(mem)._voz_activa())
        vp.VOZ_ID_ENABLED = True
        mem.cerrar()

        # 11) Voces reales: tu promedio de 5 frases reconoce tus otras frases y no a Gisel
        rutas = sorted(glob.glob(os.path.join(DIR_WAV, '*.wav')))
        abd = [r for r in rutas if os.path.basename(r).startswith('abdair_')]
        ajenas = [r for r in rutas if r not in abd]
        if len(abd) > 5 and ajenas:
            from voice_id import VoiceID
            vid = VoiceID(en_fondo=False)
            mem2 = Memoria(os.path.join(tmp, 'm2.db'))
            d2 = mem2.registrar('Abdair', None)
            leer = lambda r: vid.analizar(open(r, 'rb').read())   # noqa: E731
            for r in abd[:5]:
                mem2.agregar_voz(d2, leer(r)[0], 'explicito')
            si = no = total_a = total_o = 0
            for r in abd[5:]:
                emb, seg = leer(r)
                if emb is None or seg < vp.VOZ_MIN_S:
                    continue
                total_a += 1
                si += turno(construir(mem2), emb, seg)._convo_persona == d2
            for r in ajenas:
                emb, seg = leer(r)
                if emb is None or seg < vp.VOZ_MIN_S:
                    continue
                total_o += 1
                no += turno(construir(mem2), emb, seg)._convo_persona is None
            check('real: tus frases te identifican sin cara', si == total_a, f'({si}/{total_a})')
            check('real: las frases ajenas no se hacen pasar por ti', no == total_o,
                  f'({no}/{total_o})')
            mem2.cerrar()
        else:
            print('[--] faltan grabaciones en exp_voz/: caso real no ejecutado')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{'TODO OK' if fallos == 0 else f'{fallos} FALLA(S)'}")
    sys.exit(1 if fallos else 0)


if __name__ == '__main__':
    main()
