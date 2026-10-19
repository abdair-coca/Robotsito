"""
test_busqueda_ingeniero.py — Modo ingeniero con búsqueda en internet (fase S4), sin red.

La sesión de Claude no tiene herramientas. responder_ingeniero decide por turno entre
buscar (respuesta con fuentes, en streaming) y responder sin buscar, y dice cuándo falla
la búsqueda: sin Serper, sin resultados o sin respuesta del modelo.

  python tests/unit/test_busqueda_ingeniero.py
"""
import os
import sys
import types
from contextlib import contextmanager

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from voz import pipeline as voice_pipeline  # noqa: E402,F401  (carga los módulos del pipeline igual que en producción)
from agente import claude_code     # noqa: E402
from asistente import busqueda        # noqa: E402
from agente import modo_dueno as md  # noqa: E402
md.APPINGLES_DIR = ''   # estas pruebas cubren búsqueda sin herramientas de archivo (B1 tiene su prueba)

fallos = 0
FUENTE = {'titulo': 'Nota', 'url': 'https://a.bo/1', 'snippet': 's', 'dominio': 'a.bo',
          'fecha': '', 'posicion': 1}


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    global fallos
    fallos += 0 if ok else 1
    print(f"[{'OK' if ok else 'FALLA'}] {nombre} {detalle}", flush=True)


@contextmanager
def parches(objeto, **nuevos):
    """Reemplaza atributos del módulo y los restaura al salir."""
    originales = {k: getattr(objeto, k) for k in nuevos}
    for k, v in nuevos.items():
        setattr(objeto, k, v)
    try:
        yield
    finally:
        for k, v in originales.items():
            setattr(objeto, k, v)


def _valor(cmd: list, bandera: str):
    return cmd[cmd.index(bandera) + 1] if bandera in cmd else None


def _vp_falso():
    return types.SimpleNamespace(_memoria=None, _modo_ingeniero=False, _sesion_ing=None,
                                 _marca_voz='', _ruta_turno=None, _contexto_web=None)


def probar_sesion_sin_herramientas() -> None:
    cmd = md._crear_sesion_ingeniero(_vp_falso()).comando()
    check('sesión: sin herramientas (--tools vacío)', _valor(cmd, '--tools') == '')
    check('sesión: sin WebSearch ni permisos de herramienta',
          '--allowedTools' not in cmd and 'WebSearch' not in cmd)


def probar_entrar_ingeniero() -> None:
    dichos = []
    originales = (md._decir, claude_code.disponible, claude_code.SesionClaude.iniciar_en_fondo,
                  md.MOTOR_INGENIERO)
    md._decir = lambda vp, texto, emo: dichos.append(texto)
    claude_code.disponible = lambda: True
    claude_code.SesionClaude.iniciar_en_fondo = lambda self: None   # sin proceso real
    md.MOTOR_INGENIERO = 'claude'
    try:
        vp = _vp_falso()
        md.entrar_ingeniero(vp)
        cmd = vp._sesion_ing.comando()
    finally:
        md._decir, claude_code.disponible, claude_code.SesionClaude.iniciar_en_fondo, \
            md.MOTOR_INGENIERO = originales
    check('entrar_ingeniero: la sesión arranca sin herramientas', _valor(cmd, '--tools') == '')
    check('entrar_ingeniero: confirma en voz el modo', bool(dichos), f'(dichos={dichos})')


