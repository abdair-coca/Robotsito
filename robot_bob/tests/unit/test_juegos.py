"""
test_juegos.py — J1: adivinanzas y trivia con un LLM falso.

Qué prueba (sin red, sin hardware):
  1. proponer() pide la pregunta al LLM con el tipo correcto y deja la actual.
  2. Una respuesta correcta suma un punto; la comparación ignora tildes, mayúsculas y artículos.
  3. Una respuesta incorrecta no suma y dice la respuesta correcta.
  4. Responder sin pregunta no suma ni rompe.
  5. Una salida del LLM sin formato no deja pregunta a medias.
  6. Una coincidencia parcial de palabra ("sol" en "solar") no cuenta.
  7. Un tipo desconocido se rechaza.
  8. Si el LLM cae, el turno no se tumba: Bob lo dice y sigue.
  9. Una salida con negritas (**PREGUNTA:**) se parsea igual.
  10. "no sé" no cuenta como acierto.
  11. "ocho" acierta contra la respuesta "8".
  12. El pedido al LLM prohíbe preguntas de sí o no.

Ejecutar:
  cd robot_bob
  python tests/unit/test_juegos.py
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from asistente import juegos as J


class LLMFalso:
    """Devuelve las salidas en orden y guarda los prompts que recibió."""

    def __init__(self, *salidas: str) -> None:
        self._salidas = list(salidas)
        self.prompts: list = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self._salidas.pop(0)


ADIVINANZA = 'PREGUNTA: ¿Qué tiene dientes y no muerde?\nRESPUESTA: El peine'


def main() -> None:
    print('═' * 60)
    print('  TEST J1 — Adivinanzas y trivia (LLM falso)')
    print('═' * 60)
    fallos = 0

    def check(nombre: str, ok: bool, detalle: str = '') -> None:
        nonlocal fallos
        if ok:
            print(f'  [OK] {nombre}')
        else:
            print(f'  [FALLO] {nombre} {detalle}')
            fallos += 1

    # 1. Propone con el tipo correcto
    llm = LLMFalso(ADIVINANZA, ADIVINANZA)
    juego = J.Juego(llm)
    dicho = juego.proponer('adivinanza')
    check('proponer devuelve la pregunta y la deja como actual',
          dicho == '¿Qué tiene dientes y no muerde?' and juego.actual is not None
          and juego.actual.respuesta == 'El peine', f'-> {dicho!r}')
    check('el prompt pide una adivinanza', 'adivinanza' in llm.prompts[0])

    # 2. Acierto, sin mayúsculas, tildes ni artículo
    r = juego.responder('peine')
    check('"peine" acierta y suma 1',
          juego.puntos == 1 and juego.actual is None and '¡Correcto!' in r, f'-> {r!r}')

    juego.proponer('adivinanza')
    juego.responder('creo que es el PEINE.')
    check('"el PEINE." con frase alrededor también acierta', juego.puntos == 2,
          f'puntos={juego.puntos}')

    # 3. Fallo: no suma y dice la respuesta
    llm2 = LLMFalso('PREGUNTA: ¿Cuál es el color del cielo?\nRESPUESTA: Azul')
    j2 = J.Juego(llm2)
    j2.proponer('trivia')
    r = j2.responder('verde')
    check('respuesta incorrecta no suma y dice la correcta',
          j2.puntos == 0 and 'Era "Azul"' in r and 'Llevas 0' in r, f'-> {r!r}')
    check('el prompt de trivia pide trivia', 'trivia' in llm2.prompts[0])

    # 4. Responder sin pregunta
    j3 = J.Juego(LLMFalso())
    r = j3.responder('peine')
    check('responder sin pregunta no suma', j3.puntos == 0 and 'Todavía no hay pregunta' in r)

    # 5. Salida sin formato
    j4 = J.Juego(LLMFalso('¡Hola! Aquí va algo bonito.'))
    r = j4.proponer('adivinanza')
    check('salida sin formato deja la pregunta vacía y avisa',
          j4.actual is None and 'Se me olvidó' in r, f'-> {r!r}')

    # 6. Palabra suelta dentro de otra no cuenta
    j5 = J.Juego(LLMFalso('PREGUNTA: ¿Qué estrella nos da luz?\nRESPUESTA: sol'))
    j5.proponer('trivia')
    j5.responder('estoy pensando en el solar')
    check('"sol" dentro de "solar" no cuenta', j5.puntos == 0, f'puntos={j5.puntos}')

    # 8. El LLM cae: no tumba el turno
    def llm_caido(prompt: str) -> str:
        raise TimeoutError('sin red')

    j6 = J.Juego(llm_caido)
    try:
        r = j6.proponer('adivinanza')
        check('LLM caído no tumba el turno y avisa',
              j6.actual is None and 'no puedo pensar' in r.lower(), f'-> {r!r}')
    except Exception as e:
        check('LLM caído no tumba el turno y avisa', False, f'(lanzó {type(e).__name__})')

    # 9. Formato con negritas
    j7 = J.Juego(LLMFalso('**PREGUNTA:** ¿Qué tiene dientes y no muerde?\n**RESPUESTA:** El peine'))
    r = j7.proponer('adivinanza')
    check('formato con negritas se parsea',
          r == '¿Qué tiene dientes y no muerde?' and j7.actual is not None
          and j7.actual.respuesta == 'El peine', f'-> {r!r}')

    # 10. "no sé" no es acierto
    j8 = J.Juego(LLMFalso('PREGUNTA: ¿Qué tiene dientes?\nRESPUESTA: El peine'))
    j8.proponer('adivinanza')
    r = j8.responder('no sé, creo que el peine')
    check('"no sé" no cuenta como acierto', j8.puntos == 0 and 'No era' in r, f'puntos={j8.puntos}')

    # 11. Cifras contra palabras
    j9 = J.Juego(LLMFalso('PREGUNTA: ¿Cuántas patas tiene una araña?\nRESPUESTA: 8'))
    j9.proponer('trivia')
    j9.responder('ocho')
    check('"ocho" acierta contra la respuesta "8"', j9.puntos == 1, f'puntos={j9.puntos}')

    # 12. El pedido al LLM prohíbe sí/no
    llm_p = LLMFalso(ADIVINANZA)
    J.Juego(llm_p).proponer('adivinanza')
    check('el pedido prohíbe preguntas de sí o no', 'sí o no' in llm_p.prompts[0])

    # 7. Tipo desconocido
    try:
        J.Juego(LLMFalso()).proponer('chiste')
        check('tipo desconocido se rechaza', False, '(no lanzó ValueError)')
    except ValueError:
        check('tipo desconocido se rechaza', True)

    print('\n' + '═' * 60)
    print(f'  RESULTADO: {"TODO OK" if fallos == 0 else f"{fallos} FALLO(S)"}')
    print('═' * 60)
    sys.exit(1 if fallos else 0)


if __name__ == '__main__':
    main()
