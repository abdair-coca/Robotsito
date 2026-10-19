"""
test_llm_router.py — Rotación de LLM por cupo (Fase R1, llm_router.py), sin red.

Clientes falsos que imitan a Groq/OpenAI: cabeceras x-ratelimit-*, error 429 con
retry-after, fallas de red, stream que falla antes del primer chunk, prompt corto
para el backend local y precalentado del local cuando la nube se agota.

  python tests/unit/test_llm_router.py
"""
import os
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from llm import llm_router as lr   # noqa: E402

fallos = 0


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    global fallos
    fallos += 0 if ok else 1
    print(f"[{'OK' if ok else 'FALLA'}] {nombre} {detalle}", flush=True)


class Error429(Exception):
    status_code = 429

    def __init__(self, retry='30'):
        super().__init__('rate limit')
        self.response = SimpleNamespace(headers={'retry-after': retry})


class Error400(Exception):
    status_code = 400


class FalloRed(Exception):
    pass


def chunk(texto):
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=texto))])


class FakeCliente:
    """Responde según `plan`: lista de 'ok' | Exception | ('headers', dict) | 'stream_falla'."""

    def __init__(self, nombre, plan=None, headers=None, crudo=True):
        self.nombre = nombre
        self.plan = list(plan or [])
        self.headers = headers or {}
        self.pedidos = []
        comp = SimpleNamespace(create=self._create)
        if crudo:
            comp.with_raw_response = SimpleNamespace(create=self._create_raw)
        self.chat = SimpleNamespace(completions=comp)

    def _respuesta(self, kw):
        self.pedidos.append(kw)
        paso = self.plan.pop(0) if self.plan else 'ok'
        if isinstance(paso, Exception):
            raise paso
        if kw.get('stream'):
            if paso == 'stream_falla':
                def gen():
                    raise FalloRed('cortó antes del primer chunk')
                    yield  # noqa
                return gen()
            return iter([chunk(f'{self.nombre}: hola. '), chunk('Segunda.')])
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=f'{self.nombre}'))])

    def _create(self, **kw):
        return self._respuesta(kw)

    def _create_raw(self, **kw):
        resp = self._respuesta(kw)
        return SimpleNamespace(parse=lambda: resp, headers=dict(self.headers))


def texto_stream(s):
    return ''.join(c.choices[0].delta.content for c in s)


def router(*clientes, local=None, logs=None):
    bs = [lr.Backend(c.nombre, c, f'groq/{c.nombre}', {'reasoning_effort': 'low'}) for c in clientes]
    if local:
        bs.append(lr.Backend('ollama', local, 'llama3.2:3b', local=True))
    return lr.RouterLLM(bs, {'PROMPT LARGO': 'PROMPT CORTO'},
                        log=(logs.append if logs is not None else (lambda m: None)))


MSGS = [{'role': 'system', 'content': 'PROMPT LARGO + memoria'}, {'role': 'user', 'content': 'hola'}]


