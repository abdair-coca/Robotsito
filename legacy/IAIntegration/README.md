# Screen Agent

Le das una tarea en lenguaje natural y un modelo de visión (Groq) mira capturas
de tu pantalla, decide qué clic o tecla dar, y `pyautogui` lo ejecuta. Repite
el ciclo hasta que la tarea quede terminada.

```
screenshot -> Groq (visión) -> {acción en JSON} -> pyautogui -> repetir
```

## 1. Instalación

```bash
cd screen_agent
python3 -m venv venv
source venv/bin/activate      # en Windows: venv\Scripts\activate
pip install -r requirements.txt
```

Necesitas una API key de Groq (gratis en https://console.groq.com/keys):

```bash
export GROQ_API_KEY="tu_key_aqui"     # en Windows (PowerShell): $env:GROQ_API_KEY="tu_key"
```

## 2. Uso

```bash
python agent.py "abre el navegador y busca el clima en La Paz"
```

Por defecto te muestra cada paso y pide `Enter` para ejecutarlo (o `n` para
abortar). Cuando confíes en que funciona bien para una tarea repetitiva:

```bash
python agent.py "abre el navegador y busca el clima en La Paz" --auto
```

**Para abortar en cualquier momento:** mueve el mouse a la esquina superior
izquierda de la pantalla (0,0) — es el "failsafe" de pyautogui y corta todo
al instante — o `Ctrl+C` en la terminal.

## 3. Cómo funciona por dentro

- `agent.py` — el loop principal: toma screenshot, pide la siguiente acción,
  la ejecuta o la aborta, guarda el historial.
- `vision.py` — construye el prompt (tarea + historial + tamaño de pantalla),
  llama a la API de Groq con el screenshot en base64, y parsea el JSON de
  respuesta.
- `actions.py` — traduce ese JSON a llamadas reales de `pyautogui` (clic,
  escribir, tecla, scroll, esperar).
- `config.py` — modelo usado, límite de pasos, tamaño/calidad del screenshot.

Las coordenadas de clic viajan como **porcentaje de pantalla** (`x_pct`,
`y_pct`, 0-100), no píxeles crudos — a los modelos de lenguaje les cuesta
menos estimar bien un porcentaje que un número de píxel exacto, y así el
mismo prompt funciona sin cambios en cualquier resolución.

## 4. Limitaciones honestas (importante leer esto)

- **El modelo no es un agente de GUI especializado.** `qwen/qwen3.6-27b` (el
  modelo configurado en `config.py`) es un modelo de visión de propósito
  general, no uno entrenado específicamente para "grounding" preciso en
  interfaces (a diferencia de agentes de computer-use dedicados). Puede
  fallar el clic por unos píxeles en botones pequeños o íconos ambiguos.
  Funciona mejor con UIs simples y elementos grandes/claros (navegador,
  apps de escritorio con botones grandes) que con interfaces muy densas.
- **Groq cambia su catálogo de modelos con frecuencia.** Si `VISION_MODEL`
  en `config.py` deja de responder, revisa el listado vigente en
  https://console.groq.com/docs/models y actualiza esa constante.
- **Linux con Wayland:** `pyautogui` depende de X11 para mover el mouse y
  simular teclas. En muchas distros modernas con Wayland por defecto esto
  puede no funcionar o requerir configuración extra (XWayland, permisos).
  Si usas Wayland y falla el control del mouse, esa es la causa más probable.
- **macOS** pedirá dar permisos de "Accesibilidad" y "Grabación de pantalla"
  a la terminal/IDE desde donde corras el script.
- No hay confirmación adicional para acciones "peligrosas" específicas
  (borrar archivos, enviar formularios, cerrar sin guardar) más allá del
  modo interactivo por defecto — revisa cada paso propuesto antes de darle
  Enter, especialmente la primera vez que prueves una tarea nueva.

## 5. Ideas para mejorarlo (siguientes pasos)

- **Grounding con OCR**: en vez de que el modelo adivine coordenadas de
  texto, usar `pytesseract` para extraer todas las cajas de texto de la
  pantalla con sus posiciones, y que el modelo elija *cuál* cajita clickear
  por su contenido en vez de estimar píxeles — mucho más confiable para UIs
  con texto.
- **Restringir a una ventana**: en vez de capturar toda la pantalla, capturar
  solo la ventana de la app objetivo (reduce ruido visual y errores).
  En Windows esto se puede hacer con `pygetwindow` + `pyautogui.screenshot(region=...)`.
  En Linux/X11 con `python-xlib` o `wmctrl` para obtener la geometría de la
  ventana.
- **Guardar screenshots de cada paso** en una carpeta `logs/` para poder
  revisar después por qué falló un clic.
- **Tareas guardadas**: si terminas usando siempre las mismas 2-3 tareas
  repetitivas, vale la pena "grabar" la secuencia de acciones exitosa la
  primera vez (con IA) y después solo repetirla sin IA (más rápido, más
  barato, 100% determinístico) — ahí sí conviene el enfoque de secuencias
  fijas en vez de IA en cada paso.
