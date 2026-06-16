# Robot Bob — InteractiveGoal

## 🎯 Objetivo

Que cualquier persona que hable con Bob durante 30 segundos sienta que **Bob la entiende**. No que procesa lenguaje — que la **entiende emocionalmente**. Cada expresión de sus ojos, cada respuesta de su voz, debe coincidir con lo que está sintiendo el momento.

Bob debe parecer un compañero con quien tener una charla — no un asistente. Una persona nueva debe sonreír al despedirse. Una persona que vuelve por segunda vez debe acordarse de él.

---

## 🧠 Filosofía de diseño

| Dimensión | Decisión |
|---|---|
| **Rango emocional** | Híbrido con recuperación rápida. Bob puede sentirse mal (TRISTE, CONFUNDIDO, hasta brevemente ENOJADO) por 1-2 segundos cuando lo amerita, pero siempre vuelve al humor. Nunca se queda en negativo. |
| **Memoria de ánimo** | Acumulativa **dentro** de la conversación, reset al terminar. Tras 3-4 turnos positivos Bob está "más feliz" (más bounces, más TRAVIESO en respuestas, más FELIZ). Tras frustraciones Bob está "más cauto" (más PENSANDO, menos chistes, sacadas más cortas). |
| **Fuente de emoción** | El **LLM la decide** por frase. Bob le pide al modelo que prefije cada respuesta con `[EMO:X]`. Esto le permite reaccionar con matices que keyword-matching pierde (sarcasmo, ironía, momentos tiernos). |
| **Scope** | Foco TOTAL en la interactividad durante el chat. Out-of-chat (idle, aburrimiento, sueño) ya está pulido — no se agrega nada nuevo ahí. |

---

## 🎭 Sistema emocional: 3 capas

```
┌─────────────────────────────────────────────────────┐
│ 1. MOOD DRIFT       — sentimiento base de la convo  │
│ 2. INSTANT REACTION — flash a cosas del usuario     │
│ 3. EXPRESSION TAG   — emoción de cada frase de Bob  │
└─────────────────────────────────────────────────────┘
```

### Capa 1 — Mood Drift (estado de ánimo)

Bob arranca cada conversación con `mood = 0.0` (neutral). Cada interacción modifica:

| Evento | Δmood |
|---|---|
| Usuario dice cumplido / cariño | +0.25 |
| Bob hace un chiste y el usuario sigue la charla | +0.15 |
| Conversación fluye 4+ turnos | +0.10 por turno extra |
| Usuario insulta o crítica fuerte | -0.30 |
| Usuario silencio incomprendido | -0.05 |
| Usuario despedida | -0.10 |
| Usuario dice cosa rara o random | +0.05 (Bob lo nota gracioso) |

Mood se clampa a `[-1.0, +1.0]`. **Cada turno, mood se acerca a 0 en 5%** (decay), evitando que se quede pegado en extremos.

### Cómo mood afecta visualmente

| Rango mood | Cómo se nota |
|---|---|
| `+0.6 → +1.0` | Bob arranca cada respuesta con bounce, más TRAVIESO, ojos más grandes, micro-sonrisas. |
| `+0.2 → +0.6` | Bob ligeramente más expresivo, más guiños. |
| `-0.2 → +0.2` | Neutral, default. |
| `-0.6 → -0.2` | Bob más PENSANDO antes de responder, menos bounce, sacadas más cortas. |
| `-1.0 → -0.6` | Bob TRISTE breve al inicio de cada turno (200ms), pero el LLM compensa con humor liviano. |

### Capa 2 — Instant Reaction

Cuando el usuario dice algo, ANTES de que Bob responda, los ojos reaccionan a lo que detectamos:

