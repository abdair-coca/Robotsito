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
  / "abre el navegador …"  Playwright MCP y navegador visible, solo si se pide
  / "abre la página de …"
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
import queue
import random
import re
import threading
import time
from typing import Optional, Tuple

from asistente import busqueda
from rich.markup import escape

try:
    from config import MODO_DUENO_ENABLED
except Exception:
    MODO_DUENO_ENABLED = False
# Umbral de voz para los comandos de dueño. Default: el mismo de V4 (VOZ_UMBRAL).
try:
    from config import VOZ_UMBRAL_DUENO
except Exception:
    VOZ_UMBRAL_DUENO = None
# Umbral para que el modo ingeniero toque archivos y corra comandos (B-D11). Por debajo, el
# turno va con la sesión de lectura: no escribe ni ejecuta nada.
VOZ_UMBRAL_SHELL = 0.55

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
# Memoria de la charla, compartida por las sesiones de Claude (lectura, escritura, web): cada
# sesión recibe, en una línea, solo lo que pasó desde su último turno. Una sesión nueva (otro
# modelo, reinicio o corte) recibe los últimos MEMORIA_MAX_MSGS mensajes, recortados.
MEMORIA_MAX_MSGS = 8
MEMORIA_MAX_CHARS = 160
# Charla larga: tras estos turnos, la sesión de lectura o escritura se reinicia y sigue con la
# memoria corta, para no reenviar todo el historial en cada turno. La web no (cerraría el navegador).
ING_TURNOS_MAX = 10
# Avisos de lo que Bob hace mientras Claude usa herramientas.
AVISO_MIN_S = 3.5        # entre dos avisos distintos
AVISO_SIGUE_S = 7.0      # sin novedades en este tiempo: "aún sigo…" (menos que el corte de 10 s de la voz)

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

# Lectura del modo ingeniero (fase B1): Read, Glob y Grep solo dentro de APPINGLES_DIR
# (config.py). Vacía = sin herramientas, como antes. .env queda fuera aunque se pida.
try:
    from config import APPINGLES_DIR
except Exception:
    APPINGLES_DIR = ''
LECTURA_PROHIBIDAS = ['Read(**/.env)', 'Read(**/.env.*)']
# Sin la ruta: _REGLAS_FIJAS prohíbe decir rutas.
LECTURA_PROMPT = (' Tienes Read, Glob y Grep sobre la carpeta de trabajo de AppInglés: '
                  'úsalas para contestar sobre sus archivos. Nunca leas ni cites .env ni claves.')

# Escritura y Bash del modo ingeniero (fases B3 y B4, B-D8 y B-D9): la voz del dueño se
# verifica al activar el modo; durante el modo, Edit, Write y Bash se permiten dentro de
# AppInglés sin preguntar, salvo los bloqueos de B-D3. Los turnos con contenido de internet
# no usan esta sesión.
ESCRITURA_HERRAMIENTAS = 'Read,Glob,Grep,Edit,Write,Bash'
ESCRITURA_PERMITIDAS = ['Read', 'Glob', 'Grep']
# Comandos de lectura que Claude Code aprueba sin preguntar: no pasan por el host, así que
# los bloqueos de B-D3 para ellos van como reglas del CLI (--disallowedTools). Lo que sí se
# pregunta lo ve el host (_aprobar_escritura).
_VERBOS_LECTURA_ENV = [
    # texto
    'cat', 'type', 'more', 'less', 'head', 'tail', 'gc', 'Get-Content', 'nl', 'tac', 'rev', 'sort',
    'uniq', 'cut', 'tr', 'column', 'wc', 'sed', 'awk', 'grep', 'egrep', 'fgrep', 'rg', 'ag', 'sls',
    'Select-String', 'findstr', 'diff', 'comm', 'jq', 'bat', 'batcat', 'view', 'vi', 'vim', 'nano',
    'code',
    # binario y hexadecimal
    'xxd', 'od', 'hexdump', 'hd', 'strings', 'base64', 'certutil', 'Format-Hex', 'file', 'stat', 'dd',
    # copia y movimiento
    'copy', 'cp', 'xcopy', 'robocopy', 'Copy-Item', 'move', 'mv', 'Move-Item',
]
BASH_PROHIBIDAS_CLI = ([f'Bash({v} *.env*)' for v in _VERBOS_LECTURA_ENV] + [
    'Bash(git push)', 'Bash(git push *)', 'Bash(git reset --hard)', 'Bash(git reset --hard *)',
    'Bash(git clean)', 'Bash(git clean *)', 'Bash(git branch -D *)', 'Bash(git branch -d *)',
    'Bash(pip install *)', 'Bash(pip3 install *)', 'Bash(curl)', 'Bash(curl *)',
    'Bash(wget)', 'Bash(wget *)'])
