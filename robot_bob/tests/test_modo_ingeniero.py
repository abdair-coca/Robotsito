"""
test_modo_ingeniero.py — Modo ingeniero del dueño (V5c), sin red ni micrófono.

Entrar solo con la voz del dueño; salir con cualquier voz o al terminar la
charla. Dentro del modo, cada pedido al LLM usa el prompt de ingeniero, el
modelo de la nube y hasta 4 frases; fuera, todo vuelve a lo de siempre. Un
reinicio de identidad (identificación diferida) no apaga el modo.

  python tests/test_modo_ingeniero.py
"""
import os
import sys
import threading
import types

import numpy as np

_BOB = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, _BOB)

import voice_pipeline as vp   # noqa: E402
import modo_dueno as md       # noqa: E402

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


class FakeMem:
    def __init__(self, sim):
        self.sim = sim

    def total_voces(self, pid):
        return 10

    def verificar_voz(self, pid, emb):
        return self.sim

    def persona(self, pid):
        return (1, 'Abdair')

    def contexto(self, pid):
        return 'Estás hablando con Abdair.'


class FakeLLM:
    """Stream de 6 frases; guarda los argumentos del pedido."""
    def __init__(self):
        self.chat = self
        self.completions = self
        self.pedidos = []

    def create(self, **k):
        self.pedidos.append(k)
        # Más de SENTENCE_MIN_CHARS cada una (las más cortas se juntan con la siguiente).
        frases = ['La primera idea es buena. ', 'La segunda idea también. ',
                  'La tercera idea suma más. ', 'La cuarta idea cierra todo. ',
                  'La quinta idea ya sobra. ', 'La sexta idea también sobra. ']
        return iter([types.SimpleNamespace(
            choices=[types.SimpleNamespace(delta=types.SimpleNamespace(content=f))])
            for f in frases])


VOZ = (np.ones(256, np.float32) / 16.0, 1.8)


def construir(sim=0.8):
    v = vp.VoicePipeline(FakeMgr(), FakeSM())
    v._memoria = FakeMem(sim)
    v._convo = []
    v._system_prompt_actual = 'PROMPT NORMAL'
    v.dichas = []
    v._hablar = lambda texto, emo=None: v.dichas.append(texto)
    v._llm, v._llm_model, v._llm_extra = FakeLLM(), 'modelo-local', {}
    v._client = FakeLLM()                    # "Groq"
    v._recordatorios = types.SimpleNamespace(vencidos=lambda: [], agregar=lambda r: None)
    v._ruta_turno = None
    return v


def main() -> None:
    md.MODO_DUENO_ENABLED = True

    # 1) Detección
    for t in ('Bob, modo ingeniero', 'modo ingeniero.', 'activa el modo ingeniero',
              'entra en modo ingeniero', 'ponte en modo ingeniera'):
        check(f'entrar: "{t}"', md.detectar(t) == ('ingeniero', {}), f'-> {md.detectar(t)}')
    for t in ('sal del modo ingeniero', 'desactiva el modo ingeniero', 'modo normal',
              'salí del modo ingeniero'):
        check(f'salir: "{t}"', md.detectar(t) == ('salir_ingeniero', {}), f'-> {md.detectar(t)}')
    for t in ('¿qué es el modo ingeniero?', 'el modo ingeniero es genial', 'soy ingeniero'):
        check(f'no es comando: "{t}"', md.detectar(t) is None, f'-> {md.detectar(t)}')

    # 2) Otra voz no entra
    v = construir(sim=0.1)
    md.atender(v, 'Bob, modo ingeniero', VOZ)
    check('otra voz: no entra', not v._modo_ingeniero)

    # 3) Fuera del modo: LLM local, prompt normal, 2 frases
    v = construir()
    salida = list(v._stream_llm('hola'))
    p = v._llm.pedidos[-1]
    check('normal: modelo local', p['model'] == 'modelo-local' and not v._client.pedidos)
    check('normal: prompt normal', p['messages'][0]['content'].startswith('PROMPT NORMAL'))
    check('normal: 2 frases', len(salida) == vp.MAX_FRASES_TURNO, f'({len(salida)})')

    # 4) El dueño entra: nube, prompt de ingeniero con su memoria, 4 frases
    md.atender(v, 'Bob, modo ingeniero', VOZ)
    check('dueño: entra', v._modo_ingeniero)
    check('dueño: lo anuncia', any('Modo ingeniero activado' in d for d in v.dichas))
    salida = list(v._stream_llm('¿cómo mejoramos los servos?'))
    p = v._client.pedidos[-1] if v._client.pedidos else {}
    check('ingeniero: usa la nube', p.get('model') == vp.GROQ_LLM_MODEL)
    sistema = p.get('messages', [{}])[0].get('content', '')
    check('ingeniero: prompt de ingeniero', sistema.startswith(md.MODO_INGENIERO_PROMPT[:40]))
    check('ingeniero: con la memoria de Abdair', 'Estás hablando con Abdair.' in sistema)
    check('ingeniero: más tokens', p.get('max_tokens') == md.ING_MAX_TOKENS)
    check('ingeniero: hasta 4 frases', len(salida) == md.ING_MAX_FRASES, f'({len(salida)})')

    # 5) Un reinicio de identidad (identificación diferida) no apaga el modo
    v._reset_identidad()
    check('reinicio de identidad: sigue en modo ingeniero', v._modo_ingeniero
          and md.ajustes_ingeniero(v) is not None)

    # 6) Ya activo: lo dice
    md.atender(v, 'activa el modo ingeniero', VOZ)
    check('ya activo: lo dice', any('Ya estamos en modo ingeniero' in d for d in v.dichas))

    # 7) Cualquier voz lo apaga; vuelve todo a lo normal
    md.atender(v, 'sal del modo ingeniero', (None, 0.0))
    check('salir (sin verificar voz): apagado', not v._modo_ingeniero)
    v._llm.pedidos.clear()
    salida = list(v._stream_llm('hola de nuevo'))
    check('después de salir: modelo local y 2 frases',
          v._llm.pedidos and v._llm.pedidos[-1]['model'] == 'modelo-local'
          and len(salida) == vp.MAX_FRASES_TURNO)

    # 8) Fin de la charla apaga el modo
    v = construir()
    md.atender(v, 'Bob, modo ingeniero', VOZ)
    v._identificar = lambda: None
    v._decidir_ruta = lambda texto: 'charla'
    v._stream_and_speak = lambda texto: None
    v._grabar = lambda **k: None
    v._resumir_conversacion = lambda: (None, None, None)
    v._run_conversation(pending_text='cuéntame una idea')
    check('fin de charla: apaga el modo', not v._modo_ingeniero)

    # 9) Apagado el modo dueño: no intercepta
    md.MODO_DUENO_ENABLED = False
    check('MODO_DUENO_ENABLED=False: no es comando', not md.atender(construir(), 'Bob, modo ingeniero', VOZ))
    md.MODO_DUENO_ENABLED = True

    print(f"\n{'TODO OK' if fallos == 0 else f'{fallos} FALLA(S)'}")
    sys.exit(1 if fallos else 0)


if __name__ == '__main__':
    main()
