"""
test_stt_filtros.py — Filtros de alucinación de Whisper (Fase R1, stt.py), sin red.

Casos sacados de la calibración real del 2026-10-17 (Groq whisper-large-v3-turbo
sobre exp_voz + silencio y ruido sintéticos): mismos textos y mismas confianzas.
También el prompt con contexto y la integración en VoicePipeline._transcribir
con un cliente falso.

  python tests/test_stt_filtros.py
"""
import os
import sys
import threading
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import stt   # noqa: E402

fallos = 0


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    global fallos
    fallos += 0 if ok else 1
    print(f"[{'OK' if ok else 'FALLA'}] {nombre} {detalle}", flush=True)


def resp(texto, lp, cr=0.85, dur=3.0):
    return {'text': texto, 'segments': [{'text': texto, 'avg_logprob': lp, 'no_speech_prob': 0.0,
                                         'compression_ratio': cr, 'start': 0.0, 'end': dur}]}


PROMPT = ('Conversación en español con un robot llamado Bob. Oye Bob, hola Bob. '
          'Comandos: pon música, sube el volumen. Potosí, Bolivia, UATF.')

# (texto, avg_logprob, compression_ratio) medidos
VOZ_REAL = [
    ('Hola Bob, soy yo, ¿cómo estás hoy?', -0.40, 0.83),
    ('Mañana tenemos la feria en la universidad.', -0.18, 0.88),
    ('Gracias Bob, eres un buen compañero.', -0.45, 0.83),
    ('Estoy estudiando ingeniería informática.', -0.39, 0.86),
    ('1, 2, 3, 4, 5, 6, 7.', -0.27, 0.72),
    ('¿Te acuerdas de lo que hablamos ayer?', -0.32, 0.83),
]
ALUCINADAS = [
    ('Gracias.', -0.80, 0.53),            # Gisel_03: silencio −96 dB
    ('Gracias.', -0.77, 0.53),            # Gisel_04
    ('Gracias por ver el video.', -0.77, 0.76),   # silencio digital
    ('Conectado.', -1.06, 0.58),          # ruido blanco bajo
    ('Conectado.', -1.01, 0.58),          # ruido blanco medio
    ('¡Gracias!', -0.96, 0.58),           # click y silencio
]


