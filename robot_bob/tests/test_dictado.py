"""
test_dictado.py — Dictado largo del modo ingeniero (Fase R3), sin hardware.

Con Silero VAD real y grabaciones reales de exp_voz (gitignoreadas) se arma un
dictado de ~25 s con pausas de 0.7 s. El STT es falso: registra cada tramo, su
duración y el texto previo que recibe de contexto.

Cubre: las pausas naturales no terminan el dictado, los tramos se transcriben
mientras se habla (en orden y con el anterior de contexto), al terminar solo falta
el último, "listo" termina al instante y no queda en el texto, el silencio largo lo
termina, el tope de duración, sin cupo no se cortan tramos, "borra eso" lo
descarta, y la integración en VoicePipeline (texto unido sin pedirlo otra vez,
vocabulario técnico, nota de voz para Claude).

  python tests/test_dictado.py
"""
import os
import sys
import threading
import time
import wave

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import audio_engine as ae   # noqa: E402
import stt                  # noqa: E402

fallos = 0
SR = ae.SR
EXP = os.path.join(os.path.dirname(__file__), '..', 'exp_voz')


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    global fallos
    fallos += 0 if ok else 1
    print(f"[{'OK' if ok else 'FALLA'}] {nombre} {detalle}", flush=True)


_VAD = None


def voz_de(nombre):
    """La parte con voz de una grabación (±0.1 s), según Silero."""
    global _VAD
    _VAD = _VAD or ae.SileroVAD()
    with wave.open(os.path.join(EXP, nombre)) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), np.int16)
    _VAD.reset()
    p = [_VAD.prob(x[i:i + 512].astype(np.float32) / 32768) for i in range(0, len(x) - 511, 512)]
    voz = [i for i, v in enumerate(p) if v > 0.5]
    a, b = max(0, voz[0] * 512 - 1600), min(len(x), (voz[-1] + 1) * 512 + 1600)
    return x[a:b].copy()


def silencio(s):
    return np.zeros(int(s * SR), np.int16)


def mic_nuevo():
    return ae.Microfono(ae.SileroVAD(), abrir=False, log=lambda m: None)


def alimentar(mic, audio):
    def correr():
        for i in range(0, len(audio), ae.BLOQUE):
            mic.alimentar(audio[i:i + ae.BLOQUE])
            time.sleep(0.008)
    t = threading.Thread(target=correr, daemon=True)
    t.start()
    return t


class STTFalso:
    """Devuelve T1, T2… por tramo; 'Listo.' si el audio (voz + pausa) dura menos de 1.5 s."""

    def __init__(self, demora=0.05):
        self.pedidos = []      # (instante, segundos, previo)
        self.demora = demora

    def __call__(self, wav, previo):
        seg = (len(wav) - 44) / 2 / SR
        self.pedidos.append((time.monotonic(), seg, previo))
        time.sleep(self.demora)
        if seg < 1.5:
            return 'Listo.'
        return f'T{sum(1 for p in self.pedidos if p[1] >= 1.5)}'


