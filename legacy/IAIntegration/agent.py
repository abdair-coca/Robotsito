"""Screen Agent: le dices una tarea en lenguaje natural y la ejecuta en tu pantalla,
tomando screenshots y decidiendo clics/teclas con un modelo de visión de Groq.

USO:
    python agent.py "abre el navegador y busca el clima en La Paz"
    python agent.py "abre el navegador y busca el clima en La Paz" --auto

Sin --auto, te pide confirmar (Enter) cada paso antes de ejecutarlo.
Mueve el mouse a la esquina superior-izquierda (0,0) en cualquier momento
para abortar de inmediato (failsafe de pyautogui), o Ctrl+C en la terminal.
"""
import argparse
import os
import sys
import time

import mss
from PIL import Image

import actions
from config import CONFIRM_BATCH_SIZE, MAX_STEPS, REFINE_CLICKS, STEP_DELAY
from vision import get_next_action, refine_click_coords

TERMINAL_ACTIONS = {"done", "fail"}
CLICK_ACTIONS = {"click", "double_click", "right_click"}


def _refine_if_click(action, screenshot):
    """Segunda pasada de precisión para clicks (zoom sobre la zona objetivo)."""
    if action.get("action") not in CLICK_ACTIONS:
        return action
    try:
        x_pct = float(action["x_pct"])
        y_pct = float(action["y_pct"])
    except (KeyError, TypeError, ValueError):
        return action
    if not (0 <= x_pct <= 100 and 0 <= y_pct <= 100):
        return action  # fuera de rango: dejar que actions.execute lo rechace
    x_pct, y_pct = refine_click_coords(
        action.get("reasoning", ""), screenshot, x_pct, y_pct
    )
    action["x_pct"] = round(x_pct, 1)
    action["y_pct"] = round(y_pct, 1)
    return action


def take_screenshot():
    with mss.MSS() as sct:
        monitor = sct.monitors[1]  # monitor principal
        raw = sct.grab(monitor)
        return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")


def main():
    parser = argparse.ArgumentParser(description="Agente de IA que controla tu pantalla.")
    parser.add_argument("task", help="Descripción de la tarea en lenguaje natural")
    parser.add_argument("--auto", action="store_true", help="No pedir confirmación nunca")
    parser.add_argument(
        "--batch",
        type=int,
        default=CONFIRM_BATCH_SIZE,
        help=f"Cuántas acciones aprueba cada Enter (default: {CONFIRM_BATCH_SIZE}; 1 = confirmar cada paso)",
    )
    args = parser.parse_args()

    if "GROQ_API_KEY" not in os.environ:
        sys.exit("Falta la variable de entorno GROQ_API_KEY. Ejecuta: export GROQ_API_KEY=tu_key")

    history = []
    size = actions.screen_size()
    approved = 0  # acciones ya aprobadas por el último Enter, pendientes de consumir

    print(f"Tarea: {args.task}")
    print("Ctrl+C o mover el mouse a la esquina (0,0) aborta en cualquier momento.\n")

    for step in range(1, MAX_STEPS + 1):
        screenshot = take_screenshot()
        try:
            action = get_next_action(args.task, history, size, screenshot)
        except Exception as e:
            print(f"[Paso {step}] Error consultando al modelo: {e}")
            break

        kind = action.get("action")
        if REFINE_CLICKS:
            action = _refine_if_click(action, screenshot)
        print(f"[Paso {step}] {action.get('reasoning', '')}")
        print(f"           -> acción propuesta: {action}")

        if kind in TERMINAL_ACTIONS:
            print(f"\nAgente terminó ({kind}): {action.get('message', '')}")
            break

        if not args.auto:
            if approved == 0:
                resp = input(
                    f"           Ejecutar? [Enter=sí, aprueba las próximas {args.batch} acciones / n=abortar]: "
                )
                if resp.strip().lower() == "n":
                    print("Abortado por el usuario.")
                    break
                approved = max(1, args.batch)
            approved -= 1

        try:
            result = actions.execute(action)
        except Exception as e:
            print(f"           Error ejecutando la acción: {e}")
            history.append(f"(falló al ejecutar {kind}: {e})")
            continue

        history.append(result)
        time.sleep(STEP_DELAY)
    else:
        print(f"\nSe alcanzó el máximo de {MAX_STEPS} pasos sin que el agente marcara 'done'.")


if __name__ == "__main__":
    main()
