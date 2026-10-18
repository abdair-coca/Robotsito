"""
test_escritura_workspace.py — Sesión de escritura del modo ingeniero (fase B3), sin red.

Cubre: el comando de Claude Code (Edit y Write dentro de AppInglés, sin Bash ni
WebSearch, pedidos de permiso al host), el protocolo de permisos contra un proceso
falso que emite control_request, la confirmación por voz (sí del dueño, no, voz
ajena, voz corta, silencio) y el enrutado (solo turnos del dueño escriben; una voz
ajena no arranca la sesión). La prueba con Claude real está en test_escritura_live.py.

  python tests/test_escritura_workspace.py
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import claude_code   # noqa: E402
import modo_dueno    # noqa: E402

fallos = 0


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    global fallos
    fallos += 0 if ok else 1
    print(f"[{'OK' if ok else 'FALLA'}] {nombre} {detalle}", flush=True)


class SMFalso:
    def iniciar_hablando(self):
        pass


class MemoriaFalsa:
    def __init__(self, similitud):
        self.similitud = similitud

    def total_voces(self, persona_id):
        return 20

    def verificar_voz(self, persona_id, embedding):
        return self.similitud

    def contexto(self, persona_id):      # lo usa el prompt del modo ingeniero
        return ''


class VPFalso:
    """Lo que usan _decir, _aprobar_escritura y verificar: grabar, transcribir, hablar."""
    def __init__(self, wav=b'wav', texto='sí', similitud=0.9, seg=2.0):
        self._wav, self._texto = wav, texto
        self._analisis = ([0.1, 0.2], seg)
        self._memoria = MemoriaFalsa(similitud)
        self._sm = SMFalso()
        self.grabados = 0
        self.hablados = []
        self._modo_ingeniero = True
        self._dueno_turno = False
        self._sesion_ing = None
        self._client = None                  # respaldo Groq de ajustes_ingeniero

    def _grabar(self, **kwargs):
        self.grabados += 1
        return self._wav

    def _analizar_voz(self, wav):
        return self._analisis

    def _transcribir(self, wav, contexto=True):
        return self._texto

    def _hablar(self, texto, emo=None):
        self.hablados.append(texto)


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
carpeta = tempfile.mkdtemp(prefix='AppInglesEsc')
try:
    modo_dueno.APPINGLES_DIR = carpeta
    claude_code.disponible = lambda: True

    # 1) Confirmación: sí, no, nada
    check("es_confirmacion: 'sí' es sí", modo_dueno.es_confirmacion('sí') is True)
    check("es_confirmacion: 'sí, dale' es sí", modo_dueno.es_confirmacion('Sí, dale') is True)
    check("es_confirmacion: 'no' es no", modo_dueno.es_confirmacion('no') is False)
    check("es_confirmacion: 'no, espera' es no", modo_dueno.es_confirmacion('no, espera') is False)
    check("es_confirmacion: otra cosa es None", modo_dueno.es_confirmacion('mmm qué tal') is None)
    check("es_confirmacion: vacío es None", modo_dueno.es_confirmacion('  ') is None)

    # 2) Decisor por voz
    decidir = modo_dueno._aprobar_escritura(VPFalso(texto='sí'))
    check("decisor: sí del dueño → permite",
          decidir('Edit', {'file_path': os.path.join(carpeta, 'nota.txt')}) is True)
    vp = VPFalso(texto='no')
    check("decisor: no del dueño → niega",
          modo_dueno._aprobar_escritura(vp)('Write', {'file_path': 'nota.txt'}) is False)
    vp = VPFalso(texto='sí')
    modo_dueno._aprobar_escritura(vp)('Write', {'file_path': os.path.join(carpeta, 'nota.txt')})
    check("decisor: dice el nombre del archivo, sin ruta",
          any('nota.txt' in h for h in vp.hablados) and not any(carpeta in h for h in vp.hablados))
    vp = VPFalso(texto='sí', similitud=0.1)
    check("decisor: voz ajena con 'sí' → niega",
          modo_dueno._aprobar_escritura(vp)('Write', {'file_path': 'nota.txt'}) is False)
    vp = VPFalso(texto='sí', seg=0.5)
    check("decisor: voz corta ('corta') → niega",
          modo_dueno._aprobar_escritura(vp)('Write', {'file_path': 'nota.txt'}) is False)
    vp = VPFalso(wav=None)
    check("decisor: silencio → niega", modo_dueno._aprobar_escritura(vp)('Write', {}) is False)
    vp = VPFalso(texto='sí')
    check("decisor: Bash no se aprueba aquí (eso es B4)",
          modo_dueno._aprobar_escritura(vp)('Bash', {'command': 'dir'}) is False and vp.grabados == 0)

    # 3) Comando de la sesión de escritura
    vp = VPFalso()
    sesion = modo_dueno._sesion_escritura(vp)
    cmd = sesion.comando()
    check("escritura: cwd es AppInglés", sesion.cwd == carpeta)
    check("escritura: pide permiso al host (stdio, default)",
          valor(cmd, '--permission-prompt-tool') == 'stdio' and valor(cmd, '--permission-mode') == 'default')
    check("escritura: herramientas Read, Glob, Grep, Edit, Write",
          valor(cmd, '--tools') == 'Read,Glob,Grep,Edit,Write', f"-> {valor(cmd, '--tools')}")
    check("escritura: sin Bash ni WebSearch",
          not {'Bash', 'WebSearch'} & set(valor(cmd, '--tools').split(',') + valor(cmd, '--allowedTools').split()))
    check("escritura: solo lectura se aprueba sola", valor(cmd, '--allowedTools') == 'Read Glob Grep')
    check("escritura: .env bloqueado también para Edit y Write",
          {'Edit(**/.env)', 'Write(**/.env)'} <= set(valor(cmd, '--disallowedTools').split()))
    check("escritura: --restricted", '--restricted' in cmd)
    check("escritura: sin ajustes del usuario", valor(cmd, '--setting-sources') == '')
    check("escritura: el prompt no revela la ruta", carpeta not in valor(cmd, '--system-prompt'))

    # 4) Enrutado: solo el dueño escribe; la voz ajena usa la sesión de lectura
    lectura = claude_code.SesionClaude('lectura', nombre='ingeniero')
    vp = VPFalso()
    vp._sesion_ing = lectura
    vp._dueno_turno = False
    ajustes = modo_dueno.ajustes_ingeniero(vp)
    check("voz ajena: sesión de lectura", ajustes['sesion'] is lectura)
    check("voz ajena: no arranca la sesión de escritura", getattr(vp, '_sesion_esc', None) is None)
    vp._dueno_turno = True
    ajustes = modo_dueno.ajustes_ingeniero(vp)
    check("dueño: sesión de escritura", ajustes['sesion'].nombre == 'escritura')
    check("dueño: la sesión queda guardada para salir", vp._sesion_esc is ajustes['sesion'])
    ajustes = modo_dueno.ajustes_ingeniero(vp, motor='groq')
    check("dueño con respaldo Groq: sin sesión", 'sesion' not in ajustes)
    vp._dueno_turno = True
    modo_dueno.APPINGLES_DIR = os.path.join(carpeta, 'no_existe')
    check("carpeta inexistente: el dueño sigue con lectura",
          modo_dueno.ajustes_ingeniero(vp)['sesion'] is lectura)
    modo_dueno.APPINGLES_DIR = carpeta

    # 5) Protocolo: el host responde y el proceso recibe allow o deny
    def sesion_con(decision):
        pedidos = []

        def aprobar(herramienta, entrada):
            pedidos.append((herramienta, entrada.get('file_path')))
            if isinstance(decision, Exception):
                raise decision
            return decision
        s = claude_code.SesionClaude('x', nombre='prueba',
                                     comando=[sys.executable, '-c', HIJO_FALSO], cwd=carpeta,
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
    shutil.rmtree(carpeta, ignore_errors=True)

print(f"\nRESULTADO: {'TODO OK' if fallos == 0 else f'{fallos} FALLA(S)'}", flush=True)
sys.exit(0 if fallos == 0 else 1)
