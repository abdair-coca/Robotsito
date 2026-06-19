# Robot Bob — Pendientes / mejoras futuras

Backlog de cosas que **funcionan pero se pueden mejorar después**. No bloquean la demo.

---

## Voz (TTS)

**Estado actual:** edge-tts con `es-BO-MarceloNeural` + prosodia por emoción
(`TTS_EMO_PROSODY` en `config.py`), estilo caricaturesco simpático. Funciona y suena
menos plana, pero sigue siendo voz neural reconocible.

**Para mejorar:**
- Probar un motor más humano si aparece presupuesto/setup:
  - **ElevenLabs** — la más natural; necesita API key y el cupo gratis es chico para feria continua.
  - **Piper** (local, offline, gratis) — evaluar voces es_* y si suena mejor que edge-tts.
- Afinar la tabla `TTS_EMO_PROSODY` con pruebas reales (¿muy caricaturesco? ¿muy rápido?).
- Probar `es-BO-SofiaNeural` u otras voces y comparar A/B.
- Para el parlante del robot (8 kHz u8): revisar la cadena `TTS_FFMPEG_FILTERS` para que la
  voz aguda no sature/aliasee al bajar a 8 bits. (Hoy se usa el parlante de la laptop.)
- Considerar variar `volume`/énfasis por emoción además de rate/pitch (más expresividad).

---

<!-- Próximos pendientes acá. Formato: ## Tema → Estado actual / Para mejorar. -->
