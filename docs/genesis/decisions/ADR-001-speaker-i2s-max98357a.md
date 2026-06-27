# ADR-001 — Migrar el speaker a DAC+amp I2S (MAX98357A)

- **Fecha:** 2026-06-26
- **Estado:** Propuesto / diferido (hasta conseguir el módulo MAX98357A)
- **Fase:** 1 — El alma de Bob
- **Autor:** Abdair + Claude

---

## Contexto

La voz de Bob sale por una cadena que topa en calidad telefónica vieja:

```
edge-tts → ffmpeg → uint8 8-bit @ 8 kHz → TCP → DAC interno GPIO25 → PAM8403 → speaker
```

Dos cuellos de botella **no rompibles por software**:

1. **DAC interno 8-bit** (`machine.DAC` en GPIO25): solo 256 niveles. En el ESP32
   el DAC interno acopla ruido del radio WiFi → ~6-7 bits efectivos → piso de
   ruido alto (hiss/grano). Techo duro.
2. **8 kHz** = ancho de banda 3.4 kHz (telefónico). Consonantes s/f/t embarradas.
3. (Secundario) El playback es un **loop Python muestra-a-muestra con busy-wait**
   (`firmware/Esp32/main.py:play_mode`). A 8 kHz (125 µs/muestra) aguanta; a
   16 kHz (62 µs) el loop Python NO llega de forma fiable → no se puede subir el
   sample rate quedándose en el DAC interno.

El PAM8403 es solo un amplificador analógico Class-D: amplifica lo que el DAC
saca, no mejora la fuente.

## Decisión

Reemplazar el **DAC interno + PAM8403** por un módulo **MAX98357A**: DAC +
amplificador Class-D I2S en una sola placa (~$3 USD / ~20 BOB).

Por qué es EL salto:

- **16-bit** en vez de 8 → el piso de ruido de cuantización desaparece.
- MicroPython `machine.I2S` lo maneja con **DMA por hardware** → cero jitter del
  loop Python (la CPU solo rellena un buffer, el periférico I2S marca el tiempo).
- Permite subir a **16 kHz** (o 22.05 kHz) sin estrangular la CPU → voz nítida.
- Class-D 3.2 W: más fuerte y limpio que el PAM8403 para la feria.

## Cableado

> ⚠️ Corrección sobre el sketch inicial: NO usar GPIO22 para DIN — está ocupado
> por el L298N (motor IN3). Pines I2S elegidos entre los **libres** del DevKit.

| ESP32 (DevKit) | MAX98357A | Nota |
|---|---|---|
| GPIO26 | BCLK | reloj de bit (era DAC2, libre) |
| GPIO27 | LRC  | word-select (L/R clock) |
| GPIO14 | DIN  | datos I2S |
| 3V3    | VIN  | el módulo acepta 3.3–5 V; 5 V da más volumen |
| GND    | GND  | común |
| —      | GAIN | dejar **flotante** = 9 dB (default). A GND = 12 dB; a VIN = 3 dB |
| —      | SD   | a VIN (siempre encendido). Flotante = (L+R)/2, sirve para mono |

- Quitar el **PAM8403** y el cable de GPIO25.
- El speaker (4–8 Ω) va a los terminales del MAX98357A.
- GPIO25 queda **libre** (ya no es DAC de salida).
- Pines I2S libres confirmados contra `docs/HARDWARE.md`: 26/27/14 no chocan con
  OLED (32/33), servos (13/12), mic ADC (34), motores (19/21/22/23 + ENA/ENB
  16/4).

## Cambios de software

### Firmware (`firmware/Esp32/main.py`)
- Sustituir `dac = DAC(Pin(25))` por:
  ```python
  from machine import I2S
  audio_out = I2S(0, sck=Pin(26), ws=Pin(27), sd=Pin(14),
                  mode=I2S.TX, bits=16, format=I2S.MONO,
                  rate=16000, ibuf=8192)
  ```
- Reescribir `play_mode`: en vez del loop `_dac_write(byte)` muestra-a-muestra,
  hacer `audio_out.write(buf)` con bloques de PCM 16-bit. El DMA marca el tiempo
  → se elimina todo el busy-wait y el re-anclado de reloj.
- `SAMPLE_RATE = 16000`, los buffers de recepción pasan a manejar int16.
- El "silencio al terminar" ya no es `dac.write(128)`; basta con `deinit()` o
  escribir un bloque de ceros.

### Laptop (`robot_bob/`)
- `config.py`: `SAMPLE_RATE = 16000`. Revisar `FRAME_*`, `TTS_SEND_CHUNK_BYTES`
  (ahora son bytes de int16 → el doble por muestra).
- `voice_pipeline.py` `_mp3_a_u8`: cambiar el encoder de `pcm_u8` a
  **`pcm_s16le`** (16-bit little-endian) y el filtro `aresample=16000`. Renombrar
  la función (ya no es u8).
- `shared/voicechatLap/audio_io.py`: el framing de envío sigue igual (header de
  4 bytes BE de longitud + body), solo cambia el contenido (int16 en vez de u8).
  El mic puede quedarse en 8-bit/8 kHz (esto es solo el path del speaker).

## Mitigaciones ya aplicadas (sin esperar el módulo)

Mientras llega el MAX98357A, ya se exprimió el DAC 8-bit (commit de hoy):

- **Timing de playback** (`play_mode`): se quitó el re-anclado de reloj en cada
  refill (metía un zumbido periódico ~8 Hz en cada borde de chunk). Ahora reloj
  absoluto continuo, re-ancla solo si la red atrasa > 50 ms.
- **Dithering al bajar a 8 bits** (`_mp3_a_u8`): `-dither_method triangular` →
  el grano de cuantización se vuelve hiss suave.
- **EQ / volumen / pitch** en `config.py`: banda de presencia 2200 Hz, volumen
  0.85 (usa más rango del DAC), pitch base bajado (menos metálico).

### Opcional (hardware barato, sin esperar nada)
- **Filtro RC de reconstrucción** en GPIO25 antes del PAM8403: R≈1 kΩ en serie +
  C≈100 nF a GND (pasa-bajos ~1.6 kHz... ajustar a ~3 kHz con C≈47 nF) → suaviza
  el escalonado del DAC y quita ruido de alta frecuencia. Tapita mientras dure el
  DAC interno; se descarta al migrar a I2S.

## Consecuencias

**A favor:** salto de calidad real (8-bit telefónico → 16-bit/16 kHz limpio),
elimina jitter, más volumen para la feria, libera GPIO25.

**En contra:** requiere comprar e integrar una pieza; toca firmware Y laptop en
paralelo (el audio debe migrar en ambos lados a la vez o no suena); hay que
recablear. Riesgo bajo (es un patrón I2S estándar, muy documentado).

## Presupuesto

Registrar con `genesis-cost` al comprar:
- MAX98357A I2S amp+DAC: ~$3 USD (~20 BOB).
- (El PAM8403 actual se retira; el speaker se reutiliza.)

## Pendiente para ejecutar

1. Conseguir el módulo MAX98357A.
2. Branch `feat/speaker-i2s`.
3. Migrar firmware + laptop juntos; probar con `bob-test` voz.
4. Medir antes/después; registrar en `genesis-log`.
