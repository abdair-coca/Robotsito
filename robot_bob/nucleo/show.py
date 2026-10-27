"""
show.py — Modo presentación de Bob ("Bob, presentate").

Coreografía FIJA de ~95 s que muestra todas las habilidades en orden:
  1. Intro
  2. Emociones en los ojos OLED, con gestos de cabeza
  3. Movimiento de cabeza (pan/tilt)
  4. Giro del cuerpo (motores)
  5. Reconocimiento facial + memoria de personas ("te presento a…")
  6. Voz del dueño
  7. Modo ingeniero (mención: código, navegador, búsqueda con fuentes)
  8. Recordatorios (mención)
  9. Datos en vivo (hora + clima)
 10. Cierre: arranca música en Spotify y se queda bailando

Guion fijo (sin LLM): confiable para demo, timing exacto frase-acción.
Las teclas D, J y P (vp._cortado) lo cortan en la próxima frase o movimiento.
Cada paso degrada con gracia si su subsistema no está disponible.
Los gestos de cabeza van mientras Bob habla, chicos y lentos: con movimientos fuertes
la fuente actual hace caer el voltaje (y se reinicia la cámara).

run_show(vp) recibe el VoicePipeline en curso y usa sus piezas
(_hablar, _serial, _sm, _face_id, _memoria, _get_frame).
"""

import re
import threading
import time

from asistente.assistant import hora_actual, obtener_clima
from config import MUSICA_ENABLED
from nucleo import puente_cli

# Canción del cierre (query de búsqueda en Spotify). Editable en config.py.
try:
    from config import SHOW_CANCION
except Exception:
    SHOW_CANCION = "Daddy Cool Boney M"

# ── Detección del comando ───────────────────────────────────────────────────────
_RE_SHOW = re.compile(
    r"\b(pres[eé]nt[aá]te|presentaci[oó]n|qu[eé] sab[eé]s? hacer|"
    r"qu[eé] puedes hacer|qu[eé] hac[eé]s|qui[eé]n (eres|sos)|"
    r"mu[eé]stra(nos|te|me|les)( (todo )?lo que sab[eé]s)?|"
    r"(haz|hac[eé]) tu (show|demo|presentaci[oó]n)|demuestra)\b",
    re.IGNORECASE)


def es_comando_show(texto: str) -> bool:
    return bool(_RE_SHOW.search(texto or ""))


def voz_visible(vp):
    """vp._hablar que además muestra la frase como turno de Bob (en el CLI y la
    consola), igual que en una charla. La usan el show, la foto y los modos."""
    from rich.markup import escape
    from voz.pipeline.consola import console   # acá: voz.pipeline importa este módulo

    def hablar(texto, emo=None):
        with puente_cli.turno('bob'):
            console.print(f'[bold green]Bob[/] [dim]({emo})[/]: {escape(texto)}')
        vp._hablar(texto, emo)
    return hablar


# ── Show ────────────────────────────────────────────────────────────────────────

class _Cortado(Exception):
    """Teclas D/J/P durante el show: se deja de actuar ahí mismo."""


