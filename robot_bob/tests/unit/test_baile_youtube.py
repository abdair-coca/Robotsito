"""
test_baile_youtube.py — Baile de 10 s y plan B de YouTube cuando Spotify falla.

Sin red, sin Spotify, sin navegador ni servos:
  1. BehaviorEngine baila BAILE_MAX_S y suelta el flag; la música sigue.
  2. El monitor de baile no re-arranca el baile y apaga la música cuando para.
  3. Sin Spotify, "pon X" abre X en YouTube; "pausa" pausa YouTube.
  4. El parser de la búsqueda de YouTube saca id, título y duración.

  python tests/unit/test_baile_youtube.py
"""
import os
import sys
import threading
import types

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from asistente import music as M          # noqa: E402
from nucleo import behavior               # noqa: E402
from voz import pipeline as vp            # noqa: E402

fallos = 0


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    global fallos
    fallos += 0 if ok else 1
    print(f"[{'OK' if ok else 'FALLA'}] {nombre} {detalle}", flush=True)


class FakeSM:
    en_conversacion = False

    def __init__(self):
        self.bailando = threading.Event()
        self.musica_sonando = threading.Event()


# ── 1. Baile de BAILE_MAX_S ─────────────────────────────────────────────────────
eng = behavior.BehaviorEngine.__new__(behavior.BehaviorEngine)
eng._sm = FakeSM()
eng._baile_prev = False
eng._baile_t0 = 0.0
pasos = []
eng._tick_baile = lambda ahora: (pasos.append(ahora), setattr(eng, '_baile_prev', True))
eng._sm.bailando.set()
eng._sm.musica_sonando.set()
max_s = behavior.BAILE_MAX_S

check('baile: al empezar baila', eng._bailar(100.0) is True)
check('baile: a mitad sigue', eng._bailar(100.0 + max_s - 0.1) is True)
check('baile: a los BAILE_MAX_S para', eng._bailar(100.0 + max_s) is False
      and not eng._sm.bailando.is_set())
check('baile: la música sigue (Bob callado)', eng._sm.musica_sonando.is_set())
check('baile: dura 10 s', max_s == 10.0, f'(BAILE_MAX_S={max_s})')
# Al terminar el baile, _tick resetea _baile_prev; una canción nueva vuelve a contar.
eng._baile_prev = False
eng._sm.bailando.set()
check('baile: canción nueva arranca el conteo de cero', eng._bailar(500.0) is True
      and eng._baile_t0 == 500.0)


# ── 2. Monitor de baile ─────────────────────────────────────────────────────────
def correr_monitor(sonando: bool, sm: FakeSM, t_inicio: float, vueltas_s: float = 3.5):
    fake = types.SimpleNamespace(_detener=threading.Event(), _sm=sm, _muted=threading.Event(),
                                 _baile_hint=True, _t_baile_inicio=t_inicio)
    original = vp.sonando_musica
    vp.sonando_musica = lambda: sonando
    hilo = threading.Thread(target=vp.VoicePipeline._baile_monitor_loop, args=(fake,))
    hilo.start()
    fake._detener.wait(vueltas_s)
    fake._detener.set()
    hilo.join()
    vp.sonando_musica = original
    return fake


sm = FakeSM()
sm.musica_sonando.set()                  # ya bailó sus 10 s: bailando apagado
mon = correr_monitor(True, sm, t_inicio=0.0)
check('monitor: con música no re-arranca el baile', not sm.bailando.is_set())
check('monitor: con música Bob sigue callado', sm.musica_sonando.is_set() and mon._baile_hint)
mon = correr_monitor(False, sm, t_inicio=0.0)
check('monitor: la música paró → Bob vuelve a hablar',
      not sm.musica_sonando.is_set() and not mon._baile_hint)


# ── 3. Plan B YouTube ───────────────────────────────────────────────────────────
abiertas = []
M.webbrowser.open = lambda url: (abiertas.append(url), True)[1]
M._buscar_youtube = lambda q: ('https://www.youtube.com/watch?v=kJQP7kiw5Fk',
                               'Luis Fonsi - Despacito', 282.0)
