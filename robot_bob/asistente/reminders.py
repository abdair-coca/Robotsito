"""
reminders.py — P9 (Productividad): recordatorios / temporizadores / alarmas.

Dos piezas:
  - parse_recordatorio(texto): detecta la intención de CREAR un recordatorio en lo
    que dijo el usuario y extrae CUÁNDO (relativo "en N minutos" o absoluto "a las
    H:MM") y QUÉ recordar. Devuelve un Recordatorio o None.
  - ReminderStore: lista thread-safe de recordatorios pendientes. El monitor de
    voice_pipeline llama a vencidos() cada segundo y dispara los que tocan.

Sin dependencias nuevas (datetime + re + threading, stdlib). En memoria: los
recordatorios NO sobreviven a un reinicio (suficiente para la feria; persistirlos
en SQLite es un paso futuro).
"""

from __future__ import annotations

import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional

import rutas

# ── Gatillos de creación ────────────────────────────────────────────────────────
_TRIGGERS = ("recuérdame", "recuerdame", "recuérda me", "avísame", "avisame",
             "recordatorio", "despiértame", "despiertame", "temporizador",
             "alarma", "pon una alarma", "pon un temporizador")

# Números escritos → valor (los comunes en habla). "media"/"medio" se manejan
# aparte (son fracción, no multiplicador: "media hora" = 30 min, no 30 horas).
_NUM_PALABRA = {
    "un": 1, "una": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
    "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "quince": 15,
    "veinte": 20, "treinta": 30, "cuarenta": 40, "sesenta": 60,
}

# "en/dentro de N <unidad> [y media|y cuarto]" — relativo. Acepta dígitos, número
# escrito o "medio/media" (fracción). El sufijo "y media"/"y cuarto" suma 0.5/0.25.
_RE_REL = re.compile(
    r'\b(?:en|dentro de)\s+'
    r'(medio|media|\d+|' + '|'.join(_NUM_PALABRA) + r')\s*'
    r'(segundos?|seg|minutos?|min|horas?|hora)'
    r'(?:\s+y\s+(media|cuarto))?', re.IGNORECASE)

# "a las H[:MM]" / "a la una". Minutos por dígitos o "y media"/"y cuarto".
# Franja opcional (de la mañana/tarde/noche, am/pm).
_RE_ABS = re.compile(
    r'\ba\s+la(?:s)?\s+(\d{1,2}|una)(?:[:y\s]+(\d{1,2}|media|cuarto))?\s*'
    r'(de la mañana|de la tarde|de la noche|a\.?m\.?|p\.?m\.?|hrs?|horas)?',
    re.IGNORECASE)

# Conectores a recortar al inicio del "qué".
_RE_LEAD = re.compile(r'^(que|de|a|para|el|la|los|las|mi|tu)\s+', re.IGNORECASE)


@dataclass
class Recordatorio:
    due_ts: float        # epoch (time.time()) en que vence
    que: str             # qué recordar (texto hablable)
    cuando_str: str      # descripción legible del cuándo (para el ack)


def _valor_num(tok: str) -> int:
    tok = tok.lower()
    if tok.isdigit():
        return int(tok)
    return _NUM_PALABRA.get(tok, 0)


def _segundos_unidad(unidad: str) -> int:
    u = unidad.lower()
    if u.startswith("seg"):
        return 1
    if u.startswith("min"):
        return 60
    return 3600   # hora(s)


def _humano_relativo(secs: int) -> str:
    """Describe un lapso en la unidad más limpia ('en 30 minutos')."""
    if secs < 60:
        return f"en {secs} segundo{'s' if secs != 1 else ''}"
    if secs % 3600 == 0:
        h = secs // 3600
        return f"en {h} hora{'s' if h != 1 else ''}"
    mins = int(round(secs / 60))
    return f"en {mins} minuto{'s' if mins != 1 else ''}"


def _limpiar_que(texto: str, spans: list) -> str:
    """Quita los gatillos y los tramos de tiempo; deja el 'qué'."""
    t = texto
    # Borrar los tramos de tiempo casados (de derecha a izquierda por los índices).
    for a, b in sorted(spans, reverse=True):
        t = t[:a] + " " + t[b:]
    low = t.lower()
    for trg in _TRIGGERS:
        low_idx = low.find(trg)
        if low_idx != -1:
            t = t[:low_idx] + " " + t[low_idx + len(trg):]
            low = t.lower()
    t = re.sub(r'\s+', ' ', t).strip(" ,.¡!¿?")
    # Recortar conectores iniciales repetidos ("que de" → "")
    while True:
        nuevo = _RE_LEAD.sub('', t)
        if nuevo == t:
            break
        t = nuevo
    return t.strip(" ,.¡!¿?")


