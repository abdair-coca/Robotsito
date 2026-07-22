"""
music.py — P6: control de música por Spotify (play/pause/siguiente/anterior/volumen).

Patrón intent + acción (como reminders.py): voice_pipeline detecta el comando en
charla, este módulo lo ejecuta vía la Spotify Web API y Bob confirma con una frase.

IMPORTANTE:
  - La música NO sale por el parlante del robot. Suena por el dispositivo
    **Spotify Connect** activo (la laptop con Spotify abierto, el celular, etc.).
  - Requiere Spotify **PREMIUM**: la API de playback no controla cuentas free.
  - Credenciales en robot_bob/.env (SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET /
    SPOTIFY_REDIRECT_URI). spotipy maneja el OAuth: abre el navegador la PRIMERA
    vez para autorizar y cachea el token en robot_bob/.spotify_cache (gitignored).

Enfoque defensivo: si la feature está apagada, sin credenciales, sin spotipy, sin
dispositivo activo o sin premium, NO rompe la charla — devuelve None en el parser
(cae al LLM) o una frase de error amable en ejecutar().
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from rich.console import Console
from typing import Optional

from config import MUSICA_ENABLED, SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, SPOTIFY_REDIRECT_URI, SPOTIFY_CACHE_PATH

_SCOPE = ("user-modify-playback-state user-read-playback-state "
          "playlist-read-private playlist-read-collaborative")
_VOL_STEP = 15            # cuánto sube/baja el volumen por comando (%)

console = Console(force_terminal=True, color_system="truecolor")
_sp = None                # cliente spotipy perezoso (se crea al primer uso)
_sp_lock = threading.Lock()


# ── Intención ─────────────────────────────────────────────────────────────────
@dataclass
class MusicIntent:
    accion: str                       # play | play_playlist | resume | pause | next | prev | vol_up | vol_down | vol_set
    query: Optional[str] = None       # tema (play) o nombre de playlist (play_playlist); None = genérico
    valor: Optional[int] = None       # volumen absoluto 0-100 (solo vol_set)


# Gatillos para reproducir algo específico ("pon X", "reproduce X"). El grupo (.+)
# captura el tema. Van con el verbo al inicio para no robar frases de charla.
_RE_PLAY = re.compile(
    r"\b(?:pon(?:e|me|ele)?|reproduce|reproduci|toca|escuch(?:a|ar|emos)|"
    r"quiero escuchar|quiero oir|quiero oír)\b\s+(.+)",
    re.IGNORECASE)

# Verbo de reproducción presente en la frase (para distinguir comando de charla).
_RE_PLAY_VERB = re.compile(
    r"\b(?:pon(?:e|me|ele)?|reproduce|reproduci|toca|escuch\w*|"
    r"quiero (?:escuchar|oir|oír)|dale play)\b", re.IGNORECASE)

# Playlist: captura el nombre tras "playlist"/"lista (de reproducción)". Acepta
# plural y conectores ("de", "llamada", "que se llama"). Sin nombre → playlist genérica.
_RE_PLAYLIST = re.compile(
    r"\b(?:playlists?|listas?(?:\s+de\s+reproducci[oó]n)?)\b\s*"
    r"(?:de\s+|llamada\s+|titulada\s+|que se llama\s+)?(.*)$",
    re.IGNORECASE)

# Frases sin tema → reanudar / poner música genérica.
_KW_RESUME = ("reanuda", "reanudá", "seguí la música", "sigue la música",
              "continua la música", "continúa la música", "dale play",
              "pon música", "pone música", "ponme música", "quiero música")
_KW_PAUSE = ("pausa", "pausá", "pará la música", "para la música",
             "detén la música", "deten la música", "para la canción",
             "frena la música", "stop")
_KW_NEXT = ("siguiente canción", "próxima canción", "proxima canción",
            "otra canción", "cambia de canción", "cambia la canción",
            "salta la canción", "siguiente tema", "pasa la canción",
            "siguiente", "próxima", "proxima", "salta", "sáltala", "saltala",
            "cambia de tema", "la que sigue", "pasa a la otra", "siguiente tema",
            "ponme otra", "pon otra")
_KW_PREV = ("canción anterior", "tema anterior", "vuelve a la anterior",
            "la anterior", "regresa la canción", "atrás la canción",
            "anterior", "la de antes", "vuelve atrás", "vuelve atras",
            "retrocede", "regresa", "la previa", "pon la anterior")
_KW_VOL_UP = ("sube el volumen", "subí el volumen", "más fuerte", "mas fuerte",
              "más alto", "mas alto", "más volumen", "mas volumen",
              "sube la música", "sube la musica", "súbele", "subele",
              "ponlo más fuerte", "ponlo mas fuerte", "más duro", "mas duro")
_KW_VOL_DOWN = ("baja el volumen", "bajá el volumen", "más bajo", "mas bajo",
                "más despacio", "mas despacio", "menos volumen",
                "baja la música", "baja la musica", "bájale", "bajale",
                "ponlo más bajo", "ponlo mas bajo", "más suave", "mas suave",
                "más despacito", "mas despacito")

# Palabras de relleno a quitar del tema buscado ("pon la canción X" → "X").
_FILLER_QUERY = re.compile(
    r"^(?:la|el|una|un|esa|ese|esta|este|por favor|porfa|"
    r"canción|cancion|tema|música|musica|de|a)\s+", re.IGNORECASE)


# ── Volumen por porcentaje ("pon el volumen al 70 por ciento") ─────────────────
# Whisper a veces escribe el número como dígitos ("70") y a veces como palabra
# ("setenta"). Cubrimos ambos: dígitos directos y un mapa de palabras comunes.
_NUM_PALABRA = {
    "cero": 0, "diez": 10, "quince": 15, "veinte": 20, "veinticinco": 25,
    "treinta": 30, "treinta y cinco": 35, "cuarenta": 40, "cuarenta y cinco": 45,
    "cincuenta": 50, "cincuenta y cinco": 55, "sesenta": 60, "sesenta y cinco": 65,
    "setenta": 70, "setenta y cinco": 75, "ochenta": 80, "ochenta y cinco": 85,
    "noventa": 90, "noventa y cinco": 95, "cien": 100, "ciento": 100,
    "mitad": 50, "medio": 50, "máximo": 100, "maximo": 100, "tope": 100,
    "mínimo": 0, "minimo": 0,
}
# Dispara si el texto habla de volumen y trae un número, o si trae "X por ciento".
_RE_PORCENTAJE = re.compile(r"(\d{1,3})\s*(?:%|por\s*ciento|por\s*cien)?", re.IGNORECASE)


def _parse_vol_set(t: str) -> Optional[int]:
    """Devuelve el volumen absoluto 0-100 si `t` lo pide ('volumen al 70'), o None."""
    menciona_vol = "volumen" in t
    menciona_pct = ("por ciento" in t) or ("por cien" in t) or ("%" in t)
    if not (menciona_vol or menciona_pct):
        return None
    # 1) Dígitos ("70", "70%", "al 70 por ciento").
    m = re.search(r"\b(\d{1,3})\b", t)
    if m:
        return max(0, min(100, int(m.group(1))))
    # 2) Palabra-número (match más largo primero: "setenta y cinco" antes que "setenta").
    for palabra in sorted(_NUM_PALABRA, key=len, reverse=True):
        if palabra in t:
            return max(0, min(100, _NUM_PALABRA[palabra]))
    return None


def _limpiar_query(q: str) -> str:
    q = q.strip().strip("?!.¡¿,").strip()
    prev = None
    while prev != q:                  # saca varios rellenos encadenados
        prev = q
        q = _FILLER_QUERY.sub("", q).strip()
    return q


def parse_music_command(texto: str) -> Optional[MusicIntent]:
    """Devuelve un MusicIntent si `texto` es un comando de música, o None."""
    if not MUSICA_ENABLED:
        return None
    t = texto.lower().strip()

    # Pausa / siguiente / anterior / volumen: frases fijas (chequear antes que play,
    # porque "pasa la canción" no debe caer como búsqueda).
    if any(k in t for k in _KW_PAUSE):
        return MusicIntent("pause")
    if any(k in t for k in _KW_NEXT):
        return MusicIntent("next")
    if any(k in t for k in _KW_PREV):
        return MusicIntent("prev")
    # Volumen ABSOLUTO ("pon el volumen al 70 por ciento") ANTES que sube/baja,
    # porque "baja el volumen a 30" trae número → es vol_set, no vol_down.
    vol = _parse_vol_set(t)
    if vol is not None:
        return MusicIntent("vol_set", valor=vol)
    if any(k in t for k in _KW_VOL_UP):
        return MusicIntent("vol_up")
    if any(k in t for k in _KW_VOL_DOWN):
        return MusicIntent("vol_down")
    if any(k in t for k in _KW_RESUME):
        return MusicIntent("resume")

    # Playlist ("pon mi playlist de rock", "reproduce mis playlists"). Va ANTES de
    # _RE_PLAY para que "playlist X" no se interprete como un tema suelto. Requiere
    # un verbo de reproducción para no confundir charla ("tengo una playlist").
    if _RE_PLAY_VERB.search(t):
        mpl = _RE_PLAYLIST.search(texto)
        if mpl:
            nombre = _limpiar_query(mpl.group(1))
            # sacar posesivos sueltos que queden ("mi", "mis")
            nombre = re.sub(r"^(?:mi|mis|tu|tus)\s+", "", nombre, flags=re.IGNORECASE).strip()
            return MusicIntent("play_playlist", query=nombre or None)

    # Reproducir algo específico ("pon despacito", "reproduce bohemian rhapsody").
    m = _RE_PLAY.search(texto)
    if m:
        query = _limpiar_query(m.group(1))
        if query:
            return MusicIntent("play", query=query)
        return MusicIntent("resume")     # "pon" sin tema → reanudar
    return None


# ── Cliente Spotify (perezoso) ──────────────────────────────────────────────────
def _cliente():
    """Crea/reusa el cliente spotipy. Devuelve None si falta config o spotipy."""
    global _sp
    if _sp is not None:
        return _sp
    if not (MUSICA_ENABLED and SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET):
        return None
    with _sp_lock:
        if _sp is not None:
            return _sp
        try:
            import spotipy
            from spotipy.oauth2 import SpotifyOAuth
            auth = SpotifyOAuth(
                client_id=SPOTIFY_CLIENT_ID,
                client_secret=SPOTIFY_CLIENT_SECRET,
                redirect_uri=SPOTIFY_REDIRECT_URI,
                scope=_SCOPE,
                cache_path=SPOTIFY_CACHE_PATH,
                open_browser=True,
            )
            _sp = spotipy.Spotify(auth_manager=auth, requests_timeout=8)
        except Exception as e:
            console.print(f"[yellow][music] no se pudo iniciar Spotify: {e}[/]")
            _sp = None
    return _sp


def _dispositivo_activo(sp) -> bool:
    try:
        pb = sp.current_playback()
        return bool(pb and pb.get("device"))
    except Exception:
        return False


def esta_sonando() -> bool:
    """True si Spotify está reproduciendo algo AHORA. Lo usa el baile para saber
    cuándo parar. Defensivo: cualquier fallo (sin red/cliente/premium) → False."""
    sp = _cliente()
    if sp is None:
        return False
    try:
        pb = sp.current_playback()
        return bool(pb and pb.get("is_playing"))
    except Exception:
        return False


# ── Ejecución ─────────────────────────────────────────────────────────────────
def ejecutar(intent: MusicIntent) -> str:
    """Ejecuta el comando y devuelve una frase de confirmación/erro para que la diga
    Bob. Nunca lanza: cualquier fallo se traduce en una frase amable."""
    sp = _cliente()
    if sp is None:
        return "Mi música no está configurada todavía; falta la clave de Spotify."

    try:
        import spotipy
        # Para play con tema no hace falta dispositivo previo si Spotify resuelve uno,
        # pero el resto de acciones sí lo necesitan. Avisamos claro si no hay.
        if intent.accion != "play" and not _dispositivo_activo(sp):
            return "No veo Spotify abierto en ningún lado; abrilo y lo intento de nuevo."

        if intent.accion == "play":
            res = sp.search(q=intent.query, type="track", limit=1)
            items = res.get("tracks", {}).get("items", [])
            if not items:
                return f"No encontré «{intent.query}» en Spotify."
            track = items[0]
            uri = track["uri"]
            nombre = track["name"]
            artista = track["artists"][0]["name"] if track.get("artists") else ""
            try:
                sp.start_playback(uris=[uri])
            except spotipy.SpotifyException:
                return "Encontré la canción pero no hay un Spotify activo donde reproducirla; abrilo."
            return f"¡Dale! Poniendo {nombre}" + (f" de {artista}." if artista else ".")

        if intent.accion == "play_playlist":
            pls = sp.current_user_playlists(limit=50).get("items", [])
            if not pls:
                return "No veo playlists en tu cuenta de Spotify."
            if intent.query:
                q = intent.query.lower()
                # 1º coincidencia exacta, si no, la primera que contenga el nombre.
                match = (next((p for p in pls if p["name"].lower() == q), None)
                         or next((p for p in pls if q in p["name"].lower()), None))
                if not match:
                    return f"No encontré una playlist que se llame «{intent.query}»."
            else:
                match = pls[0]               # sin nombre → la primera de tu lista
            try:
                sp.start_playback(context_uri=match["uri"])
            except spotipy.SpotifyException:
                return "Encontré la playlist pero no hay un Spotify activo; abrilo."
            return f"¡Dale! Poniendo tu playlist {match['name']}."

        if intent.accion == "resume":
            sp.start_playback()
            return "¡Sigue la música!"

        if intent.accion == "pause":
            sp.pause_playback()
            return "Listo, pausé la música."

        if intent.accion == "next":
            sp.next_track()
            return "¡Va la siguiente!"

        if intent.accion == "prev":
            sp.previous_track()
            return "Volviendo a la anterior."

        if intent.accion == "vol_set":
            v = max(0, min(100, int(intent.valor if intent.valor is not None else 50)))
            sp.volume(v)
            return f"¡Listo! Volumen al {v} por ciento."

        if intent.accion in ("vol_up", "vol_down"):
            pb = sp.current_playback()
            actual = (pb or {}).get("device", {}).get("volume_percent")
            if actual is None:
                return "No pude leer el volumen actual de Spotify."
            nuevo = actual + _VOL_STEP if intent.accion == "vol_up" else actual - _VOL_STEP
            nuevo = max(0, min(100, nuevo))
            sp.volume(nuevo)
            verbo = "Subo" if intent.accion == "vol_up" else "Bajo"
            return f"{verbo} el volumen a {nuevo} por ciento."

        return "No entendí qué hacer con la música."

    except Exception as e:
        console.print(f"[red][music] error: {e}[/]")
        return "Uy, algo falló con Spotify; fijate que esté abierto y sea cuenta premium."
