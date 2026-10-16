"""
test_voice_id.py — Módulo de voz (V1), sin red ni micrófono.

Casos borde (WAV vacío, silencio, modelo ausente, audio a 8 kHz) y, si hay
grabaciones del experimento en robot_bob/exp_voz/, reproduce el EER de
EXP-1-001 pasando por analizar(wav_bytes), el camino que usará Bob.

  python tests/test_voice_id.py
"""
import glob
import io
import os
import sys
import wave
from itertools import combinations

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from voice_id import VoiceID, SR   # noqa: E402

DIR_WAV  = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'exp_voz'))
OBJETIVO = 'abdair'
EER_V0   = 0.0      # EER frase completa contra frase completa en EXP-1-001
fallos = 0


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    global fallos
    fallos += 0 if ok else 1
    print(f"[{'OK' if ok else 'FALLA'}] {nombre} {detalle}", flush=True)


def wav(audio_i16: np.ndarray, sr: int = SR) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(audio_i16.astype(np.int16).tobytes())
    return buf.getvalue()


def eer(mismos, distintos) -> float:
    mejor = (1.0, 1.0)
    for t in np.unique(np.concatenate([mismos, distintos])):
        frr, far = np.mean(mismos < t), np.mean(distintos >= t)
        if abs(frr - far) < mejor[1]:
            mejor = ((frr + far) / 2, abs(frr - far))
    return float(mejor[0])


def main() -> None:
    sin_modelo = VoiceID(modelo='no_existe.onnx', en_fondo=False)
    check('modelo ausente → no listo', not sin_modelo.listo)
    check('modelo ausente → analizar None', sin_modelo.analizar(wav(np.zeros(SR, np.int16)))[0] is None)

    vid = VoiceID(en_fondo=False)
    check('modelo carga', vid.listo)
    check('WAV vacío → None', vid.analizar(b'')[0] is None)
    check('silencio → None', vid.analizar(wav(np.zeros(2 * SR, np.int16)))[0] is None)
    check('basura → None', vid.analizar(b'no es un wav')[0] is None)

    rutas = sorted(glob.glob(os.path.join(DIR_WAV, '*.wav')))
    if not rutas:
        print('[--] sin grabaciones en exp_voz/: se salta la parte de EER (no ejecutado)')
    else:
        embs = {}
        for r in rutas:
            with open(r, 'rb') as f:
                datos = f.read()
            e, seg = vid.analizar(datos)
            if e is not None:
                embs.setdefault(os.path.basename(r).rsplit('_', 1)[0], []).append(e)
        check('embedding normalizado', all(abs(np.linalg.norm(e) - 1) < 1e-3
                                           for v in embs.values() for e in v))

        # El mismo audio a 8 kHz pasa por el remuestreo y da casi el mismo vector.
        with open(rutas[0], 'rb') as f:
            with wave.open(io.BytesIO(f.read()), 'rb') as w:
                a16 = np.frombuffer(w.readframes(w.getnframes()), np.int16)
        e16, _ = vid.analizar(wav(a16))
        e8, _ = vid.analizar(wav(a16[::2], 8000))
        if e16 is not None and e8 is not None:
            check('8 kHz se remuestrea', float(e16 @ e8) > 0.5, f'(sim {float(e16 @ e8):.3f})')

        otros = [h for h in embs if h != OBJETIVO]
        if OBJETIVO in embs and otros:
            ea = embs[OBJETIVO]
            eo = [e for h in otros for e in embs[h]]
            mismos = np.array([a @ b for a, b in combinations(ea, 2)])
            distintos = np.array([a @ b for a in ea for b in eo])
            e = eer(mismos, distintos)
            check('EER reproduce EXP-1-001 (±0.02)', e <= EER_V0 + 0.02,
                  f'(EER {e * 100:.1f} %, {OBJETIVO} {len(ea)} vs {", ".join(otros)})')
        else:
            print(f'[--] falta "{OBJETIVO}" u otra voz en exp_voz/: EER no ejecutado')

    print(f"\n{'TODO OK' if fallos == 0 else f'{fallos} FALLA(S)'}")
    sys.exit(1 if fallos else 0)


if __name__ == '__main__':
    main()
