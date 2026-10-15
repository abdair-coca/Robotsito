"""
test_jev_live.py — Prueba en vivo de jev_decision.decidir() contra JEV.

Necesita internet y TYPESAFE_API_KEY en robot_bob/.env. Llama de verdad al
servicio. Para la prueba de respaldo, apaga el WiFi y vuelve a correrla: debe
terminar sin cuelgue, con respaldo=True y en menos de 1 s.

  python tests/test_jev_live.py
"""
import os
import statistics
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import jev_decision as jd   # noqa: E402

CASOS = [
    ("pon algo de Queen", "musica"),
    ("me siento muy solo hoy", "charla"),
    ("¿qué hora es?", "asistente"),
    ("recuérdame tomar agua en 5 minutos", "recordatorio"),
    ("cuéntame un chiste", "charla"),
    ("gracias, eres genial", "charla"),
    ("pausa la música", "musica"),
    ("¿va a llover mañana?", "asistente"),
    ("nos vemos mañana en la cafetería", "ajeno"),
]


def main() -> int:
    if not jd.TYPESAFE_API_KEY:
        print("FALLA: falta TYPESAFE_API_KEY en robot_bob/.env")
        return 1
    print(f"modelo: {jd.JEV_MODEL} · plazo: {jd.JEV_TIMEOUT_S} s\n")
    aciertos, respaldos, latencias = 0, 0, []
    for frase, esperado in CASOS:
        d = jd.decidir(frase, "")
        latencias.append(d.latencia_s)
        respaldos += d.respaldo
        ok = d.ruta == esperado
        aciertos += ok
        print(f"{'OK ' if ok else 'MAL'} {frase!r:40} ruta={str(d.ruta):11} "
              f"emo={d.emocion:11} lat={d.latencia_s:.2f}s respaldo={d.respaldo}"
              + (f" [{d.error[:60]}]" if d.error else ""), flush=True)

    mediana = statistics.median(latencias)
    print(f"\naciertos de ruta: {aciertos}/{len(CASOS)} · respaldos: {respaldos}/{len(CASOS)} · "
          f"latencia mediana {mediana:.2f}s · máxima {max(latencias):.2f}s")
    ok = respaldos == 0 and aciertos == len(CASOS)
    print(f"RESULTADO: {'TODO OK' if ok else 'REVISAR'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
