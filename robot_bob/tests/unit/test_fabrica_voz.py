"""
test_fabrica_voz.py — F6 por voz: "Bob, trabaja en tu lista", sin red ni Claude.

Reemplaza la fábrica por una falsa y a Bob por registros. Qué prueba:
  1. detectar() reconoce cómo se pide y no se dispara con frases parecidas.
  2. Con tarea: Bob la anuncia en el mismo turno y la trabaja en segundo plano; el aviso
     del final espera a que Bob esté libre (sin charla ni otra frase suelta).
  3. Sin tareas: Bob lo dice en el turno y no arranca nada en segundo plano.

  python tests/unit/test_fabrica_voz.py
"""
import os
import sys
import threading
import time
import types

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from agente import modo_dueno as M   # noqa: E402
from fabrica import backlog as B     # noqa: E402
from fabrica import ciclo as C       # noqa: E402

fallos = 0


def check(nombre: str, ok: bool, detalle: str = '') -> None:
    global fallos
    fallos += 0 if ok else 1
    print(f"  [{'OK' if ok else 'FALLO'}] {nombre} {'' if ok else detalle}", flush=True)


TAREA = B.Tarea('T1', 'Restar', 'test de restar')


class FabricaFalsa:
    """Como Fabrica, sin git ni Claude: elige (o no) y "trabaja" al instante."""

    def __init__(self, avisar, con_tarea):
        self.avisar, self.con_tarea = avisar, con_tarea
        self.trabajadas = []

    def elegir(self):
        if not self.con_tarea:
            self.avisar('sin_tareas', C.FRASES['sin_tareas'])
            return C.Resultado('sin_tareas')
        self.avisar('empiezo', C.FRASES['empiezo'].format(id='T1', titulo='Restar'))
        return C.Resultado('elegida', TAREA, 'bob/t1-restar')

    def trabajar(self, tarea):
        self.trabajadas.append(tarea.id)
        self.avisar('listo', C.FRASES['listo'].format(id='T1'))
        return C.Resultado('listo', tarea, 'bob/t1-restar')


def bob_falso():
    dichos = []
    sm = types.SimpleNamespace(en_conversacion=True, iniciar_hablando=lambda: None)
    vp = types.SimpleNamespace(_sm=sm, _muted=threading.Event(), _detener=threading.Event(),
                               _hablar=lambda texto, emo=None: dichos.append(('turno', texto)),
                               decir_soliloquio=lambda frase: dichos.append(('libre', frase)))
    return vp, dichos


def esperar(condicion, segundos):
    limite = time.monotonic() + segundos
    while time.monotonic() < limite:
        if condicion():
            return True
        time.sleep(0.1)
    return condicion()


def main() -> None:
    print('═' * 60)
    print('  TEST F6 — Fábrica por voz ("Bob, trabaja en tu lista")')
    print('═' * 60)

    # 1. Detección
    casos = {
        'Bob, trabaja en tu lista': 'fabrica',
        'trabaja en tu backlog': 'fabrica',
        'Bob, prográmate': 'fabrica',
        'ponte a trabajar en la lista': 'fabrica',
        '¿en qué trabajas?': None,
        'trabaja en tu tarea de inglés': None,
        'Bob, modo ingeniero': 'ingeniero',
    }
    malos = {}
    for texto, esperado in casos.items():
        d = M.detectar(texto)
        if (d[0] if d else None) != esperado:
            malos[texto] = d
    check('detectar reconoce el pedido y no se confunde', not malos, f'-> {malos}')

    original = C.Fabrica
    try:
        # 2. Con tarea: anuncia en el turno, trabaja en segundo plano, avisa al estar libre
        creadas = []
        C.Fabrica = lambda avisar: creadas.append(FabricaFalsa(avisar, True)) or creadas[-1]
        vp, dichos = bob_falso()
        M.trabajar_en_lista(vp)
        check('con tarea: la anuncia en el mismo turno',
              dichos[:1] == [('turno', C.FRASES['empiezo'].format(id='T1', titulo='Restar'))],
              f'-> {dichos}')
        trabajo = esperar(lambda: creadas[0].trabajadas == ['T1'], 3.0)
        time.sleep(1.5)
        check('con tarea: la trabaja en segundo plano y no habla en plena charla',
              trabajo and len(dichos) == 1, f'-> trabajadas={creadas[0].trabajadas} {dichos}')
        vp._sm.en_conversacion = False
        avisado = esperar(lambda: len(dichos) == 2, 3.0)
        check('con tarea: al quedar libre avisa que terminó',
              avisado and dichos[1][0] == 'libre' and 'Terminé la tarea T1' in dichos[1][1]
              and dichos[1][1].startswith('[EMO:ORGULLOSO]'), f'-> {dichos}')

        # 3. Sin tareas: lo dice en el turno, nada en segundo plano
        creadas.clear()
        C.Fabrica = lambda avisar: creadas.append(FabricaFalsa(avisar, False)) or creadas[-1]
        vp, dichos = bob_falso()
        vp._sm.en_conversacion = False
        M.trabajar_en_lista(vp)
        time.sleep(1.2)
        check('sin tareas: lo dice en el turno y no trabaja',
              dichos == [('turno', C.FRASES['sin_tareas'])] and not creadas[0].trabajadas,
              f'-> {dichos}')
    finally:
        C.Fabrica = original

    print('\n' + '═' * 60)
    print(f'  RESULTADO: {"TODO OK" if fallos == 0 else f"{fallos} FALLO(S)"}')
    print('═' * 60)
    sys.exit(1 if fallos else 0)


if __name__ == '__main__':
    main()
