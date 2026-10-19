"""
test_descompositor_live.py — Fase S1 en vivo: el descompositor con Groq real.

10 preguntas (6 de actualidad: deben buscar; 4 de concepto: no deben). Mide aciertos y
latencia por modelo. Gasta cuota de Groq: 10 llamadas por modelo y pasada.

  python tests/en_vivo/test_descompositor_live.py                      (modelo por defecto)
  python tests/en_vivo/test_descompositor_live.py openai/gpt-oss-20b qwen/qwen3.8-27b
"""
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from asistente import busqueda  # noqa: E402

CASOS = [
    ('¿Cuál es la noticia de tecnología más importante de hoy?', True),
    ('¿Elon Musk tiene algún auto que vuela?', True),
    ('¿Cuánto cuesta hoy el dólar en Bolivia?', True),
    ('¿Qué pasó con la inteligencia artificial esta semana?', True),
    ('¿Quién ganó las elecciones presidenciales más recientes en Bolivia?', True),
    ('¿Salió un nuevo modelo de Claude este mes?', True),
    ('¿Qué es un servo?', False),
    ('¿Cómo funciona el PWM en un ESP32?', False),
    ('¿Qué es una red neuronal convolucional?', False),
    ('Explícame la diferencia entre TCP y UDP.', False),
]


def correr(modelo: str) -> None:
    aciertos, tiempos, fallas = 0, [], []
    for pasada in (1, 2):
        for pregunta, esperado in CASOS:
            t0 = time.monotonic()
            res = busqueda.descomponer(pregunta, modelo=modelo)
            tiempos.append(time.monotonic() - t0)
            if res is None:
                fallas.append((pregunta, 'None (inválido o error)'))
                continue
            if res['buscar'] == esperado:
                aciertos += 1
            else:
                fallas.append((pregunta, f'buscar={res["buscar"]}, consultas={res["consultas"]}'))
    total = 2 * len(CASOS)
    print(f'\n=== {modelo}: {aciertos}/{total} aciertos', flush=True)
    print(f'    latencia: mediana {statistics.median(tiempos):.2f} s, '
          f'máx {max(tiempos):.2f} s', flush=True)
    for pregunta, detalle in fallas:
        print(f'    FALLA: "{pregunta}" → {detalle}', flush=True)


if __name__ == '__main__':
    modelos = sys.argv[1:] or [busqueda.DESCOMPONER_MODELO]
    for m in modelos:
        correr(m)
