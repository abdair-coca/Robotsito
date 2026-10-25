"""
test_registro_turnos.py — Registro por turno del perfil feria, sin texto ni identidad.

Sin red ni micrófono: el LLM y el TTS son falsos y el log va a un temporal. Cubre una
línea por llamada con hora, un fallo de disco que no lanza, la bandera de memoria, que
el perfil feria enciende memoria off y registro on (el normal no), un turno con tiempos
y modelo sin el texto de la respuesta, un LLM caído marcado como respaldo y el registro
apagado sin escribir nada.

  python tests/unit/test_registro_turnos.py
"""
import json
import os
import sys
import tempfile
import threading
from types import SimpleNamespace as NS

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from voz import pipeline as vp   # noqa: E402
from voz import registro_turnos   # noqa: E402

RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
fallos = 0
vp.JEV_EN_PARALELO = False


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    global fallos
    fallos += 0 if ok else 1
    print(f"[{'OK' if ok else 'FALLA'}] {nombre} {detalle}", flush=True)


def leer(ruta):
    if not os.path.exists(ruta):
        return []
    with open(ruta, encoding="utf-8") as f:
        return [json.loads(linea) for linea in f if linea.strip()]


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


class LLMFalso:
    """Responde una frase fija; esa frase no debe llegar al log."""

    def __init__(self, caido=False):
        self.caido = caido
        self.ultimo = NS(nombre='gpt-oss-20b')   # como RouterLLM: el backend que respondió
        self.chat = NS(completions=NS(create=self.create))

    def create(self, **kw):
        if self.caido:
            raise RuntimeError('sin red')

        class S:
            def __iter__(s):
                yield NS(choices=[NS(delta=NS(content='Claro que sí, te cuento algo bonito.'))])

            def close(s):
                pass
        return S()


def turno(llm, stt_s=0.42):
    v = vp.VoicePipeline(FakeMgr(), FakeSM())
    v.reproducido = []
    v._reproducir = lambda audio: v.reproducido.append(audio) and None
    v._llm = llm
    v._stt_s = stt_s
    v._stream_and_speak('cuéntame algo')
    return v


def config_con_perfil(perfil: str) -> dict:
    ruta = os.path.join(RAIZ, 'config.py')
    with open(ruta, encoding='utf-8') as f:
        src = f.read().replace('PERFIL = "normal"', f'PERFIL = "{perfil}"', 1)
    ns = {'__file__': ruta, '__name__': 'config_de_prueba'}
    exec(compile(src, ruta, 'exec'), ns)
    return ns


def main() -> None:
    tmp = tempfile.mkdtemp()
    memoria_orig, registro_orig = vp.MEMORIA_ENABLED, vp.REGISTRO_TURNOS
    log_orig, sint_orig = registro_turnos.LOG_TURNOS, vp._sintetizar
    vp._sintetizar = lambda t, e=None: t.encode()
    try:
        # 1) Una línea por llamada, con hora y los campos dados
        log = os.path.join(tmp, 'lineas.jsonl')
        registro_turnos.registrar({'stt_s': 0.4, 'cortado': False}, log)
        registro_turnos.registrar({'stt_s': None, 'cortado': True}, log)
        lineas = leer(log)
        check('una línea por llamada', len(lineas) == 2, f'-> {len(lineas)}')
        check('cada línea lleva hora y campos', 'ts' in lineas[0] and lineas[0]['stt_s'] == 0.4)

        # 2) Fallo de disco: no lanza y el turno sigue
        bloqueo = os.path.join(tmp, 'bloqueo')
        open(bloqueo, 'w').close()
        try:
            registro_turnos.registrar({'stt_s': 1}, os.path.join(bloqueo, 'sub', 'x.jsonl'))
            sin_excepcion = True
        except Exception:
            sin_excepcion = False
        check('fallo de disco: no lanza', sin_excepcion)

        # 3) Con la bandera apagada, la identidad no está activa aunque haya cámara y base
        completo = NS(_face_id=object(), _memoria=object(), _get_frame=object())
        vp.MEMORIA_ENABLED = False
        check('memoria apagada: no activa', vp.VoicePipeline._memoria_activa(completo) is False)
        vp.MEMORIA_ENABLED = True
        check('memoria encendida: activa', vp.VoicePipeline._memoria_activa(completo) is True)
        vp.MEMORIA_ENABLED = memoria_orig

        # 4) El perfil feria enciende ambas banderas; el normal no
        feria, normal = config_con_perfil('feria'), config_con_perfil('normal')
        check('perfil feria: memoria off y registro on',
              feria['MEMORIA_ENABLED'] is False and feria['REGISTRO_TURNOS'] is True)
        check('perfil normal: memoria como antes y sin registro',
              normal['MEMORIA_ENABLED'] is True and normal['REGISTRO_TURNOS'] is False)

        # 5) Turno completo: tiempos y modelo, sin el texto de la respuesta
        log5 = os.path.join(tmp, 'turno.jsonl')
        registro_turnos.LOG_TURNOS = log5
        vp.REGISTRO_TURNOS = True
        v = turno(LLMFalso())
        lineas = leer(log5)
        check('turno: una línea', len(lineas) == 1, f'-> {len(lineas)}')
        if lineas:
            t = lineas[0]
            check('turno: tiempos, modelo y sin fallo',
                  t['stt_s'] == 0.42 and isinstance(t['llm_s'], float)
                  and isinstance(t['hasta_audio_s'], float) and isinstance(t['tts_s'], float)
                  and t['llm_modelo'] == 'gpt-oss-20b' and t['llm_fallo'] is None
                  and t['cortado'] is False,
                  json.dumps(t, ensure_ascii=False))
        check('turno: el texto de la respuesta no entra al log',
              'Claro' not in open(log5, encoding='utf-8').read())
        check('turno: el STT se consume una sola vez', v._stt_s is None)

        # 6) LLM caído: Bob dice la frase de respaldo y el turno queda marcado
        log6 = os.path.join(tmp, 'caido.jsonl')
        registro_turnos.LOG_TURNOS = log6
        turno(LLMFalso(caido=True))
        lineas = leer(log6)
        check('LLM caído: queda como respaldo',
              bool(lineas) and lineas[0]['llm_fallo'] == 'respaldo', f'-> {lineas[:1]}')

        # 7) Con el registro apagado no se escribe nada
        vp.REGISTRO_TURNOS = False
        log7 = os.path.join(tmp, 'apagado.jsonl')
        registro_turnos.LOG_TURNOS = log7
        turno(LLMFalso())
        check('registro apagado: no escribe', not os.path.exists(log7))
    finally:
        vp.MEMORIA_ENABLED, vp.REGISTRO_TURNOS = memoria_orig, registro_orig
        registro_turnos.LOG_TURNOS, vp._sintetizar = log_orig, sint_orig

    print(f"\n{'TODO OK' if fallos == 0 else f'{fallos} FALLA(S)'}", flush=True)
    sys.exit(1 if fallos else 0)


if __name__ == '__main__':
    main()
