"""Cliente de visión: manda el screenshot + tarea a Groq y devuelve la acción a ejecutar."""
import base64
import io
import json
import os

from dotenv import load_dotenv
from groq import Groq
from PIL import Image, ImageDraw

from config import (
    MAX_COMPLETION_TOKENS,
    REFINE_REGION_PCT,
    SCREENSHOT_JPEG_QUALITY,
    SCREENSHOT_MAX_WIDTH,
    VISION_MODEL,
)

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

_client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

SYSTEM_PROMPT = """Eres un agente que controla el mouse y el teclado de una computadora \
mirando capturas de pantalla. En cada turno recibes: la tarea del usuario, el historial \
de acciones ya realizadas, el tamaño real de la pantalla, y una captura de pantalla actual.

La captura lleva superpuesta una cuadrícula roja con etiquetas de porcentaje cada 10%: \
úsala como regla para estimar x_pct / y_pct con precisión.

Debes responder ÚNICAMENTE con un objeto JSON (sin texto adicional, sin markdown) con esta forma:

{
  "reasoning": "una frase corta explicando qué ves y qué vas a hacer",
  "action": "click" | "double_click" | "right_click" | "type" | "key" | "scroll" | "wait" | "done" | "fail",
  "x_pct": 0-100,
  "y_pct": 0-100,
  "text": "...",
  "key": "...",
  "scroll_amount": -10..10,
  "wait_seconds": 0.5,
  "message": "..."
}

Notas sobre los campos:
- x_pct / y_pct: posición del clic en PORCENTAJE del ancho/alto de pantalla, SIEMPRE entre 0 y 100. \
NUNCA uses píxeles aquí (ej: el centro de la pantalla es x_pct=50, y_pct=50; NO x_pct=960). \
Solo para click/double_click/right_click.
- text: solo para action=type, el texto exacto a escribir.
- key: solo para action=key, ej. "enter", "esc", "tab", "ctrl+c", "alt+tab", "win".
- scroll_amount: solo para action=scroll, negativo=bajar, positivo=subir.
- wait_seconds: solo para action=wait, cuando la pantalla parece estar cargando algo.
- message: solo para action=done/fail, explica por qué terminaste.

Reglas:
- PREFIERE SIEMPRE EL TECLADO SOBRE EL MOUSE cuando exista un camino con teclas: \
para abrir una aplicación usa key "win", luego type con el nombre, luego key "enter"; \
para buscar en un navegador usa ctrl+l, escribe la búsqueda y enter. \
El teclado es exacto; los clics pueden fallar por unos píxeles. \
Usa click solo cuando no haya alternativa razonable de teclado.
- Usa "done" en cuanto la tarea esté completa. No sigas dando pasos de más.
- Usa "fail" si la tarea es imposible o no puedes identificar con seguridad dónde hacer clic.
- Da un solo paso por respuesta.
- Si no estás razonablemente seguro de una coordenada, prefiere "wait" o "fail" antes que \
arriesgar un clic que pueda ser destructivo (cerrar sin guardar, borrar algo, enviar un \
mensaje o formulario incompleto, etc.).
- Si en el historial ya intentaste la misma acción exacta dos veces y la pantalla no \
cambió, no la repitas: usa "fail" y explica el bloqueo.
"""

REFINE_SYSTEM_PROMPT = """Te muestro un RECORTE AMPLIADO de una zona de la pantalla, con una \
cuadrícula roja de porcentajes como regla. Tu único trabajo: localizar EXACTAMENTE el objetivo \
que se te describe, DENTRO de este recorte.

Responde ÚNICAMENTE con un objeto JSON:
- Si el objetivo se ve en el recorte: {"visible": true, "x_pct": 0-100, "y_pct": 0-100}
  (porcentaje RELATIVO AL RECORTE, apuntando al CENTRO del objetivo)
- Si el objetivo NO aparece en el recorte: {"visible": false}
"""


def _draw_pct_grid(pil_image: Image.Image) -> Image.Image:
    """Superpone una regla de porcentajes (líneas cada 10%) para anclar la estimación del modelo."""
    img = pil_image.convert("RGB").copy()
    d = ImageDraw.Draw(img)
    w, h = img.size
    for pct in range(10, 100, 10):
        x = int(w * pct / 100)
        y = int(h * pct / 100)
        d.line([(x, 0), (x, h)], fill=(255, 0, 0), width=1)
        d.line([(0, y), (w, y)], fill=(255, 0, 0), width=1)
        d.text((x + 3, 3), str(pct), fill=(255, 0, 0))
        d.text((3, y + 3), str(pct), fill=(255, 0, 0))
    return img


