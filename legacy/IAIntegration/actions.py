"""Ejecuta las acciones (click, teclado, scroll) que decide el modelo."""
import time

import pyautogui

pyautogui.FAILSAFE = True  # mover el mouse a la esquina (0,0) aborta todo de inmediato
pyautogui.PAUSE = 0.15  # pequeña pausa entre comandos de pyautogui


def screen_size():
    return pyautogui.size()


def execute(action):
    """Ejecuta una acción (dict) y devuelve una descripción en texto para el historial."""
    kind = action.get("action")
    width, height = screen_size()

    if kind in ("click", "double_click", "right_click"):
        x_pct = float(action["x_pct"])
        y_pct = float(action["y_pct"])
        if not (0 <= x_pct <= 100 and 0 <= y_pct <= 100):
            # Si esto no se rechaza, la coordenada se sale de la pantalla,
            # pyautogui deja el mouse clavado en una esquina y su fail-safe
            # aborta esta acción Y todas las siguientes.
            raise ValueError(
                f"x_pct/y_pct deben ser PORCENTAJES 0-100, no píxeles "
                f"(recibido x_pct={x_pct}, y_pct={y_pct})"
            )
        # Nunca tocar la esquina exacta: es la zona de aborto del fail-safe.
        x = max(2, min(int(width * x_pct / 100), width - 3))
        y = max(2, min(int(height * y_pct / 100), height - 3))
        if kind == "click":
            pyautogui.click(x, y)
        elif kind == "double_click":
            pyautogui.doubleClick(x, y)
        else:
            pyautogui.rightClick(x, y)
        return f"{kind} en ({x}, {y})"

    if kind == "type":
        text = action.get("text", "")
        pyautogui.write(text, interval=0.02)
        return f"escribió: {text!r}"

    if kind == "key":
        key = action.get("key", "")
        keys = key.split("+")
        if len(keys) > 1:
            pyautogui.hotkey(*keys)
        else:
            pyautogui.press(key)
        return f"presionó tecla(s): {key}"

    if kind == "scroll":
        amount = int(action.get("scroll_amount", 0))
        pyautogui.scroll(amount * 20)
        return f"scroll: {amount}"

    if kind == "wait":
        seconds = float(action.get("wait_seconds", 1))
        time.sleep(seconds)
        return f"esperó {seconds}s"

    raise ValueError(f"Acción desconocida: {kind}")
