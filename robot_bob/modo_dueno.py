"""
modo_dueno.py — Comandos que solo obedecen a la voz del dueño (Fase V5).

Comandos:
  "Bob, te presento a X"   guarda la cara que Bob tiene delante con ese nombre,
                           como amigo del dueño, y la saluda.
  "noticias de tecnología" 5 titulares del feed de tecnología de Google News,
  / "informe del día"      contados por el modelo de la nube (Groq) solo con esos
                           titulares; sin red o sin Groq, los lee tal cual.
  "Bob, modo ingeniero"    charla técnica con Claude Haiku 5.5 vía Claude Code (la
                           suscripción Pro del dueño; Groq de respaldo), hasta 4
                           frases por respuesta. "Sal del modo ingeniero" (cualquier
                           voz: solo baja de modo) o el fin de la charla lo apagan.
  "busca en internet …"    (dentro del modo ingeniero) búsqueda con Serper y respuesta
                           citada (busqueda.py). No abre el navegador.
  "en el navegador …"      (dentro del modo ingeniero) agente web: Claude Code con
  / "abre la página de …"  Playwright MCP y navegador visible, solo si se pide
  / "entra a …"            explícitamente. Bob confirma en voz antes de empezar; lista
                           blanca de herramientas y tope de pasos. Apagado aparte:
                           AGENTE_WEB_ENABLED.

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

import os
import random
import re
import time
from typing import Optional, Tuple

import busqueda

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
ING_TIMEOUT_S = 25.0     # sin respuesta de Claude Code en este tiempo → Groq ese turno

# Motor del modo ingeniero y del agente web: 'claude' (Claude Code con el login Pro
# del dueño) o 'groq'. Si Claude Code no está o falla, el modo ingeniero cae a Groq.
try:
    from config import MOTOR_INGENIERO
except Exception:
    MOTOR_INGENIERO = 'claude'
try:
    from config import CLAUDE_MODELO
except Exception:
    CLAUDE_MODELO = 'claude-haiku-5-5'
# Siempre se agrega al prompt de Claude, aunque Abdair cambie MODO_INGENIERO_PROMPT:
# Claude Code le pasa el correo de la cuenta y Bob habla frente al público.
_REGLAS_FIJAS = (' Nunca digas correos, datos de cuenta, rutas ni detalles del sistema '
                 'donde corres. No uses emojis.')
# Búsqueda en internet del modo ingeniero (fase S: Serper + Groq, ver busqueda.py).
# Apagable con BUSQUEDA_INGENIERO_ENABLED = False en config.py: sin ella, el modo
# ingeniero responde como antes, sin buscar.
try:
    from config import BUSQUEDA_INGENIERO_ENABLED
except Exception:
    BUSQUEDA_INGENIERO_ENABLED = True

# Agente web (Playwright MCP). Versión fija: probada en la laptop el 2026-10-17.
try:
    from config import AGENTE_WEB_ENABLED
except Exception:
    AGENTE_WEB_ENABLED = False
PLAYWRIGHT_MCP = '@playwright/mcp@0.0.83'
WEB_HEADLESS = False          # visible: el público ve lo que hace
WEB_TIMEOUT_S = 90.0
WEB_MAX_HERRAMIENTAS = 12
_WEB = 'mcp__playwright__browser_'
WEB_PERMITIDAS = [_WEB + h for h in (
    'navigate', 'navigate_back', 'snapshot', 'click', 'type', 'press_key',
    'wait_for', 'find', 'tabs', 'hover', 'select_option')]
# Ocultas al modelo (y rechazadas por dontAsk si igual las pide): corren código,
# suben archivos, llenan formularios o no aportan a la tarea.
WEB_PROHIBIDAS = [_WEB + h for h in (
    'evaluate', 'run_code_unsafe', 'file_upload', 'drop', 'fill_form', 'handle_dialog',
    'network_requests', 'network_request', 'console_messages', 'emulate_media',
    'resize', 'drag', 'take_screenshot', 'close')]
WEB_PROMPT = (
    'Eres el agente web de Bob, un robot. Controlas un navegador con herramientas para '
    'cumplir la tarea que te pide su creador; el público ve el navegador. Usa pocos pasos. '
    'Para buscar información usa WebSearch (los buscadores en el navegador piden captcha o '
    'no muestran resultados) y después abre en el navegador la mejor fuente para que se '
    'vea. Para abrir una página concreta o interactuar con ella, usa el navegador y léela '
    'con browser_snapshot antes de responder. No inicies sesión, no escribas '
    'contraseñas ni datos personales, no compres ni pagues, no envíes formularios y no '
    'descargues archivos: si la tarea pide algo de eso, no lo hagas y dilo. Al terminar, '
    'responde en español, en 1 a 3 frases cortas para decir en voz alta, sin URLs largas '
    'ni markdown.' + _REGLAS_FIJAS)

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
# E-B: conjugado como se habla ("te pongas el modo ingeniero", "entra en modo…").
_RE_ING_ENTRAR = re.compile(
    r'\b(?:activ\w*|entr\w*|pon\w*|pas\w*|cambi\w*)\s+'
    r'(?:en\s+|a\s+|al\s+)?(?:el\s+)?modo\s+ingenier[oa]\b', re.IGNORECASE)
_RE_LIMPIA = re.compile(r'[^\wáéíóúñ ]+')


def _es_orden_sola(texto: str, orden: str) -> bool:
    t = _RE_LIMPIA.sub(' ', (texto or '').lower()).split()
    if t[:1] == ['bob']:
        t = t[1:]
    return ' '.join(t) in (orden, orden[:-1] + 'a')


# Navegador (Playwright): solo si se pide explícitamente que maneje una página: "en el
# navegador …", "abre la página de …", "entra a wikipedia.org", "navega a …", "haz clic …".
_RE_WEB = re.compile(
    r'\ben\s+(?:el\s+)?navegador\b'
    r'|\b(?:navega(?:r|s)?\s+(?:a|hasta|por)|haz\s+clic|clic(?:k)?ea)\b'
    r'|\b(?:abre|abr[ií]me|entra\s+a)\s+'
    r'(?:la\s+(?:p[aá]gina|web)\s+(?:de\s+)?\w+|\S+\.(?:com|org|net|bo|es|io|dev|ai|edu)\b)',
    re.IGNORECASE)
# Búsqueda en internet ("busca … en internet", "investiga … en google"): la responde el
# pipeline del modo ingeniero (fase S), no el navegador.
_RE_BUSCAR = re.compile(
    r'\b(?:b[uú]sca(?:me)?|investiga|averigua|consulta)\b.*\ben\s+(?:la\s+)?(?:web|internet|google|red)\b'
    r'|\ben\s+(?:la\s+)?(?:web|internet|google)\s*,?\s*(?:b[uú]sca(?:me)?|investiga|averigua)\b',
    re.IGNORECASE)
_RE_BOB_INICIO = re.compile(r'^\s*bob\s*[,.:]?\s*', re.IGNORECASE)


def detectar(texto: str) -> Optional[Tuple[str, dict]]:
    """(comando, argumentos) si el texto es un comando de dueño, o None."""
    if _RE_ING_SALIR.search(texto or ''):
        return 'salir_ingeniero', {}
    if _RE_WEB.search(texto or ''):
        return 'web', {'tarea': _RE_BOB_INICIO.sub('', texto).strip()}
    if _RE_BUSCAR.search(texto or ''):
        return 'buscar_web', {}
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
    if nombre_cmd == 'buscar_web':           # la responde el pipeline de búsqueda (fase S)
        if getattr(vp, '_modo_ingeniero', False):
            return False                     # no es un comando: el turno va al LLM
        _decir(vp, 'La búsqueda en internet la hago en modo ingeniero. Dime: Bob, modo ingeniero.',
               'CURIOSO')
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
    elif nombre_cmd == 'web':
        web(vp, args['tarea'])
    return True


# ── Modo ingeniero ──────────────────────────────────────────────────────────────
# R3: los pedidos llegan por voz (a veces dictados largos): que el modelo interprete
# los términos técnicos mal transcritos en vez de tomarlos al pie de la letra.
NOTA_VOZ = ('\n\nLo que te dice Abdair llega por reconocimiento de voz y puede traer '
            'términos técnicos mal transcritos (p. ej. "ese pe treinta y dos" es ESP32, '
            '"eme pe u" es MPU). Interpreta la intención; si algo clave no se entiende, '
            'pregúntalo en una frase.')


def _prompt_ingeniero(vp) -> str:
    from voice_pipeline import DUENO_ID
    contexto = vp._memoria.contexto(DUENO_ID) if vp._memoria is not None else ''
    return MODO_INGENIERO_PROMPT + NOTA_VOZ + (f'\n\n═══ LO QUE SABES DE ABDAIR ═══\n{contexto}'
                                               if contexto else '')


def _crear_sesion_ingeniero(vp):
    """Sesión de Claude Code del modo ingeniero, sin herramientas: buscar en internet lo
    hace el pipeline de busqueda.py (fase S), no el modelo. Leer, escribir y ejecutar irán
    en la sesión de escritura (fase B3)."""
    import claude_code
    return claude_code.SesionClaude(_prompt_ingeniero(vp) + _REGLAS_FIJAS,
                                    modelo=CLAUDE_MODELO, nombre='ingeniero')


def entrar_ingeniero(vp) -> None:
    from voice_pipeline import console
    import claude_code
    if getattr(vp, '_modo_ingeniero', False):
        _decir(vp, 'Ya estamos en modo ingeniero. ¿En qué seguimos?', 'CURIOSO')
        return
    vp._modo_ingeniero = True
    vp._sesion_ing = None
    if MOTOR_INGENIERO == 'claude' and claude_code.disponible():
        vp._sesion_ing = _crear_sesion_ingeniero(vp)
        vp._sesion_ing.iniciar_en_fondo()      # arranca mientras Bob habla
    motor = 'Claude Code' if vp._sesion_ing else 'Groq'
    console.print(f'[bold magenta][dueño][/] modo ingeniero ON ({motor})')
    _decir(vp, 'Modo ingeniero activado. ¿En qué trabajamos hoy, creador?', 'CURIOSO')


def salir_ingeniero(vp, avisar: bool = False) -> None:
    from voice_pipeline import console
    if not getattr(vp, '_modo_ingeniero', False):
        if avisar:
            _decir(vp, 'No estaba en modo ingeniero.', 'CONFUNDIDO')
        return
    vp._modo_ingeniero = False
    for atributo in ('_sesion_ing', '_sesion_web'):      # cierra Claude y el navegador
        sesion = getattr(vp, atributo, None)
        if sesion is not None:
            sesion.cerrar()
        setattr(vp, atributo, None)
    vp._contexto_web = None
    console.print('[bold magenta][dueño][/] modo ingeniero OFF')
    if avisar:
        _decir(vp, 'Modo ingeniero desactivado. Vuelvo a ser el Bob de siempre.', 'FELIZ')


def ajustes_ingeniero(vp, motor: Optional[str] = None):
    """Lo que cambia en cada pedido al LLM mientras dura el modo ingeniero, o None
    fuera del modo. Con Claude Code: {sesion, max_frases}. Con Groq (o motor='groq'
    como respaldo): {llm, modelo, extra, max_tokens, max_frases, prompt}."""
    if not getattr(vp, '_modo_ingeniero', False):
        return None
    sesion = getattr(vp, '_sesion_ing', None)
    if sesion is not None and motor != 'groq':
        return {'sesion': sesion, 'max_frases': ING_MAX_FRASES}
    from voice_pipeline import GROQ_LLM_MODEL
    extra = {'reasoning_effort': 'low'} if GROQ_LLM_MODEL.startswith('openai/gpt-oss-') else {}
    return {'llm': vp._client, 'modelo': GROQ_LLM_MODEL, 'extra': extra,
            'max_tokens': ING_MAX_TOKENS, 'max_frases': ING_MAX_FRASES,
            'prompt': _prompt_ingeniero(vp)}


# Lo que Bob no debe leer en voz alta: correos, emojis, enlaces, lista de fuentes
# y marcas de markdown.
_RE_EMAIL = re.compile(r'[\w.+-]+@[\w-]+(?:\.[\w-]+)+')
_RE_EMOJI = re.compile('[\U0001F000-\U0001FAFF☀-➿️‍]')
_RE_FUENTES = re.compile(r'\n\s*(?:fuentes?|sources?)\s*:.*', re.IGNORECASE | re.DOTALL)
_RE_LINK_MD = re.compile(r'\[([^\]]*)\]\([^)]*\)')
_RE_URL = re.compile(r'\(?https?://\S+\)?')
_RE_MARKDOWN = re.compile(r'[*#`_>]+')
_RE_FRASE = re.compile(r'(?<=[.!?…])\s+')


def frases_para_decir(texto: str, maximo: int):
    """Texto de Claude → frases limpias para la voz, a lo sumo `maximo`."""
    texto = _RE_FUENTES.sub('', texto or '')
    texto = _RE_URL.sub('', _RE_LINK_MD.sub(r'\1', texto))
    texto = _RE_EMAIL.sub('ese dato', texto)
    texto = _RE_MARKDOWN.sub('', _RE_EMOJI.sub('', texto))
    frases = [f.strip() for f in _RE_FRASE.split(' '.join(texto.split())) if f.strip()]
    return frases[:maximo]


def responder_claude(vp, texto: str, sesion, nota: str = ''):
    """Un turno del modo ingeniero con Claude Code, sin herramientas. Devuelve las frases a
    decir, o None si Claude Code no respondió (el turno sigue con Groq). `nota` es una
    instrucción para este turno, p. ej. que no pudo buscar."""
    from voice_pipeline import _contexto_asistente_turno, console
    notas = [n for n in (
        (getattr(vp, '_marca_voz', '') or '').strip(' \n[]'),
        _contexto_asistente_turno(texto, getattr(vp, '_ruta_turno', None)).strip(),
        f'Resultado de la última tarea web: {vp._contexto_web}'
        if getattr(vp, '_contexto_web', None) else '',
        nota) if n]
    vp._contexto_web = None
    mensaje = (f'[Contexto de este turno: {" ".join(notas)}]\n{texto}' if notas else texto)
    t0 = time.monotonic()
    respuesta = sesion.preguntar(mensaje, timeout=ING_TIMEOUT_S)
    if not respuesta:
        console.print('[yellow][dueño] Claude Code no respondió: este turno va con Groq[/]')
        return None
    console.print(f'[dim][dueño] Claude Code respondió en {time.monotonic() - t0:.1f} s[/]')
    return frases_para_decir(respuesta, ING_MAX_FRASES) or None


# ── Búsqueda en internet del modo ingeniero (fase S4) ─────────────────────────────
def _buscar_para(consultas):
    """Fuentes para las consultas: primero noticias y, si no hay, búsqueda general.
    Lista (puede ser []), o None si Serper no respondió."""
    fuentes = busqueda.buscar_todas(consultas, noticias=True)
    if fuentes == []:
        fuentes = busqueda.buscar_todas(consultas, noticias=False)
    return fuentes


def _responder_con_fuentes(vp, texto: str, consultas, sesion):
    """Generador de frases: busca, rankea y responde citando [n], en streaming. Si no hay
    fuentes o algo falla, lo dice Claude (sin herramientas) con esa nota."""
    from voice_pipeline import console
    fuentes = _buscar_para(consultas)
    if fuentes is None:
        nota = 'No pude buscar en internet ahora: dilo y responde con lo que sabes.'
    elif not fuentes:
        nota = 'La búsqueda no encontró nada: dilo y responde con lo que sabes.'
    else:
        dichas = []
        for frase in busqueda.responder_con_fuentes(texto, fuentes):
            dichas.append(frase)
            yield frase
        if dichas:
            vp._contexto_web = ' '.join(dichas)     # el turno siguiente de Claude lo sabe
            return
        nota = 'No pude armar la respuesta con las fuentes: dilo y responde con lo que sabes.'
    console.print(f'[yellow][dueño] búsqueda sin respuesta: {nota}[/]')
    yield from (responder_claude(vp, texto, sesion, nota=nota) or [])


def responder_ingeniero(vp, texto: str, sesion):
    """Generador de frases de un turno del modo ingeniero. Primero descompone la pregunta
    (busqueda.descomponer): si hace falta buscar, responde con fuentes; si no, Claude Code
    sin herramientas. Si el descompositor falla, busca con la pregunta tal cual."""
    if not BUSQUEDA_INGENIERO_ENABLED:
        yield from (responder_claude(vp, texto, sesion) or [])
        return
    plan = busqueda.descomponer(texto)
    if plan is None or plan['buscar']:
        consultas = plan['consultas'] if plan else [texto]
        yield from _responder_con_fuentes(vp, texto, consultas, sesion)
        return
    yield from (responder_claude(vp, texto, sesion) or [])


# ── Agente web (dentro del modo ingeniero) ──────────────────────────────────────
_RE_SI = re.compile(r'^\W*(?:s[ií]|dale|claro|hazlo|adelante|ok(?:ay)?|vale|va|confirmo|de una)\b',
                    re.IGNORECASE)


def _sesion_web(vp):
    import claude_code
    from voice_pipeline import console
    sesion = getattr(vp, '_sesion_web', None)
    if sesion is None:
        args = ['-y', PLAYWRIGHT_MCP, '--isolated',
                '--output-dir', os.path.join(claude_code.CWD_DEFAULT, 'web')]
        if WEB_HEADLESS:
            args.append('--headless')
        sesion = claude_code.SesionClaude(
            WEB_PROMPT, modelo=CLAUDE_MODELO, nombre='web', herramientas='WebSearch',
            mcp={'playwright': {'command': 'npx', 'args': args}},
            permitidas=['WebSearch'] + WEB_PERMITIDAS, prohibidas=WEB_PROHIBIDAS)
        vp._sesion_web = sesion
        console.print('[dim][dueño] agente web: sesión nueva (Playwright MCP)[/]')
    return sesion


def web(vp, tarea: str) -> None:
    """Tarea en la web con confirmación en voz. El navegador queda abierto hasta
    salir del modo ingeniero; el resultado se dice y queda de contexto."""
    import claude_code
    from voice_pipeline import console
    if not AGENTE_WEB_ENABLED:
        _decir(vp, 'Todavía no tengo activado el navegador.', 'CONFUNDIDO')
        return
    if not getattr(vp, '_modo_ingeniero', False):
        _decir(vp, 'La web la manejo en modo ingeniero. Dime: Bob, modo ingeniero.', 'CURIOSO')
        return
    if MOTOR_INGENIERO != 'claude' or not claude_code.disponible():
        _decir(vp, 'No encuentro Claude Code en esta laptop, así que no puedo usar el navegador.',
               'TRISTE')
        return
    # Confirmación en voz: un "no" o una voz ajena cancelan.
    _decir(vp, f'¿Hago esto en la web: {tarea}? Dime sí o no.', 'CURIOSO')
    vp._sm.iniciar_escuchando()
    audio = vp._grabar(initial_timeout=6.0, invitar=False)
    respuesta = vp._transcribir(audio) if audio else ''
    if audio and verificar(vp, vp._analizar_voz(audio)) == 'no':
        _decir(vp, random.choice(RECHAZOS).format(dueno=_nombre_dueno(vp)), 'SOSPECHANDO')
        return
    if not _RE_SI.search(respuesta or ''):
        console.print(f'[dim][dueño] web cancelada (respuesta: "{respuesta}")[/]')
        _decir(vp, 'Listo, no lo hago.', 'FELIZ')
        return
    _decir(vp, 'Voy a la web.', 'PENSANDO')
    pasos = []
    # Una tarea tarda 15–30 s (medido): Bob cuenta lo que hace para no quedar mudo.
    avisos = {'WebSearch': 'Buscando en internet...',
              'navigate': 'Abriendo la página para que la vean.'}

    def al_usar(nombre, entrada):
        pasos.append(nombre.replace(_WEB, ''))
        console.print(f'[dim][web] {pasos[-1]} '
                      f'{str(entrada.get("url") or entrada.get("query") or entrada.get("text") or "")[:80]}[/]')
        aviso = avisos.pop(pasos[-1], None)
        if aviso:
            _decir(vp, aviso, 'PENSANDO')
    t0 = time.monotonic()
    resultado = _sesion_web(vp).preguntar(tarea, timeout=WEB_TIMEOUT_S, al_usar=al_usar,
                                          max_herramientas=WEB_MAX_HERRAMIENTAS)
    console.print(f'[bold magenta][dueño][/] web: {len(pasos)} pasos en '
                  f'{time.monotonic() - t0:.1f} s ({"ok" if resultado else "falló"})')
    if not resultado:
        _decir(vp, 'No pude terminar la tarea en la web. ¿Probamos de otra forma?', 'TRISTE')
        return
    frases = frases_para_decir(resultado, 3)
    for frase in frases:
        _decir(vp, frase, 'ORGULLOSO')
    vp._contexto_web = ' '.join(frases)
    vp._convo.append({'role': 'assistant', 'content': ' '.join(frases)})


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
