"""
test_fabrica_live.py — La fábrica con Claude real (Sonnet) en un repo de juguete (fase F).

Arma el mismo repo temporal que test_fabrica_ciclo (calc.py, su test y la tarea "restar")
y corre un ciclo completo con Claude Code de verdad: programa en el worktree pidiendo
permiso por cada Edit y Write, las compuertas corren los tests y otro Claude revisa el diff.
Gasta dos sesiones de Claude (Sonnet, suscripción Pro) y unos minutos.

  PYTHONIOENCODING=utf-8 python tests/en_vivo/test_fabrica_live.py
"""
import os
import sys
import tempfile
import time

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(AQUI, '..', '..')))
sys.path.insert(0, os.path.abspath(os.path.join(AQUI, '..', 'unit')))

from agente import claude_code          # noqa: E402
from fabrica import ciclo as C          # noqa: E402
import test_fabrica_ciclo as T          # noqa: E402  (repo de juguete, git, borrar)

fallos = 0


def check(nombre: str, ok: bool, detalle: str = '') -> None:
    global fallos
    fallos += 0 if ok else 1
    print(f"  [{'OK' if ok else 'FALLO'}] {nombre} {'' if ok else detalle}", flush=True)


def main() -> None:
    if not claude_code.disponible():
        print('No está el CLI de Claude Code (claude): no se puede probar en vivo.')
        sys.exit(1)
    tmp = tempfile.mkdtemp(prefix='fabrica_live_')
    try:
        repo = T.repo_juguete(tmp)
        eventos = []
        f = C.Fabrica(repo=repo, base='base', apagada=lambda: False,
                      avisar=lambda evento, texto: (eventos.append(evento),
                                                    print(f'[aviso] {texto}', flush=True)))
        t0 = time.monotonic()
        r = f.ciclo()
        print(f'\n[{time.monotonic() - t0:.0f} s] resultado={r.resultado} rama={r.rama} '
              f'commit={r.commit} motivo={r.motivo}\n')
        with open(os.path.join(r.carpeta, 'informe.md'), encoding='utf-8') as fh:
            print(fh.read())
        check('resultado listo', r.resultado == 'listo', f'-> {r.resultado} ({r.motivo})')
        if r.commit:
            archivos = T.git(repo, 'diff', '--name-only', 'base', r.rama).split()
            check('el commit trae calc.py y un test de tests/unit',
                  'robot_bob/calc.py' in archivos
                  and any(a.startswith('robot_bob/tests/unit/test_') for a in archivos),
                  f'-> {archivos}')
            check('nada fuera de robot_bob ni la configuración',
                  all(a.startswith('robot_bob/') and not a.endswith('config.py') for a in archivos),
                  f'-> {archivos}')
            autor = T.git(repo, 'log', '-1', '--format=%an <%ae>', r.rama).strip()
            check('commit de "Bob"', autor == 'Bob <cocaabdair@gmail.com>', f'-> {autor}')
        check('avisos: empiezo y listo', eventos == ['empiezo', 'listo'], f'-> {eventos}')
    finally:
        T.borrar(tmp)

    print('\n' + '═' * 60)
    print(f'  RESULTADO: {"TODO OK" if fallos == 0 else f"{fallos} FALLO(S)"}')
    print('═' * 60)
    sys.exit(1 if fallos else 0)


if __name__ == '__main__':
    main()