| Lo que dice el usuario | Reacción inmediata (pulso) |
|---|---|
| "te quiero", "eres genial" | **AMOR** 1.5s |
| "jaja", "qué gracioso" | **FELIZ** 800ms |
| "no entiendo" (lo que Bob dijo) | **CONFUNDIDO** 1.2s |
| "estás triste?", "qué pasa" | **CURIOSO** 700ms |
| insulto / palabra fea | **TRISTE** 400ms → **CONFUNDIDO** 800ms (recuperación rápida) |
| despedida | **TRISTE** 900ms |
| pregunta filosófica larga | **PENSANDO** sostenido |
| "tienes hambre?", absurdo cariñoso | **TRAVIESO** 600ms |

### Capa 3 — Expression Tag por frase de Bob

Cada respuesta del LLM viene prefijada con un tag:

```
[EMO:FELIZ] ¡Hola! ¿Qué te trae por la feria?
[EMO:CURIOSO] ¿De qué carrera eres?
[EMO:TRAVIESO] Jaja, esa estuvo buena. ¿Y tú a quién engañas?
[EMO:PENSANDO] Ufff, esa es una pregunta difícil. Déjame ver...
[EMO:FELIZ] La verdad es que sí, me encanta hablar con la gente.
```

El parser extrae el tag antes del TTS. Si no hay tag, default es `HABLANDO`.

**Vocabulario disponible** (de los 21 del firmware): `FELIZ`, `MUY_FELIZ`, `CURIOSO`, `TRAVIESO`, `PENSANDO`, `SORPRENDIDO`, `CONFUNDIDO`, `TRISTE`, `AMOR`, `HABLANDO`.

---

## ✨ Comportamientos específicos a implementar

### Durante LISTENING (Bob escucha)
- Ojos miran fijo al usuario con sacadas micro reforzadas (Bob "engancha" la atención)
- Cejas levantadas ligeramente (atención)
- Si pasa más de 3s sin que el usuario diga nada → CURIOSO breve (Bob "te invita" a seguir)

### Durante THINKING (Bob procesa)
- Estado PENSANDO en el OLED — fija la mirada arriba-derecha
- Cabeza congelada (ya implementado)
- Si tarda más de 2.5s (LLM lento) → mostrar `MUY_FELIZ` micro pulse cuando finalmente arranca a hablar — es como "¡ya sé!"

### Durante SPEAKING (Bob habla)
- Estado se determina por el `[EMO:X]` del LLM
- Si la frase termina con "?" → al final, pasar momentáneamente a CURIOSO (200ms) antes de volver a LISTENING
- Si tiene risa explícita ("jaja", "jejeje") → MUY_FELIZ durante esa palabra

### Reacciones de continuidad
- Tras 3 turnos positivos seguidos → próxima entrada a SPEAKING arranca con MUY_FELIZ (200ms) y FELIZ
- Tras 2 fallos de STT seguidos → próxima salida del LLM se prefija con CONFUNDIDO + Bob agrega "ya me concentro mejor, dime de nuevo"
- Tras "te quiero" / "eres genial" → mood salta a +0.6 directamente, próxima respuesta usa TRAVIESO y bromea

---

## 🗣️ Cambio al system prompt del LLM

El system prompt actual ya define personalidad. Agregar SECCIÓN nueva:

```
═══ FORMATO DE EMOCIONES ═══
Cada frase tuya DEBE empezar con un tag de emoción entre corchetes:
  [EMO:FELIZ]      respuestas positivas, alegres, agradeciendo
  [EMO:MUY_FELIZ]  emocionado, sorprendido positivamente (úsalo poco)
  [EMO:CURIOSO]    preguntas que devuelves, intriga genuina
  [EMO:TRAVIESO]   chistes, bromas, picardía
  [EMO:PENSANDO]   reflexión, "déjame ver", "buena pregunta..."
  [EMO:CONFUNDIDO] no entendiste, "espera, ¿cómo?"
  [EMO:TRISTE]     despedidas, momentos tiernos, lamentos breves
  [EMO:AMOR]       cariño correspondido, momentos dulces
  [EMO:HABLANDO]   neutral, default

Pon UN tag por frase (no por palabra). Si tu respuesta tiene 2 frases,
tag al inicio de cada una. NO uses el tag dentro de la frase, solo al
comienzo y entre corchetes.

Ejemplo:
[EMO:CURIOSO] ¿De qué carrera eres? [EMO:TRAVIESO] No me digas que eres
de comunicación, te voy a robar el micrófono.
```

