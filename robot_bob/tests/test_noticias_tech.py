"""
test_noticias_tech.py — Noticias de tecnología del modo dueño (V5b).

Simula el feed y el modelo de la nube: resumen limpio (sin viñetas ni markdown),
respaldo leyendo titulares si Groq falla o devuelve vacío, aviso sin internet,
rechazo con otra voz, y el parámetro url de obtener_noticias (caché por URL y
NOTICIAS_ENABLED solo para el feed general). Al final, si hay red, baja el feed
real de tecnología.

  python tests/test_noticias_tech.py
"""
import io
import os
import sys
import threading
import types

import numpy as np

_BOB = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, _BOB)

import assistant              # noqa: E402
import voice_pipeline as vp   # noqa: E402
import modo_dueno as md       # noqa: E402

fallos = 0
TITULARES = ['Tesla presenta el nuevo Roadster', 'China toma ventaja en robots humanoides',
             'La IA cambia la industria de los chips', 'Un robot recorre supermercados',
             'Nueva versión de Android llega a más equipos']


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

    def __getattr__(self, n):
        return lambda *a, **k: None


class FakeMgr:
    def __getattr__(self, n):
        return lambda *a, **k: None


class FakeMem:
    """Lo mínimo de Memoria para la puerta de voz: el dueño con su voz."""
    def __init__(self, sim):
        self.sim = sim

    def total_voces(self, pid):
        return 10

    def verificar_voz(self, pid, emb):
        return self.sim

    def persona(self, pid):
        return (1, 'Abdair')


def cliente(respuesta=None, error=None):
    """Imita Groq: chat.completions.create devuelve `respuesta` o lanza `error`."""
    llamadas = []

    def create(**k):
        llamadas.append(k)
        if error:
            raise error
        msg = types.SimpleNamespace(content=respuesta)
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])
    c = types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create)))
    return c, llamadas


def construir(sim=0.8):
    v = vp.VoicePipeline(FakeMgr(), FakeSM())
    v._memoria = FakeMem(sim)
    v._convo = []
    v.dichas = []
    v._hablar = lambda texto, emo=None: v.dichas.append(texto)
    return v


VOZ = (np.ones(256, np.float32) / 16.0, 1.8)


