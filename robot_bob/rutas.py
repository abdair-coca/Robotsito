"""Rutas de datos y de código externo, relativas a robot_bob/.

Un módulo no calcula sus rutas con __file__: al moverlo a una subcarpeta, su
__file__ cambia. Por eso todas las rutas salen de aquí. Este archivo se queda en
la raíz de robot_bob/, así que RAIZ no cambia con las reestructuras.
"""
import os

RAIZ = os.path.dirname(os.path.abspath(__file__))

CONFIG_PY = os.path.join(RAIZ, "config.py")
DEVICES_JSON = os.path.join(RAIZ, "devices.json")
DB_MEMORIA = os.path.join(RAIZ, "memory.db")
DB_RECORDATORIOS = os.path.join(RAIZ, "reminders.db")
LOGS_DIR = os.path.join(RAIZ, "logs")
MODELOS_DIR = os.path.join(RAIZ, "models")

# Código de audio compartido (AudioIO, wake word) que vive fuera de robot_bob/.
VOICECHAT_DIR = os.path.abspath(os.path.join(RAIZ, "..", "shared", "voicechatLap"))