---

## 🏗️ Implementación — Fases sugeridas

### Fase A — Mood drift + LLM tags (núcleo emocional)
1. Agregar `mood: float` a `StateMachine`
2. Hook en cada turno: aplicar Δmood según contenido
3. Modificar system prompt agregando la sección de tags
4. Parser de tags en `voice_pipeline._stream_llm` que extrae `[EMO:X]` y los pasa al OLED
5. Decay automático del mood (5% por turno)
6. Reset de mood al `fin_turno`

### Fase B — Instant reactions ricas
1. Expandir `expression_engine.react_to_user_text` con más detección
2. Detectar insultos (lista de palabras) → TRISTE → CONFUNDIDO en secuencia
3. Detectar risa del usuario → FELIZ inmediato
4. Detectar pregunta filosófica → PENSANDO antes del LLM

### Fase C — Reacciones contextuales (continuidad)
1. Track de "turnos positivos seguidos" en StateMachine
2. Hook al entrar SPEAKING: prefijar con MUY_FELIZ si mood alto
3. Track de "fallos de STT seguidos" → mensaje empático cuando se llega a 2

### Fase D — Pulido fino
1. Sacadas reforzadas en LISTENING
2. CURIOSO al final de preguntas de Bob
3. MUY_FELIZ momentáneo al salir de LLM lento
4. Cualquier ajuste basado en pruebas reales

---

## 🎯 Criterio de éxito

La integración se considera exitosa cuando podemos decir SÍ a TODAS estas:

1. **¿Una persona nueva sonríe al despedirse de Bob?**
2. **¿Si le dices "te quiero Bob" tu sentís que él lo recibe con cariño visible?**
3. **¿Si lo insultas, ves brevemente que le afectó pero rápidamente lo asume con humor?**
4. **¿Las emociones de los ojos durante el habla coinciden con el tono de cada frase?**
5. **¿En una charla larga (8+ turnos), notas que Bob se va "soltando" — más bounces, más bromas?**
6. **¿Cuando vuelves a hablar con él después de minutos, sentís que parte de cero (mood reseteado) y no que arrastra tu última interacción?**

---

## 🚫 Lo que NO está en este alcance

- Memoria entre conversaciones (cross-session mood)
- Reacción a transeúntes que no inician charla
- Variación por horario del día
- Reconocimiento facial individual (Bob no sabe "es la misma persona que vino antes")
- Expresividad facial 3D / animaciones más complejas que las 21 emociones del firmware
- Cambios en el motor de aburrimiento / sueño (solo bug-fix del Zzz loop ya hecho)

---

## 📍 Estado actual

| Capa | Status |
|---|---|
| Layer 1 (Mood Drift) | ✅ Fase A — validado en vivo |
| Layer 2 (Instant Reaction) | ✅ Fase B — tabla completa (8 reacciones + secuencia de insulto) |
| Layer 3 (LLM Expression Tag) | ✅ Fase A — validado en vivo |
| Continuidad (rachas, empatía STT, arranque eufórico) | ✅ Fase C |
| Pulido fino (invitación, CURIOSO post-pregunta, ¡ya sé!, sacadas escucha) | ✅ Fase D — requiere recompilar .mpy |
| Bug del Zzz loop | ✅ arreglado |
| Cabeza quieta en THINKING | ✅ arreglado |

**InteractiveGoal completo.** Validar con el guión de criterios de éxito (sección 🎯) en la próxima sesión de prueba.

Próximo paso sugerido: Fase A completa (mood + LLM tags) en una sola pasada, luego prueba en vivo con dos o tres conversaciones cortas para validar antes de seguir con Fase B.
