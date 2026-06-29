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
from typing import Optional

try:
    from config import (MUSICA_ENABLED, SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET,
                        SPOTIFY_REDIRECT_URI, SPOTIFY_CACHE_PATH)
except Exception:
    MUSICA_ENABLED = False
    SPOTIFY_CLIENT_ID = SPOTIFY_CLIENT_SECRET = SPOTIFY_REDIRECT_URI = ""
    SPOTIFY_CACHE_PATH = ".spotify_cache"

_SCOPE = "user-modify-playback-state user-read-playback-state"
_VOL_STEP = 15            # cuánto sube/baja el volumen por comando (%)

_sp = None                # cliente spotipy perezoso (se crea al primer uso)
_sp_lock = threading.Lock()


# ── Intención ─────────────────────────────────────────────────────────────────
@dataclass
class MusicIntent:
    accion: str                       # play | resume | pause | next | prev | vol_up | vol_down
    query: Optional[str] = None       # tema/artista a buscar (solo accion == 'play')


# Gatillos para reproducir algo específico ("pon X", "reproduce X"). El grupo (.+)
# captura el tema. Van con el verbo al inicio para no robar frases de charla.
_RE_PLAY = re.compile(
    r"\b(?:pon(?:e|me|ele)?|reproduce|reproduci|toca|escuch(?:a|ar|emos)|"
    r"quiero escuchar|quiero oir|quiero oír)\b\s+(.+)",
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
            "salta la canción", "siguiente tema", "pasa la canción")
_KW_PREV = ("canción anterior", "tema anterior", "vuelve a la anterior",
            "la anterior", "regresa la canción", "atrás la canción")
_KW_VOL_UP = ("sube el volumen", "subí el volumen", "más fuerte", "más alto",
              "más volumen", "sube la música")
_KW_VOL_DOWN = ("baja el volumen", "bajá el volumen", "más bajo", "más despacio",
                "menos volumen", "baja la música")

# Palabras de relleno a quitar del tema buscado ("pon la canción X" → "X").
_FILLER_QUERY = re.compile(
    r"^(?:la|el|una|un|esa|ese|esta|este|por favor|porfa|"
    r"canción|cancion|tema|música|musica|de|a)\s+", re.IGNORECASE)


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
    if any(k in t for k in _KW_VOL_UP):
        return MusicIntent("vol_up")
    if any(k in t for k in _KW_VOL_DOWN):
        return MusicIntent("vol_down")
    if any(k in t for k in _KW_RESUME):
        return MusicIntent("resume")

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
            print(f"[music] no se pudo iniciar Spotify: {e}")
            _sp = None
    return _sp


def _dispositivo_activo(sp) -> bool:
    try:
        pb = sp.current_playback()
        return bool(pb and pb.get("device"))
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
        print(f"[music] error: {e}")
        return "Uy, algo falló con Spotify; fijate que esté abierto y sea cuenta premium."
