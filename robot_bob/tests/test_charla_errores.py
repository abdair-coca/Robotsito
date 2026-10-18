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


def e_b() -> None:
    import music
    import modo_dueno as md
    from jev_decision import Decision
    print('\n== E-B: comandos de música y modo ingeniero ==')
    music.MUSICA_ENABLED = True
    vp.MUSICA_ENABLED = True

    # 1) Frases del log: el tema sale limpio
    r = music.parse_music_command('Ok, quiero que pongas Mario to Life de Michael Giaccino en Spotify.')
    check('E-B: "pongas Mario to Life… en Spotify" → play con el tema',
          r is not None and r.accion == 'play' and 'mario to life' in r.query.lower()
          and 'spotify' not in r.query.lower(), f'-> {r}')
    r = music.parse_music_command('Bobsito, quiero que pongas música, estoy un poco aburrido.')
    check('E-B: "música, estoy aburrido" no busca "estoy aburrido"',
          r is not None and 'aburrido' not in (r.query or '').lower(), f'-> {r}')
    r = music.parse_music_command('pon música de Queen')
    check('E-B: "pon música de Queen" → Queen', r is not None and r.query == 'Queen', f'-> {r}')
    r = music.parse_music_command('quiero que pongas algo de rock')
    check('E-B: "pongas algo de rock" → rock', r is not None and r.query == 'rock', f'-> {r}')
    r = music.parse_music_command('ponlo')
    check('E-B: "ponlo" sola no es un comando de play', r is None or r.accion != 'play', f'-> {r}')

    # 2) Modo ingeniero con las formas del log
    for texto, esperado in [('Hey Bob, quiero que te pongas el modo ingeniero', 'ingeniero'),
                            ('Bob, modo ingeniero', 'ingeniero'),
                            ('entra en modo ingeniero', 'ingeniero'),
                            ('sal del modo ingeniero', 'salir_ingeniero')]:
        d = md.detectar(texto)
        check(f'E-B: "{texto}" → {esperado}', d is not None and d[0] == esperado, f'-> {d}')
    d = md.detectar('¿qué es el modo ingeniero?')
    check('E-B: una pregunta sobre el modo no lo activa', d is None, f'-> {d}')

    # 3) "¿Qué canción?" → la respuesta del turno siguiente es el tema
    def charla(textos, rutas):
        v = construir()                 # apaga JEV real: se enciende después
        vp.JEV_ROUTING_ENABLED = True
        vp.JEV_EN_PARALELO = False
        ejecutadas = []
        vp.ejecutar_musica = lambda intent: (ejecutadas.append(intent), 'Listo.')[1]
        cola_t, cola_r = list(textos), list(rutas)
        v._grabar = lambda **k: b'wav' if cola_t else None

        def transcribir(audio, **k):
            return cola_t.pop(0) if cola_t else ''
        v._transcribir = transcribir
        vp.decidir = lambda frase, ultimo: Decision(cola_r.pop(0) if cola_r else 'charla', 'FELIZ',
                                                    0.9, 0.9, 0.1, False, '')
        v.llm_llamadas = []
        v._stream_and_speak = lambda texto: v.llm_llamadas.append(texto)
        v._run_conversation()
        vp.JEV_ROUTING_ENABLED = False
        return v, ejecutadas

    v, ejecutadas = charla(['me gustaría que pusieras un tema', 'Mario to Life'],
                           ['musica_play', 'charla'])
    check('E-B: JEV "pon música" sin tema → pregunta, no inventa',
          '¿Qué canción quieres que ponga?' in v.dichas and not v.llm_llamadas, f'-> {v.dichas}')
    check('E-B: la respuesta siguiente es el tema',
          len(ejecutadas) == 1 and 'mario' in (ejecutadas[0].query or '').lower(),
          f'-> {[(e.accion, e.query) for e in ejecutadas]}')

    v, ejecutadas = charla(['me gustaría que pusieras un tema', 'no, mejor no'],
                           ['musica_play', 'charla'])
    check('E-B: "no, mejor no" cancela sin reproducir',
          'Ok, no pongo nada.' in v.dichas and not ejecutadas, f'-> {v.dichas} {ejecutadas}')


class _Falla(Exception):
    pass


def _chunk(texto):
    from types import SimpleNamespace as NS
    return NS(choices=[NS(delta=NS(content=texto))])


