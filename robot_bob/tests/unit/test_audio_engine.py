"""
test_audio_engine.py — Micrófono continuo + Silero VAD + fin de frase adaptativo (Fase R2).

Sin hardware: el audio entra con Microfono.alimentar() desde un hilo, a 4x tiempo
real. Usa el Silero VAD real (models/silero_vad_v6.2.onnx) y grabaciones reales de
exp_voz (gitignoreadas; sin ellas el test avisa y sale).

Cubre: el inicio no se corta aunque la voz empiece antes de escuchar (preroll),
una pausa a mitad de frase no la corta si el texto quedó incompleto, una frase
completa termina enseguida con la transcripción adelantada, el ruido no es voz,
la voz de Bob no cuenta, golpes cortos, nivel mínimo, tope de duración, y que
VoicePipeline reutiliza la transcripción adelantada (sin pedirla dos veces).

  python tests/unit/test_audio_engine.py
"""
import os
import sys
import threading
import time
import wave

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from voz import audio_engine as ae   # noqa: E402
from voz import stt                  # noqa: E402

fallos = 0
SR = ae.SR
EXP = os.path.join(os.path.dirname(__file__), '..', '..', 'exp_voz')
VELOCIDAD = 4.0              # alimentación a 4x tiempo real


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    global fallos
    fallos += 0 if ok else 1
    print(f"[{'OK' if ok else 'FALLA'}] {nombre} {detalle}", flush=True)


def leer(nombre, desde_s, hasta_s):
    with wave.open(os.path.join(EXP, nombre)) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), np.int16)
    return x[int(desde_s * SR):int(hasta_s * SR)].copy()


def silencio(s):
    return np.zeros(int(s * SR), np.int16)


def mic_nuevo():
    return ae.Microfono(ae.SileroVAD(), abrir=False, log=lambda m: None)


def alimentar(mic, audio, pausa_entre=True):
    """Alimenta en bloques a VELOCIDAD x tiempo real (en un hilo aparte)."""
    def correr():
        for i in range(0, len(audio), ae.BLOQUE):
            mic.alimentar(audio[i:i + ae.BLOQUE])
            if pausa_entre:
                time.sleep(ae.MS_BLOQUE / 1000 / VELOCIDAD)
    t = threading.Thread(target=correr, daemon=True)
    t.start()
    return t


def escuchar(mic, audio, previo=None, **kw):
    """`previo` se alimenta ANTES de empezar a escuchar (voz que ya había empezado)."""
    if previo is not None:
        mic.alimentar(previo)
    t = alimentar(mic, audio)
    kw.setdefault('timeout_inicial', 3.0)
    frase = mic.escuchar(**kw)
    t.join(timeout=30)
    return frase


