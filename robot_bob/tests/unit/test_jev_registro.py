"""
test_jev_registro.py — Registro de decisiones de JEV en logs/decisiones.jsonl (J4).

Sin red ni micrófono: el cliente del SDK es falso y el log va a un temporal.
Cubre una línea por decisión, campos y tipos, respaldo con error, texto de la
frase fuera del log, varias llamadas seguidas, fallo de disco sin excepción y
escritura desde varios hilos.

  python tests/unit/test_jev_registro.py
"""
import json
import os
import sys
import tempfile
import threading

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from voz import jev_decision as jd   # noqa: E402
from voz.jev_decision import Decision   # noqa: E402

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
    def __init__(self, fn):
        self.fn = fn

    def system_one(self, **kwargs):
        return self.fn(kwargs)


def responder(kwargs, ruta="charla", emocion="FELIZ", conf=0.9):
    valores = {"ruta": ruta, "emocion": emocion}
    return Resultado({clave: Respuesta(valores[clave], conf) for clave in kwargs["questions"]})


def leer(ruta):
    with open(ruta, encoding="utf-8") as f:
        return [json.loads(linea) for linea in f if linea.strip()]


def usar(cliente):
    jd._get_cliente = lambda: cliente
    return cliente


defecto = jd.LOG_DECISIONES
get_original = jd._get_cliente
clave_original = jd.TYPESAFE_API_KEY
tmp = tempfile.mkdtemp()
jd.TYPESAFE_API_KEY = "clave-de-prueba"

try:
    # 0) La ruta por defecto es robot_bob/logs/decisiones.jsonl
    check("ruta por defecto", defecto.replace("\\", "/").endswith("robot_bob/logs/decisiones.jsonl"),
          f"-> {defecto}")

    # 1) registrar(): una línea con los campos y tipos del criterio
    log = os.path.join(tmp, "registro.jsonl")
    jd.registrar(Decision("musica_play", "TRAVIESO", 0.8, 0.7, 0.48123, False, ""), log)
    filas = leer(log)
    campos = {"ts", "ruta", "emocion", "latencia_s", "respaldo",
              "confianza_ruta", "confianza_emocion", "error"}
    check("registrar: una línea", len(filas) == 1, f"-> {len(filas)}")
    f = filas[0] if filas else {}
    check("registrar: campos completos", set(f) == campos, f"-> {sorted(f)}")
    check("registrar: valores", f.get("ruta") == "musica_play" and f.get("emocion") == "TRAVIESO"
          and f.get("latencia_s") == 0.481 and f.get("respaldo") is False, f"-> {f}")
    check("registrar: ts ISO", isinstance(f.get("ts"), str) and len(f["ts"]) == 19
          and f["ts"][10] == "T", f"-> {f.get('ts')}")

    # 2) Respaldo: ruta null, respaldo true y el error registrado
    jd.registrar(Decision(None, "CURIOSO", 0.0, 0.0, 1.0, True, "ruta: plazo agotado"), log)
    filas = leer(log)
    check("respaldo: ruta null, respaldo y error", filas[-1]["ruta"] is None
          and filas[-1]["respaldo"] is True and "plazo" in filas[-1]["error"], f"-> {filas[-1]}")
    check("append: dos líneas, la primera intacta", len(filas) == 2 and filas[0]["ruta"] == "musica_play")

    # 3) decidir() real con cliente falso: una línea por llamada, sin el texto de la frase
    log_d = os.path.join(tmp, "decidir.jsonl")
    jd.LOG_DECISIONES = log_d
    usar(ClienteFalso(lambda k: responder(k, "charla", "FELIZ", 0.9)))
    jd.decidir("frase secreta de prueba")
    filas = leer(log_d)
    check("decidir: registra una línea", len(filas) == 1 and filas[0]["ruta"] == "charla"
          and filas[0]["emocion"] == "FELIZ" and filas[0]["respaldo"] is False, f"-> {filas}")
    with open(log_d, encoding="utf-8") as f:
        contenido = f.read()
    check("decidir: el texto de la frase no va al log", "frase secreta" not in contenido)
    jd.decidir("otra frase")
    check("decidir: una línea por llamada", len(leer(log_d)) == 2)

    # 4) Sin clave: respaldo, y también se registra
    jd.TYPESAFE_API_KEY = ""
    jd.decidir("hola")
    jd.TYPESAFE_API_KEY = "clave-de-prueba"
    filas = leer(log_d)
    check("sin clave: respaldo registrado", len(filas) == 3 and filas[-1]["respaldo"] is True
          and filas[-1]["error"] == "falta TYPESAFE_API_KEY", f"-> {filas[-1]}")

    # 5) Fallo de disco: no lanza y no corta el turno
    bloqueo = os.path.join(tmp, "archivo_bloqueado")
    open(bloqueo, "w").close()
    try:
        jd.registrar(Decision("charla", "FELIZ", 0.9, 0.9, 0.5, False, ""),
                     os.path.join(bloqueo, "sub", "x.jsonl"))
        sin_excepcion = True
    except Exception:
        sin_excepcion = False
    check("fallo de disco: no lanza", sin_excepcion)

    # 6) Varios hilos a la vez: cada línea sigue siendo JSON válido
    log_hilos = os.path.join(tmp, "hilos.jsonl")
    hilos = [threading.Thread(target=jd.registrar,
                              args=(Decision("charla", "FELIZ", 0.9, 0.9, 0.5, False, ""), log_hilos))
             for _ in range(40)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    try:
        filas = leer(log_hilos)
        valido = True
    except json.JSONDecodeError:
        filas, valido = [], False
    check("hilos: 40 líneas JSON válidas", valido and len(filas) == 40, f"-> {len(filas)}")
finally:
    jd._get_cliente = get_original
    jd.TYPESAFE_API_KEY = clave_original
    jd.LOG_DECISIONES = defecto

print(f"\nRESULTADO: {'TODO OK' if fallos == 0 else f'{fallos} FALLA(S)'}", flush=True)
sys.exit(0 if fallos == 0 else 1)