ESCRITURA_PROHIBIDAS = (LECTURA_PROHIBIDAS + ['Edit(**/.env)', 'Edit(**/.env.*)',
                                              'Write(**/.env)', 'Write(**/.env.*)']
                        + BASH_PROHIBIDAS_CLI)
ESCRITURA_PROMPT = (' Puedes leer, crear y editar archivos y ejecutar comandos de shell dentro de la '
                    'carpeta de trabajo de AppInglés. No hagas git push, no instales paquetes, no '
                    'descargues nada, no cambies de carpeta y no leas ni escribas .env ni claves. '
                    'Para correr un script usa su ruta relativa a la carpeta de trabajo, con '
                    'barras / (el shell es bash: la barra invertida se pierde) y sin opciones, '
                    'por ejemplo: python src/app.py. '
                    'Si un comando no se permite, dilo en una frase. Si un comando devuelve mucha '
                    'salida, resúmela en una o dos frases; nunca la copies ni la leas completa.')
ACCION_ESCRITURA = {'Write': 'crear o reemplazar', 'Edit': 'editar'}
# Bloqueos de B-D3 para Bash. Son por texto: un comando puede esconderlos. Se aplican en el
# pedido de permiso de cada comando (Bash no está en --allowedTools).
BASH_BLOQUEADOS = [re.compile(p, re.IGNORECASE) for p in (
    r'\bgit\s+push\b',
    r'\bgit\s+reset\s+--hard\b',
    r'\bgit\s+clean\b',
    r'\bgit\s+branch\s+-d\b',                      # -D y -d: se bloquean los dos
    r'\.env\b',                                    # leer o copiar .env
    r'\brm\b[^;&|]*\s-\w*r',                       # rm -rf, rm -r, rm -fr
    r'\bremove-item\b.*-recurse',
    r'\b(?:rmdir|rd|del)\b[^;&|]*\s/s\b',
    r'\bpip3?\s+install\b',
    r'\bpython\S*\s+-m\s+pip\s+install\b',
    r'\b(?:curl|wget)\b',
    r'\binvoke-(?:webrequest|restmethod)\b',
)]
# Allowlist de Bash (B-D10): cada tramo de un comando encadenado (&&, ||, ;, |, &) debe empezar
# por uno de estos. Lo que no esté aquí se niega, aunque no caiga en BASH_BLOQUEADOS.
_VERBOS_BASH = r'(?:dir|ls|cat|type|more|head|tail|mkdir|md|rm|cd|chdir|pushd|set-location|sl|push-location)'
# Ruta de un script, sin o con comillas (Claude usa la ruta absoluta entre comillas). Con comillas
# se excluyen los caracteres que el shell expande o encadena.
_RUTA_SCRIPT = r'''(?:[\w.\\/:-]+{ext}|"[^"$`%;&|<>]+{ext}"|'[^'$`%;&|<>]+{ext}')'''
BASH_PERMITIDOS = [re.compile(p, re.IGNORECASE) for p in (
    rf'{_VERBOS_BASH}(?:\s|$)',
    r'git\s+(?:status|log|diff|show)(?:\s|$)',
    # opciones sueltas que no ejecutan código (-I, -u...); -c y -m quedan fuera
    r'(?:python3?|py)\s+(?:-[BEISsu]\s+)*' + _RUTA_SCRIPT.format(ext=r'\.py') + r'(?:\s|$)',
    r'node\s+(?:-v|--version)(?:\s|$)',
    r'node\s+' + _RUTA_SCRIPT.format(ext=r'\.m?js') + r'(?:\s|$)',
    r'npm\s+(?:-v|--version|run|test|ls|list)(?:\s|$)',
)]
# Cambiar de carpeta: solo dentro de AppInglés (B-D3).
_RE_CAMBIO = re.compile(r'(?:^|[;&|]\s*)(cd|chdir|pushd|set-location|sl|push-location)\b([^;&|]*)',
                        re.IGNORECASE)

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
    'El navegador sigue abierto entre pedidos: si el pedido habla de algo que ya está en '
    'pantalla (por ejemplo "abre el de 200 Bs"), trabaja sobre la página actual: léela con '
    'browser_snapshot, busca ese elemento y haz clic en él; no vuelvas a buscar desde cero. '
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
    r'|\babr\w*\s+(?:el\s+|un\s+|tu\s+|mi\s+)?navegador\b'
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
# Con el navegador ya abierto, también va al agente web lo que se pide sobre la página en
# pantalla ("ábrelo", "abras el tenis de 200 Bs", "baja", "vuelve atrás", "en esta página…"),
# salvo que hable de archivos o código (eso es de AppInglés).
_RE_WEB_SEGUIR = re.compile(
    r'\b(?:[aá]br[eaií]\w*|entr(?:a|es|ar|ame|alo|ala)|clic\w*|cliqu\w*|pincha\w*|presiona\w*'
    r'|selecciona\w*|el[ií]g\w*|escog\w*|baj[ae]\w*|desplaz\w*|scroll\w*|regres\w*|atr[aá]s'
    r'|agreg\w*|añad\w*|compara\w*)\b'
    r'|\b(?:la|esa|esta)\s+(?:p[aá]gina|web|pesta[ñn]a|tienda)\b', re.IGNORECASE)
