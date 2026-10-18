"""
test_charla_errores.py — Regresiones de los errores de la charla del 2026-10-17 (Fase E).

Cada caso reproduce una frase exacta del log real. Sin red, sin micrófono ni parlante.

  python tests/test_charla_errores.py
"""
import io
import os
import sys
import threading
import wave

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import stt                # noqa: E402
import voice_pipeline as vp   # noqa: E402

fallos = 0


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    global fallos
    fallos += 0 if ok else 1
    print(f"[{'OK' if ok else 'FALLA'}] {nombre} {detalle}", flush=True)


def resp(texto, lp, cr=0.85, dur=1.5):
    return {'text': texto, 'segments': [{'text': texto, 'avg_logprob': lp, 'no_speech_prob': 0.0,
                                         'compression_ratio': cr, 'start': 0.0, 'end': dur}]}


def wav_seg(segundos: float) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(np.zeros(int(segundos * 16000), np.int16).tobytes())
    return buf.getvalue()


# Prompt real de Whisper en modo dueño (el que tenía la charla del log)
PROMPT_DUENO = stt.armar_prompt(vp.STT_PROMPT, vp.STT_VOCAB_DUENO, '')


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


def construir():
    vp.JEV_ROUTING_ENABLED = False      # sin JEV real (red)
    v = vp.VoicePipeline(FakeMgr(), FakeSM())
    v.dichas = []
    v._hablar = lambda texto, emo=None: v.dichas.append(texto)
    return v


def e_a() -> None:
    print('\n== E-A: filtros de Whisper ==')
    # 1–2) Órdenes del dueño que el eco del prompt descartaba (conf −0.48)
    r = stt.filtrar(resp('Modo ingeniero.', -0.48), PROMPT_DUENO)
    check('E-A: "Modo ingeniero." no es eco', r.texto == 'Modo ingeniero.', f'-> {r}')
    r = stt.filtrar(resp('Abdair, Proyecto Génesis.', -0.48), PROMPT_DUENO)
    check('E-A: "Abdair, Proyecto Génesis." no es eco', r.texto == 'Abdair, Proyecto Génesis.', f'-> {r}')
    # 3) El eco real del prompt sigue descartándose
    r = stt.filtrar(resp('Conversación en español.', -0.82), PROMPT_DUENO)
    check('E-A: eco real ("Conversación en español.") fuera', r.descartado == 'eco_prompt', f'-> {r}')
    # 4) "Bob, ok, ponlo." (−0.78): era la confirmación y se descartaba como ruido
    r = stt.filtrar(resp('Bob, ok, ponlo.', -0.78), PROMPT_DUENO)
    check('E-A: "Bob, ok, ponlo." pasa', r.texto == 'Bob, ok, ponlo.', f'-> {r}')
    # 5) Sin "Bob", en la charla, la misma confianza también pasa
    r = stt.filtrar(resp('Ok, ponlo.', -0.78), PROMPT_DUENO)
    check('E-A: "Ok, ponlo." pasa (−0.78 > −0.9)', r.texto == 'Ok, ponlo.', f'-> {r}')
    # 6–7) Ruido real sigue descartándose
    r = stt.filtrar(resp('Conectado.', -1.06, 0.58, 1.0), PROMPT_DUENO)
    check('E-A: "Conectado." (ruido real) fuera', bool(r.descartado), f'-> {r.descartado!r}')
    r = stt.filtrar(resp('Los cerve...', -1.03, 0.62, 1.0), PROMPT_DUENO)
    check('E-A: "Los cerve..." (ruido) fuera', r.descartado == 'ruido', f'-> {r.descartado!r}')
    # 8) "Bob" en la frase salva de la alucinación
    r = stt.filtrar(resp('Bob, gracias.', -0.8), PROMPT_DUENO)
    check('E-A: "Bob, gracias." no es alucinación', r.texto == 'Bob, gracias.', f'-> {r}')
    # 9) "Gracias." sin Bob con −0.8 sigue siendo alucinación (silencio de Gisel)
    r = stt.filtrar(resp('Gracias.', -0.80, 0.53, 1.0), PROMPT_DUENO)
    check('E-A: "Gracias." de silencio fuera', r.descartado == 'alucinacion', f'-> {r.descartado!r}')

    print('\n== E-A: "¿Perdón?" al descartar voz real ==')
    audios = [wav_seg(1.5)]
    v = construir()
    v._grabar = lambda **k: audios.pop(0) if audios else None

    def transcribir(audio, **k):
        v._stt_descartado, v._stt_fallo, v._stt_dudosa = 'eco_prompt', False, False
        return ''
    v._transcribir = transcribir
    v._run_conversation()
    check('E-A: voz real descartada → "¿Perdón? No te escuché bien."',
          '¿Perdón? No te escuché bien.' in v.dichas, f'-> {v.dichas}')

    audios = [wav_seg(0.3)]
    v = construir()
    v._grabar = lambda **k: audios.pop(0) if audios else None
    v._transcribir = transcribir
    v._run_conversation()
    check('E-A: ruido corto descartado → sin "¿Perdón?"',
          not any('Perdón' in d for d in v.dichas), f'-> {v.dichas}')


def main() -> None:
    e_a()
    print(f"\n{'TODO OK' if fallos == 0 else f'{fallos} FALLA(S)'}")
    sys.exit(1 if fallos else 0)


if __name__ == '__main__':
    main()
