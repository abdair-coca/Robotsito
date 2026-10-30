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