_RE_NO_WEB = re.compile(r'\b(?:archivo|fichero|carpeta|c[oó]digo|script|comando|terminal'
                        r'|funci[oó]n|modo)\b', re.IGNORECASE)


def detectar(texto: str, navegador: bool = False) -> Optional[Tuple[str, dict]]:
    """(comando, argumentos) si el texto es un comando de dueño, o None.
    navegador: el agente web ya tiene el navegador abierto (ver _RE_WEB_SEGUIR)."""
    if _RE_ING_SALIR.search(texto or ''):
        return 'salir_ingeniero', {}
    if _RE_WEB.search(texto or ''):
        return 'web', {'tarea': _RE_BOB_INICIO.sub('', texto).strip()}
    if _RE_BUSCAR.search(texto or ''):
        return 'buscar_web', {}
    if navegador and _RE_WEB_SEGUIR.search(texto or '') and not _RE_NO_WEB.search(texto or ''):
        return 'web', {'tarea': _RE_BOB_INICIO.sub('', texto).strip()}
    if _RE_ING_ENTRAR.search(texto or '') or _es_orden_sola(texto, 'modo ingeniero'):
        return 'ingeniero', {}
    m = _RE_PRESENTAR.search(texto or '')
    if m:
        from voz.pipeline import _NO_NOMBRE
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
    from voz.pipeline import DUENO_ID, VOZ_ID_MIN_S, VOZ_MIN_MUESTRAS, VOZ_UMBRAL
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
    from voz.pipeline import DUENO_ID
    p = vp._memoria.persona(DUENO_ID)
    return p[1] if p and p[1] else 'mi creador'


def atender(vp, texto: str, analisis) -> bool:
    """Si el texto es un comando de dueño: verifica la voz, responde y lo
    ejecuta si corresponde. Devuelve True si era un comando (aunque se rechace)."""
    if not MODO_DUENO_ENABLED:
        return False
    cmd = detectar(texto, navegador=_navegador_abierto(vp))
    if cmd is None:
        return False
    nombre_cmd, args = cmd
    from voz.pipeline import console
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
    from voz.pipeline import DUENO_ID
    contexto = vp._memoria.contexto(DUENO_ID) if vp._memoria is not None else ''
    return MODO_INGENIERO_PROMPT + NOTA_VOZ + (f'\n\n═══ LO QUE SABES DE ABDAIR ═══\n{contexto}'
                                               if contexto else '')


def _crear_sesion_ingeniero(vp):
    """Sesión de Claude Code del modo ingeniero. Buscar en internet lo hace el pipeline
    de busqueda.py (fase S). Con APPINGLES_DIR, lee esa carpeta (fase B1); sin ella no
    tiene herramientas. Escribir y ejecutar irán en la sesión de escritura (fase B3)."""
    from agente import claude_code
    if not APPINGLES_DIR:
        return claude_code.SesionClaude(_prompt_ingeniero(vp) + _REGLAS_FIJAS,
                                        modelo=CLAUDE_MODELO, nombre='ingeniero')
    # cwd propio y vacío: la temporal compartida tiene logs del agente web.
    return claude_code.SesionClaude(_prompt_ingeniero(vp) + _REGLAS_FIJAS + LECTURA_PROMPT,
                                    modelo=CLAUDE_MODELO, nombre='ingeniero',
                                    herramientas='Read,Glob,Grep',
                                    permitidas=['Read', 'Glob', 'Grep'],
                                    prohibidas=LECTURA_PROHIBIDAS, add_dirs=[APPINGLES_DIR],
                                    cwd=os.path.join(claude_code.CWD_DEFAULT, 'lectura'))


def _cambia_fuera(comando: str) -> bool:
    """True si el comando cambia de carpeta fuera de AppInglés, o sin destino claro."""
    base = os.path.normcase(os.path.abspath(APPINGLES_DIR)) if APPINGLES_DIR else None
    for m in _RE_CAMBIO.finditer(comando):
        destino = m.group(2).strip().strip('"\'')
        if not destino or base is None or destino.startswith('~') or any(c in destino for c in '%$'):
            return True                          # cd sin argumento, a la casa o por variable
        ruta = os.path.normcase(os.path.abspath(os.path.join(APPINGLES_DIR, destino)))
        if ruta != base and not ruta.startswith(base + os.sep):
            return True
    return False


