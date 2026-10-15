"""
test_jev_decision.py — Pruebas de jev_decision.decidir() con _post simulado.

No llama a la red. Cada petición lleva una sola pregunta (ruta o emoción).
Cubre éxito, cuerpo de cada pregunta, timeout, error HTTP, respuestas
inválidas, fallo parcial, plazo total y clave ausente.

  python tests/test_jev_decision.py
"""
import os
import socket
import sys
import time
import urllib.error

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import jev_decision as jd   # noqa: E402

fallos = 0


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    global fallos
    fallos += 0 if ok else 1
    print(f"[{'OK' if ok else 'FALLA'}] {nombre} {detalle}", flush=True)


def responder(cuerpo: dict, ruta: str = "charla", emocion: str = "CURIOSO", conf: float = 0.9) -> dict:
    """Respuesta simulada solo con la(s) pregunta(s) que trae el cuerpo."""
    valores = {"ruta": ruta, "emocion": emocion}
    return {"answers": {clave: {"type": "choice", "choice": valores[clave], "confidence": conf}
                        for clave in cuerpo["questions"]}}


def con_post(simulado):
    original = jd._post
    jd._post = simulado
    return original


clave_original = jd.TYPESAFE_API_KEY
jd.TYPESAFE_API_KEY = "clave-de-prueba"
original = None
try:
    # 1) Éxito: ambas partes válidas
    original = con_post(lambda c: responder(c, "musica", "TRAVIESO", 0.8))
    d = jd.decidir("pon música de Shakira", "¡Claro!")
    check("éxito: ruta y emoción", d.ruta == "musica" and d.emocion == "TRAVIESO" and not d.respaldo,
          f"-> ruta={d.ruta} emocion={d.emocion} respaldo={d.respaldo}")
    check("éxito: confianzas leídas", d.confianza_ruta == 0.8 and d.confianza_emocion == 0.8)

    # 2) Cuerpo: cada petición lleva UNA pregunta con sus criterios y el último turno
    cuerpos = []
    con_post(lambda c: (cuerpos.append(c), responder(c))[1])
    jd.decidir("¿qué es un robot?", "Soy Bob.")
    preguntas = [list(c["questions"]) for c in cuerpos]
    check("cuerpo: dos peticiones, una pregunta cada una",
          sorted(map(tuple, preguntas)) == [("emocion",), ("ruta",)], f"-> {preguntas}")
    criterios_ok = all(
        set(c["questions"][k]["criteria"]) == (set(jd.RUTAS) if k == "ruta" else set(jd.EMOCIONES))
        for c in cuerpos for k in c["questions"])
    check("cuerpo: criterios completos", criterios_ok)
    check("cuerpo: último turno en el estado",
          all(c["state"].get("last_bob_turn") == "Soy Bob." for c in cuerpos))

    # 3) Timeout en ambas -> respaldo completo, a tiempo
    jd.JEV_TIMEOUT_S = 0.3
    con_post(lambda c: (time.sleep(2.0), responder(c))[1])
    t0 = time.monotonic(); d = jd.decidir("hola"); tardo = time.monotonic() - t0
    jd.JEV_TIMEOUT_S = 1.0
    check("timeout en ambas: respaldo completo a tiempo",
          d.respaldo and d.ruta is None and d.emocion == jd.EMOCION_RESPALDO and tardo < 1.0,
          f"-> tardó {tardo:.2f}s, error={d.error[:70]}")

    # 4) Error HTTP en ambas -> respaldo
    def http500(c):
        raise urllib.error.HTTPError(jd.ENDPOINT, 500, "server error", None, None)
    con_post(http500)
    d = jd.decidir("hola")
    check("HTTP 500: respaldo", d.respaldo and d.ruta is None and d.emocion == jd.EMOCION_RESPALDO)

    # 5) Ruta fuera del esquema, emoción válida -> ruta respaldo, emoción de JEV
    con_post(lambda c: responder(c, "inventada", "AMOR"))
    d = jd.decidir("hola")
    check("ruta inválida: ruta a respaldo y emoción de JEV",
          d.ruta is None and d.emocion == "AMOR" and d.respaldo)

    # 6) Emoción fuera de la lista (PENSANDO), ruta válida -> emoción CURIOSO
    con_post(lambda c: responder(c, "charla", "PENSANDO"))
    d = jd.decidir("hola")
    check("emoción fuera de la lista: CURIOSO y ruta de JEV",
          d.emocion == jd.EMOCION_RESPALDO and d.ruta == "charla" and d.respaldo)

    # 7) Fallo parcial por plazo: ruta a tiempo, emoción tarda
    def emocion_lenta(c):
        if "emocion" in c["questions"]:
            time.sleep(2.0)
        return responder(c, "musica")
    jd.JEV_TIMEOUT_S = 0.3
    con_post(emocion_lenta)
    t0 = time.monotonic(); d = jd.decidir("pon algo de Queen"); tardo = time.monotonic() - t0
    jd.JEV_TIMEOUT_S = 1.0
    check("fallo parcial: ruta de JEV, emoción CURIOSO, a tiempo",
          d.ruta == "musica" and d.emocion == jd.EMOCION_RESPALDO and d.respaldo and tardo < 1.0,
          f"-> tardó {tardo:.2f}s")

    # 8) Respuesta sin 'answers' -> respaldo
    con_post(lambda c: {"model": "jev-1.13.0"})
    d = jd.decidir("hola")
    check("sin answers: respaldo", d.respaldo and d.ruta is None)

    # 9) Clave ausente: no llama a la red
    llamadas = []
    con_post(lambda c: llamadas.append(1) or responder(c))
    jd.TYPESAFE_API_KEY = ""
    d = jd.decidir("hola")
    check("sin clave: respaldo sin llamar", d.respaldo and not llamadas, f"-> {d.error}")
    jd.TYPESAFE_API_KEY = "clave-de-prueba"
finally:
    if original is not None:
        jd._post = original
    jd.TYPESAFE_API_KEY = clave_original
    jd.JEV_TIMEOUT_S = 1.0

print(f"\nRESULTADO: {'TODO OK' if fallos == 0 else f'{fallos} FALLA(S)'}", flush=True)
sys.exit(0 if fallos == 0 else 1)