def main() -> None:
    if not (os.path.exists(ae.MODELO_DEFAULT) and os.path.isdir(EXP)):
        print('[AVISO] faltan models/silero_vad_v6.2.onnx o exp_voz/: no se puede probar')
        sys.exit(1)

    A = leer('abdair_06.wav', 0.85, 3.25)      # "Bob, pon una canción para bailar un rato." (2.4 s)
    B = leer('abdair_13.wav', 0.90, 3.30)      # "Quiero que reconozcas mi voz entre mucha gente."
    dur = lambda f: len(f.audio) / SR          # noqa: E731

    # 1) Preroll: la voz empezó 0.2 s antes de escuchar y no se corta
    mic = mic_nuevo()
    corte = int(0.35 * SR)                     # 0.35 s de A (≈0.2 s de voz) antes de escuchar
    f = escuchar(mic, np.concatenate([A[corte:], silencio(1.5)]), previo=np.concatenate([silencio(0.5), A[:corte]]),
                 silencio_fin_ms=500)
    check('inicio no cortado: la frase incluye lo dicho antes de escuchar',
          f is not None and dur(f) >= len(A) / SR - 0.15, f'-> {dur(f) if f else None:.2f} s vs {len(A) / SR:.2f} s')

    # 2) Sin transcripción adelantada: termina con silencio_fin (500 ms)
    mic = mic_nuevo()
    f = escuchar(mic, np.concatenate([silencio(0.4), A, silencio(2.0)]), silencio_fin_ms=500)
    sobra = dur(f) - len(A) / SR if f else None
    check('fin por silencio: ~0.5 s después de la voz', f is not None and 0.2 < sobra < 1.2,
          f'-> sobra {sobra:.2f} s')
    check('segundos de voz razonables', f is not None and 1.5 < f.segundos_voz < 3.0,
          f'-> {f.segundos_voz if f else None}')

    # 3) Frase completa: corta en la pausa corta con el texto ya listo
    mic = mic_nuevo()
    pedidos = []

    def stt_completo(wav):
        pedidos.append(len(wav))
        time.sleep(0.05)
        return ('Bob, pon una canción para bailar un rato.', False, False, '')
    t = alimentar(mic, np.concatenate([silencio(0.4), A, silencio(3.0)]))
    f = mic.escuchar(timeout_inicial=3.0, al_pausa=stt_completo)
    audio_al_volver = mic._n * ae.MS_BLOQUE / 1000     # en tiempo de audio, no de reloj
    t.join(timeout=30)
    check('completa: trae la transcripción adelantada', f is not None and f.adelantado is not None
          and f.adelantado.result()[0].startswith('Bob, pon'))
    check('completa: un solo pedido de STT', len(pedidos) == 1, f'-> {len(pedidos)}')
    check('completa: el audio termina en la pausa corta (~0.45 s)', f is not None
          and dur(f) - len(A) / SR < 0.8, f'-> sobra {dur(f) - len(A) / SR:.2f} s')
    check('completa: el wav es el mismo que se transcribió', f is not None and len(f.wav()) == pedidos[0])
    check('completa: terminó antes de la pausa larga', audio_al_volver < 0.4 + len(A) / SR + 1.2,
          f'-> volvió con {audio_al_volver:.2f} s de audio (voz hasta {0.4 + len(A) / SR:.2f} s)')

    # 4) Pausa a mitad de frase + texto incompleto ("…de"): no corta, sigue con B
    mic = mic_nuevo()
    pedidos = []

    def stt_incompleto(wav):
        pedidos.append(len(wav))
        time.sleep(0.05)
        return ('Bob, pon una canción de' if len(pedidos) == 1 else 'todo junto', False, False, '')
    f = escuchar(mic, np.concatenate([silencio(0.4), A, silencio(0.7), B, silencio(2.5)]),
                 al_pausa=stt_incompleto)
    largo_ab = (len(A) + len(B)) / SR + 0.7
    check('incompleta: la pausa de 0.7 s no corta la frase', f is not None and dur(f) >= largo_ab - 0.2,
          f'-> {dur(f) if f else None:.2f} s vs {largo_ab:.2f} s')
    check('incompleta: la transcripción final es la de todo', f is not None and f.adelantado is not None
          and f.adelantado.result()[0] == 'todo junto')

    # 5) Pausa + texto completo pero vuelve a hablar antes de que vuelva el STT → sigue
    mic = mic_nuevo()

    def stt_lento(wav):
        time.sleep(0.6)       # vuelve después de que B ya empezó
        return ('Bob, pon una canción.', False, False, '')
    f = escuchar(mic, np.concatenate([silencio(0.4), A, silencio(0.5), B, silencio(2.5)]), al_pausa=stt_lento)
    check('siguió hablando: lo adelantado se descarta y la frase sigue',
          f is not None and dur(f) >= (len(A) + len(B)) / SR, f'-> {dur(f) if f else None:.2f} s')

    # 6) Ruido (blanco −35 dB, zumbido 50 Hz): no es voz → None al vencer el plazo
    rng = np.random.default_rng(0)
    for nombre, ruido in (('blanco −35 dB', rng.normal(0, 600, SR * 3)),
                          ('zumbido 50 Hz', 1000 * np.sin(2 * np.pi * 50 * np.arange(SR * 3) / SR))):
        mic = mic_nuevo()
        f = escuchar(mic, np.clip(ruido, -32768, 32767).astype(np.int16), timeout_inicial=2.0)
        check(f'ruido no es voz: {nombre}', f is None)

    # 7) La voz de Bob (y su eco) no cuenta como del usuario
    mic = mic_nuevo()
    mic.bob_habla(True)
    mic.alimentar(A)
    mic.bob_habla(False)
    f = escuchar(mic, silencio(2.5), timeout_inicial=2.0)
    check('voz de Bob ignorada', f is None)
    mic = mic_nuevo()
    mic.bob_habla(True)
    mic.alimentar(A[:SR])
    mic.bob_habla(False)
    f = escuchar(mic, np.concatenate([silencio(0.5), B, silencio(1.5)]))
    check('después de Bob, el usuario sí (sin la cola de Bob)', f is not None
          and dur(f) < len(B) / SR + 1.4, f'-> {dur(f) if f else None:.2f} s')

    # 8) Golpe corto (< 150 ms) antes de la frase: no es la frase
    mic = mic_nuevo()
    golpe = (rng.normal(0, 12000, int(0.06 * SR)) * np.exp(-np.arange(int(0.06 * SR)) / 200)).astype(np.int16)
    f = escuchar(mic, np.concatenate([silencio(0.3), golpe, silencio(0.8), A, silencio(1.5)]))
    check('golpe corto ignorado, la frase llega', f is not None and f.segundos_voz > 1.5)

    # 9) Nivel mínimo: voz muy baja (lejana) no arranca en reposo
    mic = mic_nuevo()
    lejos = (A.astype(np.float32) * 0.03).astype(np.int16)
    f = escuchar(mic, np.concatenate([silencio(0.3), lejos, silencio(1.0)]), timeout_inicial=3.0, nivel_min=0.35)
    check('voz lejana no arranca con nivel_min', f is None)

    # 10) Tope de duración
    mic = mic_nuevo()
    f = escuchar(mic, np.concatenate([silencio(0.3), A, A, A, silencio(1.0)]), max_s=3.0)
    check('tope de duración', f is not None and dur(f) <= 3.1, f'-> {dur(f) if f else None:.2f} s')

    # 11) Plazo inicial sin nadie hablando → None; cancelar → None
    mic = mic_nuevo()
    check('plazo inicial vencido', escuchar(mic, silencio(2.0), timeout_inicial=1.0) is None)
    mic = mic_nuevo()
    ev = threading.Event()
    threading.Timer(0.2, ev.set).start()
    check('cancelar', mic.escuchar(timeout_inicial=5.0, cancelar=ev) is None)

    # 11b) Tecla S: silenciado no oye nada y el plazo no corre; al activarlo, oye lo siguiente
    mic = mic_nuevo()
    mic.silenciado = True

    def correr_silenciado():
        for audio, mudo in ((np.concatenate([silencio(0.3), A, silencio(1.5)]), True),
                            (np.concatenate([silencio(0.3), B, silencio(1.5)]), False)):
            mic.silenciado = mudo
            for i in range(0, len(audio), ae.BLOQUE):
                mic.alimentar(audio[i:i + ae.BLOQUE])
                time.sleep(ae.BLOQUE / SR / 4)
    t = threading.Thread(target=correr_silenciado, daemon=True)
    t.start()
    f = mic.escuchar(timeout_inicial=1.0)
    t.join()
    mudos = [b for b in mic._bloques if b.mudo]
    check('silenciado: entra silencio', mudos and all(not b.audio.any() for b in mudos))
    check('silenciado: el plazo no vence y oye lo dicho después de activarlo',
          f is not None and abs(dur(f) - len(B) / SR) < 0.8, f'-> {dur(f) if f else None}')

    # 12) Reglas sueltas: frase completa y cupo de STT
    check('completa: "¿cómo estás?"', ae.frase_completa('¿Cómo estás?'))
    check('incompleta: "pon música de"', not ae.frase_completa('pon música de'))
    check('incompleta: "quiero que…"', not ae.frase_completa('quiero que...'))
    check('incompleta: termina en coma', not ae.frase_completa('mira, ahora,'))
    check('vacío cuenta como completo (ruido)', ae.frase_completa(''))
    reloj = [0.0]
    cupo = stt.CupoSTT(por_minuto=20, reloj=lambda: reloj[0])
    for _ in range(12):
        cupo.registrar()
    check('cupo: 12 usados, libre con reserva 4', cupo.usados() == 12 and cupo.libre(4))
    check('cupo: sin lugar con reserva 8', not cupo.libre(8))
    reloj[0] = 61.0
    check('cupo: se libera al pasar el minuto', cupo.usados() == 0)

    # 13) VoicePipeline: usa la transcripción adelantada (sin pedirla otra vez)
    from voz import pipeline as vp

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

    v = vp.VoicePipeline(FakeMgr(), FakeSM())
    v._mic = mic = mic_nuevo()
    llamadas = []

    def bruto(wav, contexto=True):
        llamadas.append(len(wav))
        time.sleep(0.05)
        return 'Bob, pon una canción para bailar un rato.', False, False, ''
    v._stt_bruto = bruto
    t = alimentar(mic, np.concatenate([silencio(0.4), A, silencio(2.5)]))
    wav = v._grabar(initial_timeout=3.0, adelantar_stt=True)
    t.join(timeout=30)
    texto = v._transcribir(wav)
    check('pipeline: usa el texto adelantado', texto.startswith('Bob, pon') and len(llamadas) == 1,
          f'-> {texto!r}, {len(llamadas)} pedidos')
    t = alimentar(mic, np.concatenate([silencio(0.4), B, silencio(2.0)]))
    wav = v._grabar(initial_timeout=3.0, invitar=False, reposo=True)
    t.join(timeout=30)
    v._transcribir(wav, contexto=False)
    check('pipeline: en reposo no adelanta (un pedido al final)', len(llamadas) == 2)
    v._cupo_stt = stt.CupoSTT(por_minuto=10)
    for _ in range(5):
        v._cupo_stt.registrar()
    t = alimentar(mic, np.concatenate([silencio(0.4), A, silencio(2.0)]))
    wav = v._grabar(initial_timeout=3.0, adelantar_stt=True)
    t.join(timeout=30)
    check('pipeline: con poco cupo no adelanta', v._stt_listo is None)
    v._stt_listo = None

    print(f"\n{'TODO OK' if fallos == 0 else f'{fallos} FALLA(S)'}")
    sys.exit(1 if fallos else 0)


if __name__ == '__main__':
    main()
