# Speaker — salida de audio (DAC interno + PAM8403)

- **Tipo:** hardware
- **Estado:** active (migración a I2S planificada — ver ADR-001)
- **Fase:** Fase 1

## Propósito

Reproducir la voz de Bob (TTS) por un parlante físico. Es la "boca" de Bob: la
laptop sintetiza la voz (edge-tts), la convierte a PCM y la manda por TCP al
ESP32 DevKit, que la saca por el DAC interno hacia un amplificador y el parlante.

## Conexiones / Dependencias

Cadena completa de la voz:

```
edge-tts (laptop) → ffmpeg (EQ + dither) → uint8 8-bit @ 8 kHz
  → TCP PORT_SPK 5006 → ESP32 DAC interno (GPIO25) → amp PAM8403 → parlante 4-8 Ω
```

| Pieza | Detalle |
|---|---|
| Amplificador | **PAM8403** (Class-D analógico). Solo amplifica lo que el DAC saca; no mejora la fuente. |
| DAC | **Interno del ESP32**, `machine.DAC` en **GPIO25** (DAC1). 8 bits (256 niveles). |
| Transporte | TCP `PORT_SPK = 5006` (laptop → ESP32). Header 4 bytes BE de longitud + body. Solo activo si `USE_ROBOT_SPEAKER = True`. |
| Firmware | `firmware/Esp32/main.py` — hilo `hilo_audio`, función `play_mode` (reproduce muestra a muestra con timing absoluto por busy-wait). |
| Laptop | `robot_bob/voice_pipeline.py` (`_synthesize_mp3`, `_mp3_a_u8`, `_reproducir_mp3_robot`) + `shared/voicechatLap/audio_io.py` (transporte). |

## Notas técnicas

### Knobs de calidad (todos en `robot_bob/config.py`)
- `TTS_VOLUME` (0.0–1.0): ganancia final. En DAC de 8 bits, **más** volumen = más
  rango usado = menos ruido de cuantización. Actual `0.85`. Si el parlante físico
  satura/cruje, bajar a `0.75`.
- `TTS_FFMPEG_FILTERS`: cadena ffmpeg. Banda de presencia `equalizer=f=2200:g=3`
  = inteligibilidad de consonantes (el realce más importante). `highpass=180`
  corta zumbido grave; `lowpass=3400` evita aliasing bajo Nyquist (4 kHz);
  `alimiter=0.92` frena el clip al pasar a uint8.
- `TTS_PITCH_BASE` + `TTS_EMO_PROSODY`: pitch por emoción. Agudos altos suenan
  metálicos en el parlante chico — techo recortado. Subir para más caricatura.
- `voice_pipeline._mp3_a_u8`: `-dither_method triangular` al bajar a 8 bits
  (grano áspero → hiss suave).
- Pacing de envío: `voice_pipeline.py` `_reproducir_mp3_robot`, factor `0.90` del
  `time.sleep`. Sube hacia 1.0 si hay chasquidos (overrun); baja si entrecorta.

### Gotchas
- **DAC interno = techo de calidad telefónico.** 256 niveles + ruido acoplado del
  radio WiFi → ~6-7 bits efectivos. No se rompe por software.
- **8 kHz fijo:** debe coincidir laptop (`SAMPLE_RATE`) y firmware. El playback es
  un loop Python; a >8 kHz (62 µs/muestra @ 16 kHz) **no llega** de forma fiable.
- `play_mode` mantiene un reloj absoluto continuo entre refills; re-anclarlo en
  cada chunk metía un zumbido periódico (~8 Hz). No reintroducir el re-anclado por
  refill.
- `dac.write(128)` = punto medio = silencio. Se deja así al terminar/cerrar.

### Mejora futura
- **ADR-001** (`docs/genesis/decisions/ADR-001-speaker-i2s-max98357a.md`):
  reemplazar DAC interno + PAM8403 por **MAX98357A** (DAC+amp I2S, 16-bit, DMA,
  16 kHz). Es el salto real de calidad. Pieza pendiente de compra (~20 BOB).
- Opción barata mientras tanto: filtro RC de reconstrucción en GPIO25 antes del
  PAM8403 (R≈1 kΩ + C≈47 nF) → suaviza el escalonado del DAC.

### Relacionado
- Pinout completo del DevKit: `docs/HARDWARE.md`.
- Mic (otra mitad del audio): MAX9814 en GPIO34 (ADC), 8 kHz uint8.