_id_bak, _sp_bak = M.SPOTIFY_CLIENT_ID, M._sp
M.SPOTIFY_CLIENT_ID, M._sp = '', None       # Spotify no disponible

msg = M.ejecutar(M.MusicIntent('play', query='despacito'))
check('youtube: sin Spotify pone el tema en YouTube', 'Poniendo' in msg and 'YouTube' in msg
      and abiertas == ['https://www.youtube.com/watch?v=kJQP7kiw5Fk'], repr(msg))
check('youtube: cuenta como música sonando', M.esta_sonando())

teclas = []
user32 = types.SimpleNamespace(keybd_event=lambda *a: teclas.append(a))
import ctypes                               # noqa: E402
_windll_bak = getattr(ctypes, 'windll', None)
ctypes.windll = types.SimpleNamespace(user32=user32)
msg = M.ejecutar(M.MusicIntent('pause'))
if _windll_bak is not None:
    ctypes.windll = _windll_bak
check('youtube: pausa con la tecla multimedia', 'pausé' in msg and len(teclas) == 2
      and teclas[0][0] == 0xB3, repr(msg))
check('youtube: tras pausar ya no suena', not M.esta_sonando())

abiertas.clear()
M.ejecutar(M.MusicIntent('play', query='despacito', desde_s=5))
check('youtube: desde_s arranca en ese segundo',
      abiertas == ['https://www.youtube.com/watch?v=kJQP7kiw5Fk&t=5s'], repr(abiertas))
M._yt_hasta = 0.0

reproducidos = []
_cliente_bak = M._cliente
M._cliente = lambda: types.SimpleNamespace(
    search=lambda **k: {'tracks': {'items': [{'uri': 'spotify:track:x', 'name': 'Mambo No. 5',
                                              'artists': [{'name': 'Lou Bega'}]}]}},
    start_playback=lambda **k: reproducidos.append(k))
msg = M.ejecutar(M.MusicIntent('play', query='mambo no 5', desde_s=5))
M._cliente = _cliente_bak
check('spotify: desde_s va como position_ms', 'Poniendo' in msg
      and reproducidos == [{'uris': ['spotify:track:x'], 'position_ms': 5000}], repr(reproducidos))

M._buscar_youtube = lambda q: None          # búsqueda falló (sin red)
abiertas.clear()
msg = M.ejecutar(M.MusicIntent('play', query='despacito'))
check('youtube: sin video abre la búsqueda', 'búsqueda' in msg
      and abiertas[0].startswith(M._YT_BUSQUEDA), repr(msg))
check('youtube: la búsqueda sola no cuenta como sonando', not M.esta_sonando())

msg = M.ejecutar(M.MusicIntent('vol_up'))
check('youtube: volumen sin Spotify avisa', 'configurada' in msg, repr(msg))
M.SPOTIFY_CLIENT_ID, M._sp = _id_bak, _sp_bak


# ── 4. Parser de la búsqueda de YouTube ─────────────────────────────────────────
import importlib                            # noqa: E402
M = importlib.reload(M)                     # vuelve la _buscar_youtube real
HTML = ('...{"itemSectionRenderer":{"contents":[{"videoRenderer":{"videoId":"Jnq9wPDoDKg",'
        '"thumbnail":{},"title":{"runs":[{"text":"Kiss Me \\u0026 Co"}]},'
        '"lengthText":{"accessibility":{"accessibilityData":{"label":"3 minutos"}},'
        '"simpleText":"3:16"}}}]}}...')


class Resp:
    def read(self):
        return HTML.encode()


M.urllib.request.urlopen = lambda req, timeout=0: Resp()
hallado = M._buscar_youtube('kiss me')
check('parser: id, título y duración', hallado == (
    'https://www.youtube.com/watch?v=Jnq9wPDoDKg', 'Kiss Me & Co', 196.0), repr(hallado))
check('parser: duración con horas', M._segundos('1:02:03') == 3723.0)

print(f"\nRESULTADO: {'TODO OK' if fallos == 0 else f'{fallos} FALLO(S)'}")
sys.exit(1 if fallos else 0)
