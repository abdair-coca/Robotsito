"""
test_busqueda_ingeniero.py — Búsqueda web del modo ingeniero (fase B0), sin red ni micrófono.

Sesión de Claude Code con WebSearch y nada más (sin lectura, escritura ni comandos),
bandera BUSQUEDA_INGENIERO_ENABLED, aviso "Buscando en internet..." una vez por turno
y tope de búsquedas por turno, cableado en entrar_ingeniero y responder_claude.

  python tests/test_busqueda_ingeniero.py
"""
import os
import sys
import types

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import voice_pipeline  # noqa: E402,F401  (carga los módulos del pipeline igual que en producción)
import claude_code     # noqa: E402
import modo_dueno as md  # noqa: E402

fallos = 0
PROHIBIDAS_B0 = ('Read', 'Edit', 'Write', 'Bash', 'Glob', 'Grep')


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    global fallos
    fallos += 0 if ok else 1
    print(f"[{'OK' if ok else 'FALLA'}] {nombre} {detalle}", flush=True)


def _valor(cmd: list, bandera: str):
    """Valor que sigue a una bandera en la línea de comando, o None si no está."""
    return cmd[cmd.index(bandera) + 1] if bandera in cmd else None


def _vp_falso():
    return types.SimpleNamespace(_memoria=None, _modo_ingeniero=False, _sesion_ing=None,
                                 _marca_voz='', _ruta_turno=None, _contexto_web=None)


def probar_sesion_con_busqueda() -> None:
    s = md._crear_sesion_ingeniero(_vp_falso())
    cmd = s.comando()
    check('sesión con búsqueda: --tools WebSearch', _valor(cmd, '--tools') == 'WebSearch',
          f'(--tools={_valor(cmd, "--tools")!r})')
    check('sesión con búsqueda: --allowedTools WebSearch',
          _valor(cmd, '--allowedTools') == 'WebSearch')
    check('sesión con búsqueda: --permission-mode dontAsk',
          _valor(cmd, '--permission-mode') == 'dontAsk')
    herramientas = [t for t in cmd if t in PROHIBIDAS_B0]
    check('sesión con búsqueda: sin lectura, escritura ni comandos', not herramientas,
          f'(encontradas: {herramientas})')
    check('sesión con búsqueda: nota de WebSearch en el prompt',
          md.NOTA_BUSQUEDA in s.system_prompt)


def probar_bandera_apagada() -> None:
    original = md.BUSQUEDA_INGENIERO_ENABLED
    md.BUSQUEDA_INGENIERO_ENABLED = False
    try:
        s = md._crear_sesion_ingeniero(_vp_falso())
        cmd = s.comando()
    finally:
        md.BUSQUEDA_INGENIERO_ENABLED = original
    check('bandera apagada: --tools vacío (como antes)', _valor(cmd, '--tools') == '')
    check('bandera apagada: sin --allowedTools ni WebSearch',
          '--allowedTools' not in cmd and 'WebSearch' not in cmd)
    check('bandera apagada: sin nota de WebSearch', md.NOTA_BUSQUEDA not in s.system_prompt)


def probar_aviso_una_vez() -> None:
    dichos = []
    original = md._decir
    md._decir = lambda vp, texto, emo: dichos.append(texto)
    try:
        al_usar = md._avisar_busqueda(_vp_falso())
        al_usar('WebSearch', {'query': 'noticias de hoy'})
        al_usar('WebSearch', {'query': 'otra búsqueda'})
        al_usar('Read', {'file_path': 'x.py'})
    finally:
        md._decir = original
    check('aviso: una sola vez por turno con dos búsquedas',
          dichos == ['Buscando en internet...'], f'(dichos={dichos})')


def probar_aviso_por_turno() -> None:
    dichos = []
    original = md._decir
    md._decir = lambda vp, texto, emo: dichos.append(texto)
    try:
        vp = _vp_falso()
        md._avisar_busqueda(vp)('WebSearch', {'query': 'a'})
        md._avisar_busqueda(vp)('WebSearch', {'query': 'b'})
    finally:
        md._decir = original
    check('aviso: cada turno (nuevo callback) vuelve a avisar', len(dichos) == 2,
          f'(avisos={len(dichos)})')


def probar_responder_claude_cablea() -> None:
    llamadas = []

    class SesionFalsa:
        def preguntar(self, texto, timeout=None, al_usar=None, max_herramientas=None):
            llamadas.append({'timeout': timeout, 'al_usar': al_usar,
                             'max_herramientas': max_herramientas})
            return 'Hola, Abdair.'

    frases = md.responder_claude(_vp_falso(), 'hola', SesionFalsa())
    check('responder_claude: pasa al_usar y el tope de búsquedas',
          len(llamadas) == 1 and callable(llamadas[0]['al_usar'])
          and llamadas[0]['max_herramientas'] == md.ING_MAX_BUSQUEDAS,
          f'(max_herramientas={llamadas[0]["max_herramientas"] if llamadas else None})')
    check('responder_claude: devuelve las frases', frases == ['Hola, Abdair.'],
          f'(frases={frases})')


def probar_entrar_ingeniero() -> None:
    dichos = []
    originales = (md._decir, claude_code.disponible, claude_code.SesionClaude.iniciar_en_fondo,
                  md.MOTOR_INGENIERO)
    md._decir = lambda vp, texto, emo: dichos.append(texto)
    claude_code.disponible = lambda: True
    # Sin proceso real: solo nos interesa la configuración de la sesión.
    claude_code.SesionClaude.iniciar_en_fondo = lambda self: None
    md.MOTOR_INGENIERO = 'claude'
    try:
        vp = _vp_falso()
        md.entrar_ingeniero(vp)
        cmd = vp._sesion_ing.comando()
    finally:
        md._decir, claude_code.disponible, claude_code.SesionClaude.iniciar_en_fondo, \
            md.MOTOR_INGENIERO = originales
    check('entrar_ingeniero: la sesión tiene WebSearch y nada más',
          _valor(cmd, '--tools') == 'WebSearch' and not any(t in PROHIBIDAS_B0 for t in cmd))
    check('entrar_ingeniero: confirma en voz el modo', bool(dichos), f'(dichos={dichos})')


def main() -> int:
    probar_sesion_con_busqueda()
    probar_bandera_apagada()
    probar_aviso_una_vez()
    probar_aviso_por_turno()
    probar_responder_claude_cablea()
    probar_entrar_ingeniero()
    print(f"\n{'OK' if fallos == 0 else f'{fallos} FALLA(S)'}", flush=True)
    return 1 if fallos else 0


if __name__ == '__main__':
    sys.exit(main())