def _screenshot_to_base64_jpeg(pil_image: Image.Image) -> str:
    if pil_image.width > SCREENSHOT_MAX_WIDTH:
        ratio = SCREENSHOT_MAX_WIDTH / pil_image.width
        new_size = (SCREENSHOT_MAX_WIDTH, int(pil_image.height * ratio))
        pil_image = pil_image.resize(new_size)
    buffer = io.BytesIO()
    pil_image.convert("RGB").save(buffer, format="JPEG", quality=SCREENSHOT_JPEG_QUALITY)
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


def _chat_json(system_prompt, user_text, image_b64, max_tokens):
    """Llamada a Groq en modo JSON con reintentos; devuelve el dict parseado."""
    kwargs = {
        "model": VISION_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_text},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                ],
            },
        ],
        "temperature": 0.2,
        "max_completion_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }
    if "qwen" in VISION_MODEL:
        # Modelos razonadores: el razonamiento no debe aparecer en
        # message.content, solo el JSON final (si no, json_object no valida).
        kwargs["reasoning_format"] = "hidden"

    last_error = None
    for _ in range(3):
        try:
            completion = _client.chat.completions.create(**kwargs)
        except Exception as e:
            # json_validate_failed es intermitente (el modelo a veces genera
            # JSON inválido); reintentar suele bastar.
            if "json_validate_failed" in str(e):
                last_error = e
                continue
            raise
        raw = completion.choices[0].message.content
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            last_error = ValueError(f"El modelo no devolvió JSON válido: {raw!r}")
            continue
    raise last_error


def get_next_action(task, history, screen_size, screenshot):
    """Envía la tarea + historial + screenshot a Groq y devuelve la acción parseada como dict.

    task: str con la descripción de la tarea.
    history: list[str] con la descripción de cada acción ya ejecutada.
    screen_size: tuple (width, height) en píxeles reales de la pantalla.
    screenshot: PIL.Image con la captura actual.
    """
    b64 = _screenshot_to_base64_jpeg(_draw_pct_grid(screenshot))
    width, height = screen_size

    history_text = "\n".join(f"{i + 1}. {h}" for i, h in enumerate(history)) or "(sin acciones previas)"

    user_text = (
        f"Tarea del usuario: {task}\n\n"
        f"Tamaño real de la pantalla: {width}x{height} px\n\n"
        f"Historial de acciones ya realizadas:\n{history_text}\n\n"
        "Esta es la captura de pantalla ACTUAL. Decide el siguiente paso."
    )

    return _chat_json(SYSTEM_PROMPT, user_text, b64, MAX_COMPLETION_TOKENS)


def refine_click_coords(target_description, screenshot, x_pct, y_pct):
    """Segunda pasada de precisión: recorta la zona del click aproximado, la amplía,
    y pide al modelo la posición exacta dentro del recorte.

    Devuelve (x_pct, y_pct) absolutos refinados; si algo falla o el objetivo no
    se ve en el recorte, devuelve las coordenadas originales sin cambios.
    """
    width, height = screenshot.size
    box_w = max(1, int(width * REFINE_REGION_PCT / 100))
    box_h = max(1, int(height * REFINE_REGION_PCT / 100))
    cx = int(width * x_pct / 100)
    cy = int(height * y_pct / 100)
    x0 = max(0, min(cx - box_w // 2, width - box_w))
    y0 = max(0, min(cy - box_h // 2, height - box_h))

    crop = screenshot.crop((x0, y0, x0 + box_w, y0 + box_h))
    crop = crop.resize((box_w * 2, box_h * 2))  # ampliar para que se vea el detalle

    user_text = f"Objetivo a localizar en el recorte: {target_description}"
    try:
        result = _chat_json(
            REFINE_SYSTEM_PROMPT,
            user_text,
            _screenshot_to_base64_jpeg(_draw_pct_grid(crop)),
            MAX_COMPLETION_TOKENS,
        )
    except Exception:
        return x_pct, y_pct

    if not result.get("visible"):
        return x_pct, y_pct
    try:
        rx = float(result["x_pct"])
        ry = float(result["y_pct"])
    except (KeyError, TypeError, ValueError):
        return x_pct, y_pct
    if not (0 <= rx <= 100 and 0 <= ry <= 100):
        return x_pct, y_pct

    abs_x = x0 + rx / 100 * box_w
    abs_y = y0 + ry / 100 * box_h
    return abs_x / width * 100, abs_y / height * 100
