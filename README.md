# Robot Bob 🤖

Robot interactivo de feria: reconoce caras, te sigue con la cabeza (pan/tilt),
muestra emociones en una pantalla OLED y conversa por voz en español con IA en la
nube. Hecho por **Abdair** (Ing. Informática, UATF — Potosí, Bolivia).

El cerebro corre en la **laptop** (visión MediaPipe + IA Groq); los ESP32 hacen
servos/OLED/audio (DevKit) y el video (CAM).

## Arranque rápido

```bash
cd robot_bob
.\venv311\Scripts\Activate.ps1      # Windows PowerShell  (Python 3.11)
python discovery.py                  # localiza las IPs de los ESP32 (tras cambiar de WiFi)
python main.py                       # sistema completo — Q para salir
```

Primera vez: `cp .env.example .env` y poner la `GROQ_API_KEY`
(https://console.groq.com/keys).

## Mapa del repo

| Carpeta | Qué es |
|---|---|
| `robot_bob/` | **Sistema canónico** — el cerebro Python de la laptop. Empieza por aquí. |
| `firmware/` | Código que corre DENTRO de los ESP32 (DevKit MicroPython, CAM Arduino, OLED). |
| `shared/` | Dependencias vivas compartidas (`voicechatLap`: audio TCP, wake word). |
| `legacy/` | Prototipos viejos de los que nació Bob. No se editan. |
| `docs/` | Toda la documentación (ver abajo). |

## Documentación

| Doc | Contenido |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Hilos, máquina de estados, módulos, sistema emocional, backends. |
| [docs/HARDWARE.md](docs/HARDWARE.md) | Componentes, pines, puertos TCP, firmware, brownout. |
| [docs/NETWORK.md](docs/NETWORK.md) | Auto-discovery de IPs, mDNS, multi-WiFi, troubleshooting. |
| [docs/ROADMAP.md](docs/ROADMAP.md) | Features hechas y pendientes por prioridad. |
| [docs/FEATURES.md](docs/FEATURES.md) | Feature "Actitud" (soliloquio + muecas). |
| [docs/LOCOMOTION.md](docs/LOCOMOTION.md) | Motores DC / tracción diferencial. |
| [docs/HISTORY.md](docs/HISTORY.md) | Etapas de construcción del proyecto. |

> Para LLMs: el índice de contexto está en [agents.md](agents.md).
