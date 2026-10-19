"""
test_escritura_workspace.py — Sesión de escritura del modo ingeniero (fase B3, ajustada), sin red.

Cubre: el comando de Claude Code (Edit y Write dentro de AppInglés, sin Bash ni
WebSearch, pedidos de permiso al host), el decisor (Edit y Write sin voz; el resto
negado), el protocolo contra un proceso falso que emite control_request, y el
enrutado: la sesión de escritura existe mientras dura el modo y los turnos con
contenido de internet van a la sesión de lectura. La prueba con Claude real está en
test_escritura_live.py.

  python tests/test_escritura_workspace.py
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from agente import claude_code   # noqa: E402
from agente import modo_dueno    # noqa: E402

fallos = 0


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    global fallos
    fallos += 0 if ok else 1
    print(f"[{'OK' if ok else 'FALLA'}] {nombre} {detalle}", flush=True)


class MemoriaFalsa:
    def contexto(self, persona_id):      # lo usa el prompt del modo ingeniero
        return ''


class VPFalso:
    def __init__(self):
        self._memoria = MemoriaFalsa()
        self._modo_ingeniero = True
        self._sesion_ing = None
        self._contexto_web = None
        self._client = None              # respaldo Groq de ajustes_ingeniero


def valor(cmd: list, flag: str) -> str:
    return cmd[cmd.index(flag) + 1]


# Proceso falso: recibe el mensaje del turno, pide permiso para Write, lee la respuesta
# del host y termina el turno con el behavior que recibió.
HIJO_FALSO = r'''
import json, sys
for linea in sys.stdin:
    ev = json.loads(linea)
    if ev.get("type") == "user":
        print(json.dumps({"type": "control_request", "request_id": "r1",
              "request": {"subtype": "can_use_tool", "tool_name": "Write",
                          "input": {"file_path": "x.txt", "content": "hola"}}}), flush=True)
        ctl = json.loads(sys.stdin.readline())
        beh = ctl["response"]["response"].get("behavior", "vacio")
        print(json.dumps({"type": "result", "is_error": False, "result": beh}), flush=True)
        break
'''

original_apdir = modo_dueno.APPINGLES_DIR
original_disp = claude_code.disponible
original_descomponer = modo_dueno.busqueda.descomponer
original_con_fuentes = modo_dueno._responder_con_fuentes
original_claude = modo_dueno.responder_claude
carpeta = tempfile.mkdtemp(prefix='AppInglesEsc')
try:
    modo_dueno.APPINGLES_DIR = carpeta
    claude_code.disponible = lambda: True

    # 1) Decisor: Edit y Write sin voz; cualquier otra herramienta, no
    decidir = modo_dueno._aprobar_escritura()
    check("decisor: Write se permite sin voz", decidir('Write', {'file_path': 'nota.txt'}) is True)
    check("decisor: Edit se permite sin voz", decidir('Edit', {'file_path': 'nota.txt'}) is True)
    check("decisor: Bash corre sin pedir nada (B4; los bloqueos van en test_bash_workspace)",
          decidir('Bash', {'command': 'dir'}) is True)
    check("decisor: WebSearch se niega", decidir('WebSearch', {'query': 'x'}) is False)

    # 2) Comando de la sesión de escritura
    vp = VPFalso()
    sesion = modo_dueno._sesion_escritura(vp)
    cmd = sesion.comando()
    check("escritura: cwd es AppInglés", sesion.cwd == carpeta)
    check("escritura: pide permiso al host (stdio, default)",
          valor(cmd, '--permission-prompt-tool') == 'stdio' and valor(cmd, '--permission-mode') == 'default')
    check("escritura: herramientas Read, Glob, Grep, Edit, Write",
          valor(cmd, '--tools') == 'Read,Glob,Grep,Edit,Write,Bash', f"-> {valor(cmd, '--tools')}")
    check("escritura: sin WebSearch",
          'WebSearch' not in set(valor(cmd, '--tools').split(',') + valor(cmd, '--allowedTools').split()))
    check("escritura: solo lectura se aprueba sola", valor(cmd, '--allowedTools') == 'Read Glob Grep')
    check("escritura: .env bloqueado también para Edit y Write",
          {'Edit(**/.env)', 'Write(**/.env)'} <= set(valor(cmd, '--disallowedTools').split()))
    check("escritura: --restricted", '--restricted' in cmd)
    check("escritura: sin ajustes del usuario", valor(cmd, '--setting-sources') == '')
    check("escritura: el prompt no revela la ruta", carpeta not in valor(cmd, '--system-prompt'))

    # 3) Enrutado: durante el modo, la sesión de escritura; sin modo, nada
    lectura = claude_code.SesionClaude('lectura', nombre='ingeniero')
    vp = VPFalso()
    vp._sesion_ing = lectura
    ajustes = modo_dueno.ajustes_ingeniero(vp)
    check("modo ingeniero con carpeta: sesión de escritura", ajustes['sesion'].nombre == 'escritura')
    check("sesión de escritura guardada para salir", vp._sesion_esc is ajustes['sesion'])
    check("respaldo Groq: sin sesión", 'sesion' not in modo_dueno.ajustes_ingeniero(vp, motor='groq'))
    modo_dueno.APPINGLES_DIR = os.path.join(carpeta, 'no_existe')
    check("carpeta inexistente: sesión de lectura", modo_dueno.ajustes_ingeniero(vp)['sesion'] is lectura)
    modo_dueno.APPINGLES_DIR = carpeta
    vp._modo_ingeniero = False
    check("fuera del modo: sin ajustes", modo_dueno.ajustes_ingeniero(vp) is None)

    # 4) Turnos con internet no escriben: se responden con la sesión de lectura
    usadas = []

    def con_fuentes(vp_, texto, consultas, sesion_turno):
        usadas.append(('fuentes', sesion_turno.nombre if sesion_turno else None))
        yield 'respuesta con fuentes'

    def claude_falso(vp_, texto, sesion_turno, nota=''):
        usadas.append(('claude', sesion_turno.nombre if sesion_turno else None))
        return ['respuesta']

    modo_dueno._responder_con_fuentes = con_fuentes
    modo_dueno.responder_claude = claude_falso

    def responder(plan, contexto_web=None, sesion_escritura=True, voz=0.9):
        modo_dueno.busqueda.descomponer = lambda texto: plan
        vp = VPFalso()
        vp._sesion_ing = lectura
        vp._contexto_web = contexto_web
        vp._voz_turno = voz
        escritura = modo_dueno._sesion_escritura(vp) if sesion_escritura else None
        usadas.clear()
        list(modo_dueno.responder_ingeniero(vp, 'pregunta', escritura))
        return list(usadas)

    check("búsqueda en internet: va a lectura",
          responder({'buscar': True, 'consultas': ['x']}) == [('fuentes', 'ingeniero')])
    check("resultado web previo: va a lectura",
          responder({'buscar': False, 'consultas': []}, contexto_web='dato') == [('claude', 'ingeniero')])
    check("turno sin internet: va a escritura",
          responder({'buscar': False, 'consultas': []}) == [('claude', 'escritura')])
    check("voz bajo 0.55: va a lectura (B-D11)",
          responder({'buscar': False, 'consultas': []}, voz=0.50) == [('claude', 'ingeniero')])
    check("sin voz verificada: va a lectura (B-D11)",
          responder({'buscar': False, 'consultas': []}, voz=None) == [('claude', 'ingeniero')])
    usadas.clear()
    modo_dueno.busqueda.descomponer = lambda texto: {'buscar': True, 'consultas': ['x']}
    vp = VPFalso()
    vp._sesion_ing = None
    escritura = modo_dueno._sesion_escritura(vp)
    frases = list(modo_dueno.responder_ingeniero(vp, 'pregunta', escritura))
    check("internet sin sesión de lectura: no responde (Groq toma el turno)",
          frases == [] and usadas == [])

    # 5) Protocolo: el host responde y el proceso recibe allow o deny
    HIJOS = [sys.executable, '-c', HIJO_FALSO]

    def sesion_con(decision):
        pedidos = []

        def aprobar(herramienta, entrada):
            pedidos.append((herramienta, entrada.get('file_path')))
            if isinstance(decision, Exception):
                raise decision
            return decision
        s = claude_code.SesionClaude('x', nombre='prueba', comando=HIJOS, cwd=carpeta,
                                     aprobar=aprobar if decision is not None else None)
        return s, pedidos

    for decision, esperado, nombre in [(True, 'allow', 'permite → allow'),
                                       (False, 'deny', 'niega → deny'),
                                       (RuntimeError('falla'), 'deny', 'error del decisor → deny'),
                                       (None, 'deny', 'sin decisor → deny')]:
        s, pedidos = sesion_con(decision)
        try:
            resultado = s.preguntar('crea x.txt', timeout=30)
        finally:
            s.cerrar()
        check(f"protocolo: {nombre}", resultado == esperado, f"-> {resultado}")
        if decision is True:
            check("protocolo: el decisor recibe herramienta y ruta",
                  pedidos == [('Write', 'x.txt')], f"-> {pedidos}")

    check("protocolo: el stderr no cae en la carpeta de trabajo",
          not any('stderr' in n for n in os.listdir(carpeta)))
finally:
    modo_dueno.APPINGLES_DIR = original_apdir
    claude_code.disponible = original_disp
    modo_dueno.busqueda.descomponer = original_descomponer
    modo_dueno._responder_con_fuentes = original_con_fuentes
    modo_dueno.responder_claude = original_claude
    shutil.rmtree(carpeta, ignore_errors=True)

print(f"\nRESULTADO: {'TODO OK' if fallos == 0 else f'{fallos} FALLA(S)'}", flush=True)
sys.exit(0 if fallos == 0 else 1)
