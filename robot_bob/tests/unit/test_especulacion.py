"""
test_especulacion.py — LLM adelantado mientras JEV decide la ruta (Fase R1), sin red.

JEV simulado lento (0.4 s) y LLM falso que registra cuándo arranca cada pedido.
Cubre: en charla el pedido sale antes de que JEV termine y se usa ese mismo; si
JEV dice que era un comando, se cancela y no se habla; con un comando que ven las
reglas no se pide nada; si el contexto cambia (ruta asistente) se pide de nuevo;
a mitad de charla sin pregunta de Bob no se adelanta; LLM caído → respaldo.

  python tests/unit/test_especulacion.py
"""
import os
import sys
import threading
import time
from types import SimpleNamespace as NS

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from voz import pipeline as vp          # noqa: E402
from voz.jev_decision import Decision    # noqa: E402

fallos = 0
JEV_S = 0.4


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    global fallos
    fallos += 0 if ok else 1
    print(f"[{'OK' if ok else 'FALLA'}] {nombre} {detalle}", flush=True)


class FakeSM:
    en_conversacion = False
    stt_fail_streak = 0
    positive_streak = 0
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
    def __init__(self, partes=('¡Claro que sí! ', 'Te cuento algo bonito hoy.'), error=None):
        self.partes = partes
        self.error = error
        self.pedidos = []      # (instante, kwargs)
        self.cerrados = 0
        self.chat = NS(completions=NS(create=self.create))

    def create(self, **kw):
        self.pedidos.append((time.monotonic(), kw))
        if self.error:
            raise self.error
        llm = self

        class Stream:
            def __iter__(s):
                for p in llm.partes:
                    time.sleep(0.05)
                    yield NS(choices=[NS(delta=NS(content=p))])

            def close(s):
                llm.cerrados += 1
        return Stream()


def construir(textos, rutas, llm=None):
    """textos: lo que dice el usuario por turno; rutas: lo que decide JEV por turno."""
    v = vp.VoicePipeline(FakeMgr(), FakeSM())
    v._llm = llm or FakeLLM()
    v._llm_model, v._llm_extra = 'fake', {}
    v.dichas, v.hablado = [], []
    v.jev_fin = []
    cola_t, cola_r = list(textos), list(rutas)
    v._transcribir = lambda audio, **k: cola_t.pop(0) if cola_t else ''
    v._grabar = lambda **k: b'\x00' * 3200 if cola_t else None
    v._hablar = lambda texto, emo=None: v.hablado.append(texto)
    v._reproducir = lambda mp3: v.dichas.append(mp3.decode()) and None

    def decidir(frase, ultimo):
        time.sleep(JEV_S)
        v.jev_fin.append(time.monotonic())
        ruta = cola_r.pop(0) if cola_r else 'charla'
        return Decision(ruta, 'FELIZ', 0.9, 0.9, JEV_S, False, '')
    vp.decidir = decidir
    return v


