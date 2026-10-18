"""
test_jev_decision.py — Pruebas de jev_decision.decidir() con el cliente del SDK simulado.

No llama a la red. Reemplaza _get_cliente() por un cliente falso que registra
cada llamada a system_one. Cubre éxito, cuerpo de cada pregunta, timeout,
error del SDK, respuestas inválidas, fallo parcial, plazo total y clave ausente.

  python tests/test_jev_decision.py
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import jev_decision as jd   # noqa: E402

jd.LOG_DECISIONES = os.path.join(tempfile.mkdtemp(), "decisiones.jsonl")   # no tocar logs/ reales

fallos = 0


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    global fallos
    fallos += 0 if ok else 1
    print(f"[{'OK' if ok else 'FALLA'}] {nombre} {detalle}", flush=True)


class Respuesta:
    def __init__(self, choice, confidence):
        self.choice = choice
        self.confidence = confidence


class Resultado:
    def __init__(self, answers):
        self.answers = answers


class ClienteFalso:
    """Imita TypeSafeClient.system_one: registra kwargs y devuelve lo que diga la función."""
    def __init__(self, fn):
        self.fn = fn
        self.llamadas = []

    def system_one(self, **kwargs):
        self.llamadas.append(kwargs)
        return self.fn(kwargs)


def responder(kwargs, ruta="charla", emocion="CURIOSO", conf=0.9):
    valores = {"ruta": ruta, "emocion": emocion}
    return Resultado({clave: Respuesta(valores[clave], conf) for clave in kwargs["questions"]})


clave_original = jd.TYPESAFE_API_KEY
get_original = jd._get_cliente
jd.TYPESAFE_API_KEY = "clave-de-prueba"


def usar(cliente):
    jd._get_cliente = lambda: cliente
    return cliente


try:
    # 1) Éxito: ambas partes válidas
    usar(ClienteFalso(lambda k: responder(k, "musica", "TRAVIESO", 0.8)))
    d = jd.decidir("pon música de Shakira", "¡Claro!")
    check("éxito: ruta y emoción", d.ruta == "musica" and d.emocion == "TRAVIESO" and not d.respaldo,
          f"-> ruta={d.ruta} emocion={d.emocion} respaldo={d.respaldo}")
    check("éxito: confianzas leídas", d.confianza_ruta == 0.8 and d.confianza_emocion == 0.8)

    # 2) Cuerpo: dos llamadas, una pregunta cada una, con criterios y último turno
    cliente = usar(ClienteFalso(responder))
    jd.decidir("¿qué es un robot?", "Soy Bob.")
    preguntas = [list(k["questions"]) for k in cliente.llamadas]
    check("llamadas: dos, una pregunta cada una",
          sorted(map(tuple, preguntas)) == [("emocion",), ("ruta",)], f"-> {preguntas}")
    criterios_ok = all(
        set(k["questions"][c].criteria) == (set(jd.RUTAS) if c == "ruta" else set(jd.EMOCIONES))
        for k in cliente.llamadas for c in k["questions"])
    check("llamadas: criterios completos", criterios_ok)
    check("llamadas: modelo y último turno",
          all(k["model"] == jd.JEV_MODEL and k["state"].get("last_bob_turn") == "Soy Bob."
              for k in cliente.llamadas))

    # 3) Timeout en ambas -> respaldo completo, a tiempo
    jd.JEV_TIMEOUT_S = 0.3
    usar(ClienteFalso(lambda k: (time.sleep(2.0), responder(k))[1]))
    t0 = time.monotonic(); d = jd.decidir("hola"); tardo = time.monotonic() - t0
    jd.JEV_TIMEOUT_S = 1.0
    check("timeout en ambas: respaldo completo a tiempo",
          d.respaldo and d.ruta is None and d.emocion == jd.EMOCION_RESPALDO and tardo < 1.0,
          f"-> tardó {tardo:.2f}s")

    # 4) Error del SDK en ambas -> respaldo
    def fallar(k):
        raise ConnectionError("red caída")
    usar(ClienteFalso(fallar))
    d = jd.decidir("hola")
    check("error del SDK: respaldo", d.respaldo and d.ruta is None and d.emocion == jd.EMOCION_RESPALDO,
          f"-> {d.error[:60]}")

    # 5) Ruta fuera del esquema, emoción válida
    usar(ClienteFalso(lambda k: responder(k, "inventada", "AMOR")))
    d = jd.decidir("hola")
    check("ruta inválida: ruta a respaldo y emoción de JEV",
          d.ruta is None and d.emocion == "AMOR" and d.respaldo)

    # 6) Emoción fuera de la lista, ruta válida
    usar(ClienteFalso(lambda k: responder(k, "charla", "PENSANDO")))
    d = jd.decidir("hola")
    check("emoción fuera de la lista: CURIOSO y ruta de JEV",
          d.emocion == jd.EMOCION_RESPALDO and d.ruta == "charla" and d.respaldo)

    # 7) Fallo parcial por plazo: ruta a tiempo, emoción tarda
    def emocion_lenta(k):
        if "emocion" in k["questions"]:
            time.sleep(2.0)
        return responder(k, "musica")
    jd.JEV_TIMEOUT_S = 0.3
    usar(ClienteFalso(emocion_lenta))
    t0 = time.monotonic(); d = jd.decidir("pon algo de Queen"); tardo = time.monotonic() - t0
    jd.JEV_TIMEOUT_S = 1.0
    check("fallo parcial: ruta de JEV, emoción CURIOSO, a tiempo",
          d.ruta == "musica" and d.emocion == jd.EMOCION_RESPALDO and d.respaldo and tardo < 1.0,
          f"-> tardó {tardo:.2f}s")

    # 8) Clave ausente: no llama al SDK
    cliente = usar(ClienteFalso(responder))
    jd.TYPESAFE_API_KEY = ""
    d = jd.decidir("hola")
    check("sin clave: respaldo sin llamar", d.respaldo and not cliente.llamadas, f"-> {d.error}")
    jd.TYPESAFE_API_KEY = "clave-de-prueba"
finally:
    jd._get_cliente = get_original
    jd.TYPESAFE_API_KEY = clave_original
    jd.JEV_TIMEOUT_S = 1.0

print(f"\nRESULTADO: {'TODO OK' if fallos == 0 else f'{fallos} FALLA(S)'}", flush=True)
sys.exit(0 if fallos == 0 else 1)
