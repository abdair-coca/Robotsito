"""
modo_dueno.py — Comandos que solo obedecen a la voz del dueño (Fase V5).

Comandos:
  "Bob, te presento a X"   guarda la cara que Bob tiene delante con ese nombre,
                           como amigo del dueño, y la saluda.
  "noticias de tecnología" 5 titulares del feed de tecnología de Google News,
  / "informe del día"      contados por el modelo de la nube (Groq) solo con esos
                           titulares; sin red o sin Groq, los lee tal cual.
  "Bob, modo ingeniero"    charla técnica con el modelo de la nube, hasta 4 frases
                           por respuesta. "Sal del modo ingeniero" (cualquier voz:
                           solo baja de modo) o el fin de la charla lo apagan.

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

# Noticias de tecnología (feed de Google News, sin API key).
try:
    from config import NOTICIAS_TECH_RSS_URL
except Exception:
    NOTICIAS_TECH_RSS_URL = ('https://news.google.com/rss/headlines/section/topic/'
                             'TECHNOLOGY?hl=es-419&gl=BO&ceid=BO:es-419')
try:
    from config import NOTICIAS_TECH_MAX
except Exception:
    NOTICIAS_TECH_MAX = 5

# Modo ingeniero. El prompt lo afina Abdair en config.py (MODO_INGENIERO_PROMPT);
# este es el de arranque.
try:
    from config import MODO_INGENIERO_PROMPT
except Exception:
    MODO_INGENIERO_PROMPT = (
        'Eres Bob en MODO INGENIERO: el compañero técnico de tu creador, Abdair, estudiante '
        'de Ingeniería Informática en la UATF (Potosí, Bolivia). Juntos construyen el '
        'Proyecto Génesis, un humanoide de bajo costo con IA, y tú eres su prototipo: '
        'ESP32, cámara ESP32-CAM, servos pan/tilt, ojos OLED, voz (STT, LLM y TTS) y '
        'memoria de personas por cara y por voz. En este modo hablas de ingeniería con '
        'precisión, propones ideas innovadoras y baratas explicando por qué funcionarían, '
        'y haces preguntas técnicas para profundizar. Si algo no lo sabes, lo dices. '
        'Respondes en español, para decir en voz alta: máximo 4 frases cortas, de unas '
        '20 palabras cada una, sin markdown, listas ni código. Casi siempre terminas con '
        'una pregunta.')
ING_MAX_FRASES = 4
ING_MAX_TOKENS = 600     # gpt-oss gasta parte en razonar: 160 no alcanza para 4 frases

# ── Detección ───────────────────────────────────────────────────────────────────
_RE_PRESENTAR = re.compile(
    r'\b(?:te\s+presento\s+a|te\s+quiero\s+presentar\s+a|conoce\s+a)\s+'
    r'(?:mi\s+(?:amig[oa]|compañer[oa]|herman[oa]|prim[oa]|profe(?:sor|sora)?|novi[oa])\s+)?'
    r'([a-záéíóúñ]{2,20})', re.IGNORECASE)
# "te presento a mi amiga" (sin nombre) no es un comando: el LLM le pregunta.
_NO_NOMBRE_PRESENTAR = {'mi', 'mis', 'este', 'esta', 'ese', 'esa', 'todos', 'todas',
                        'alguien', 'ellos', 'ellas', 'nadie'}


# Las noticias generales ("noticias de hoy") siguen abiertas a todos (assistant.py).
_RE_NOTICIAS_TECH = re.compile(
    r'\b(?:noticias|novedades|titulares)\s+(?:de\s+)?(?:la\s+)?'
    r'(?:tecnolog[ií]a|tech|tecnol[oó]gic[ao]s)\b'
    r'|\bqu[eé]\s+hay\s+de\s+nuevo\s+en\s+(?:la\s+)?tecnolog[ií]a\b'
    r'|\binforme\s+del\s+d[ií]a\b', re.IGNORECASE)
_RE_ING_SALIR = re.compile(
    r'\b(?:sal[ií]?(?:r)?|desactiva(?:r)?|apaga(?:r)?|termina(?:r)?|quita(?:r)?)\s+'
    r'(?:del?\s+|el\s+)?modo\s+ingenier[oa]\b|\bmodo\s+normal\b', re.IGNORECASE)
# Entrar pide un verbo ("activa el modo ingeniero") o la orden sola ("Bob, modo
# ingeniero"): así "¿qué es el modo ingeniero?" no lo activa.
_RE_ING_ENTRAR = re.compile(
    r'\b(?:activa(?:r)?|entra(?:r)?|pon(?:te|er)?|pasa(?:r)?|cambia(?:r)?)\s+'
    r'(?:en\s+|a\s+|al\s+)?(?:el\s+)?modo\s+ingenier[oa]\b', re.IGNORECASE)
_RE_LIMPIA = re.compile(r'[^\wáéíóúñ ]+')


def _es_orden_sola(texto: str, orden: str) -> bool:
    t = _RE_LIMPIA.sub(' ', (texto or '').lower()).split()
    if t[:1] == ['bob']:
        t = t[1:]
    return ' '.join(t) in (orden, orden[:-1] + 'a')


def detectar(texto: str) -> Optional[Tuple[str, dict]]:
    """(comando, argumentos) si el texto es un comando de dueño, o None."""
    if _RE_ING_SALIR.search(texto or ''):
        return 'salir_ingeniero', {}
    if _RE_ING_ENTRAR.search(texto or '') or _es_orden_sola(texto, 'modo ingeniero'):
        return 'ingeniero', {}
    m = _RE_PRESENTAR.search(texto or '')
    if m:
        from voice_pipeline import _NO_NOMBRE
        nombre = m.group(1)
        if nombre.lower() not in _NO_NOMBRE | _NO_NOMBRE_PRESENTAR:
            return 'presentar', {'nombre': nombre.capitalize()}
    if _RE_NOTICIAS_TECH.search(texto or ''):
        return 'noticias_tech', {}
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
    if nombre_cmd == 'salir_ingeniero':      # bajar de modo no necesita la voz del dueño
        salir_ingeniero(vp, avisar=True)
        return True
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
    elif nombre_cmd == 'noticias_tech':
        noticias_tech(vp)
    elif nombre_cmd == 'ingeniero':
        entrar_ingeniero(vp)
    return True


# ── Modo ingeniero ──────────────────────────────────────────────────────────────
def entrar_ingeniero(vp) -> None:
    from voice_pipeline import console
    if getattr(vp, '_modo_ingeniero', False):
        _decir(vp, 'Ya estamos en modo ingeniero. ¿En qué seguimos?', 'CURIOSO')
        return
    vp._modo_ingeniero = True
    console.print('[bold magenta][dueño][/] modo ingeniero ON')
    _decir(vp, 'Modo ingeniero activado. ¿En qué trabajamos hoy, creador?', 'CURIOSO')


def salir_ingeniero(vp, avisar: bool = False) -> None:
    from voice_pipeline import console
    if not getattr(vp, '_modo_ingeniero', False):
        if avisar:
            _decir(vp, 'No estaba en modo ingeniero.', 'CONFUNDIDO')
        return
    vp._modo_ingeniero = False
    console.print('[bold magenta][dueño][/] modo ingeniero OFF')
    if avisar:
        _decir(vp, 'Modo ingeniero desactivado. Vuelvo a ser el Bob de siempre.', 'FELIZ')


def ajustes_ingeniero(vp):
    """Lo que cambia en cada pedido al LLM mientras dura el modo ingeniero, o None
    fuera del modo: {llm, modelo, extra, max_tokens, max_frases, prompt}."""
    if not getattr(vp, '_modo_ingeniero', False):
        return None
    from voice_pipeline import GROQ_LLM_MODEL, DUENO_ID
    extra = {'reasoning_effort': 'low'} if GROQ_LLM_MODEL.startswith('openai/gpt-oss-') else {}
    contexto = vp._memoria.contexto(DUENO_ID) if vp._memoria is not None else ''
    prompt = MODO_INGENIERO_PROMPT + (f'\n\n═══ LO QUE SABES DE ABDAIR ═══\n{contexto}'
                                      if contexto else '')
    return {'llm': vp._client, 'modelo': GROQ_LLM_MODEL, 'extra': extra,
            'max_tokens': ING_MAX_TOKENS, 'max_frases': ING_MAX_FRASES, 'prompt': prompt}


def fin_charla(vp) -> None:
    """Al terminar la charla se apaga todo modo de dueño que dure la charla."""
    salir_ingeniero(vp)


# ── Noticias de tecnología del día ──────────────────────────────────────────────
_PROMPT_NOTICIAS = (
    'Eres Bob, un robot simpático que le cuenta las noticias a su creador. Te pasan '
    'titulares REALES de noticias de tecnología de hoy. Cuéntaselos en español, para '
    'decir en voz alta: una línea por noticia, con una o dos frases cortas y '
    'entusiasmo. Usa SOLO lo que dicen los titulares: no inventes datos, cifras, '
    'nombres ni detalles que no estén. Empieza con una línea corta de introducción y '
    'termina con una línea corta de cierre. Sin listas, viñetas, números ni markdown.')
_ORDINALES = ['Primera', 'Segunda', 'Tercera', 'Cuarta', 'Quinta', 'Sexta', 'Séptima']
_RE_VINETA = re.compile(r'^\s*(?:[-*•·]+|\d+[.)-])\s*')


def _resumir_noticias(vp, titulares):
    """Líneas para decir, redactadas por el modelo de la nube, o None si falla."""
    from voice_pipeline import GROQ_LLM_MODEL, console
    lista = '\n'.join(f'{i}. {t}' for i, t in enumerate(titulares, 1))
    extra = {'reasoning_effort': 'low'} if GROQ_LLM_MODEL.startswith('openai/gpt-oss-') else {}
    try:
        r = vp._client.chat.completions.create(
            model=GROQ_LLM_MODEL, temperature=0.3, max_tokens=900,
            messages=[{'role': 'system', 'content': _PROMPT_NOTICIAS},
                      {'role': 'user', 'content': f'Titulares de hoy:\n{lista}'}],
            **extra)
        texto = (r.choices[0].message.content or '').replace('**', '')
    except Exception as e:
        console.print(f'[yellow][dueño] resumen de noticias falló: {e}[/]')
        return None
    lineas = [_RE_VINETA.sub('', l).strip() for l in texto.splitlines()]
    lineas = [l for l in lineas if l]
    return lineas[:len(titulares) + 2] or None


def _leer_titulares(titulares):
    """Respaldo sin modelo: los titulares tal cual, con intro y cierre fijos."""
    lineas = ['Estas son las noticias de tecnología de hoy.']
    for i, t in enumerate(titulares):
        lineas.append(f'{_ORDINALES[i] if i < len(_ORDINALES) else "Otra"}: {t.rstrip(".")}.')
    lineas.append('¡Eso es todo por hoy!')
    return lineas


def noticias_tech(vp) -> None:
    from assistant import obtener_noticias
    from voice_pipeline import console
    _decir(vp, 'Déjame revisar qué pasó hoy en el mundo de la tecnología...', 'PENSANDO')
    titulares = obtener_noticias(NOTICIAS_TECH_MAX, url=NOTICIAS_TECH_RSS_URL)
    if not titulares:
        _decir(vp, 'No pude traer las noticias de tecnología, parece que no hay internet. '
                   '¿Probamos en un rato?', 'TRISTE')
        return
    lineas = _resumir_noticias(vp, titulares)
    origen = 'nube'
    if lineas is None:
        lineas, origen = _leer_titulares(titulares), 'titulares'
    console.print(f'[bold magenta][dueño][/] noticias de tecnología: {len(titulares)} '
                  f'titulares ({origen})')
    for linea in lineas:
        _decir(vp, linea, 'EMOCIONADO')
    vp._convo.append({'role': 'assistant', 'content': ' '.join(lineas)})


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
