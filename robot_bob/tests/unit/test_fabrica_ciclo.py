"""
test_fabrica_ciclo.py — F2 a F6 de la fábrica en un repo de juguete, con un Claude falso.

Arma un repo git temporal (robot_bob/calc.py, su test y un .dev/PLAN.md con una tarea) y
corre ciclos de la fábrica con un Claude falso que edita pasando por el control de permisos,
como el de verdad. Sin red y sin gastar cupo. Qué prueba:
  1. Camino feliz: rama bob/t1-restar con un commit de "Bob" que trae solo los archivos de
     la tarea; config.py llega al worktree pero no se commitea; el worktree se borra; la rama
     base no cambia; quedan registro, informe, línea en TASK.md y avisos.
  2. Una tarea con rama no se reintenta: el ciclo siguiente no tiene tareas.
  3. Sin test en tests/unit, no hay commit.
  4. Si rompe un test que pasaba, no hay commit.
  5. Si su propio test falla, no hay commit.
  6. Si el revisor rechaza, no hay commit.
  7. config.py, .env, requirements.txt y lo de fuera de robot_bob se niegan.
  8. Frenos: tope del día, perfil feria y una tarea a la vez.
  9. Ayudantes: veredicto del revisor, nombre de la rama, zonas de la fábrica.

  python tests/unit/test_fabrica_ciclo.py
"""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from fabrica import backlog as B   # noqa: E402
from fabrica import ciclo as C     # noqa: E402

fallos = 0


def check(nombre: str, ok: bool, detalle: str = '') -> None:
    global fallos
    fallos += 0 if ok else 1
    print(f"  [{'OK' if ok else 'FALLO'}] {nombre} {'' if ok else detalle}", flush=True)


ENCABEZADO_TEST = ("import os, sys\n"
                   "sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "
                   "'..', '..')))\nimport calc\n")
TEST_CALC = ENCABEZADO_TEST + ("ok = calc.sumar(2, 2) == 4\nprint('[OK] sumar' if ok else "
                               "'[FALLO] sumar')\nsys.exit(0 if ok else 1)\n")
TEST_RESTAR = ENCABEZADO_TEST + ("ok = calc.restar(5, 3) == 2\nprint('TODO OK' if ok else "
                                 "'[FALLO] restar')\nsys.exit(0 if ok else 1)\n")
TEST_RESTAR_MALO = TEST_RESTAR.replace('== 2', '== 99')
CALC = 'def sumar(a, b):\n    return a + b\n'
CALC_CON_RESTAR = CALC + '\n\ndef restar(a, b):\n    return a - b\n'
CALC_ROTO = CALC_CON_RESTAR.replace('return a + b', 'return a - b')
PLAN = ('# Plan\n\n- [ ] **T1. Restar.** Agregar restar(a, b) a calc.py.\n'
        '  - Criterio: test que verifica restar(5, 3) == 2.\n')


