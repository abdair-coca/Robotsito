"""
python -m fabrica — un ciclo de la fábrica desde la terminal (F6).

Toma la siguiente tarea automática de .dev/PLAN.md, la programa en su rama, la prueba y
la commitea. Sale con 0 si terminó una tarea o no había nada que hacer, y con 1 si no.

  cd robot_bob
  python -m fabrica
"""
import sys

from fabrica.ciclo import Fabrica


def main() -> int:
    try:                     # la consola de Windows (cp1252) no imprime todo lo que dice Claude
        sys.stdout.reconfigure(errors='replace')
    except Exception:
        pass
    r = Fabrica(avisar=lambda evento, texto: print(f'[fábrica] {texto}', flush=True)).ciclo()
    if r.rama:
        print(f'[fábrica] rama {r.rama}' + (f', commit {r.commit}' if r.commit else ''), flush=True)
    return 0 if r.resultado in ('listo', 'sin_tareas') else 1


if __name__ == '__main__':
    sys.exit(main())