def parse_recordatorio(texto: str, ahora: Optional[datetime] = None) -> Optional[Recordatorio]:
    """
    Si `texto` pide crear un recordatorio, devuelve un Recordatorio; si no, None.
    Requiere un gatillo (recuérdame/avísame/alarma/…) Y una expresión de tiempo.
    """
    low = texto.lower()
    if not any(trg in low for trg in _TRIGGERS):
        return None
    ahora = ahora or datetime.now()

    spans = []
    due_ts = None
    cuando_str = ""

    m = _RE_REL.search(texto)
    if m:
        tok = m.group(1).lower()
        unit_secs = _segundos_unidad(m.group(2))
        if tok in ("medio", "media"):
            base = 0.5
        else:
            n = _valor_num(tok)
            if n <= 0:
                return None
            base = float(n)
        extra = (m.group(3) or "").lower()        # "media" / "cuarto" / ""
        if extra == "media":
            base += 0.5
        elif extra == "cuarto":
            base += 0.25
        secs = int(round(base * unit_secs))
        if secs <= 0:
            return None
        due_ts = time.mktime(ahora.timetuple()) + secs
        cuando_str = _humano_relativo(secs)
        spans.append(m.span())
    else:
        m = _RE_ABS.search(texto)
        if not m:
            return None
        h_tok = m.group(1).lower()
        h = 1 if h_tok == "una" else int(h_tok)
        mtok = (m.group(2) or "").lower()
        if mtok == "media":
            mnt = 30
        elif mtok == "cuarto":
            mnt = 15
        elif mtok.isdigit():
            mnt = int(mtok)
        else:
            mnt = 0
        franja = (m.group(3) or "").lower()
        if h > 23 or mnt > 59:
            return None
        if ("tarde" in franja or "noche" in franja or franja.startswith("p")) and h < 12:
            h += 12
        if "mañana" in franja and h == 12:
            h = 0
        # "mañana" como día (no la franja "de la mañana") → fuerza el día siguiente.
        low = texto.lower()
        forzar_manana = bool(re.search(r'\bmañana\b', low)) and "de la mañana" not in low
        objetivo = ahora.replace(hour=h, minute=mnt, second=0, microsecond=0)
        if forzar_manana:
            objetivo += timedelta(days=1)
        elif objetivo <= ahora:
            objetivo += timedelta(days=1)   # ya pasó hoy → mañana
        due_ts = time.mktime(objetivo.timetuple())
        cuando_str = f"a las {objetivo.strftime('%H:%M')}"
        spans.append(m.span())

    que = _limpiar_que(texto, spans) or "tu recordatorio"
    return Recordatorio(due_ts=due_ts, que=que, cuando_str=cuando_str)


class ReminderStore:
    """Recordatorios pendientes, guardados en SQLite (reminders.db) para que
    sobrevivan a un reinicio de Bob. ruta=':memory:' para pruebas.

    Un vencido queda marcado como entregado al devolverlo. Si venció con Bob
    apagado, sigue pendiente y se avisa al arrancar.
    """

    def __init__(self, ruta: str = rutas.DB_RECORDATORIOS) -> None:
        self._lock = threading.Lock()
        self._con = sqlite3.connect(ruta, check_same_thread=False)
        self._con.execute(
            'CREATE TABLE IF NOT EXISTS recordatorios ('
            ' id INTEGER PRIMARY KEY,'
            ' due_ts REAL NOT NULL,'
            ' que TEXT NOT NULL,'
            ' cuando_str TEXT NOT NULL,'
            ' entregado INTEGER NOT NULL DEFAULT 0)')
        self._con.commit()

    def agregar(self, r: Recordatorio) -> None:
        with self._lock:
            self._con.execute(
                'INSERT INTO recordatorios (due_ts, que, cuando_str) VALUES (?, ?, ?)',
                (r.due_ts, r.que, r.cuando_str))
            self._con.commit()

    def vencidos(self, now_ts: Optional[float] = None) -> List[Recordatorio]:
        """Devuelve y MARCA como entregados los recordatorios cuyo due_ts ya pasó."""
        now_ts = now_ts if now_ts is not None else time.time()
        with self._lock:
            filas = self._con.execute(
                'SELECT id, due_ts, que, cuando_str FROM recordatorios'
                ' WHERE entregado = 0 AND due_ts <= ? ORDER BY due_ts',
                (now_ts,)).fetchall()
            if filas:
                self._con.executemany(
                    'UPDATE recordatorios SET entregado = 1 WHERE id = ?',
                    [(f[0],) for f in filas])
                self._con.commit()
        return [Recordatorio(due_ts=f[1], que=f[2], cuando_str=f[3]) for f in filas]

    def hay_vencidos(self, now_ts: Optional[float] = None) -> bool:
        """Como vencidos() pero sin marcarlos."""
        now_ts = now_ts if now_ts is not None else time.time()
        with self._lock:
            fila = self._con.execute(
                'SELECT 1 FROM recordatorios WHERE entregado = 0 AND due_ts <= ? LIMIT 1',
                (now_ts,)).fetchone()
        return fila is not None

    def pendientes(self) -> int:
        with self._lock:
            return self._con.execute(
                'SELECT COUNT(*) FROM recordatorios WHERE entregado = 0').fetchone()[0]

    def cerrar(self) -> None:
        with self._lock:
            self._con.close()