def es_bash_bloqueado(comando: str) -> bool:
    """True si el comando cae en un bloqueo de B-D3, intenta salir de AppInglés o no está
    en la allowlist de B-D10 (cada tramo debe empezar por un comando permitido)."""
    texto = comando or ''
    if any(p.search(texto) for p in BASH_BLOQUEADOS) or _cambia_fuera(texto):
        return True
    tramos = [t.strip() for t in re.split(r'&&|\|\||[;&|]', texto) if t.strip()]
    return not tramos or not all(any(p.match(t) for p in BASH_PERMITIDOS) for t in tramos)


def _aprobar_escritura():
    """Decisor de los permisos de la sesión de escritura (B3, B4). Sin voz: Edit y Write
    dentro de AppInglés se permiten; Bash se permite salvo que caiga en un bloqueo de B-D3
    (B-D9). Cualquier otra herramienta se niega. Queda en la terminal."""
    from voz.pipeline import console

    def aprobar(herramienta: str, entrada: dict) -> bool:
        if herramienta in ACCION_ESCRITURA:
            permitido = True
            detalle = os.path.basename(str(entrada.get('file_path') or '')) or '-'
        elif herramienta == 'Bash':
            comando = str(entrada.get('command') or '')
            permitido = not es_bash_bloqueado(comando)
            detalle = comando[:100]
        else:
            permitido, detalle = False, ''
        console.print(f'[dueño] {herramienta} {escape(detalle)}: {"sí" if permitido else "no"}')
        return permitido
    return aprobar


def _sesion_escritura(vp):
    """Sesión de escritura (B3): Read, Glob, Grep, Edit y Write con cwd en AppInglés. Existe
    mientras dura el modo ingeniero, que el dueño activó por voz. Los turnos con contenido
    de internet no la usan (ver responder_ingeniero)."""
    from agente import claude_code
    from voz.pipeline import console
    sesion = getattr(vp, '_sesion_esc', None)
    if sesion is None:
        sesion = claude_code.SesionClaude(
            _prompt_ingeniero(vp) + _REGLAS_FIJAS + ESCRITURA_PROMPT,
            modelo=CLAUDE_MODELO, nombre='escritura',
            herramientas=ESCRITURA_HERRAMIENTAS, permitidas=ESCRITURA_PERMITIDAS,
            prohibidas=ESCRITURA_PROHIBIDAS, cwd=APPINGLES_DIR, restringido=True,
            aprobar=_aprobar_escritura())
        vp._sesion_esc = sesion
        console.print('[dim][dueño] sesión de escritura (AppInglés)[/]')
    return sesion


def _puede_escribir(vp) -> bool:
    """Carpeta existente y Claude Code disponible. El dueño ya fue verificado al activar."""
    from agente import claude_code
    return bool(APPINGLES_DIR and os.path.isdir(APPINGLES_DIR)
                and MOTOR_INGENIERO == 'claude' and claude_code.disponible())


def entrar_ingeniero(vp) -> None:
    from voz.pipeline import console
    from agente import claude_code
    if getattr(vp, '_modo_ingeniero', False):
        _decir(vp, 'Ya estamos en modo ingeniero. ¿En qué seguimos?', 'CURIOSO')
        return
    vp._modo_ingeniero = True
    vp._sesion_ing = None
    vp._ing_visto, vp._ing_web = {}, set()
    if MOTOR_INGENIERO == 'claude' and claude_code.disponible():
        vp._sesion_ing = _crear_sesion_ingeniero(vp)
        vp._sesion_ing.iniciar_en_fondo()      # arranca mientras Bob habla
    motor = 'Claude Code' if vp._sesion_ing else 'Groq'
    console.print(f'[bold magenta][dueño][/] modo ingeniero ON ({motor})')
    _decir(vp, 'Modo ingeniero activado. ¿En qué trabajamos hoy, creador?', 'CURIOSO')


def salir_ingeniero(vp, avisar: bool = False) -> None:
    from voz.pipeline import console
    if not getattr(vp, '_modo_ingeniero', False):
        if avisar:
            _decir(vp, 'No estaba en modo ingeniero.', 'CONFUNDIDO')
        return
    vp._modo_ingeniero = False
    for atributo in ('_sesion_ing', '_sesion_web', '_sesion_esc'):   # cierra Claude y el navegador
        sesion = getattr(vp, atributo, None)
        if sesion is not None:
            sesion.cerrar()
        setattr(vp, atributo, None)
    vp._contexto_web = None
    vp._ing_visto, vp._ing_web = {}, set()
    console.print('[bold magenta][dueño][/] modo ingeniero OFF')
    if avisar:
        _decir(vp, 'Modo ingeniero desactivado. Vuelvo a ser el Bob de siempre.', 'FELIZ')