def main() -> None:
    if not (os.path.exists(ae.MODELO_DEFAULT) and os.path.isdir(EXP)):
        print('[AVISO] faltan models/silero_vad_v6.2.onnx o exp_voz/: no se puede probar')
        sys.exit(1)
    frases = [voz_de(f'abdair_{i:02d}.wav') for i in (3, 6, 7, 10, 11, 13, 16, 17, 18, 19)]
    listo = voz_de('abdair_01.wav')[:int(0.6 * SR)]          # voz corta (~0.6 s)
    p07 = silencio(0.7)

    # 1) Dictado de ~25 s con pausas de 0.7 s: no se corta; tramos en el camino
    cuerpo = np.concatenate([np.concatenate([f, p07]) for f in frases])
    dur_cuerpo = len(cuerpo) / SR
    mic, s = mic_nuevo(), STTFalso()
    t = alimentar(mic, np.concatenate([silencio(0.4), cuerpo, silencio(2.5)]))
    frase = mic.dictar(s, timeout_inicial=3.0)
    t_fin = time.monotonic()
    texto = frase.adelantado.result(timeout=10) if frase else None
    t.join(timeout=60)
    check('dictado: las pausas de 0.7 s no lo cortan', frase is not None
          and len(frase.audio) / SR >= dur_cuerpo - 0.8, f'-> {len(frase.audio) / SR:.1f} s de {dur_cuerpo:.1f} s')
    n = len(s.pedidos)
    check('dictado: varios tramos', n >= 3, f'-> {n} tramos: {[round(p[1], 1) for p in s.pedidos]}')
    check('dictado: tramos de ≥ 8 s salvo el último', all(p[1] >= 8.0 for p in s.pedidos[:-1]))
    check('dictado: texto unido en orden', texto == ' '.join(f'T{i}' for i in range(1, n + 1)), f'-> {texto!r}')
    check('dictado: cada tramo con el anterior de contexto',
          s.pedidos[0][2] == '' and all(s.pedidos[i][2].endswith(f'T{i}') for i in range(1, n)),
          f'-> {[p[2] for p in s.pedidos]}')
    antes = sum(1 for p in s.pedidos if p[0] < t_fin)
    check('dictado: al terminar ya estaban casi todos', antes >= n - 1, f'-> {antes}/{n} antes del fin')

    # 2) "listo" (voz corta tras una pausa) termina al instante y no queda en el texto
    mic, s = mic_nuevo(), STTFalso()
    t = alimentar(mic, np.concatenate([silencio(0.4)] + [np.concatenate([f, p07]) for f in frases[:4]]
                                      + [listo, silencio(3.0)]))
    frase = mic.dictar(s, timeout_inicial=3.0)
    audio_al_volver = mic._n * ae.MS_BLOQUE / 1000
    texto = frase.adelantado.result(timeout=10) if frase else None
    t.join(timeout=60)
    fin_listo = 0.4 + sum(len(f) for f in frases[:4]) / SR + 0.7 * 4 + len(listo) / SR
    check('listo: termina antes del silencio largo', audio_al_volver < fin_listo + 1.4,
          f'-> volvió en {audio_al_volver:.1f} s (listo termina en {fin_listo:.1f} s)')
    check('listo: no queda en el texto', texto and 'Listo' not in texto and texto.startswith('T1'),
          f'-> {texto!r}')

    # 3) Silencio largo (sin "listo") termina el dictado
    mic, s = mic_nuevo(), STTFalso()
    t = alimentar(mic, np.concatenate([silencio(0.4), frases[0], p07, frases[1], silencio(3.0)]))
    frase = mic.dictar(s, timeout_inicial=3.0, pausa_fin_ms=1600)
    audio_al_volver = mic._n * ae.MS_BLOQUE / 1000
    texto = frase.adelantado.result(timeout=10) if frase else None
    t.join(timeout=60)
    fin_voz = 0.4 + (len(frases[0]) + len(frases[1])) / SR + 0.7
    check('silencio de 1.6 s lo termina', frase is not None and fin_voz + 1.3 < audio_al_volver < fin_voz + 2.6,
          f'-> {audio_al_volver:.1f} s (voz hasta {fin_voz:.1f} s)')
    check('dictado corto: un solo pedido, con todo', len(s.pedidos) == 1 and texto == 'T1',
          f'-> {len(s.pedidos)} pedidos, {texto!r}')

    # 4) Tope de duración
    mic, s = mic_nuevo(), STTFalso()
    t = alimentar(mic, np.concatenate([silencio(0.3), cuerpo]))
    frase = mic.dictar(s, timeout_inicial=3.0, max_s=10.0)
    t.join(timeout=60)
    check('tope de duración', frase is not None and len(frase.audio) / SR <= 10.1)

    # 5) Sin cupo: no corta tramos en el camino, uno solo al final
    mic, s = mic_nuevo(), STTFalso()
    t = alimentar(mic, np.concatenate([silencio(0.4), cuerpo, silencio(2.5)]))
    frase = mic.dictar(s, timeout_inicial=3.0, puede_tramo=lambda: False)
    texto = frase.adelantado.result(timeout=10) if frase else None
    t.join(timeout=60)
    largos = [p for p in s.pedidos if p[1] >= 1.5]
    check('sin cupo: un solo tramo con todo', len(largos) == 1 and largos[0][1] >= dur_cuerpo - 1.0,
          f'-> {[round(p[1], 1) for p in s.pedidos]}')

    # 6) Reglas de texto
    check('fin: "Listo."', stt.es_fin_dictado('Listo.') and stt.es_fin_dictado('Eso es todo, Bob.'))
    check('fin: "listo" dentro de una frase no', not stt.es_fin_dictado('el código ya está listo para probar'))
    check('cancelar: "…no, borra eso"', stt.es_cancelar_dictado('quiero un servo de 12 voltios, no, borra eso'))
    check('cancelar: "olvídalo"', stt.es_cancelar_dictado('Olvídalo.'))
    check('cancelar: frase normal no', not stt.es_cancelar_dictado('cancela la suscripción de prueba y luego'))
    check('unir: sin vacíos ni palabra de fin', stt.unir_dictado(['Hola.', '', 'Mundo.', 'Listo.']) == 'Hola. Mundo.')

    # 7) VoicePipeline: dictado en modo ingeniero, texto unido sin pedirlo otra vez
    import voice_pipeline as vp
    import modo_dueno as md

    class FakeSM:
        mood = 0.0
        stt_fail_streak = 0
        positive_streak = 0

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

    v = vp.VoicePipeline(FakeMgr(), FakeSM())
    v._mic = mic = mic_nuevo()
    v._modo_ingeniero = True
    llamadas = []

    def bruto(wav, contexto=True, previo=None):
        llamadas.append(previo)
        time.sleep(0.05)
        return f'tramo{len(llamadas)}', False, False, ''
    v._stt_bruto = bruto
    t = alimentar(mic, np.concatenate([silencio(0.4), cuerpo, silencio(2.5)]))
    wav = v._grabar(initial_timeout=3.0, adelantar_stt=True, dictado=True)
    t.join(timeout=60)
    n_antes = len(llamadas)
    texto = v._transcribir(wav)
    check('pipeline: texto unido de los tramos', texto == ' '.join(f'tramo{i}' for i in range(1, n_antes + 1))
          and len(llamadas) == n_antes, f'-> {texto!r}')
    check('pipeline: tramos con el anterior de contexto', llamadas[0] is None and llamadas[1] == 'tramo1')

    v2 = vp.VoicePipeline(FakeMgr(), FakeSM())
    v2._modo_ingeniero = True
    p = v2._prompt_stt(True, 'el servo se calienta')
    check('pipeline: vocabulario técnico en modo ingeniero', 'MPU6050' in p and p.endswith('el servo se calienta'))
    v2._modo_ingeniero = False
    check('pipeline: sin vocabulario técnico fuera', 'MPU6050' not in v2._prompt_stt(True))

    # 8) "borra eso" en modo ingeniero: no llega al LLM
    vp.JEV_EN_PARALELO = False
    v3 = vp.VoicePipeline(FakeMgr(), FakeSM())
    v3._modo_ingeniero = True
    v3.dicho, v3.al_llm = [], []
    v3._hablar = lambda texto, emo=None: v3.dicho.append(texto)
    v3._stream_and_speak = lambda texto: v3.al_llm.append(texto)
    turnos = ['quiero un servo de doce voltios para la cabeza, no, borra eso']
    v3._grabar = lambda **k: b'wav' if turnos else None
    v3._transcribir = lambda audio, **k: turnos.pop(0) if turnos else ''
    v3._run_conversation(pending_audio=b'wav')
    check('borra eso: Bob lo confirma y no va al LLM', 'Listo, lo borré. Te escucho.' in v3.dicho
          and not v3.al_llm, f'-> {v3.dicho} / {v3.al_llm}')

    # 9) Claude sabe que el texto viene de voz
    v4 = vp.VoicePipeline(FakeMgr(), FakeSM())
    check('nota de voz en el prompt del ingeniero', md.NOTA_VOZ in md._prompt_ingeniero(v4))

    print(f"\n{'TODO OK' if fallos == 0 else f'{fallos} FALLA(S)'}")
    sys.exit(1 if fallos else 0)


if __name__ == '__main__':
    main()