def main() -> None:
    # 1) Voz real pasa intacta; alucinaciones medidas se descartan
    for t, lp, cr in VOZ_REAL:
        r = stt.filtrar(resp(t, lp, cr), PROMPT)
        check(f'voz real pasa: "{t}"', r.texto == t and not r.dudosa and not r.descartado, f'-> {r}')
    for t, lp, cr in ALUCINADAS:
        r = stt.filtrar(resp(t, lp, cr), PROMPT)
        check(f'alucinación fuera: "{t}" ({lp})', r.texto == '' and r.descartado, f'-> {r.descartado}')

    # 2) "gracias" real (confianza alta) no se descarta
    r = stt.filtrar(resp('Gracias.', -0.20, 0.53), PROMPT)
    check('"gracias" real pasa', r.texto == 'Gracias.')
    r = stt.filtrar(resp('sí', -0.25, 0.4, 0.5), PROMPT)
    check('"sí" corto y claro pasa', r.texto == 'sí')

    # 3) Eco del prompt (large-v3 con silencio: "POTOSÍ, BOLIVIA, UATF.")
    r = stt.filtrar(resp('POTOSÍ, BOLIVIA, UATF.', -0.30), PROMPT)
    check('eco del prompt fuera', r.descartado == 'eco_prompt', f'-> {r}')
    r = stt.filtrar(resp('Hola Bob, pon música de Queen', -0.20), PROMPT)
    check('frase con palabras del prompt pero nueva: pasa', r.texto == 'Hola Bob, pon música de Queen')

    # 4) Bucles
    bucle = '1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 11, 11, 12, 13, 17, 17, 17, 17, 17, 17, 18.'
    r = stt.filtrar(resp(bucle, -0.13, 1.6), PROMPT)
    check('bucle: repeticiones colapsadas', r.texto.count('17') == 1 and r.texto.count('11') == 1
          and r.texto.endswith('18.'), f'-> {r.texto}')
    check('bucle de 2 palabras', stt.quitar_bucles('pon la pon la pon la música') == 'pon la música')
    check('repetición doble normal se queda', stt.quitar_bucles('no no quiero') == 'no no quiero')
    r = stt.filtrar({'text': 'ja ja ja ja ja', 'segments': [
        {'text': 'ja ja ja ja ja', 'avg_logprob': -0.2, 'compression_ratio': 3.1, 'start': 0, 'end': 2}]})
    check('segmento con compresión alta fuera', r.descartado == 'bucle')
    r = stt.filtrar({'text': 'Hola Bob. ja ja ja ja ja ja', 'segments': [
        {'text': 'Hola Bob.', 'avg_logprob': -0.3, 'compression_ratio': 0.9, 'start': 0, 'end': 1},
        {'text': 'ja ja ja ja ja ja', 'avg_logprob': -0.2, 'compression_ratio': 3.0, 'start': 1, 'end': 3}]})
    check('solo se va el segmento en bucle', r.texto == 'Hola Bob.', f'-> {r.texto}')

    # 5) Dudosa: frase larga con confianza muy baja → Bob pide repetir
    r = stt.filtrar(resp('Conectado, ¿qué es lo que se ha hecho?', -1.39, 0.84), PROMPT)  # Gisel_01
    check('dudosa: Gisel_01 (−1.39)', r.dudosa and r.texto, f'-> {r}')
    r = stt.filtrar(resp('Los cerve...', -1.03, 0.62), PROMPT)   # Gisel_11 cortada (2 palabras)
    check('corta y muy baja: ruido', r.descartado == 'ruido')

    # 6) Confianza ponderada por duración
    r = stt.filtrar({'text': 'a b c d', 'segments': [
        {'text': 'hola cómo estás hoy', 'avg_logprob': -0.2, 'compression_ratio': 0.9, 'start': 0, 'end': 3},
        {'text': 'mm', 'avg_logprob': -2.0, 'compression_ratio': 0.5, 'start': 3, 'end': 3.2}]})
    check('confianza ponderada por duración', -0.35 < r.confianza < -0.25 and not r.dudosa,
          f'-> {r.confianza}')

    # 7) Sin segmentos (formato json) y vacío
    check('sin segmentos: pasa el texto', stt.filtrar({'text': 'hola Bob'}).texto == 'hola Bob')
    check('vacío: sin descarte', stt.filtrar({'text': ''}).descartado == '')
    check('objeto del SDK (atributos)', stt.filtrar(SimpleNamespace(
        text='hola Bob', segments=[SimpleNamespace(text='hola Bob', avg_logprob=-0.2,
                                                   compression_ratio=0.9, start=0, end=1)])).texto == 'hola Bob')

    # 8) Prompt con contexto: lo último de Bob va al final; respeta el largo
    p = stt.armar_prompt('Base Bob.', 'Modo ingeniero.', '¿Cómo te llamas?')
    check('prompt: base, vocab y contexto al final', p == 'Base Bob. Modo ingeniero. ¿Cómo te llamas?', f'-> {p}')
    p = stt.armar_prompt('x' * 2000, '', 'contexto final')
    check('prompt: tope de largo y contexto conservado', len(p) <= stt.PROMPT_MAX_CHARS
          and p.endswith('contexto final'))
    check('prompt sin contexto', stt.armar_prompt('Base.', '', '') == 'Base.')

    # 9) Integración en VoicePipeline._transcribir (cliente falso)
    import voice_pipeline as vp

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
    pedidos = []

    def cliente(respuesta, headers=None):
        def crear(**kw):
            pedidos.append(kw)
            obj = SimpleNamespace(model_dump=lambda: respuesta)
            return SimpleNamespace(parse=lambda: obj, headers=headers or {})
        return SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(
            with_raw_response=SimpleNamespace(create=crear), create=None)))

    v._client = cliente(resp('Conectado.', -1.06, 0.58))
    check('pipeline: ruido → "" y marca descartado', v._transcribir(b'wav') == ''
          and v._stt_descartado and not v._stt_fallo, f'-> {v._stt_descartado!r}')
    check('pipeline: pide verbose_json', pedidos[-1]['response_format'] == 'verbose_json')
    v._client = cliente(resp('Me gusta programar robots.', -0.3))
    v._convo = [{'role': 'assistant', 'content': '¿Qué te gusta hacer en las noches?'}]
    check('pipeline: voz real', v._transcribir(b'wav') == 'Me gusta programar robots.'
          and not v._stt_dudosa)
    check('pipeline: prompt con lo último de Bob',
          pedidos[-1]['prompt'].endswith('¿Qué te gusta hacer en las noches?'))
    v._transcribir(b'wav', contexto=False)
    check('pipeline: en reposo sin contexto', 'noches' not in pedidos[-1]['prompt'])
    v._client = cliente(resp('Conectado, ¿qué es lo que se ha hecho?', -1.39))
    check('pipeline: dudosa', v._transcribir(b'wav') and v._stt_dudosa)

    print(f"\n{'TODO OK' if fallos == 0 else f'{fallos} FALLA(S)'}")
    sys.exit(1 if fallos else 0)


if __name__ == '__main__':
    main()