def run_show(vp) -> bool:
    """Corre el show completo. Devuelve True si cerró con música + baile
    (la charla debe terminar ahí para dejar a Bob bailando con wake activo)."""
    sm, serial = vp._sm, vp._serial
    hablar = voz_visible(vp)
    cortado = getattr(vp, '_cortado', lambda: False)

    def seguir():
        if cortado():
            raise _Cortado

    sm.show_activo.set()     # congela BehaviorEngine: los servos son del show
    sm.oled_ocupar()         # sin muecas pisando las caras del guion

    def ojos(estado):
        serial.cmd_estado(estado)

    cabeza = _Cabeza(serial, (90.0, 80.0))

    def decir(frase, estado, gesto=None):
        """Cara + frase; el gesto de cabeza va mientras habla (no antes ni después)."""
        seguir()
        ojos(estado)
        hilo = threading.Thread(target=gesto, daemon=True, name='show-gesto') if gesto else None
        if hilo:
            hilo.start()
        hablar(frase, estado)
        if hilo:
            hilo.join(timeout=5.0)
        seguir()

    con_baile = False
    try:
        # ── 1. INTRO ────────────────────────────────────────────────────────
        ojos('MUY_FELIZ')
        serial.cmd_servo(90, 80)
        time.sleep(0.3)
        decir('¡Hola, hola, amiguitos! Soy Bob, un robotito hecho en Potosí con '
              'mucho cariño... y unos cuantos cables.', 'MUY_FELIZ', cabeza.asentir)
        decir('¿Quieren ver todo lo que sé hacer? ¡Acérquense, que no muerdo!',
              'TRAVIESO', cabeza.ladito)

        # ── 2. EMOCIONES OLED + GESTOS ──────────────────────────────────────
        decir('Mis ojitos muestran cómo me siento. ¿Ven?', 'FELIZ')
        for estado, frase, gesto in (
                ('CURIOSO',     'Así me pongo cuando algo me da curiosidad.', cabeza.ladito),
                ('SORPRENDIDO', '¡Así, cuando algo me sorprende!', cabeza.atras),
                ('PENSANDO',    'Así, cuando pienso bien fuerte.', None),
                ('AVERGONZADO', 'Así, cuando me dicen algo bonito... ay, no me miren tanto.',
                 cabeza.mirar_abajo),
                ('TRISTE',      'Así, cuando se van sin despedirse...', cabeza.mirar_abajo),
                ('MUY_FELIZ',   '¡Pero se me pasa rapidito, porque ustedes vinieron a '
                                'verme!', cabeza.asentir)):
            time.sleep(0.3)
            decir(frase, estado, gesto)

        # ── 3. CABEZA (lenta y suave) ───────────────────────────────────────
        decir('Mi cabecita se mueve solita, despacito, y te sigue '
              'a donde vayas.', 'CURIOSO')
        for destino in ((55, 85), (125, 85), (90, 70), (90, 95), (90, 85)):
            seguir()
            cabeza.ir(destino)
            time.sleep(0.15)

        # ── 4. CUERPO ───────────────────────────────────────────────────────
        decir('Y si te me escondés... ¡giro con todo mi cuerpito '
              'para encontrarte!', 'TRAVIESO')
        seguir()
        _wiggle_cuerpo(serial)

        # ── 5. RECONOCIMIENTO FACIAL + MEMORIA ──────────────────────────────
        _demo_reconocimiento(vp, decir, cabeza)

        # ── 6. VOZ DEL DUEÑO ────────────────────────────────────────────────
        decir('Y no solo veo: también escucho. Reconozco la voz de mi creador, '
              'así que hay cositas que solo le hago caso a él.', 'ORGULLOSO', cabeza.asentir)

        # ── 7. MODO INGENIERO ───────────────────────────────────────────────
        decir('Cuando trabajamos juntos, me pongo en modo ingeniero: leo su código, '
              'lo arreglo, instalo lo que falte y le abro su app en el navegador.',
              'EMOCIONADO', cabeza.ladito)
        decir('Y les voy contando lo que hago; si busco algo en internet, digo de dónde '
              'lo saqué. ¡Nada de inventar!', 'ORGULLOSO')

        # ── 8. RECORDATORIOS ────────────────────────────────────────────────
        decir('También te cuido: decime «Bob, recordame algo» '
              'y yo te aviso justo a tiempo.', 'AMOR')

        # ── 9. DATOS EN VIVO ────────────────────────────────────────────────
        _demo_asistente(decir)

        # ── 10. CIERRE MUSICAL ──────────────────────────────────────────────
        con_baile = _cierre_musical(vp, decir, ojos, cabeza)
    except _Cortado:
        serial.cmd_motor(0, 0)       # por si cortó girando
        con_baile = False
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


_PAN_LIM, _TILT_LIM = (20, 160), (40, 130)    # los de facial_tracker (tilt menor = mira arriba)


class _Cabeza:
    """Gestos de cabeza chicos y lentos alrededor de donde está; cada gesto vuelve al
    punto de partida. Un lock evita que un gesto y un movimiento se pisen."""

    def __init__(self, serial, pos):
        self._serial = serial
        self.pos = (float(pos[0]), float(pos[1]))
        self._lock = threading.Lock()

    def ir(self, destino, paso=1.5, dt=0.06):
        pan = max(_PAN_LIM[0], min(_PAN_LIM[1], destino[0]))
        tilt = max(_TILT_LIM[0], min(_TILT_LIM[1], destino[1]))
        with self._lock:
            self.pos = _mover_suave(self._serial, self.pos, (pan, tilt), paso, dt)

    def _ida_vuelta(self, d_pan, d_tilt, veces=1, pausa=0.3):
        base = self.pos
        for _ in range(veces):
            self.ir((base[0] + d_pan, base[1] + d_tilt), paso=1.0, dt=0.04)
            time.sleep(pausa)
            self.ir(base, paso=1.0, dt=0.04)

    def asentir(self):          # "sí", contento
        self._ida_vuelta(0, 6, veces=2, pausa=0.05)

    def ladito(self):           # curioso, pícaro
        self._ida_vuelta(10, 0, pausa=0.6)

    def atras(self):            # sorpresa: la cabeza sube un poquito
        self._ida_vuelta(0, -8, pausa=0.8)

    def mirar_abajo(self):      # vergüenza o tristeza
        self._ida_vuelta(-6, 10, pausa=1.2)

    def _vaiven(self, d_pan, veces, dt):
        base = self.pos
        for d in (d_pan, -d_pan) * veces:
            self.ir((base[0] + d, base[1]), paso=1.0, dt=dt)
        self.ir(base, paso=1.0, dt=dt)

    def negar(self):            # "no", gruñón
        self._vaiven(8, veces=2, dt=0.04)

    def temblar(self):          # miedo: tiritón chiquito (uno fuerte baja el voltaje)
        self._vaiven(3, veces=4, dt=0.03)


