"""
test_claude_code.py — Sesión persistente de Claude Code (claude_code.py), sin red.

Usa un CLI falso (un script de Python que habla stream-json) para probar: turno
normal, varias preguntas en el mismo proceso, error del turno, proceso que muere,
tiempo agotado, tope de herramientas y que el hijo no reciba CLAUDECODE ni
ANTHROPIC_API_KEY. También revisa el comando real que se armaría (aislamiento,
MCP y listas de herramientas) sin ejecutarlo.

  python tests/unit/test_claude_code.py
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from agente import claude_code   # noqa: E402

fallos = 0

# CLI falso: por cada mensaje responde según su texto.
FALSO = r'''
import json, os, sys, time
for linea in sys.stdin:
    ev = json.loads(linea)
    out = lambda e: (sys.stdout.write(json.dumps(e) + "\n"), sys.stdout.flush())
    if ev.get('type') == 'control_request':      # interrupt: sin turno en curso, solo responde
        out({"type": "control_response", "response": {"subtype": "success",
                                                       "request_id": ev.get("request_id")}})
        continue
    msg = ev['message']['content']
    if msg == 'muere':
        sys.exit(3)
    if msg == 'lento':
        time.sleep(5)
    if msg == 'error':
        out({"type": "result", "is_error": True, "subtype": "error_during_execution"})
        continue
    if msg == 'entorno':
        out({"type": "result", "is_error": False,
             "result": f"CC={os.environ.get('CLAUDECODE')} KEY={os.environ.get('ANTHROPIC_API_KEY')}"})
        continue
    if msg.startswith('herramientas'):
        for i in range(int(msg.split()[1])):
            out({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": f"mcp__x__h{i}", "input": {"url": "u"}}]}})
    out({"type": "assistant", "message": {"content": [{"type": "text", "text": "eco: " + msg}]}})
    out({"type": "result", "is_error": False, "result": "eco: " + msg})
'''


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    global fallos
    fallos += 0 if ok else 1
    print(f"[{'OK' if ok else 'FALLA'}] {nombre} {detalle}", flush=True)


def main() -> None:
    tmp = tempfile.mkdtemp(prefix='bob_cc_')
    falso = os.path.join(tmp, 'claude_falso.py')
    with open(falso, 'w', encoding='utf-8') as f:
        f.write(FALSO)
    base = dict(cwd=tmp, comando=[sys.executable, falso])
    try:
        # 1) Turnos en el mismo proceso
        s = claude_code.SesionClaude('prompt', nombre='t1', **base)
        check('turno normal', s.preguntar('hola', timeout=10) == 'eco: hola')
        pid = s._proc.pid if s._proc else None
        check('segundo turno, mismo proceso', s.preguntar('otra', timeout=10) == 'eco: otra'
              and s._proc is not None and s._proc.pid == pid)

        # 2) Error del turno → None, el proceso sigue vivo
        check('error del turno → None', s.preguntar('error', timeout=10) is None)
        check('después del error sigue vivo', s.vivo)

        # 3) Entorno del hijo: sin CLAUDECODE ni ANTHROPIC_API_KEY
        os.environ['CLAUDECODE'] = '1'
        os.environ['ANTHROPIC_API_KEY'] = 'sk-prueba'
        s2 = claude_code.SesionClaude('prompt', nombre='t2', **base)
        r = s2.preguntar('entorno', timeout=10)
        check('el hijo no recibe CLAUDECODE ni la API key', r == 'CC=None KEY=None', f'-> {r}')
        del os.environ['CLAUDECODE'], os.environ['ANTHROPIC_API_KEY']
        s2.cerrar()

        # 4) Proceso que muere → None; el siguiente turno lo rearranca
        check('proceso que muere → None', s.preguntar('muere', timeout=10) is None)
        check('el siguiente turno rearranca', s.preguntar('vuelve', timeout=10) == 'eco: vuelve')

        # 5) Tiempo agotado → None y cierra
        check('tiempo agotado → None', s.preguntar('lento', timeout=1.0) is None)
        check('tiempo agotado: proceso cerrado', not s.vivo)

        # 6) Tope de herramientas → None y cierra; dentro del tope, avisa cada uso
        usadas = []
        r = s.preguntar('herramientas 3', timeout=10, max_herramientas=5,
                        al_usar=lambda n, e: usadas.append(n))
        check('dentro del tope: responde y avisa cada herramienta',
              r == 'eco: herramientas 3' and usadas == ['mcp__x__h0', 'mcp__x__h1', 'mcp__x__h2'])
        check('pasado el tope → None', s.preguntar('herramientas 8', timeout=10,
                                                   max_herramientas=5) is None)
        check('pasado el tope: proceso cerrado', not s.vivo)
        s.cerrar()

        # 6b) Contadores: procesos lanzados y turnos del proceso actual
        c = claude_code.SesionClaude('prompt', nombre='t3', **base)
        c.preguntar('uno', timeout=10)
        c.preguntar('dos', timeout=10)
        check('contadores: 1 proceso, 2 turnos', c.arranques == 1 and c.turnos == 2,
              f'-> {c.arranques}, {c.turnos}')
        c.preguntar('muere', timeout=10)
        c.preguntar('tres', timeout=10)
        check('contadores: proceso nuevo, turnos desde 0', c.arranques == 2 and c.turnos == 1,
              f'-> {c.arranques}, {c.turnos}')
        c.cerrar()

        # 6c) conservar: pasado el tope interrumpe sin cerrar (el navegador del MCP sigue)
        k = claude_code.SesionClaude('prompt', nombre='t4', conservar=True, **base)
        k.preguntar('hola', timeout=10)
        pid = k._proc.pid
        check('conservar: pasado el tope → None', k.preguntar('herramientas 8', timeout=10,
                                                             max_herramientas=5) is None)
        check('conservar: mismo proceso vivo', k.vivo and k._proc.pid == pid)
        check('conservar: el turno siguiente responde lo suyo', k.preguntar('sigue', timeout=10)
              == 'eco: sigue')
        k.cerrar()

        # 7) Comando real (sin ejecutarlo): aislamiento, modelo, MCP y listas
        real = claude_code.SesionClaude('SYS', modelo='claude-haiku-5-5', cwd=tmp, nombre='w',
                                        mcp={'pw': {'command': 'npx', 'args': ['x']}},
                                        permitidas=['a', 'b'], prohibidas=['c'])
        c = real.comando()

        def valor(flag):
            return c[c.index(flag) + 1] if flag in c else None
        check('comando: -p y stream-json de ida y vuelta', '-p' in c and
              valor('--input-format') == 'stream-json' and valor('--output-format') == 'stream-json')
        check('comando: Haiku 5.5 con effort low', valor('--model') == 'claude-haiku-5-5'
              and valor('--effort') == 'low')
        check('comando: sin settings ni herramientas propias',
              valor('--setting-sources') == '' and valor('--tools') == ''
              and '--strict-mcp-config' in c and '--no-session-persistence' in c)
        check('comando: system prompt propio', valor('--system-prompt') == 'SYS')
        check('comando: no usa --bare (exige API key)', '--bare' not in c)
        check('comando: MCP por archivo con dontAsk', valor('--permission-mode') == 'dontAsk'
              and valor('--mcp-config', ).endswith('w-mcp.json'))
        check('comando: listas de herramientas', valor('--allowedTools') == 'a b'
              and valor('--disallowedTools') == 'c')
        sin_mcp = claude_code.SesionClaude('SYS', cwd=tmp).comando()
        check('comando sin MCP: sin permisos ni listas', '--permission-mode' not in sin_mcp
              and '--allowedTools' not in sin_mcp and '--mcp-config' not in sin_mcp)
        ws = claude_code.SesionClaude('SYS', cwd=tmp, herramientas='WebSearch',
                                      permitidas=['WebSearch']).comando()
        check('comando con WebSearch: la habilita y pide dontAsk',
              ws[ws.index('--tools') + 1] == 'WebSearch' and 'dontAsk' in ws
              and '--mcp-config' not in ws)
        check('cwd por defecto fuera del repo', 'robot_bob' not in claude_code.CWD_DEFAULT)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{'TODO OK' if fallos == 0 else f'{fallos} FALLA(S)'}")
    sys.exit(1 if fallos else 0)


if __name__ == '__main__':
    main()