def correr(texto: str, *, activa: bool = True, plan='no llamar', buscar=None,
           frases_busqueda=('Dato [1].',), frases_claude=('Respuesta de Claude.',)):
    """Corre responder_ingeniero con componentes falsos. Devuelve (frases, registro)."""
    reg = {'claude': [], 'buscar': [], 'fuentes': 0, 'contexto': None}

    def descomponer(pregunta, **kw):
        if plan == 'no llamar':
            raise AssertionError('no debía descomponer')
        return plan

    def buscar_todas(consultas, noticias=False, timeout=None):
        reg['buscar'].append((tuple(consultas), noticias))
        return buscar(noticias) if buscar else None

    def responder_con_fuentes(pregunta, fuentes, **kw):
        reg['fuentes'] = len(fuentes)
        for f in frases_busqueda:
            yield f

    def claude(vp, t, sesion, nota=''):
        reg['claude'].append(nota)
        return list(frases_claude)

    vp = _vp_falso()
    with parches(md, BUSQUEDA_INGENIERO_ENABLED=activa, responder_claude=claude), \
            parches(busqueda, descomponer=descomponer, buscar_todas=buscar_todas,
                    responder_con_fuentes=responder_con_fuentes):
        frases = list(md.responder_ingeniero(vp, texto, sesion=None))
    reg['contexto'] = vp._contexto_web
    return frases, reg


def probar_flujo() -> None:
    frases, reg = correr('¿Qué es un servo?', activa=False)
    check('bandera apagada: responde Claude sin descomponer ni buscar',
          frases == ['Respuesta de Claude.'] and not reg['buscar'])

    frases, reg = correr('¿Qué es un servo?', plan={'buscar': False, 'consultas': []})
    check('concepto: no busca, responde Claude sin herramientas',
          frases == ['Respuesta de Claude.'] and reg['claude'] == [''] and not reg['buscar'])

    frases, reg = correr('¿Cuál es el dólar?', plan={'buscar': True, 'consultas': ['q1', 'q2']},
                         buscar=lambda noticias: [FUENTE])
    check('actualidad: busca con las consultas y responde con fuentes',
          frases == ['Dato [1].'] and reg['buscar'] == [(('q1', 'q2'), True)]
          and reg['fuentes'] == 1 and not reg['claude'])
    check('actualidad: guarda lo dicho y las fuentes como contexto del turno siguiente',
          reg['contexto'] == 'Dato [1]. Fuentes de esa búsqueda: [1] Nota (a.bo): https://a.bo/1')

    frases, reg = correr('¿Cuál es el dólar?', plan={'buscar': True, 'consultas': ['q1']},
                         buscar=lambda noticias: None)
    check('Serper caído: responde sin fuentes y lo dice',
          len(reg['claude']) == 1 and 'No pude buscar' in reg['claude'][0])

    frases, reg = correr('¿Cuál es el dólar?', plan={'buscar': True, 'consultas': ['q1']},
                         buscar=lambda noticias: [] if noticias else [FUENTE])
    check('sin noticias: reintenta con búsqueda general',
          reg['buscar'] == [(('q1',), True), (('q1',), False)] and reg['fuentes'] == 1)

    frases, reg = correr('¿Algo raro?', plan={'buscar': True, 'consultas': ['q1']},
                         buscar=lambda noticias: [])
    check('sin resultados: lo dice Claude',
          len(reg['claude']) == 1 and 'no encontró nada' in reg['claude'][0])

    frases, reg = correr('¿Cuál es el dólar hoy?', plan=None, buscar=lambda noticias: [FUENTE])
    check('descompositor caído: busca con la pregunta tal cual',
          reg['buscar'][0] == (('¿Cuál es el dólar hoy?',), True))

    frases, reg = correr('¿Cuál es el dólar?', plan={'buscar': True, 'consultas': ['q1']},
                         buscar=lambda noticias: [FUENTE], frases_busqueda=())
    check('respuesta con fuentes vacía: Claude responde con la nota',
          len(reg['claude']) == 1 and 'No pude armar' in reg['claude'][0]
          and frases == ['Respuesta de Claude.'])


def main() -> int:
    probar_sesion_sin_herramientas()
    probar_entrar_ingeniero()
    probar_flujo()
    print(f"\n{'OK' if fallos == 0 else f'{fallos} FALLA(S)'}", flush=True)
    return 1 if fallos else 0


if __name__ == '__main__':
    sys.exit(main())