def ajustes_ingeniero(vp, motor: Optional[str] = None):
    """Lo que cambia en cada pedido al LLM mientras dura el modo ingeniero, o None
    fuera del modo. Con Claude Code: {sesion, max_frases}. Con Groq (o motor='groq'
    como respaldo): {llm, modelo, extra, max_tokens, max_frases, prompt}."""
    if not getattr(vp, '_modo_ingeniero', False):
        return None
    if motor != 'groq' and _puede_escribir(vp):      # B3: turno del dueño
        return {'sesion': _sesion_escritura(vp), 'max_frases': ING_MAX_FRASES}
    sesion = getattr(vp, '_sesion_ing', None)
    if sesion is not None and motor != 'groq':
        return {'sesion': sesion, 'max_frases': ING_MAX_FRASES}
    from voz.pipeline import GROQ_LLM_MODEL
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
# B5: una "frase" más larga es salida de un comando copiada tal cual (sin puntos, así que el
# corte por puntuación no la parte). Nunca se lee entera.
FRASE_MAX_CHARS = 240


def _acortar(frase: str) -> str:
    if len(frase) <= FRASE_MAX_CHARS:
        return frase
    return frase[:FRASE_MAX_CHARS].rsplit(' ', 1)[0].rstrip(' ,;:-') + '.'


def frases_para_decir(texto: str, maximo: int):
    """Texto de Claude → frases limpias para la voz, a lo sumo `maximo` y de a lo sumo
    FRASE_MAX_CHARS caracteres cada una."""
    texto = _RE_FUENTES.sub('', texto or '')
    texto = _RE_URL.sub('', _RE_LINK_MD.sub(r'\1', texto))
    texto = _RE_EMAIL.sub('ese dato', texto)
    texto = _RE_MARKDOWN.sub('', _RE_EMOJI.sub('', texto))
    frases = [f.strip() for f in _RE_FRASE.split(' '.join(texto.split())) if f.strip()]
    return [_acortar(f) for f in frases[:maximo]]


def _corto(texto: str, maximo: int) -> str:
    texto = ' '.join((texto or '').split())
    return texto if len(texto) <= maximo else texto[:maximo].rsplit(' ', 1)[0] + '…'


def _memoria_pendiente(vp, sesion, sin_web: bool = False):
    """(línea con lo que `sesion` no vio de la charla, clave, fin). La clave es el proceso
    de la sesión: uno nuevo (otro modelo, reinicio o corte) no vio nada y recibe los últimos
    MEMORIA_MAX_MSGS. sin_web: deja fuera las respuestas con contenido de internet (B-D1:
    no llegan a la sesión de escritura). El mensaje del turno en curso no entra."""
    convo = getattr(vp, '_convo', None) or []
    fin = len(convo) - (1 if convo and convo[-1].get('role') == 'user' else 0)
    clave = (sesion.nombre, sesion.arranques + (0 if sesion.vivo else 1))
    visto = getattr(vp, '_ing_visto', {}).get(clave)
    web = getattr(vp, '_ing_web', set())
    lineas = [f'{"Bob" if convo[i].get("role") == "assistant" else "Persona"}: '
              f'{_corto(convo[i].get("content", ""), MEMORIA_MAX_CHARS)}'
              for i in range(max(visto or 0, fin - MEMORIA_MAX_MSGS), fin)
              if not (sin_web and i in web)]
    return ' | '.join(lineas), clave, fin


def _marcar_visto(vp, sesion, hasta: int) -> None:
    if not hasattr(vp, '_ing_visto'):
        vp._ing_visto = {}
    vp._ing_visto[(sesion.nombre, sesion.arranques)] = hasta


class Aviso(str):
    """Frase de lo que Bob está haciendo mientras Claude usa herramientas: se dice, pero no
    es la respuesta del turno (no va al historial)."""


# Herramienta → (al empezar, si tarda). {archivo} es solo el nombre, sin ruta (_REGLAS_FIJAS).
_AVISOS = {
    'Read': ('Estoy leyendo el archivo {archivo}.', 'Aún sigo leyéndolo.'),
    'Glob': ('Estoy buscando archivos.', 'Sigo buscando los archivos.'),
    'Grep': ('Estoy buscando en el código.', 'Sigo buscando en el código.'),
    'Edit': ('Estoy editando el archivo {archivo}.', 'Aún sigo editándolo.'),
    'Write': ('Estoy escribiendo el archivo {archivo}.', 'Aún sigo escribiéndolo.'),
    'Bash': ('Estoy ejecutando un comando.', 'El comando sigue corriendo.'),
    'WebSearch': ('Estoy buscando en internet.', 'Sigo buscando en internet.'),
    _WEB + 'navigate': ('Estoy abriendo la página.', 'La página sigue cargando.'),
    _WEB + 'navigate_back': ('Vuelvo a la página anterior.', 'La página sigue cargando.'),
    _WEB + 'snapshot': ('Estoy mirando la página.', 'Sigo revisando la página.'),
    _WEB + 'find': ('Estoy buscando en la página.', 'Sigo buscando en la página.'),
    _WEB + 'click': ('Estoy haciendo clic.', 'Sigo esperando a la página.'),
    _WEB + 'type': ('Estoy escribiendo en la página.', 'Sigo escribiendo.'),
    _WEB + 'wait_for': ('Estoy esperando a que cargue.', 'Todavía está cargando.'),
}
_AVISO_GENERICO = ('Estoy trabajando en eso.', 'Sigo en eso, ya casi.')
_AVISO_EXTRA = ['Un momento más.', 'Ya casi termino.']    # tras el primer "aún sigo…"


