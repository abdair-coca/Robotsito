"""
modo_dueno.py — Comandos que solo obedecen a la voz del dueño (Fase V5).

Comandos:
  "Bob, te presento a X"   guarda la cara que Bob tiene delante con ese nombre,
                           como amigo del dueño, y la saluda.

Puerta común: decide solo la VOZ del turno (el comando lo da quien habla, no
quien está frente a la cámara), contra el promedio de voz del dueño:
  · voz del dueño     → "Voz verificada" y ejecuta
  · otra voz          → lo rechaza con gracia y la charla sigue
  · frase muy corta   → pide repetirlo (no ejecuta nada)
  · sin perfil de voz → pide enseñarle la voz primero

atender(vp, texto, analisis) recibe el VoicePipeline en curso (como show.py) y
usa sus piezas (_hablar, _sm, _face_id, _memoria, _get_frame). Devuelve True si
el texto era un comando de dueño (ejecutado o rechazado).

Apagado por defecto: MODO_DUENO_ENABLED en config.py.
"""

import random
import re
import time
from typing import Optional, Tuple

try:
    from config import MODO_DUENO_ENABLED
except Exception:
    MODO_DUENO_ENABLED = False
# Umbral de voz para los comandos de dueño. Default: el mismo de V4 (VOZ_UMBRAL).
try:
    from config import VOZ_UMBRAL_DUENO
except Exception:
    VOZ_UMBRAL_DUENO = None

CARA_ESPERA_S = 1.5     # reintentos para ver la cara de la persona presentada

# ── Detección ───────────────────────────────────────────────────────────────────
_RE_PRESENTAR = re.compile(
    r'\b(?:te\s+presento\s+a|te\s+quiero\s+presentar\s+a|conoce\s+a)\s+'
    r'(?:mi\s+(?:amig[oa]|compañer[oa]|herman[oa]|prim[oa]|profe(?:sor|sora)?|novi[oa])\s+)?'
    r'([a-záéíóúñ]{2,20})', re.IGNORECASE)
# "te presento a mi amiga" (sin nombre) no es un comando: el LLM le pregunta.
_NO_NOMBRE_PRESENTAR = {'mi', 'mis', 'este', 'esta', 'ese', 'esa', 'todos', 'todas',
                        'alguien', 'ellos', 'ellas', 'nadie'}


def detectar(texto: str) -> Optional[Tuple[str, dict]]:
    """('presentar', {'nombre': X}) si el texto es un comando de dueño, o None."""
    m = _RE_PRESENTAR.search(texto or '')
    if m:
        from voice_pipeline import _NO_NOMBRE
        nombre = m.group(1)
        if nombre.lower() not in _NO_NOMBRE | _NO_NOMBRE_PRESENTAR:
            return 'presentar', {'nombre': nombre.capitalize()}
    return None


# ── Puerta de dueño ─────────────────────────────────────────────────────────────
RECHAZOS = [
    'Eso solo se lo hago a mi creador. ¡Buen intento!',
    'Mmm, esa voz no es la de mi creador. Eso queda entre él y yo.',
    'Ese truco solo funciona con la voz de {dueno}. ¡Casi me engañas!',
]


def verificar(vp, analisis) -> str:
    """'si' | 'no' | 'corta' | 'sin_perfil' según la voz del turno."""
    from voice_pipeline import DUENO_ID, VOZ_ID_MIN_S, VOZ_MIN_MUESTRAS, VOZ_UMBRAL
    emb, seg = analisis if analisis else (None, 0.0)
    if emb is None or seg < VOZ_ID_MIN_S:
        return 'corta'
    if vp._memoria.total_voces(DUENO_ID) < VOZ_MIN_MUESTRAS:
        return 'sin_perfil'
    sim = vp._memoria.verificar_voz(DUENO_ID, emb)
    umbral = VOZ_UMBRAL if VOZ_UMBRAL_DUENO is None else VOZ_UMBRAL_DUENO
    vp._log_dueno = f'sim {sim:.2f} (umbral {umbral:.2f}, {seg:.1f} s)'
    return 'si' if sim is not None and sim >= umbral else 'no'


def _decir(vp, texto: str, emo: str) -> None:
    vp._sm.iniciar_hablando()
    vp._hablar(texto, emo)


