"""Una línea JSON por turno de charla, en logs/turnos.jsonl.

Solo tiempos, modelo y fallos. Nunca guarda lo que dice la gente ni nada que
identifique a una persona; por eso el perfil feria lo enciende junto con
MEMORIA_ENABLED = False.
"""
import json
import os
import threading
from datetime import datetime

import rutas

LOG_TURNOS = os.path.join(rutas.LOGS_DIR, "turnos.jsonl")
_lock = threading.Lock()


def registrar(linea: dict, ruta_archivo=None) -> None:
    """Agrega una línea con su hora. Nunca lanza: un fallo de disco no corta el turno."""
    linea = {"ts": datetime.now().isoformat(timespec="seconds"), **linea}
    destino = ruta_archivo or LOG_TURNOS
    try:
        with _lock:
            os.makedirs(os.path.dirname(destino), exist_ok=True)
            with open(destino, "a", encoding="utf-8") as f:
                f.write(json.dumps(linea, ensure_ascii=False) + "\n")
    except (OSError, TypeError, ValueError) as e:
        import errores
        errores.aviso("REGISTRO", "No pude guardar el registro de este turno; Bob sigue igual.",
                      f"{destino}: {e}")
