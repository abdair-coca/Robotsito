"""
memory.py — Memoria persistente de Bob (SQLite).

Dos tablas:
  personas  — id, nombre, edad, gustos, temas, relacion, primera_vez, ultima_vez,
              embedding (BLOB del vector facial L2-normalizado de face_id).
  episodios — recuerdos por persona (texto + fecha).

Identidad por CARA: reconocer(embedding) compara el embedding contra los guardados
por similitud coseno (= producto punto, porque están L2-normalizados).

Thread-safe (lock + check_same_thread=False) porque lo usa el hilo de voz.
"""

import os
import sqlite3
import threading
import numpy as np
from datetime import datetime

_DB_DEFAULT = os.path.join(os.path.dirname(__file__), 'memory.db')

# Umbral de similitud coseno para considerar "misma persona" (ArcFace: misma
# cara ~0.45-0.7, distinta <0.3). Tuneable en config.MEMORIA_SIM_THRESHOLD.
try:
    from config import MEMORIA_SIM_THRESHOLD as _SIM_THR
except Exception:
    _SIM_THR = 0.40


class Memoria:
    def __init__(self, ruta: str = _DB_DEFAULT):
        self._lock = threading.Lock()
        self._con  = sqlite3.connect(ruta, check_same_thread=False)
        self._con.execute('''CREATE TABLE IF NOT EXISTS personas(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT, edad INTEGER, gustos TEXT, temas TEXT, relacion TEXT,
            primera_vez TEXT, ultima_vez TEXT, embedding BLOB)''')
        self._con.execute('''CREATE TABLE IF NOT EXISTS episodios(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            persona_id INTEGER, texto TEXT, fecha TEXT)''')
        self._con.commit()

    @staticmethod
    def _ahora() -> str:
        return datetime.now().strftime('%Y-%m-%d %H:%M')

    # ── Reconocimiento ──────────────────────────────────────────────────────
    def reconocer(self, embedding, umbral: float = _SIM_THR):
        """Devuelve (id, nombre, score) de la persona más parecida ≥ umbral, o None."""
        if embedding is None:
            return None
        with self._lock:
            filas = self._con.execute('SELECT id, nombre, embedding FROM personas').fetchall()
        mejor = None
        for pid, nombre, blob in filas:
            if not blob:
                continue
            emb = np.frombuffer(blob, dtype=np.float32)
            score = float(np.dot(embedding, emb))   # coseno (ambos normalizados)
            if mejor is None or score > mejor[2]:
                mejor = (pid, nombre, score)
        if mejor and mejor[2] >= umbral:
            return mejor
        return None

    # ── Altas / actualizaciones ─────────────────────────────────────────────
    def registrar(self, nombre, embedding, edad=None) -> int:
        blob = embedding.tobytes() if embedding is not None else None
        with self._lock:
            cur = self._con.execute(
                'INSERT INTO personas(nombre, edad, primera_vez, ultima_vez, embedding) '
                'VALUES(?,?,?,?,?)',
                (nombre, edad, self._ahora(), self._ahora(), blob))
            self._con.commit()
            return cur.lastrowid

    def marcar_visto(self, pid: int) -> None:
        with self._lock:
            self._con.execute('UPDATE personas SET ultima_vez=? WHERE id=?',
                              (self._ahora(), pid))
            self._con.commit()

    def actualizar(self, pid: int, **campos) -> None:
        permitidos = {'nombre', 'edad', 'gustos', 'temas', 'relacion'}
        campos = {k: v for k, v in campos.items() if k in permitidos and v is not None}
        if not campos:
            return
        sets = ', '.join(f'{k}=?' for k in campos)
        with self._lock:
            self._con.execute(f'UPDATE personas SET {sets} WHERE id=?',
                              (*campos.values(), pid))
            self._con.commit()

    def actualizar_embedding(self, pid: int, embedding) -> None:
        if embedding is None:
            return
        with self._lock:
            self._con.execute('UPDATE personas SET embedding=? WHERE id=?',
                              (embedding.tobytes(), pid))
            self._con.commit()

    def agregar_episodio(self, pid: int, texto: str) -> None:
        if not texto:
            return
        with self._lock:
            self._con.execute('INSERT INTO episodios(persona_id, texto, fecha) VALUES(?,?,?)',
                              (pid, texto, self._ahora()))
            self._con.commit()

    # ── Lecturas ────────────────────────────────────────────────────────────
    def persona(self, pid: int):
        with self._lock:
            return self._con.execute(
                'SELECT id,nombre,edad,gustos,temas,relacion,primera_vez,ultima_vez '
                'FROM personas WHERE id=?', (pid,)).fetchone()

    def episodios(self, pid: int, limite: int = 5):
        with self._lock:
            return self._con.execute(
                'SELECT texto, fecha FROM episodios WHERE persona_id=? '
                'ORDER BY id DESC LIMIT ?', (pid, limite)).fetchall()

    def total_personas(self) -> int:
        with self._lock:
            return self._con.execute('SELECT COUNT(*) FROM personas').fetchone()[0]

    def contexto(self, pid: int) -> str:
        """Bloque de texto con lo que Bob sabe de la persona (para el system prompt)."""
        p = self.persona(pid)
        if not p:
            return ''
        _, nombre, edad, gustos, temas, relacion, primera, ultima = p
        l = [f'Estás hablando con {nombre or "alguien que ya conociste antes"}.']
        if edad:     l.append(f'Edad aproximada: {edad}.')
        if gustos:   l.append(f'Le gusta: {gustos}.')
        if temas:    l.append(f'Temas favoritos: {temas}.')
        if relacion: l.append(f'Su relación contigo: {relacion}.')
        if ultima:   l.append(f'La última vez que hablaron fue: {ultima}.')
        eps = self.episodios(pid, 3)
        if eps:
            l.append('Recuerdos de charlas anteriores:')
            for texto, fecha in eps:
                l.append(f'- ({fecha}) {texto}')
        return '\n'.join(l)

    def cerrar(self) -> None:
        with self._lock:
            try:
                self._con.close()
            except Exception:
                pass
