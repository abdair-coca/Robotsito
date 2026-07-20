"""
show.py — Modo presentación de Bob ("Bob, presentate").

Coreografía FIJA de ~75 s que muestra todas las habilidades en orden:
  1. Intro
  2. Emociones en los ojos OLED
  3. Movimiento de cabeza (pan/tilt)
  4. Giro del cuerpo (motores)
  5. Reconocimiento facial + memoria de personas
  6. Recordatorios (mención)
  7. Datos en vivo (hora + clima)
  8. Cierre: arranca música en Spotify y se queda bailando

Guion fijo (sin LLM): confiable para demo, timing exacto frase-acción.
Cada paso degrada con gracia si su subsistema no está disponible.

run_show(vp) recibe el VoicePipeline en curso y usa sus piezas
(_hablar, _serial, _sm, _face_id, _memoria, _get_frame).
"""

import re
import time

from assistant import hora_actual, obtener_clima
from config import MUSICA_ENABLED

# Canción del cierre (query de búsqueda en Spotify). Editable en config.py.
from config import SHOW_CANCION

# ── Detección del comando ───────────────────────────────────────────────────────
_RE_SHOW = re.compile(
    r"\b(pres[eé]nt[aá]te|presentaci[oó]n|qu[eé] sab[eé]s? hacer|"
    r"qu[eé] puedes hacer|qu[eé] hac[eé]s|qui[eé]n (eres|sos)|"
    r"mu[eé]stra(nos|te|me|les)( (todo )?lo que sab[eé]s)?|"
    r"(haz|hac[eé]) tu (show|demo|presentaci[oó]n)|demuestra)\b",
    re.IGNORECASE)


def es_comando_show(texto: str) -> bool:
    return bool(_RE_SHOW.search(texto or ""))


# ── Show ────────────────────────────────────────────────────────────────────────

def run_show(vp) -> bool:
    """Corre el show completo. Devuelve True si cerró con música + baile
    (la charla debe terminar ahí para dejar a Bob bailando con wake activo)."""
    sm, serial = vp._sm, vp._serial
    hablar = vp._hablar

    sm.show_activo.set()     # congela BehaviorEngine: los servos son del show
    sm.oled_ocupar()         # sin muecas pisando las caras del guion

    def ojos(estado):
        serial.cmd_estado(estado)

    con_baile = False
    try:
        # ── 1. INTRO ────────────────────────────────────────────────────────
        ojos('MUY_FELIZ')
        serial.cmd_servo(90, 80)
        pos = (90.0, 80.0)
        time.sleep(0.3)
        hablar('¡Hola amiguitos! Soy Bob, un robotito hecho en Potosí con '
               'mucho cariño. ¿Quieren ver todo lo que sé hacer? ¡Miren!',
               'MUY_FELIZ')

        # ── 2. EMOCIONES OLED ───────────────────────────────────────────────
        hablar('Mis ojitos muestran cómo me siento. ¿Ven?', 'FELIZ')
        for estado, frase in (
                ('CURIOSO',     'Así me pongo cuando algo me da curiosidad.'),
                ('PENSANDO',    'Así, cuando pienso bien fuerte.'),
                ('SOSPECHANDO', 'Así, cuando alguien se hace el misterioso...'),
                ('MUY_FELIZ',   '¡Y así de feliz me pongo cuando me visitan!')):
            ojos(estado)
            time.sleep(0.4)
            hablar(frase, estado)

        # ── 3. CABEZA (lenta y suave) ───────────────────────────────────────
        hablar('Mi cabecita se mueve solita, despacito, y te sigue '
               'a donde vayas.', 'CURIOSO')
        for destino in ((55, 85), (125, 85), (90, 70), (90, 95), (90, 85)):
            pos = _mover_suave(serial, pos, destino)
            time.sleep(0.15)

        # ── 4. CUERPO ───────────────────────────────────────────────────────
        hablar('Y si te me escondés... ¡giro con todo mi cuerpito '
               'para encontrarte!', 'SOSPECHANDO')
        _wiggle_cuerpo(serial)

        # ── 5. RECONOCIMIENTO FACIAL + MEMORIA ──────────────────────────────
        _demo_reconocimiento(vp, hablar, ojos)

        # ── 6. RECORDATORIOS ────────────────────────────────────────────────
        hablar('También te cuido: decime «Bob, recordame algo» '
               'y yo te aviso justo a tiempo.', 'PENSANDO')

        # ── 7. DATOS EN VIVO ────────────────────────────────────────────────
        _demo_asistente(hablar, ojos)

        # ── 8. CIERRE MUSICAL ───────────────────────────────────────────────
        con_baile = _cierre_musical(vp, hablar, ojos)
    finally:
        sm.oled_liberar()
        sm.show_activo.clear()
    return con_baile