class _Stream:
    """Entrega los trozos y luego, si `falla`, lanza como lo hace la red a mitad."""

    def __init__(self, trozos, falla=False):
        self.trozos, self.falla = trozos, falla

    def __iter__(self):
        for t in self.trozos:
            yield _chunk(t)
        if self.falla:
            raise _Falla('corte de red')

    def close(self):
        pass


class _LLM:
    def __init__(self, stream):
        from types import SimpleNamespace as NS
        self.stream = stream
        self.chat = NS(completions=NS(create=lambda **kw: self.stream))


def _texto_hablado(v, llm_stream, texto='hola'):
    v._llm, v._llm_model, v._llm_extra = _LLM(llm_stream), 'fake', {}
    salida = list(v._stream_llm(texto))
    return [vp._EMO_TAG_RE.sub('', s).strip() for s in salida]


def e_c() -> None:
    print('\n== E-C: respuesta del LLM ==')
    # 1) Guardia: las frases del log que afirman una acción no hecha se cambian
    for frase in ['Voy a crear una carpeta llamada “AppInglés” y dentro le dejo la descripción.',
                  'Voy a abrir Spotify y buscar “Mario to Life” de Michael Giacone.',
                  'Espera un momento, tu playlist está a punto de sonar.',
                  'Pongo el modo ingeniero, recalibrando mi firmware y afinando cada servo.',
                  'Puedo reproducir tu playlist favorita, solo dime el nombre de la canción.',
                  'Puedo abrir Spotify si me das permiso.']:
        check(f'E-C: acción no hecha → cambiada: "{frase[:45]}…"', vp._promete_accion(frase))
    # 2) Lo que no es una promesa, o es una negación, se deja igual
    for frase in ['Lo siento, Abdair, no puedo reproducir música directamente.',
                  'Vamos a diseñar la app juntos, primero definimos su objetivo.',
                  'Vamos a escuchar una canción de Queen.',
                  'Podemos armar una app con lecciones diarias y repeticiones.',
                  '¡Qué buena idea, Abdair!']:
        check(f'E-C: sin cambios: "{frase[:45]}…"', not vp._promete_accion(frase))

    # 3) En la charla, una frase que promete acción sale como la frase honesta
    v = construir()
    salida = _texto_hablado(v, _Stream(['Voy a crear una carpeta para el proyecto. ']))
    check('E-C: la charla dice la frase honesta, no la promesa',
          salida == [vp.FRASE_SIN_ACCION] and 'carpeta' not in ' '.join(salida), f'-> {salida}')

    # 4) Stream que se corta a mitad: lo ya dicho queda y Bob lo avisa
    v = construir()
    salida = _texto_hablado(v, _Stream(['Hola, qué tal bien. ', 'Estoy pensando en'], falla=True))
    check('E-C: corte a mitad → lo dicho + "se me cortó la idea"',
          len(salida) == 2 and salida[0].startswith('Hola, qué tal bien.') and salida[1] == vp.FRASE_CORTE,
          f'-> {salida}')

    # 5) Corte antes de la primera frase: solo el aviso, sin silencio
    v = construir()
    salida = _texto_hablado(v, _Stream([], falla=True))
    check('E-C: corte sin nada dicho → solo el aviso', salida == [vp.FRASE_CORTE], f'-> {salida}')

    # 6) Pedido adelantado: el error del stream se relanza al consumidor (antes se perdía)
    llm = _LLM(_Stream(['Hola. '], falla=True))
    pre = vp._Prefetch(llm, {'model': 'fake', 'messages': []})
    dichos, lanzo = [], False
    try:
        for c in pre:
            dichos.append(c)
    except _Falla:
        lanzo = True
    check('E-C: prefetch relanza el error del stream', lanzo and len(dichos) == 1,
          f'-> lanzó={lanzo}, chunks={len(dichos)}')

    # 7) La regla de charla va en el prompt del pedido
    v = construir()
    _llm, kwargs, _ = v._armar_pedido('hola', None, None)
    sistema = kwargs['messages'][0]['content']
    check('E-C: la regla "no tienes herramientas" va en el prompt',
          'no tienes herramientas' in sistema and 'nombres propios' in sistema)


def main() -> None:
    e_a()
    e_b()
    e_c()
    print(f"\n{'TODO OK' if fallos == 0 else f'{fallos} FALLA(S)'}")
    sys.exit(1 if fallos else 0)


if __name__ == '__main__':
    main()
