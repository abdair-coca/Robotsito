# Feature 1 — "Actitud": Bob habla solo, piensa en voz alta y hace muecas

Plan de acción para que Bob parezca **vivo cuando NADIE le habla**: suelta comentarios
espontáneos, piensa en voz alta, tiene actitud (sass, opiniones) y hace muecas con los
ojos y la cabeza. Objetivo emocional: que un transeúnte que pasa y lo escucha
"hablando solo" sienta que hay **alguien** ahí, no una máquina en espera.

> Esto EXTIENDE la capa out-of-chat (idle/presencia sin conversación). NO toca el núcleo
> de conversación ya validado (sistema emocional en [ARCHITECTURE.md](ARCHITECTURE.md) §5).
> Es comportamiento autónomo nuevo.

---

## 1. Qué es (y qué no)

| Es | No es |
|---|---|
| Soliloquio: Bob se habla a sí mismo / al aire. | Conversación con un usuario (eso ya existe). |
| Pensar en voz alta: "uff… ¿qué hora será?", "me aburro, che". | Asistente que responde preguntas. |
| Actitud: opiniones, quejas livianas, picardía sin que le pregunten. | Hablar encima de una charla real en curso. |
| Muecas: secuencias expresivas de ojos + micro-gestos de cabeza. | Animación facial nueva 3D (seguimos con los 21 estados del firmware). |

---

## 2. Filosofía de diseño

- **Solo cuando está libre.** Dispara únicamente en `IDLE`, `PRESENCE` (alguien cerca pero
  sin conversación) y `CONVERSATION_IDLE`. **NUNCA** durante `LISTENING`/`THINKING`/`SPEAKING`
  de un turno real.