def main() -> None:
    md.MODO_DUENO_ENABLED = True
    original_obtener = assistant.obtener_noticias

    # 1) Detección
    for t in ('Bob, noticias de tecnología', '¿qué hay de nuevo en tecnología?',
              'dame el informe del día', 'novedades tecnológicas', 'titulares de tech',
              '¿cuáles son las noticias de la tecnología de hoy?'):
        check(f'detecta: "{t}"', md.detectar(t) == ('noticias_tech', {}), f'-> {md.detectar(t)}')
    for t in ('noticias de hoy', 'me gusta la tecnología', 'qué hay de nuevo'):
        check(f'no detecta: "{t}"', md.detectar(t) is None, f'-> {md.detectar(t)}')

    pedidos = []
    assistant.obtener_noticias = lambda n=None, url=None: (pedidos.append((n, url)), TITULARES[:n])[1]
    try:
        # 2) Resumen de la nube: limpio, con el modelo y los titulares correctos
        v = construir()
        v._client, llamadas = cliente('**¡Hola creador!**\n1. Tesla mostró su Roadster.\n'
                                      '- China avanza en robots.\n\n• Los chips cambian.\n'
                                      'Un robot va al súper.\nAndroid se actualiza.\n¡Eso es todo!')
        md.atender(v, 'Bob, noticias de tecnología', VOZ)
        check('nube: pide 5 titulares del feed de tecnología',
              pedidos[-1] == (5, md.NOTICIAS_TECH_RSS_URL), f'-> {pedidos[-1]}')
        check('nube: usa el modelo de la nube', llamadas and llamadas[0]['model'] == vp.GROQ_LLM_MODEL)
        check('nube: el prompt trae los titulares', llamadas and
              all(t in llamadas[0]['messages'][1]['content'] for t in TITULARES))
        habladas = v.dichas[2:]          # 0: "Voz verificada", 1: "Déjame revisar…"
        check('nube: sin viñetas, números ni markdown',
              habladas and not any(h[:1] in '-*•0123456789' or '**' in h for h in habladas),
              f'-> {habladas}')
        check('nube: dice todas las líneas', len(habladas) == 7, f'({len(habladas)})')
        check('nube: queda en el historial', v._convo and 'Roadster' in v._convo[-1]['content'])

        # 3) Groq falla → lee los titulares
        v = construir()
        v._client, _ = cliente(error=RuntimeError('sin red'))
        md.atender(v, 'noticias de tecnología', VOZ)
        check('respaldo (error): lee los titulares',
              any(d.startswith('Primera: Tesla') for d in v.dichas), f'-> {v.dichas}')
        check('respaldo (error): con cierre', v.dichas[-1] == '¡Eso es todo por hoy!')

        # 4) Groq devuelve vacío → también lee los titulares
        v = construir()
        v._client, _ = cliente('   ')
        md.atender(v, 'noticias de tecnología', VOZ)
        check('respaldo (vacío): lee los titulares', any(d.startswith('Quinta:') for d in v.dichas))

        # 5) Sin internet → lo dice
        assistant.obtener_noticias = lambda n=None, url=None: None
        v = construir()
        v._client, llamadas = cliente('no debería llamarse')
        md.atender(v, 'noticias de tecnología', VOZ)
        check('sin internet: lo dice', any('no hay internet' in d for d in v.dichas))
        check('sin internet: no llama al modelo', not llamadas)

        # 6) Otra voz → rechazo, sin bajar noticias
        pedidos.clear()
        assistant.obtener_noticias = lambda n=None, url=None: (pedidos.append((n, url)), TITULARES)[1]
        v = construir(sim=0.1)
        md.atender(v, 'noticias de tecnología', VOZ)
        check('otra voz: no baja noticias', not pedidos)
    finally:
        assistant.obtener_noticias = original_obtener

    # 7) obtener_noticias(url=…): usa esa URL, caché por URL, NOTICIAS_ENABLED solo el general
    rss = (b'<rss><channel><item><title>Titular A - Fuente</title></item>'
           b'<item><title>Video | Titular B - Otra</title></item></channel></rss>')
    urls = []

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    original_urlopen = assistant.urllib.request.urlopen
    original_enabled = assistant.NOTICIAS_ENABLED
    assistant.urllib.request.urlopen = lambda req, timeout=None: (urls.append(req.full_url), Resp(rss))[1]
    try:
        assistant._noticias_cache.clear()
        assistant.NOTICIAS_ENABLED = False
        check('NOTICIAS_ENABLED=False: el general no baja', assistant.obtener_noticias() is None)
        r = assistant.obtener_noticias(5, url='https://ejemplo/tech')
        check('url: baja ese feed, corta la fuente y el "Video |"', r == ['Titular A', 'Titular B'] and
              urls == ['https://ejemplo/tech'], f'-> {r} {urls}')
        assistant.obtener_noticias(5, url='https://ejemplo/tech')
        check('url: segunda vez sale de la caché', len(urls) == 1)
    finally:
        assistant.urllib.request.urlopen = original_urlopen
        assistant.NOTICIAS_ENABLED = original_enabled
        assistant._noticias_cache.clear()

    # 8) Feed real (si hay red)
    real = assistant.obtener_noticias(5, url=md.NOTICIAS_TECH_RSS_URL)
    if real:
        check('feed real de tecnología', len(real) == 5, f'-> {real[:2]}…')
    else:
        print('[--] sin red: feed real no ejecutado')

    print(f"\n{'TODO OK' if fallos == 0 else f'{fallos} FALLA(S)'}")
    sys.exit(1 if fallos else 0)


if __name__ == '__main__':
    main()