def _mover_suave(serial, desde, hasta, paso=1.5, dt=0.06):
    """Interpola pan/tilt en pasitos chicos → movimiento lento y suave (~25°/s),
    nada de saltos bruscos. Devuelve la posición final alcanzada."""
    pan, tilt = float(desde[0]), float(desde[1])
    pan_f, tilt_f = float(hasta[0]), float(hasta[1])
    while abs(pan - pan_f) > paso or abs(tilt - tilt_f) > paso:
        pan  += max(-paso, min(paso, pan_f - pan))
        tilt += max(-paso, min(paso, tilt_f - tilt))
        serial.cmd_servo(pan, tilt)
        time.sleep(dt)
    serial.cmd_servo(pan_f, tilt_f)
    return (pan_f, tilt_f)


def _wiggle_cuerpo(serial) -> None:
    """Giro corto a un lado y al otro. No hace nada si los motores están off."""
    from config import MOTORES_ENABLED, GIRO_VELOCIDAD
    if not MOTORES_ENABLED:
        return
    v = int(min(90, GIRO_VELOCIDAD))
    for izq, der, dur in ((v, -v, 0.35), (0, 0, 0.25), (-v, v, 0.35), (0, 0, 0.1)):
        serial.cmd_motor(izq, der)
        time.sleep(dur)
    serial.cmd_motor(0, 0)


def _demo_reconocimiento(vp, hablar, ojos) -> None:
    hablar('Ahora lo mejor: yo veo con mi propia cámara... '
           'y nunca olvido una cara.', 'CURIOSO')
    activo = (vp._face_id is not None and vp._memoria is not None
              and vp._get_frame is not None and vp._face_id.listo)
    if not activo:
        hablar('Mi memoria de caras está descansando ahora, pero cuando está '
               'despierta, ¡me acuerdo de todos!', 'FELIZ')
        return
    time.sleep(0.8)         # que alguien quede frente a la cámara
    try:
        emb, _ = vp._face_id.analizar(vp._get_frame())
        m = vp._memoria.reconocer(emb) if emb is not None else None
        total = vp._memoria.total_personas()
    except Exception:
        emb, m, total = None, None, 0
    if m and m[1]:
        hablar(f'¡{m[1]}! A vos ya te conozco. ¡Qué lindo verte de nuevo!',
               'MUY_FELIZ')
    elif emb is not None:
        hablar('A vos todavía no te conozco... ¡pero ya te estoy guardando '
               'en mi corazoncito!', 'SOSPECHANDO')
    else:
        hablar('Ahora no veo bien ninguna carita. ¡Pónganse frente a mi '
               'cámara la próxima!', 'CURIOSO')
    if total:
        hablar(f'Ya tengo {total} amiguitos guardados en mi memoria.', 'FELIZ')


def _demo_asistente(hablar, ojos) -> None:
    ojos('PENSANDO')
    frase = f'¿Datos en vivo? Fácil: son las {hora_actual()}'
    clima = obtener_clima()      # None si no hay red (timeout 3 s)
    if clima:
        frase += f', y en Potosí el cielo está así: {clima}'
    frase += '. ¿Y saben qué es lo mejor? ¡Puedo hacer todo eso solito!'
    hablar(frase, 'MUY_FELIZ')


def _cierre_musical(vp, hablar, ojos) -> bool:
    hablar('Y ahora sí... ¡lo que más me gusta en el mundo: bailar! '
           '¡DJ, dale play!', 'MUY_FELIZ')
    if not MUSICA_ENABLED:
        hablar('Hoy vine sin mi Spotify... pero decime «Bob» y charlamos '
               'igual. ¡Gracias!', 'FELIZ')
        return False
    from music import MusicIntent, ejecutar
    ack = ejecutar(MusicIntent(accion='play', query=SHOW_CANCION))
    if 'Poniendo' in ack:        # frase de éxito de music.ejecutar
        ojos('MUY_FELIZ')
        vp._t_baile_inicio = time.monotonic()
        vp._baile_hint = True
        vp._sm.bailando.set()
        return True
    hablar('Mi DJ no me responde ahora... ¡otro día les bailo! '
           'Decime «Bob» y charlamos. ¡Gracias!', 'PENSANDO')
    return False