def _aviso(herramienta: str, entrada: dict) -> Tuple[str, str]:
    ahora, sigue = _AVISOS.get(herramienta, _AVISO_GENERICO)
    archivo = os.path.basename(str(entrada.get('file_path') or '')).strip()
    return ahora.format(archivo=archivo).replace(' el archivo .', ' un archivo.'), sigue


def _preguntar_narrando(sesion, mensaje: str, timeout: float, max_herramientas=None,
                        al_usar=None):
    """Generador: corre el turno de Claude en un hilo y, mientras, da un Aviso por cada
    herramienta nueva (como mucho uno cada AVISO_MIN_S; si llegan varias, la última) y un
    "aún sigo…" cada AVISO_SIGUE_S sin novedades. Devuelve (return) la respuesta o None.
    al_usar: además, recibe cada herramienta (p. ej. para la terminal)."""
    usos: queue.Queue = queue.Queue()
    hecho = {}

    def uso(nombre, entrada):
        if al_usar:
            al_usar(nombre, entrada)
        usos.put((nombre, entrada))

    def correr():
        try:
            hecho['r'] = sesion.preguntar(mensaje, timeout=timeout, al_usar=uso,
                                          max_herramientas=max_herramientas)
        finally:
            usos.put(None)
    threading.Thread(target=correr, daemon=True, name=f'narrar-{sesion.nombre}').start()
    actual, pendiente, t_dicho, veces = None, None, 0.0, 0
    while True:
        ahora = time.monotonic()
        if pendiente and ahora - t_dicho >= AVISO_MIN_S:
            actual, pendiente, t_dicho, veces = pendiente, None, ahora, 0
            yield Aviso(actual[0])
            continue
        if pendiente is None and actual and ahora - t_dicho >= AVISO_SIGUE_S:
            t_dicho = ahora
            yield Aviso(actual[1] if veces == 0 else _AVISO_EXTRA[(veces - 1) % len(_AVISO_EXTRA)])
            veces += 1
            continue
        espera = (t_dicho + (AVISO_MIN_S if pendiente else AVISO_SIGUE_S) - ahora
                  if (pendiente or actual) else AVISO_SIGUE_S)
        try:
            ev = usos.get(timeout=max(0.05, espera))
        except queue.Empty:
            continue
        if ev is None:
            return hecho.get('r')
        nuevo = _aviso(*ev)
        if actual is None or nuevo[0] != actual[0]:
            pendiente = nuevo


def _navegador_abierto(vp) -> bool:
    sesion = getattr(vp, '_sesion_web', None)
    return bool(getattr(vp, '_modo_ingeniero', False) and sesion is not None and sesion.vivo)


def responder_claude(vp, texto: str, sesion, nota: str = ''):
    """Generador de un turno del modo ingeniero con Claude Code: Avisos mientras usa
    herramientas y luego las frases a decir. Si Claude Code no respondió no da frases (el
    turno sigue con Groq). `nota` es una instrucción para este turno, p. ej. que no pudo
    buscar."""
    from voz.pipeline import _contexto_asistente_turno, console
    if sesion.vivo and sesion.turnos >= ING_TURNOS_MAX:     # charla larga: proceso nuevo
        console.print(f'[dim][dueño] {sesion.nombre}: {sesion.turnos} turnos, sigue con la '
                      'memoria corta[/]')
        sesion.cerrar()
    memoria, _, fin = _memoria_pendiente(vp, sesion, sin_web=sesion.nombre == 'escritura')
    notas = [n for n in (
        (getattr(vp, '_marca_voz', '') or '').strip(' \n[]'),
        _contexto_asistente_turno(texto, getattr(vp, '_ruta_turno', None)).strip(),
        f'Resultado de la última tarea web: {vp._contexto_web}'
        if getattr(vp, '_contexto_web', None) else '',
        nota) if n]
    vp._contexto_web = None
    mensaje = ((f'[Antes en esta charla: {memoria}]\n' if memoria else '')
               + (f'[Contexto de este turno: {" ".join(notas)}]\n' if notas else '') + texto)
    t0 = time.monotonic()
    respuesta = yield from _preguntar_narrando(sesion, mensaje, ING_TIMEOUT_S)
    if not respuesta:
        console.print('[yellow][dueño] Claude Code no respondió: este turno va con Groq[/]')
        return
    console.print(f'[dim][dueño] Claude Code respondió en {time.monotonic() - t0:.1f} s[/]')
    _marcar_visto(vp, sesion, fin + 2)            # este turno y su respuesta ya los sabe
    yield from frases_para_decir(respuesta, ING_MAX_FRASES)


