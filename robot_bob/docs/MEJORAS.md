# Plan de mejoras — Robot Bob

Priorizado: 🔴 alto impacto/bajo esfuerzo → 🟡 medio → 🟢 calidad de vida.

---

## 🔴 1. Dividir `voice_pipeline.py` (1846→1209 líneas)

✅ Extraído:
- `audio_helpers.py` — funciones puras (RMS, WAV, splitter, intención de giro, alucinaciones)
- `tts_engine.py` — edge-tts + reproducción (ESP32/laptop)
- `recorder.py` — grabación mic laptop/robot, VAD, endpointing, anti-eco

❌ Queda dentro de `voice_pipeline.py`:
- `llm_client.py` — cliente LLM (Groq/Gemini), streaming, guard de frases
- `wake_monitor.py` — wake word loop
- `soliloquy.py` — soliloquio loop, banco local, generación LLM
- reminders, background monitors, baile, reconexión

---

## 🔴 2. Centralizar imports de config (~40 try/except duplicados)

Cada módulo reinventa:
```python
try:
    from config import X
except Exception:
    X = default
```

**Acción:** `config.py` exporta dataclass `Config` con todos los valores + defaults.
Módulos importan `from config import cfg` una vez.

---

## 🔴 3. Sacar side-effects de `config.py`

`import config` escanea red (discovery) — puede colgar 5-30s o fallar.

**Acción:** Discovery se llama explícito en `main.py`. `config.py` solo define
constantes. Sin I/O al importar.

---

## 🔴 4. Eliminar duplicación de `_DEFAULT_OLED`

`expression_engine.py` redefined `_DEFAULT_OLED` de `state_machine.py`.

**Acción:** `expression_engine` importa `_OLED_STATE` de `state_machine`. Un solo source
of truth.

---

## 🔴 5. Encapsulación de `_serial` en BehaviorEngine

`behavior.py` accede `self._sm._serial` — atributo privado de otra clase.

**Acción:** Pasar `serial_mgr` directamente a `BehaviorEngine.__init__`.

---

## 🟡 6. Tests unitarios (sin hardware)

20 tests de integración requieren ESP32 real. No se puede correr `pytest` rápido.

**Acción:** Mocks para `SerialManager`, `FacialTracker`, `StateMachine`. Tests de
parseo (music, reminders, expresiones) ya pueden ser unitarios hoy.

---

## 🟡 7. Reemplazar pygame para audio

`pygame` (~40MB) + tempfiles por cada frase TTS. I/O de disco innecesario.

**Acción:** `sounddevice` o `miniaudio` para playback directo desde bytes en
memoria. Sin archivos temporales.

---

## 🟡 8. Optimizar búsqueda de embeddings en `memory.py`

`reconocer()` carga TODOS los BLOBs de SQLite por cada frame de cámara.

**Acción:** Mantener índice en memoria (dict de numpy arrays). Sincronizar con
SQLite solo en escritura.

---

## 🟡 9. Cache de respuestas LLM

Preguntas repetitivas ("cuéntame un chiste") queman tokens de Groq al pedo.

**Acción:** LRU cache en memoria (dict + TTL) con hash del input del usuario.

---

## 🟡 10. Manejo de errores fino en wake monitor

`_grabar_wake_laptop` falla silencioso si el micrófono default cambia.

**Acción:** Listar dispositivos, reintentar con fallback, loggear error específico.

---

## 🟢 11. Single ffmpeg pipe para TTS

`_synthesize_mp3` → `_mp3_to_wav` = dos subprocesos por frase.

**Acción:** edge-tts → pipe único a ffmpeg. O edge-tts output directo a WAV.

---

## 🟢 12. Tipado estricto

Mezcla de type hints y código sin tipos.

**Acción:** Migrar a Python 3.11+ types, habilitar `pyright` o `mypy`.

---

## 🟢 13. Integrar FPS cap en `cv2.waitKey`

```python
# actual
cv2.waitKey(1)
...
time.sleep(TARGET_PERIOD - elapsed)

# mejor
cv2.waitKey(max(1, int(remaining_ms * 1000)))
```

---

## 🟢 14. Excepciones específicas en `serial_manager._enviar`

`except Exception` genérico no distingue error de socket (reconectable) vs bug de
protocolo.

**Acción:** `except (OSError, socket.error)` para reconexión. Otras excepciones
loggear sin reconectar.

---

## 🟢 15. MACs de placas en config, no en código

`discovery.py` hardcodea `KNOWN_MAC_ROLES`.

**Acción:** Mover a `config.py` o `devices.json`.

---

## 🟢 16. Async nativo (futuro)

`edge_tts` es async pero envuelto en `asyncio.run()`. No escala si hay
concurrencia real.

**Acción:** No urgente. Si se agrega concurrencia real, migrar pipeline a
`asyncio` completo.