def main() -> None:
    # 1) Duraciones de las cabeceras de Groq
    check('segundos: "1m30.5s"', lr.segundos('1m30.5s') == 90.5)
    check('segundos: "7.66s" / "250ms" / "2"', lr.segundos('7.66s') == 7.66
          and lr.segundos('250ms') == 0.25 and lr.segundos('2') == 2.0)
    check('segundos: basura → None', lr.segundos('mañana') is None and lr.segundos(None) is None)

    # 2) Camino feliz: el primero responde; stream y no stream; extras del backend
    a, b = FakeCliente('a'), FakeCliente('b')
    r = router(a, b)
    s = r.chat.completions.create(model='ignorado', messages=MSGS, stream=True, max_tokens=50,
                                  reasoning_effort='high')
    check('stream del primero', texto_stream(s) == 'a: hola. Segunda.' and not b.pedidos)
    check('modelo y extras los pone el backend', a.pedidos[0]['model'] == 'groq/a'
          and a.pedidos[0]['reasoning_effort'] == 'low')
    resp = r.chat.completions.create(messages=MSGS, max_tokens=50)
    check('no stream', resp.choices[0].message.content == 'a' and r.ultimo.nombre == 'a')

    # 3) 429 con retry-after → salta al segundo y el primero queda en pausa
    logs = []
    a, b = FakeCliente('a', [Error429('30')]), FakeCliente('b')
    r = router(a, b, logs=logs)
    s = r.chat.completions.create(messages=MSGS, stream=True, max_tokens=50)
    check('429 → responde el segundo', texto_stream(s).startswith('b:'))
    check('429: primero en pausa ~30 s', 25 < r.backends[0].bloqueado_hasta - time.monotonic() <= 31)
    r.chat.completions.create(messages=MSGS, stream=True, max_tokens=50)
    check('en pausa no se le pide', len(a.pedidos) == 1 and len(b.pedidos) == 2)
    check('avisa el cambio en el log', any('sin cupo (429)' in m for m in logs))

    # 4) Cabeceras: pocos tokens este minuto → pausa hasta el reset
    a = FakeCliente('a', headers={'x-ratelimit-remaining-requests': '900',
                                  'x-ratelimit-remaining-tokens': '100',   # < ~168 que pide
                                  'x-ratelimit-reset-tokens': '12.5s'})
    b = FakeCliente('b')
    r = router(a, b)
    texto_stream(r.chat.completions.create(messages=MSGS, stream=True, max_tokens=160))
    check('pocos tokens: igual responde este pedido', len(a.pedidos) == 1)
    check('pocos tokens: pausa hasta el reset', 10 < r.backends[0].bloqueado_hasta - time.monotonic() <= 13)
    texto_stream(r.chat.completions.create(messages=MSGS, stream=True, max_tokens=160))
    check('pocos tokens: el siguiente va al segundo', len(b.pedidos) == 1)

    a = FakeCliente('a', headers={'x-ratelimit-remaining-requests': '0',
                                  'x-ratelimit-reset-requests': '2h'})
    r = router(a, FakeCliente('b'))
    r.chat.completions.create(messages=MSGS, max_tokens=50)
    check('sin pedidos por hoy: pausa ~2 h', r.backends[0].bloqueado_hasta - time.monotonic() > 7000)

    # 5) Falla de red / stream que falla al empezar / 400 → siguiente
    a, b = FakeCliente('a', [FalloRed()]), FakeCliente('b')
    r = router(a, b)
    check('falla de red → siguiente', texto_stream(
        r.chat.completions.create(messages=MSGS, stream=True)).startswith('b:'))
    a, b = FakeCliente('a', ['stream_falla']), FakeCliente('b')
    r = router(a, b)
    check('stream falla antes del 1er chunk → siguiente', texto_stream(
        r.chat.completions.create(messages=MSGS, stream=True)).startswith('b:'))
    a = FakeCliente('a', [Error400()])
    r = router(a, FakeCliente('b'))
    r.chat.completions.create(messages=MSGS)
    check('400: pausa larga (modelo no sirve)', r.backends[0].bloqueado_hasta - time.monotonic() > 3000)

    # 6) Toda la nube caída → local con el prompt corto
    a, b = FakeCliente('a', [Error429()]), FakeCliente('b', [Error429()])
    loc = FakeCliente('ollama', crudo=False)
    r = router(a, b, local=loc)
    s = r.chat.completions.create(messages=MSGS, stream=True, max_tokens=50)
    check('nube sin cupo → local', texto_stream(s).startswith('ollama:'))
    sys_local = next(m for m in loc.pedidos[-1]['messages'] if m['role'] == 'system')['content']
    check('local: prompt corto + el resto intacto', sys_local == 'PROMPT CORTO + memoria',
          f'-> {sys_local!r}')
    check('local: sin extras de Groq', 'reasoning_effort' not in loc.pedidos[-1])
    check('nube: prompt largo intacto', MSGS[0]['content'] == 'PROMPT LARGO + memoria')

    # 7) Todos caídos → excepción (Bob dice la frase de respaldo)
    r = router(FakeCliente('a', [FalloRed()]), local=FakeCliente('ollama', [FalloRed()], crudo=False))
    try:
        r.chat.completions.create(messages=MSGS, stream=True)
        check('todos caídos → excepción', False)
    except RuntimeError as e:
        check('todos caídos → excepción', 'ningún LLM' in str(e), f'-> {e}')

    # 8) Precalentar el local cuando queda una sola nube libre
    a, b = FakeCliente('a', [Error429()]), FakeCliente('b')
    loc = FakeCliente('ollama', crudo=False)
    r = router(a, b, local=loc)
    r.chat.completions.create(messages=MSGS, stream=True)
    t0 = time.monotonic()
    while not loc.pedidos and time.monotonic() - t0 < 2:
        time.sleep(0.02)
    check('queda una nube → precalienta el local',
          loc.pedidos and loc.pedidos[0]['max_tokens'] == 1)
    a2, b2 = FakeCliente('a'), FakeCliente('b')
    loc2 = FakeCliente('ollama', crudo=False)
    r2 = router(a2, b2, local=loc2)
    r2.chat.completions.create(messages=MSGS, stream=True)
    time.sleep(0.2)
    check('con nube de sobra no precalienta', not loc2.pedidos)

    print(f"\n{'TODO OK' if fallos == 0 else f'{fallos} FALLA(S)'}")
    sys.exit(1 if fallos else 0)


if __name__ == '__main__':
    main()