def _wiggle_cuerpo(serial) -> None:
    """Giro corto a un lado y al otro. No hace nada si los motores están off."""
    try:
        from config import MOTORES_ENABLED, GIRO_VELOCIDAD
    except Exception:
        return
    if not MOTORES_ENABLED:
        return
    v = int(min(90, GIRO_VELOCIDAD))
    for izq, der, dur in ((v, -v, 0.35), (0, 0, 0.25), (-v, v, 0.35), (0, 0, 0.1)):
        serial.cmd_motor(izq, der)
        time.sleep(dur)
    serial.cmd_motor(0, 0)


def _demo_reconocimiento(vp, decir, cabeza) -> None:
    decir('Ahora lo mejor: yo veo con mi propia cámara... '
          'y nunca olvido una cara.', 'CURIOSO', cabeza.ladito)
    activo = (vp._face_id is not None and vp._memoria is not None
              and vp._get_frame is not None and vp._face_id.listo)
    if not activo:
        decir('Mi memoria de caras está descansando ahora, pero cuando está '
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
        decir(f'¡{m[1]}! A vos ya te conozco. ¡Qué lindo verte de nuevo!',
              'AMOR', cabeza.asentir)
    elif emb is not None:
        decir('A vos todavía no te conozco... ¡pero ya te estoy guardando '
              'en mi corazoncito!', 'AVERGONZADO', cabeza.mirar_abajo)
    else:
        decir('Ahora no veo bien ninguna carita. ¡Pónganse frente a mi '
              'cámara la próxima!', 'CURIOSO')
    if total:
        decir(f'Ya tengo {total} amiguitos guardados en mi memoria.', 'FELIZ')
    decir('Y si mi creador me dice «Bob, te presento a alguien», ¡esa persona '
          'también pasa a ser mi amiguito!', 'MUY_FELIZ', cabeza.asentir)


def _demo_asistente(decir) -> None:
    frase = f'¿Datos en vivo? Fácil: son las {hora_actual()}'
    clima = obtener_clima()      # None si no hay red (timeout 3 s)
    if clima:
        frase += f', y en Potosí el cielo está así: {clima}'
    frase += '. ¿Y saben qué es lo mejor? ¡Puedo hacer todo eso solito!'
    decir(frase, 'MUY_FELIZ')


def _cierre_musical(vp, decir, ojos, cabeza) -> bool:
    decir('Gracias por quedarse conmigo todo este ratito. Me hicieron muy, '
          'muy feliz.', 'AMOR', cabeza.mirar_abajo)
    decir('Y ahora sí... ¡lo que más me gusta en el mundo: bailar! '
          '¡DJ, dale play!', 'EMOCIONADO', cabeza.asentir)
    hablar = voz_visible(vp)
    if not MUSICA_ENABLED:
        hablar('Hoy vine sin mi Spotify... pero decime «Bob» y charlamos '
               'igual. ¡Gracias!', 'FELIZ')
        return False
    from asistente.music import MusicIntent, ejecutar
    ack = ejecutar(MusicIntent(accion='play', query=SHOW_CANCION))
    if 'Poniendo' in ack:        # frase de éxito de music.ejecutar
        ojos('MUY_FELIZ')
        vp._t_baile_inicio = time.monotonic()
        vp._baile_hint = True
        vp._sm.musica_sonando.set()
        vp._sm.bailando.set()
        return True
    hablar('Mi DJ no me responde ahora... ¡otro día les bailo! '
           'Decime «Bob» y charlamos. ¡Gracias!', 'PENSANDO')
    return False
