"""Frases fijas del pipeline: relleno, despedidas, saludos, openers y preguntas de voz."""
from __future__ import annotations



RELLENOS = ['Mmm, a ver...', 'Déjame pensar...', 'Mmm...', 'A ver, a ver...', 'Buena pregunta...']


FRASE_SIN_ACCION = 'Eso todavía no lo puedo hacer desde la charla.'


FRASE_CORTE = 'Perdón, se me cortó la idea. ¿Me lo repites?'


REGLA_CHARLA = (' En esta charla no tienes herramientas: solo hablas. Nunca digas que creaste, '
                'abriste, pusiste, enviaste, reproduces o cambiaste algo; si te piden algo que no '
                'puedes hacer, dilo sin rodeos. No repitas literalmente tu respuesta anterior. '
                'Copia los nombres propios tal como vienen en el mensaje.')


# Respuestas cortas cuando JEV decide que Bob debe irse ("vete", "adiós Bob")
DESPEDIDAS_JEV = [
    'Ok, aquí ando si me necesitas.',
    '¡Nos vemos!',
    'Ya me callo, cuídate.',
]


# Saludos rápidos cuando el usuario gatilla wake word ("Bob ...")
WAKE_GREETINGS = [
    '¡Hola bola!',
    '¿Qué pasa calabaza?',
    '¡Aquí estoy!',
    '¡Dime, dime!',
    '¡Sí dime!',
    '¿Qué tranza compadre?',
    '¡Te escucho!',
    '¿Qué hubo qué hay?',
    '¡A la orden!',
    '¿Qué onda banana?',
    '¿Qué tal lechuga?',
    '¡Aquí Bob, reportándose!',
]


# Openers cuando Bob inicia solo (vio una cara permanente, se animó a romper hielo).
# Preguntas abiertas para invitar a charlar — el usuario no esperaba ser hablado.
AUTO_OPENERS = [
    '¡Hola! Soy Bob, ¿y tú cómo te llamas?',
    '¡Buenas! ¿De qué carrera eres?',
    '¡Hey! ¿Qué te trae por la feria?',
    '¡Hola! Te estaba mirando, ¿charlamos?',
    '¡Buenas! ¿Cómo va tu día?',
    '¡Eh, hola! ¿Te cuento un chiste?',
    '¡Hola! ¿Vienes a ver robots o a ver robots?',
    '¡Saludos! ¿Te puedo preguntar algo?',
    '¡Hola! Me aburría, ¿hablamos un rato?',
    '¡Buenas! ¿Sabías que llevo aquí horas? Cuéntame algo.',
    '¡Hola! ¿Eres de Potosí o de visita?',
    '¡Hey! ¿Qué opinas de los robots conversacionales?',
]


# Preguntas abiertas: invitan a hablar 2 s o más (EXP-1-001: con 1 s no alcanza).
PREGUNTAS_VOZ = [
    'Cuéntame qué hiciste hoy.',
    '¿Qué es lo que más te gusta de programar?',
    'Descríbeme el lugar donde estás ahora.',
    '¿Qué música te gusta escuchar y por qué?',
    'Cuéntame algo que quieras que haga en la feria.',
]


NEXT_TURN_TIMEOUT_VOZ = 8.0   # s de espera por cada respuesta al aprender la voz