# ── Búsqueda en internet del modo ingeniero (fase S4) ─────────────────────────────
def _buscar_para(consultas):
    """Fuentes para las consultas: primero noticias y, si no hay, búsqueda general.
    Lista (puede ser []), o None si Serper no respondió."""
    fuentes = busqueda.buscar_todas(consultas, noticias=True)
    if fuentes == []:
        fuentes = busqueda.buscar_todas(consultas, noticias=False)
    return fuentes


def _mostrar_fuentes(consultas, fuentes) -> None:
    """Terminal: las consultas y cada fuente con su enlace, para ver de dónde sale la respuesta."""
    from voz.pipeline import console
    console.print(f'[bold cyan][búsqueda][/] consultas: {escape(" | ".join(consultas))}')
    for i, f in enumerate(fuentes, 1):
        fecha = f' · {f["fecha"]}' if f.get('fecha') else ''
        titulo = escape(f'[{i}] {f["titulo"]}')
        console.print(f'  {titulo} · {escape(f["dominio"] + fecha)}')
        console.print(f'      {escape(f["url"])}')


def _responder_con_fuentes(vp, texto: str, consultas, sesion):
    """Generador de frases: busca, rankea y responde citando [n], en streaming. Si no hay
    fuentes o algo falla, lo dice Claude (sin herramientas) con esa nota."""
    from voz.pipeline import console
    fuentes = _buscar_para(consultas)
    if fuentes is None:
        nota = 'No pude buscar en internet ahora: dilo y responde con lo que sabes.'
    elif not fuentes:
        nota = 'La búsqueda no encontró nada: dilo y responde con lo que sabes.'
    else:
        _mostrar_fuentes(consultas, fuentes)
        dichas = []
        for frase in busqueda.responder_con_fuentes(texto, fuentes):
            dichas.append(frase)
            console.print(f'  [bold cyan]Bob:[/] {escape(frase)}')
            yield frase
        if dichas:
            # el turno siguiente de Claude lo sabe: lo dicho y las fuentes, para poder explicarlas
            lista = '; '.join(f'[{i}] {f["titulo"]} ({f["dominio"]}): {f["url"]}'
                              for i, f in enumerate(fuentes, 1))
            vp._contexto_web = f'{" ".join(dichas)} Fuentes de esa búsqueda: {lista}'
            return
        nota = 'No pude armar la respuesta con las fuentes: dilo y responde con lo que sabes.'
    console.print(f'[yellow][dueño] búsqueda sin respuesta: {nota}[/]')
    yield from responder_claude(vp, texto, sesion, nota=nota)


def _sesion_sin_escritura(vp, sesion):
    """Un turno con contenido de internet no puede usar la sesión de escritura: en su lugar,
    la de lectura del modo. Las demás sesiones se usan tal cual."""
    if sesion is not None and getattr(sesion, 'nombre', '') == 'escritura':
        return getattr(vp, '_sesion_ing', None)
    return sesion


def responder_ingeniero(vp, texto: str, sesion):
    """Generador de frases de un turno del modo ingeniero. Primero descompone la pregunta
    (busqueda.descomponer): si hace falta buscar, responde con fuentes; si no, Claude Code
    sin herramientas. Si el descompositor falla, busca con la pregunta tal cual.
    Un turno con contenido de internet (búsqueda o resultado web) no escribe: va con la
    sesión de lectura (B-D1). Si no hay sesión de lectura, no responde y lo hace Groq."""
    if BUSQUEDA_INGENIERO_ENABLED:
        plan = busqueda.descomponer(texto)
        con_busqueda = plan is None or plan['buscar']
        consultas = (plan['consultas'] if plan else [texto]) if con_busqueda else []
    else:
        con_busqueda, consultas = False, []
    con_web = con_busqueda or bool(getattr(vp, '_contexto_web', None))
    voz = getattr(vp, '_voz_turno', None)
    sin_voz_firme = voz is None or voz < VOZ_UMBRAL_SHELL     # B-D11: sin voz del dueño, sin shell
    sesion_turno = _sesion_sin_escritura(vp, sesion) if (con_web or sin_voz_firme) else sesion
    if sesion is not None and sesion_turno is None:    # solo había escritura: Groq responde
        return
    if con_web:          # la respuesta (va en _convo después del pedido) no llega a la escritura
        vp._ing_web = getattr(vp, '_ing_web', set())
        vp._ing_web.add(len(getattr(vp, '_convo', [])))
    if con_busqueda:
        yield from _responder_con_fuentes(vp, texto, consultas, sesion_turno)
        return
    yield from responder_claude(vp, texto, sesion_turno)