def main() -> None:
    synth_original, decidir_original = vp._sintetizar, vp.decidir
    vp._sintetizar = lambda t, e=None: t.encode()
    vp.JEV_ROUTING_ENABLED = True
    vp.JEV_EN_PARALELO = True
    try:
        # 1) Charla: el pedido sale antes de que JEV termine y se usa ese mismo
        v = construir(['¿cómo estás hoy?'], ['charla'])
        v._run_conversation(pending_audio=b'\x00' * 3200)
        p = v._llm.pedidos
        check('charla: un solo pedido al LLM', len(p) == 1, f'-> {len(p)}')
        check('charla: salió antes de que JEV terminara',
              p and v.jev_fin and p[0][0] < v.jev_fin[0] - 0.2,
              f'-> {(v.jev_fin[0] - p[0][0]) if p and v.jev_fin else None}')
        check('charla: Bob dice la respuesta', any('Claro que sí' in d for d in v.dichas), f'-> {v.dichas}')
        check('charla: historial con la frase y la respuesta',
              [m['role'] for m in v._convo] == ['user', 'assistant'])

        # 2) JEV dice "giro" (las reglas no lo veían): se cancela, no se habla del LLM
        v = construir(['¿me ves bien desde ahí?'], ['giro_derecha'])
        v._run_conversation(pending_audio=b'\x00' * 3200)
        time.sleep(0.3)
        check('comando de JEV: el pedido adelantado se cancela', v._llm.cerrados >= 1
              and v._prefetch is None)
        check('comando de JEV: no habla lo del LLM', not v.dichas and v.hablado, f'-> {v.hablado}')
        check('comando de JEV: no queda en el historial', not any(m['role'] == 'user' for m in v._convo))

        # 3) Comando que ven las reglas → no se adelanta nada
        v = construir(['recuérdame en 5 minutos tomar agua'], ['recordatorio'])
        v._run_conversation(pending_audio=b'\x00' * 3200)
        check('comando por reglas: ningún pedido al LLM', not v._llm.pedidos)

        # 4) Ruta asistente sin palabras clave: el contexto cambia → pedido nuevo
        v = construir(['¿qué onda?'], ['asistente'])
        v._run_conversation(pending_audio=b'\x00' * 3200)
        p = v._llm.pedidos
        check('contexto distinto: se pide de nuevo', len(p) == 2, f'-> {len(p)}')
        sys2 = p[-1][1]['messages'][0]['content'] if p else ''
        check('contexto distinto: el nuevo trae la hora', 'Hora actual' in sys2)
        check('contexto distinto: Bob habla una sola respuesta',
              sum('Claro que sí' in d for d in v.dichas) == 1, f'-> {v.dichas}')

        # 5) A mitad de charla, Bob no preguntó y no le dicen "Bob" → no se adelanta
        llm = FakeLLM(partes=('Qué lindo día hace hoy. ',))
        v = construir(['hola, ¿qué tal?', 'mi hermana estudia medicina'], ['charla', 'charla'], llm)
        v._run_conversation(pending_audio=b'\x00' * 3200)
        p = llm.pedidos
        check('2º turno sin pregunta: pide después de JEV', len(p) == 2 and p[1][0] >= v.jev_fin[1] - 0.01,
              f'-> {len(p)}')

        # 6) ...pero si Bob terminó con pregunta, sí se adelanta
        llm = FakeLLM(partes=('¿Y tú qué estudias hoy? ',))
        v = construir(['hola, ¿qué tal?', 'ingeniería informática'], ['charla', 'charla'], llm)
        v._run_conversation(pending_audio=b'\x00' * 3200)
        p = llm.pedidos
        check('2º turno tras una pregunta: se adelanta', len(p) == 2 and p[1][0] < v.jev_fin[1] - 0.2)

        # 7) Flag apagado → como antes (pide después de JEV)
        vp.JEV_EN_PARALELO = False
        v = construir(['¿cómo estás hoy?'], ['charla'])
        v._run_conversation(pending_audio=b'\x00' * 3200)
        p = v._llm.pedidos
        check('flag apagado: pide después de JEV', len(p) == 1 and p[0][0] >= v.jev_fin[0] - 0.01)
        vp.JEV_EN_PARALELO = True

        # 8) LLM caído en el pedido adelantado → frase de respaldo
        v = construir(['¿cómo estás hoy?'], ['charla'], FakeLLM(error=RuntimeError('ningún LLM')))
        v._run_conversation(pending_audio=b'\x00' * 3200)
        check('LLM caído: dice la frase de respaldo', any(vp.FRASE_RESPALDO_LLM in d for d in v.dichas),
              f'-> {v.dichas}')
    finally:
        vp._sintetizar, vp.decidir = synth_original, decidir_original

    print(f"\n{'TODO OK' if fallos == 0 else f'{fallos} FALLA(S)'}")
    sys.exit(1 if fallos else 0)


if __name__ == '__main__':
    main()
