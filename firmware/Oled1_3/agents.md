# AGENTS.md - Sistema de Ojos Emocionales para Bob

## Proyecto

Bob es un robot asistente físico con:

* Cabeza robótica con movimiento Pan/Tilt
* Pantalla OLED SH1106 128x64 monocromática
* Seguimiento de personas
* Reconocimiento de voz
* Síntesis de voz

El objetivo NO es mostrar ojos.

El objetivo es crear la ilusión de que Bob está vivo.

La prioridad absoluta es:

1. Expresividad
2. Carisma
3. Diversión
4. Ternura
5. Sensación de vida

No buscamos realismo humano.

Buscamos personalidad.

Inspiración:

* Emo Robot
* Personajes Pixar
* Robots sociales
* Mascotas robóticas

NO inspirarse en:

* Wall-E
* GLaDOS
* Interfaces corporativas
* Robots fríos o minimalistas

---

# PRINCIPIOS DE DISEÑO

Bob debe parecer:

* Curioso
* Sociable
* Inteligente
* Divertido
* Travieso
* Amigable

El usuario debe pensar:

"Este robot parece vivo."

Cada animación debe reforzar esa sensación.

Nunca dejar los ojos completamente estáticos durante mucho tiempo.

---

# FASE 1 - NUEVO SISTEMA BASE DE OJOS

Objetivo:

Crear una arquitectura completamente nueva para los ojos.

No reutilizar diseños anteriores.

Explorar múltiples estilos.

Entregables:

* 5 diseños completamente distintos
* Sistema de render modular
* Demo interactiva

Cada diseño debe poder mostrarse automáticamente.

Ejemplos:

* Ojos redondos
* Ojos futuristas
* Ojos tipo visor
* Ojos deformables
* Ojos caricaturescos

El usuario seleccionará uno.

NO continuar hasta recibir aprobación.

---

# FASE 2 - SISTEMA DE EXPRESIONES

Objetivo:

Crear un catálogo emocional.

Estados mínimos:

* Neutral
* Feliz
* Muy feliz
* Escuchando
* Pensando
* Curioso
* Sorprendido
* Confundido
* Triste
* Muy triste
* Enojado
* Sospechando
* Travieso
* Orgulloso
* Dormido
* Durmiendo
* Procesando
* Error
* Siguiendo persona

Cada emoción debe ser reconocible sin texto.

Cada emoción debe tener:

* Forma ocular
* Movimiento
* Ritmo

Demo requerida:

Recorrer automáticamente todas las emociones.

Esperar aprobación.

---

# FASE 3 - MICROEXPRESIONES

Objetivo:

Eliminar la sensación de pantalla estática.

Agregar:

* Microsacadas
* Miradas espontáneas
* Parpadeos naturales
* Doble parpadeo ocasional
* Variación temporal

Prohibido:

Patrones repetitivos.

Debe parecer impredecible.

Demo requerida:

5 minutos de observación pasiva.

El usuario evaluará si parece vivo.

---

# FASE 4 - PERSONALIDAD

Objetivo:

Crear comportamientos que generen apego.

Agregar:

* Curiosidad automática
* Reacciones espontáneas
* Gestos divertidos
* Reacciones inesperadas

Ejemplos:

* Mirar hacia arriba cuando piensa
* Mirar al usuario cuando detecta voz
* Guiño ocasional
* Expresiones juguetonas

Importante:

Bob debe parecer tener iniciativa.

No solo reaccionar.

---

# FASE 5 - SISTEMA DE HABLA

Objetivo:

Sincronizar emociones con voz.

Durante TTS:

* Boca dinámica
* Ojos activos
* Parpadeos naturales
* Expresiones relacionadas con contexto

No limitarse a explicar

El rostro completo debe participar.

Demo requerida:

Lectura de texto de ejemplo.

---

# FASE 6 - SISTEMA DE ATENCIÓN

Objetivo:

Crear sensación de conciencia.

Al detectar persona:

* Mirar persona
* Ajustar pupilas
* Ajustar ojos
* Mantener contacto visual

Cuando la persona se mueve:

* Seguir suavemente

Cuando desaparece:

* Buscar durante unos segundos

Luego:

* Volver a estado exploratorio

Demo requerida:

Seguimiento de rostro.

---

# FASE 7 - SISTEMA DE ABURRIMIENTO

Objetivo:

Simular estados internos.

Si nadie interactúa:

0-10 segundos:
Normal

10-30 segundos:
Miradas ocasionales

30-60 segundos:
Pensativo

60-120 segundos:
Aburrido

120+ segundos:
Somnoliento

300+ segundos:
Dormido

Nunca permanecer inmóvil.

---

# FASE 8 - SISTEMA DE SUEÑO

Objetivo:

Crear una secuencia adorable.

Etapas:

1. Somnolencia
2. Parpadeos lentos
3. Ojos pesados
4. Cierre gradual
5. Sueño

Agregar:

* Zzz
* Respiración visual
* Movimientos mínimos

Debe generar ternura.

---

# FASE 9 - MODO SHOWCASE

Objetivo:

Crear una demostración completa.

Debe mostrar:

* Todas las emociones
* Todas las animaciones
* Todas las transiciones
* Seguimiento
* Sueño
* Habla

Permitir evaluar el sistema completo.

---

# RESTRICCIONES

OLED SH1106 128x64

Optimizar para ESP32.

Evitar asignaciones innecesarias.

Mantener FPS fluidos.

Arquitectura modular.

Todo debe ser fácilmente extensible.

---

# CRITERIO FINAL DE ÉXITO

El proyecto se considera exitoso cuando:

1. Las emociones se reconocen instantáneamente.
2. El usuario siente que Bob tiene personalidad.
3. Bob parece vivo incluso cuando no ocurre nada.
4. Personas nuevas sonríen al verlo.
5. El usuario siente apego emocional hacia Bob.