- **No ser molesto.** Cadencia con cooldown y probabilidad. Mejor poco y bueno que un loro.
- **Engancha, no espanta.** En `PRESENCE` el soliloquio sirve de carnada ("¿me vas a dejar
  hablando solo o qué?") para invitar a la persona a iniciar charla.
- **Coherente con el mood y la personalidad** del system prompt (compañero de feria UATF).
- **Interrumpible.** Si aparece voz real o wake word mientras Bob habla solo, corta y
  atiende (reusa el barge-in existente).

---

## 3. Disparadores

| Estado | Condición | Qué suelta |
|---|---|---|
| `IDLE` (solo, aburrido) | Tras `SOLO_IDLE_MIN_S` sin cara + cada evaluación con prob. `P_SOLILOQUIO` | Mutter de aburrimiento / curiosidad / "¿habrá alguien?" + mueca |
| `PRESENCE` (alguien cerca, no habla) | Cara visible > `PRESENCE_NUDGE_S` sin iniciar charla | Comentario-carnada con actitud para invitar |
| `CONVERSATION_IDLE` (entre turnos, antes del timeout) | Silencio del usuario > `GAP_FILLER_S` | "Pensar en voz alta" liviano para llenar el hueco |
| Cualquiera de los anteriores | Timer suelto | **Mueca pura** (sin voz): secuencia de ojos + micro-cabeceo |

Reglas duras del scheduler:
- Cooldown global `SOLILOQUIO_COOLDOWN_S` entre soliloquios hablados.
- Nunca dos hablados seguidos sin pausa; las muecas puras (sin voz) sí pueden ser más frecuentes.
- Se desactiva en sueño profundo (`idle_ms > SLEEP_THR`) salvo un bostezo/ronquido ocasional.

---

## 4. Generación de contenido

Dos fuentes, en cascada (barata → rica):

1. **Banco local curado** (`config.py`, listas por categoría: aburrimiento, curiosidad,
   actitud, carnada, gap-filler). Cero costo, cero latencia, siempre disponible. Cada frase
   ya trae su `[EMO:X]`. Es el default y el fallback sin internet.
2. **LLM (Groq) "modo soliloquio"** (opcional, `SOLILOQUIO_USA_LLM`): prompt corto y barato
   (`MAX_TOKENS` ~40) que pide UNA línea espontánea coherente con el contexto (hora, mood,
   si hay alguien cerca). Da variedad pero cuesta llamadas + latencia → rate-limit fuerte.

> Recomendado arrancar con el **banco local** (Fase A) y sumar LLM después (Fase C). Mezcla
> 80% banco / 20% LLM para variedad sin quemar API.

---

## 5. Entrega (voz + ojos + cabeza)

- **Voz:** TTS por el **parlante de la laptop** (`USE_ROBOT_SPEAKER=False`, ya estable).
  Reutiliza el pipeline TTS de `voice_pipeline`. Frases cortas (1 oración).
- **Ojos (OLED):** el `[EMO:X]` de la frase manda el estado; para muecas puras se envía una
  **secuencia** (ej. `CURIOSO`→`TRAVIESO`→`HABLANDO`) vía `serial_manager.cmd_estado`.
- **Cabeza (muecas físicas):** micro-gestos **suaves** (ladeo, asentir corto) vía
  `behavior` / `cmd_servo`. ⚠️ **Límite de poder:** la fuente actual hace brownout con
  movimiento fuerte → muecas de cabeza **lentas y de poca amplitud** hasta tener fuente ≥2 A.
  Si hay dudas, mueca = solo OLED.

---

## 6. Integración crítica (lo que puede romper)

1. **Eco / auto-disparo del mic.** Mientras Bob habla solo, su voz entra al mic y podría
   disparar el wake word o el barge-in. **Mitigación:** durante el soliloquio hablado,
   silenciar el wake-monitor y aplicar el `BARGE_IN_SETTLE_MS` ya existente; reanudar el
   mic al terminar. Reusar la lógica de gating que ya usa el TTS de conversación.
2. **No pisar conversación real.** El scheduler consulta `StateMachine.estado` y aborta si
   no está en {IDLE, PRESENCE, CONVERSATION_IDLE}. Si entra un turno real, el soliloquio
   en curso se corta limpio (mismo stop del barge-in).
3. **Costo de API.** Si se usa LLM, rate-limit duro (cooldown + tope por minuto). El banco
   local no cuesta nada → preferirlo.
4. **Poder (brownout).** Muecas de cabeza suaves; nunca coreografías. Hablar (TTS) no
   mueve servos → seguro.
5. **Concurrencia.** El soliloquio comparte el lock del TTS y el `serial_manager` (cola
   priorizada ya thread-safe). No abrir un segundo dueño del audio ni del serial.

---

## 7. Archivos a tocar

| Archivo | Cambio |
|---|---|
| `config.py` | Flags + tiempos + banco de frases (sección nueva "SOLILOQUIO"). |
| `voice_pipeline.py` | Método `decir_soliloquio(texto)` (TTS + gating de mic) reutilizando el pipeline TTS; generador LLM opcional. |
| `state_machine.py` | Helper `puede_soliloquiar()` (estado válido) + hook de "última actividad". |
| `behavior.py` | Tick del scheduler de soliloquio + muecas de cabeza suaves. |
| `expression_engine.py` | Secuencias de mueca (OLED) reutilizando los 21 estados. |
| `Esp32/oled_ojos.py` (opcional) | Solo si se quieren muecas nuevas; recompilar `.mpy`. Evitable al inicio. |

---

## 8. Fases de implementación (gateadas — test + feedback entre cada una)

### Fase A — Soliloquio hablado con banco local (MVP)
1. Sección `SOLILOQUIO` en `config.py`: `SOLILOQUIO_ENABLED`, tiempos, prob, cooldown, banco
   de frases por categoría (cada una con `[EMO:X]`).
2. `voice_pipeline.decir_soliloquio(texto)`: parsea `[EMO]`, manda estado al OLED, reproduce
   TTS por laptop, **silencia wake-monitor durante** y lo reanuda al terminar.
3. Scheduler en `behavior` (tick existente @20 Hz, contador propio): si `puede_soliloquiar()`
   y pasó cooldown y prob → elige frase de la categoría según estado y la dice.
4. **Test:** dejar a Bob solo 2-3 min → suelta comentarios espaciados, ojos coherentes, sin
   pisar nada. Validar que su voz NO se auto-dispara como wake.

### Fase B — Muecas (ojos + cabeza suave)
1. Secuencias de mueca en `expression_engine` (puro OLED, sin voz).
2. Micro-gestos de cabeza suaves en `behavior` (respetando poder).
3. Scheduler de muecas (más frecuente que el hablado, sin audio).
4. **Test:** Bob "hace caras" estando solo; cabeza se mueve poco y suave; sin brownout.

### Fase C — Variedad con LLM (opcional)
1. Prompt "modo soliloquio" + `decir_soliloquio` con fuente LLM (rate-limited).
2. Mezcla banco/LLM (~80/20). Coherencia con mood/hora/presencia.
3. **Test:** variedad sin repetición molesta; sin quemar API; latencia tolerable.

### Fase D — Pulido
1. Carnada en PRESENCE afinada (invita sin acosar).
2. Gap-filler en CONVERSATION_IDLE.
3. Cadencias ajustadas con pruebas reales de feria.

---

## 9. Config nueva (esbozo en `config.py`)

```python
# ── SOLILOQUIO / ACTITUD (out-of-chat) ──
SOLILOQUIO_ENABLED      = True
SOLO_IDLE_MIN_S         = 12.0    # s solo antes de empezar a hablar solo
PRESENCE_NUDGE_S        = 6.0     # s con alguien cerca sin charla → carnada
GAP_FILLER_S            = 3.5     # s de silencio entre turnos → pensar en voz alta
P_SOLILOQUIO            = 0.35    # prob por evaluación
SOLILOQUIO_COOLDOWN_S   = 25.0    # s mínimos entre soliloquios hablados
MUECA_COOLDOWN_S        = 8.0     # muecas puras (sin voz) más seguidas
SOLILOQUIO_USA_LLM      = False   # Fase C
SOLILOQUIO_LLM_MAX_TOK  = 40

BANCO_SOLILOQUIO = {
  "aburrimiento": ["[EMO:PENSANDO] Uff… cuánto silencio, ¿no?",
                   "[EMO:TRAVIESO] Si nadie viene, me pongo a contar baldosas."],
  "curiosidad":   ["[EMO:CURIOSO] ¿Qué habrá para comer en la feria hoy?"],
  "actitud":      ["[EMO:TRAVIESO] Yo acá, el robot más sociable y nadie me habla, qué fea."],
  "carnada":      ["[EMO:CURIOSO] Ey, ¿me vas a dejar hablando solo o te animás?"],
  "gap_filler":   ["[EMO:PENSANDO] Mmm… déjame pensar…"],
}
```

---

## 10. Criterio de éxito

1. Bob solo > 1 min: suelta comentarios espontáneos espaciados y con ojos coherentes.
2. Un transeúnte que solo lo escucha siente que "hay alguien", no una máquina en pausa.
3. En PRESENCE, la carnada invita a iniciar charla sin sonar repetitiva ni acosadora.
4. **Su propia voz NUNCA se auto-dispara** como wake word ni como barge-in.
5. El soliloquio **jamás** pisa una conversación real; corta limpio si entra un usuario.
6. Sin brownout por las muecas de cabeza (con la limitación de poder actual).

---

## 11. Fuera de alcance / riesgos

- **Fuera:** memoria entre sesiones, coreografías de baile, muecas con hardware nuevo (brazos).
- **Riesgo poder:** muecas de cabeza limitadas hasta tener fuente ≥2 A (ver Objetivo 2 en
  `agents.md`). Mitigación: arrancar con muecas solo-OLED.
- **Riesgo eco:** si el gating del mic falla, Bob se auto-conversa. Prioridad #1 de testing.
- **Riesgo costo:** LLM en soliloquio puede sumar llamadas; el banco local es el camino seguro.

---

## 📍 Estado de implementación

| Fase | Status |
|---|---|
| A — Soliloquio hablado con banco local | ✅ implementado y validado en vivo |
| B — Muecas (ojos + cabeza suave) | ✅ implementado; cabeza gateada por poder (config `MUECA_HEAD_ENABLED`) |
| C — Variedad con LLM (mezcla banco/LLM) | ✅ implementado (`SOLILOQUIO_USA_LLM`, `_RATIO`) |
| D — Pulido | ✅ anti-repetición + carnada acotada (`SOLILOQUIO_MAX_CARNADAS`) + cadencia con jitter |
| Gap-filler en CONVERSATION_IDLE | ⏸️ diferido (bajo valor, riesgo de pisar el cierre de charla) |

**Coherencia de sueño:** soliloquio y muecas se callan cuando `is_asleep()` (pose de
sueño = quieto y callado). `SLEEP_THR_S` ahora se lee de config.

**Feature "Actitud" completo.** Para feria, bajar `SOLILOQUIO_LLM_RATIO` (~0.3) y subir
`SOLILOQUIO_COOLDOWN_S` (~20) para cuidar costo/latencia; los valores actuales son de demo.