# ── Agente web (dentro del modo ingeniero) ──────────────────────────────────────


def _sesion_web(vp):
    from agente import claude_code
    from voz.pipeline import console
    sesion = getattr(vp, '_sesion_web', None)
    if sesion is None:
        args = ['-y', PLAYWRIGHT_MCP, '--isolated',
                '--output-dir', os.path.join(claude_code.CWD_DEFAULT, 'web')]
        if WEB_HEADLESS:
            args.append('--headless')
        sesion = claude_code.SesionClaude(
            WEB_PROMPT, modelo=CLAUDE_MODELO, nombre='web', herramientas='WebSearch',
            mcp={'playwright': {'command': 'npx', 'args': args}},
            permitidas=['WebSearch'] + WEB_PERMITIDAS, prohibidas=WEB_PROHIBIDAS,
            conservar=True)                  # un turno cortado no cierra el navegador
        vp._sesion_web = sesion
        console.print('[dim][dueño] agente web: sesión nueva (Playwright MCP)[/]')
    return sesion


def web(vp, tarea: str) -> None:
    """Tarea en el navegador (Playwright), sin pedir confirmación: la voz del dueño ya se
    verificó en el comando. El navegador queda abierto hasta salir del modo ingeniero; el
    resultado se dice, se muestra en la terminal y queda de contexto."""
    from agente import claude_code
    from voz.pipeline import console
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
    _decir(vp, 'Dale, lo hago en el navegador.' if _navegador_abierto(vp) else 'Abro el navegador.',
           'PENSANDO')
    sesion = _sesion_web(vp)
    memoria, _, fin = _memoria_pendiente(vp, sesion)
    vp._convo.append({'role': 'user', 'content': tarea})
    mensaje = (f'[Antes en esta charla: {memoria}]\n' if memoria else '') + tarea
    pasos = []

    def al_usar(nombre, entrada):
        pasos.append(nombre.replace(_WEB, ''))
        console.print(f'[dim][web] {pasos[-1]} '
                      f'{str(entrada.get("url") or entrada.get("query") or entrada.get("text") or "")[:80]}[/]')
    t0 = time.monotonic()
    narrar = _preguntar_narrando(sesion, mensaje, WEB_TIMEOUT_S,
                                 max_herramientas=WEB_MAX_HERRAMIENTAS, al_usar=al_usar)
    try:
        while True:
            _decir(vp, next(narrar), 'PENSANDO')
    except StopIteration as fin_turno:
        resultado = fin_turno.value
    console.print(f'[bold magenta][dueño][/] web: {len(pasos)} pasos en '
                  f'{time.monotonic() - t0:.1f} s ({"ok" if resultado else "falló"})')
    if not resultado:
        aviso = ('No alcancé a terminar, pero el navegador sigue abierto. ¿Sigo desde ahí?'
                 if _navegador_abierto(vp) else
                 'No pude terminar la tarea en la web. ¿Probamos de otra forma?')
        _decir(vp, aviso, 'TRISTE')
        vp._convo.append({'role': 'assistant', 'content': aviso})
        return
    _marcar_visto(vp, sesion, fin + 2)
    console.print('[bold cyan][web] resultado:[/]')
    console.print(f'  {escape(resultado)}')
    frases = frases_para_decir(resultado, 3)
    for frase in frases:
        _decir(vp, frase, 'ORGULLOSO')
    vp._contexto_web = ' '.join(frases)
    vp._ing_web = getattr(vp, '_ing_web', set())
    vp._ing_web.add(len(vp._convo))               # contenido de internet: no va a la escritura
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
    from voz.pipeline import GROQ_LLM_MODEL, console
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
    from asistente.assistant import obtener_noticias
    from voz.pipeline import console
    _decir(vp, 'Déjame revisar qué pasó hoy en el mundo de la tecnología...', 'PENSANDO')
    titulares = obtener_noticias(NOTICIAS_TECH_MAX, url=NOTICIAS_TECH_RSS_URL)
    if not titulares:
        _decir(vp, 'No pude traer las noticias de tecnología, parece que no hay internet. '
                   '¿Probamos en un rato?', 'TRISTE')
        return
    console.print('[bold cyan][noticias][/] titulares:')
    for t in titulares:
        console.print(f'  • {escape(t)}')
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
    from voz.pipeline import DUENO_ID, console
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
