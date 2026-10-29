"""
test_sin_red.py — Bob sin internet (O2 del plan del modo sin internet), sin red ni micrófono.

Sin internet Bob no escucha: solo teclas. Simula la grabación, Whisper y Groq:
  1. Sin red, el wake monitor no graba ni transcribe.
  2. Con red de vuelta, el monitor escucha y "Bob" arranca la charla.
  3. Un fallo de conexión de Groq marca la caída; otro error de Groq no.
  4. Sin red, la charla (p. ej. tras una tecla) termina sin escuchar.
  5. La barra del CLI recibe `red`.

  python tests/unit/test_sin_red.py
"""
import os
import sys
import threading

import httpx

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from groq import APIConnectionError   # noqa: E402
from nucleo import estado_cli   # noqa: E402
from voz import pipeline as vp   # noqa: E402

vp.JEV_EN_PARALELO = False
vp.MAX_RETRIES = 0          # un intento: sin las pausas entre reintentos

fallos = 0


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
        self.musica_sonando = threading.Event()
        self.show_activo = threading.Event()
        self.notificados = []

    def notificar_wake_word(self, payload=None, saludar=True, buscar=True):
        self.notificados.append(payload)

    def __getattr__(self, n):
        return lambda *a, **k: None


class FakeMgr:
    def __getattr__(self, n):
        return lambda *a, **k: None


def construir():
    return vp.VoicePipeline(FakeMgr(), FakeSM())


def contar_escucha(v, textos):
    """Reemplaza grabar y transcribir. Devuelve la lista de grabaciones hechas."""
    grabadas = []

    def grabar(**k):
        if not textos:
            v._detener.set()
            return None
        grabadas.append(textos[0])
        return b'\x00' * 3200

    v._grabar = v._grabar_wake = grabar
    v._transcribir = lambda audio, **k: textos.pop(0)
    return grabadas


# ── 1. Sin red, el monitor no graba ─────────────────────────────────────────────
v = construir()
v._red.marcar_caida()
grabadas = contar_escucha(v, ['Bob hola'])
hilo = threading.Thread(target=v._wake_monitor_loop, daemon=True)
hilo.start()
hilo.join(1.2)
v._detener.set()
hilo.join(2.0)
check('sin red: el monitor no graba', grabadas == [], f'-> {grabadas}')
check('sin red: no arranca la charla', v._sm.notificados == [])

# ── 2. Con red de vuelta, escucha ───────────────────────────────────────────────
v = construir()
v._red.marcar_caida()
v._red._sondear = lambda: True
v._red.revisar()
grabadas = contar_escucha(v, ['Bob hola'])
v._wake_monitor_loop()
check('vuelve la red: el monitor graba', grabadas == ['Bob hola'], f'-> {grabadas}')
check('vuelve la red: "Bob" arranca la charla', len(v._sm.notificados) == 1,
      f'-> {v._sm.notificados}')


# ── 3. Fallos de Groq ───────────────────────────────────────────────────────────
class FakeTrans:
    def __init__(self, error):
        self.error = error

    def create(self, **k):
        raise self.error


class FakeClient:
    def __init__(self, error):
        self.audio = type('A', (), {'transcriptions': FakeTrans(error)})()


v = construir()
v._client = FakeClient(APIConnectionError(request=httpx.Request('POST', 'https://api.groq.com')))
r = v._stt_bruto(b'\x00' * 3200)
check('sin conexión con Groq: fallo y sin red', r[1] is True and not v.en_linea, f'-> {r}')

v = construir()
v._client = FakeClient(RuntimeError('cupo agotado'))
r = v._stt_bruto(b'\x00' * 3200)
check('otro error de Groq: fallo pero sigue con red', r[1] is True and v.en_linea, f'-> {r}')

# ── 4. Sin red, la charla no escucha ────────────────────────────────────────────
v = construir()
v._red.marcar_caida()
grabadas = contar_escucha(v, ['hola'])
v._identificar = v._reset_identidad = lambda *a, **k: None
v._run_conversation()
check('sin red: la charla termina sin grabar', grabadas == [], f'-> {grabadas}')

# O3: tecla 5 sin red → Bob dice la frase y no se queda escuchando
v = construir()
v._red.marcar_caida()
grabadas = contar_escucha(v, ['hola'])
v._identificar = v._reset_identidad = lambda *a, **k: None
actuadas = []
run_modo_original = vp.run_modo
vp.run_modo = lambda vpl, nombre: actuadas.append(nombre) or True
try:
    v.modo_manual('5')
    v._run_conversation()
finally:
    vp.run_modo = run_modo_original
check('sin red: tecla 5 dice el saludo y no escucha', actuadas == ['saludo'] and grabadas == [],
      f'-> {actuadas} {grabadas}')

# ── 5. Barra del CLI ────────────────────────────────────────────────────────────
class SMBarra:
    estado = type('Estado', (), {'name': 'IDLE'})()
    musica_sonando = threading.Event()
    bailando = threading.Event()


check('estado del CLI: red False', estado_cli.foto(SMBarra(), v, object()).get('red') is False)
v._red._sondear = lambda: True
v._red.revisar()
check('estado del CLI: red True', estado_cli.foto(SMBarra(), v, object()).get('red') is True)

print(f"\n{'TODO OK' if fallos == 0 else f'{fallos} FALLA(S)'}")
sys.exit(1 if fallos else 0)