def git(repo, *args):
    r = subprocess.run(['git', '-C', repo, *args], capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    if r.returncode != 0:
        raise RuntimeError(r.stderr)
    return r.stdout


def escribir(ruta, texto):
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    with open(ruta, 'w', encoding='utf-8') as f:
        f.write(texto)


def borrar(carpeta):
    def solo_lectura(func, ruta, _):      # git deja sus objetos en solo lectura en Windows
        os.chmod(ruta, stat.S_IWRITE)
        func(ruta)
    shutil.rmtree(carpeta, onerror=solo_lectura)


def repo_juguete(tmp):
    repo = os.path.join(tmp, 'repo')
    escribir(os.path.join(repo, 'robot_bob', 'calc.py'), CALC)
    escribir(os.path.join(repo, 'robot_bob', 'tests', 'unit', 'test_calc.py'), TEST_CALC)
    escribir(os.path.join(repo, '.gitignore'),
             '.fabrica/\n.dev/\n__pycache__/\nrobot_bob/config.py\nrobot_bob/logs/\n')
    subprocess.run(['git', 'init', '-q', '-b', 'base', repo], check=True)
    git(repo, 'add', '.')
    git(repo, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '-m', 'inicio')
    escribir(os.path.join(repo, '.dev', 'PLAN.md'), PLAN)
    escribir(os.path.join(repo, '.dev', 'TASK.md'), '# Current Task\n')
    escribir(os.path.join(repo, 'robot_bob', 'config.py'), 'PERFIL = "normal"\n')  # gitignorado
    return repo


class ClaudeFalso:
    """Hace de Claude: edita con el mismo control de permisos que el de verdad."""

    def __init__(self, aprobar, ediciones, respuesta):
        self.aprobar, self.ediciones, self.respuesta = aprobar, ediciones, respuesta
        self.negados = []

    def preguntar(self, texto, timeout=0.0, max_herramientas=None):
        for herramienta, ruta, contenido in self.ediciones:
            decision = self.aprobar(herramienta, {'file_path': ruta})
            if decision is True and isinstance(contenido, bytes):   # tal cual, sin pasar a CRLF
                with open(ruta, 'wb') as f:
                    f.write(contenido)
            elif decision is True:
                escribir(ruta, contenido)
            else:
                self.negados.append(os.path.basename(ruta))
        return self.respuesta

    def cerrar(self):
        pass


def fabrica(repo, guion, revision='APROBADO', **kw):
    """guion(raiz_wt) → lista de (herramienta, ruta, contenido) que hace el Claude falso."""
    eventos, sesiones = [], {}

    def crear_sesion(prompt, raiz, aprobar=None, edicion=True):
        if edicion:
            s = ClaudeFalso(aprobar, guion(raiz), 'calc.py y test_restar.py')
            s.vio_config = os.path.exists(os.path.join(raiz, 'config.py'))
        else:
            s = ClaudeFalso(None, [], revision)
        sesiones['impl' if edicion else 'rev'] = s
        return s

    kw.setdefault('apagada', lambda: False)
    f = C.Fabrica(repo=repo, base='base', crear_sesion=crear_sesion,
                  avisar=lambda evento, texto: eventos.append(evento), **kw)
    return f, eventos, sesiones


def bueno(raiz):
    return [('Write', os.path.join(raiz, 'calc.py'), CALC_CON_RESTAR),
            ('Write', os.path.join(raiz, 'tests', 'unit', 'test_restar.py'), TEST_RESTAR)]


def sin_commit(repo, rama):
    """La rama quedó (la tarea no se reintenta) pero sin commits nuevos."""
    return git(repo, 'rev-parse', rama).strip() == git(repo, 'rev-parse', 'base').strip()


def escenario(nombre, guion, esperado, revision='APROBADO'):
    tmp = tempfile.mkdtemp(prefix='fabrica_')
    try:
        repo = repo_juguete(tmp)
        f, eventos, _ = fabrica(repo, guion, revision)
        r = f.ciclo()
        check(f'{nombre}: resultado {esperado}, sin commit y avisa',
              r.resultado == esperado and sin_commit(repo, r.rama) and eventos[-1] == 'fallo',
              f'-> {r.resultado} ({r.motivo}) eventos={eventos}')
        return r
    finally:
        borrar(tmp)


def main() -> None:
    print('═' * 60)
    print('  TEST F2–F6 — Ciclo de la fábrica (repo de juguete, Claude falso)')
    print('═' * 60)

    # 1–2. Camino feliz y no reintentar
    tmp = tempfile.mkdtemp(prefix='fabrica_')
    try:
        repo = repo_juguete(tmp)
        base_antes = git(repo, 'rev-parse', 'base').strip()
        f, eventos, ses = fabrica(repo, bueno)
        r = f.ciclo()
        check('feliz: resultado listo', r.resultado == 'listo', f'-> {r.resultado} ({r.motivo})')
        check('feliz: rama bob/t1-restar', r.rama == 'bob/t1-restar', f'-> {r.rama}')
        autor = git(repo, 'log', '-1', '--format=%an <%ae>', r.rama).strip()
        check('feliz: commit de "Bob" con el correo del dueño',
              autor == 'Bob <cocaabdair@gmail.com>', f'-> {autor}')
        archivos = sorted(git(repo, 'diff', '--name-only', 'base', r.rama).split())
        check('feliz: el commit trae solo los archivos de la tarea',
              archivos == ['robot_bob/calc.py', 'robot_bob/tests/unit/test_restar.py'],
              f'-> {archivos}')
        mensaje = git(repo, 'log', '-1', '--format=%s', r.rama).strip()
        check('feliz: mensaje del commit', mensaje == 'feat(t1): restar', f'-> {mensaje}')
        check('feliz: config.py llegó al worktree (y no está en el commit)', ses['impl'].vio_config)
        worktrees = git(repo, 'worktree', 'list').strip().splitlines()
        check('feliz: el worktree se borró', len(worktrees) == 1, f'-> {worktrees}')
        check('feliz: la rama base no cambió', git(repo, 'rev-parse', 'base').strip() == base_antes)
        with open(os.path.join(repo, 'robot_bob', 'logs', 'fabrica', 'registro.jsonl'),
                  encoding='utf-8') as fh:
            registro = [json.loads(x) for x in fh if x.strip()]
        check('feliz: registro e informe', registro[-1]['resultado'] == 'listo'
              and os.path.exists(os.path.join(r.carpeta, 'informe.md')), f'-> {registro}')
        with open(os.path.join(repo, '.dev', 'TASK.md'), encoding='utf-8') as fh:
            check('feliz: línea en TASK.md', 'bob/t1-restar' in fh.read())
        check('feliz: avisa al empezar y al terminar', eventos == ['empiezo', 'listo'],
              f'-> {eventos}')
        check('feliz: el revisor recibió el diff', 'rev' in ses)

        f2, eventos2, _ = fabrica(repo, bueno)
        r2 = f2.ciclo()
        check('con rama: la tarea no se reintenta', r2.resultado == 'sin_tareas'
              and eventos2 == ['sin_tareas'], f'-> {r2.resultado} {eventos2}')
    finally:
        borrar(tmp)

    # 3–6. Compuertas que frenan el commit
    escenario('sin test', lambda raiz: bueno(raiz)[:1], 'sin_test')
    escenario('rompe un test que pasaba',
              lambda raiz: [('Write', os.path.join(raiz, 'calc.py'), CALC_ROTO), bueno(raiz)[1]],
              'rompe')
    escenario('su test falla',
              lambda raiz: [bueno(raiz)[0], ('Write', os.path.join(raiz, 'tests', 'unit',
                                                                   'test_restar.py'),
                                             TEST_RESTAR_MALO)], 'test_falla')
    r = escenario('el revisor rechaza', bueno, 'rechazado',
                  revision='**RECHAZADO**: el test no prueba nada')

    # 6b. Saltos de línea: el repo en CRLF y Claude escribe en LF (visto en la prueba en vivo)
    tmp = tempfile.mkdtemp(prefix='fabrica_')
    try:
        repo = repo_juguete(tmp)
        f, eventos, ses = fabrica(repo, lambda raiz: [
            ('Write', os.path.join(raiz, 'calc.py'), CALC_CON_RESTAR.encode('utf-8')),
            bueno(raiz)[1]])
        r = f.ciclo()
        with open(os.path.join(r.carpeta, 'informe.md'), encoding='utf-8') as fh:
            informe = fh.read()
        check('saltos de línea: el diff muestra solo el cambio real',
              r.resultado == 'listo' and '+def restar(a, b):' in informe
              and '-def sumar(a, b):' not in informe, f'-> {r.resultado}')
    finally:
        borrar(tmp)

    # 7. Zonas prohibidas
    tmp = tempfile.mkdtemp(prefix='fabrica_')
    try:
        repo = repo_juguete(tmp)
        f, eventos, ses = fabrica(repo, lambda raiz: [
            ('Write', os.path.join(raiz, 'config.py'), 'PERFIL = "feria"\n'),
            ('Write', os.path.join(raiz, '.env'), 'CLAVE=1\n'),
            ('Write', os.path.join(raiz, 'requirements.txt'), 'requests\n'),
            ('Write', os.path.join(raiz, '..', 'fuera.py'), 'x = 1\n'),
            ('Bash', os.path.join(raiz, 'calc.py'), CALC_ROTO)])
        r = f.ciclo()
        negados = sorted(ses['impl'].negados)
        check('prohibidos: se niegan config.py, .env, requirements, fuera de robot_bob y Bash',
              negados == sorted(['config.py', '.env', 'requirements.txt', 'fuera.py', 'calc.py']),
              f'-> {negados}')
        with open(os.path.join(repo, 'robot_bob', 'config.py'), encoding='utf-8') as fh:
            check('prohibidos: sin cambios, y el config.py real sigue igual',
                  r.resultado == 'sin_cambios' and 'normal' in fh.read(), f'-> {r.resultado}')
    finally:
        borrar(tmp)

    # 8. Frenos
    tmp = tempfile.mkdtemp(prefix='fabrica_')
    try:
        repo = repo_juguete(tmp)
        registro = os.path.join(repo, 'robot_bob', 'logs', 'fabrica', 'registro.jsonl')
        hoy = time.strftime('%Y-%m-%d')
        escribir(registro, ''.join(json.dumps({'fecha': hoy, 'resultado': 'rechazado'}) + '\n'
                                   for _ in range(C.MAX_POR_DIA)))
        f, eventos, _ = fabrica(repo, bueno)
        r = f.ciclo()
        ramas = git(repo, 'branch', '--list', 'bob/*').strip()
        check('tope del día: no trabaja y no crea rama', r.resultado == 'tope' and not ramas
              and eventos == ['tope'], f'-> {r.resultado} ramas={ramas!r}')
        os.remove(registro)

        f, eventos, _ = fabrica(repo, bueno, apagada=lambda: True)
        check('perfil feria: apagada', f.ciclo().resultado == 'apagada' and eventos == ['apagada'])

        f, eventos, _ = fabrica(repo, bueno)
        C._LOCK.acquire()
        try:
            r = f.ciclo()
        finally:
            C._LOCK.release()
        check('una tarea a la vez: ocupada', r.resultado == 'ocupada' and eventos == ['ocupada'],
              f'-> {r.resultado}')
    finally:
        borrar(tmp)

    # 9. Ayudantes
    check('veredicto: APROBADO con markdown', C.veredicto('**APROBADO**\nbien hecho')[0])
    check('veredicto: RECHAZADO con motivo', C.veredicto('RECHAZADO: falta el test') ==
          (False, 'falta el test.'))
    check('veredicto: sin respuesta o sin veredicto no aprueba',
          not C.veredicto(None)[0] and not C.veredicto('Creo que está bien')[0])
    tarea = B.Tarea('J1', 'Adivinanzas y trivia por LLM', 'test')
    check('nombre de rama', C.nombre_rama(tarea) == 'bob/j1-adivinanzas-y-trivia-por-llm',
          f'-> {C.nombre_rama(tarea)}')
    raiz = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
    check('zonas de la fábrica: reminders.db y requirements',
          C.prohibido_fabrica(os.path.join(raiz, 'reminders.db'), raiz) is not None
          and C.prohibido_fabrica(os.path.join(raiz, 'requirements.txt'), raiz) is not None
          and C.prohibido_fabrica(os.path.join(raiz, 'calc.py'), raiz) is None)

    print('\n' + '═' * 60)
    print(f'  RESULTADO: {"TODO OK" if fallos == 0 else f"{fallos} FALLO(S)"}')
    print('═' * 60)
    sys.exit(1 if fallos else 0)


if __name__ == '__main__':
    main()
