"""
test_fabrica_backlog.py — F1 de la fase F: el lector elige la siguiente tarea automática.

Qué prueba (texto de juguete, sin red, sin hardware):
  1. Solo entran las tareas pendientes [ ] con criterio.
  2. Las [x] y [~] no cuentan.
  3. Una tarea sin criterio no entra (F-D2).
  4. Un criterio que pide algo en vivo o la confirmación del dueño no es automático.
  5. siguiente() devuelve la primera automática, en el orden del plan.

Ejecutar:
  cd robot_bob
  python tests/unit/test_fabrica_backlog.py
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from fabrica import backlog as B

PLAN_JUGUETE = """# Plan de juguete

## Fase X

- [x] **X0. Ya hecha.** Nada que hacer.
  - Criterio: test de algo que ya pasó.
- [ ] **X1. Sin criterio.** Esta no entra.
- [ ] **X2. En vivo.** Probar con el robot.
  - Criterio: en vivo, desenchufar la cámara.
- [ ] **X3. Confirmación.** Pide permiso.
  - Criterio: confirmación del usuario.
- [~] **X4. A medias.** No cuenta.
  - Criterio: test automático.
- [ ] **X5. Automática.** Esta sí.
  - Criterio: test con un LLM falso. Corre sin red.
- [ ] **X6. Otra automática.** Después de X5.
  - Criterio: test de la otra.
"""

PLAN_SIN_AUTOMATICAS = """## Fase Y

- [ ] **Y1. En vivo.** Probar con el robot.
  - Criterio: en vivo, con el wifi apagado.
"""


def main() -> None:
    print('═' * 60)
    print('  TEST F1 — Lector del backlog (fábrica de Bob)')
    print('═' * 60)
    fallos = 0

    def check(nombre: str, ok: bool, detalle: str = '') -> None:
        nonlocal fallos
        if ok:
            print(f'  [OK] {nombre}')
        else:
            print(f'  [FALLO] {nombre} {detalle}')
            fallos += 1

    pendientes = [t.id for t in B.leer(PLAN_JUGUETE)]
    check('solo entran pendientes con criterio',
          pendientes == ['X2', 'X3', 'X5', 'X6'], f'-> {pendientes}')

    auto = {t.id: B.automatica(t) for t in B.leer(PLAN_JUGUETE)}
    check('en vivo y confirmación no son automáticas',
          auto == {'X2': False, 'X3': False, 'X5': True, 'X6': True}, f'-> {auto}')

    sig = B.siguiente(PLAN_JUGUETE)
    check('siguiente() devuelve X5 (primera automática en orden)',
          sig is not None and sig.id == 'X5' and sig.titulo == 'Automática',
          f'-> {sig}')

    check('sin automáticas, siguiente() devuelve None',
          B.siguiente(PLAN_SIN_AUTOMATICAS) is None)

    x5 = next(t for t in B.leer(PLAN_JUGUETE) if t.id == 'X5')
    check('la tarea guarda su detalle', x5.detalle == 'Esta sí.', f'-> {x5.detalle!r}')
    sig = B.siguiente(PLAN_JUGUETE, saltar={'x5'})
    check('saltar una tarea (ya tiene rama) pasa a la siguiente',
          sig is not None and sig.id == 'X6', f'-> {sig}')
    check('saltar todas las automáticas devuelve None',
          B.siguiente(PLAN_JUGUETE, saltar={'X5', 'x6'}) is None)

    print('\n' + '═' * 60)
    print(f'  RESULTADO: {"TODO OK" if fallos == 0 else f"{fallos} FALLO(S)"}')
    print('═' * 60)
    sys.exit(1 if fallos else 0)


if __name__ == '__main__':
    main()