def _nombre_dueno(vp) -> str:
    from voice_pipeline import DUENO_ID
    p = vp._memoria.persona(DUENO_ID)
    return p[1] if p and p[1] else 'mi creador'


def atender(vp, texto: str, analisis) -> bool:
    """Si el texto es un comando de dueño: verifica la voz, responde y lo
    ejecuta si corresponde. Devuelve True si era un comando (aunque se rechace)."""
    if not MODO_DUENO_ENABLED:
        return False
    cmd = detectar(texto)
    if cmd is None:
        return False
    nombre_cmd, args = cmd
    from voice_pipeline import console
    veredicto = verificar(vp, analisis)
    console.print(f'[bold magenta][dueño][/] {nombre_cmd}: {veredicto} '
                  f'{getattr(vp, "_log_dueno", "")}')
    if veredicto == 'corta':
        _decir(vp, 'No alcancé a reconocer tu voz, ¿me lo dices otra vez?', 'CURIOSO')
        return True
    if veredicto == 'sin_perfil':
        _decir(vp, 'Todavía no conozco la voz de mi creador. Primero dime: Bob, aprende mi voz.',
               'CONFUNDIDO')
        return True
    if veredicto == 'no':
        _decir(vp, random.choice(RECHAZOS).format(dueno=_nombre_dueno(vp)), 'SOSPECHANDO')
        return True
    _decir(vp, 'Voz verificada, creador.', 'ORGULLOSO')
    if nombre_cmd == 'presentar':
        presentar(vp, args['nombre'])
    return True


# ── "Bob, te presento a X" ──────────────────────────────────────────────────────
def _cara_delante(vp):
    """(embedding, edad) de la cara más grande frente a Bob, con unos reintentos."""
    if vp._face_id is None or not vp._face_id.listo or vp._get_frame is None:
        return None, None
    t0 = time.monotonic()
    while True:
        try:
            emb, edad = vp._face_id.analizar(vp._get_frame())
        except Exception:
            return None, None
        if emb is not None or time.monotonic() - t0 >= CARA_ESPERA_S:
            return emb, edad
        time.sleep(0.2)


def presentar(vp, nombre: str) -> None:
    """Guarda (o reconoce) a la persona que está frente a la cámara con ese
    nombre y la saluda. La charla sigue con esa persona."""
    from voice_pipeline import DUENO_ID, console
    mem = vp._memoria
    emb, edad = _cara_delante(vp)
    if emb is None:
        _decir(vp, f'No veo a nadie frente a mí. ¿Dónde está {nombre}? Que se ponga '
                   'delante de mis ojos.', 'CONFUNDIDO')
        return
    dueno = _nombre_dueno(vp)
    m = mem.reconocer(emb)
    if m and m[0] == DUENO_ID:
        _decir(vp, f'¡Pero si ese eres tú, {dueno}! A ti ya te conozco de memoria.', 'TRAVIESO')
        return
    if m:
        pid, viejo, score = m
        if viejo != nombre:
            mem.actualizar(pid, nombre=nombre)
        mem.marcar_visto(pid)
        saludo = f'¡Ah, pero si ya nos conocemos! Hola de nuevo, {nombre}.'
        console.print(f'[bold magenta][dueño][/] presentado (ya conocido): id={pid} '
                      f'{viejo} → {nombre} (score {score:.2f})')
    else:
        pid = mem.registrar(nombre, emb, edad)
        mem.actualizar(pid, relacion=f'amigo de {dueno}')
        mem.registrar_interaccion(pid, 20, 15)     # amigo del dueño: no arranca de cero
        score = None
        saludo = (f'¡Mucho gusto, {nombre}! Cualquier amigo de {dueno} es mi amigo. '
                  'Ya me guardé tu cara, la próxima vez te voy a reconocer.')
        console.print(f'[bold magenta][dueño][/] persona nueva presentada: id={pid} {nombre}')
    # La charla sigue con la persona presentada.
    vp._convo_persona, vp._convo_emb, vp._convo_edad = pid, emb, edad
    vp._convo_face_score = score
    vp._persona_nombre = nombre
    vp._system_prompt_actual = vp._prompt_conocido(pid)
    _decir(vp, saludo, 'MUY_FELIZ')
    vp._convo.append({'role': 'assistant', 'content': saludo})
