"""
memory.py — Memoria persistente de Bob (SQLite).

Tres tablas:
  personas  — id, nombre, edad, gustos, temas, relacion, primera_vez, ultima_vez,
              embedding (BLOB del vector facial L2-normalizado de face_id).
  episodios — recuerdos por persona (texto + fecha).
  voces     — muestras de voz por persona (BLOB del vector de voice_id + fecha +
              origen 'pasivo'|'explicito'), hasta MAX_VOCES por persona.

Identidad por CARA: reconocer(embedding) compara el embedding contra los guardados
por similitud coseno (= producto punto, porque están L2-normalizados).
Identidad por VOZ: verificar_voz(pid, embedding) compara contra el promedio de las
muestras de esa persona (verificación de UNA persona, no búsqueda entre todas).

Thread-safe (lock + check_same_thread=False) porque lo usa el hilo de voz.
"""

import sqlite3
import threading
import numpy as np
from datetime import datetime

import rutas

_DB_DEFAULT = rutas.DB_MEMORIA

# Umbral de similitud coseno para considerar "misma persona" (ArcFace: misma
# cara ~0.45-0.7, distinta <0.3). Tuneable en config.MEMORIA_SIM_THRESHOLD.
try:
    from config import MEMORIA_SIM_THRESHOLD as _SIM_THR
except Exception:
    _SIM_THR = 0.40

# Tope de muestras de voz por persona. Al pasarlo se borra la más vieja de origen
# 'pasivo'; las 'explicito' (frases leídas a pedido) se conservan mientras haya
# pasivas que borrar.
MAX_VOCES = 40


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
        self._con.execute('''CREATE TABLE IF NOT EXISTS voces(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            persona_id INTEGER, embedding BLOB, fecha TEXT, origen TEXT)''')
        self._con.commit()
        self._migrar()

    def _migrar(self) -> None:
        """Agrega columnas nuevas a bases viejas (P2: relaciones sociales)."""
        cols = {r[1] for r in self._con.execute('PRAGMA table_info(personas)')}
        for col, ddl in (('amistad', 'INTEGER DEFAULT 0'),
                         ('confianza', 'INTEGER DEFAULT 0'),
                         ('interacciones', 'INTEGER DEFAULT 0')):
            if col not in cols:
                self._con.execute(f'ALTER TABLE personas ADD COLUMN {col} {ddl}')
        self._con.commit()

    @staticmethod
    def _ahora() -> str:
        return datetime.now().strftime('%Y-%m-%d %H:%M')

    # ── Reconocimiento ──────────────────────────────────────────────────────
    def reconocer(self, embedding, umbral: float = _SIM_THR):
        """Devuelve (id, nombre, score) de la persona más parecida ≥ umbral, o None."""
        mejor = self.mas_parecida(embedding)
        if mejor and mejor[2] >= umbral:
            return mejor
        return None

    def mas_parecida(self, embedding):
        """(id, nombre, score) de la persona más parecida SIN umbral, o None si no
        hay caras guardadas. Cuando no reconoce a alguien, dice cuánto faltó."""
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
        return mejor

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

    def registrar_interaccion(self, pid: int, d_amistad: int, d_confianza: int) -> None:
        """Suma una interacción y ajusta amistad/confianza (clamp 0..100)."""
        with self._lock:
            self._con.execute(
                'UPDATE personas SET interacciones = interacciones + 1, '
                'amistad   = MAX(0, MIN(100, amistad   + ?)), '
                'confianza = MAX(0, MIN(100, confianza + ?)) WHERE id=?',
                (int(d_amistad), int(d_confianza), pid))
            self._con.commit()

    def nivel_relacion(self, pid: int):
        """Devuelve (etiqueta, amistad, interacciones) para describir la relación."""
        with self._lock:
            r = self._con.execute('SELECT amistad, interacciones FROM personas WHERE id=?',
                                  (pid,)).fetchone()
        if not r:
            return ('desconocido', 0, 0)
        amistad, inter = (r[0] or 0), (r[1] or 0)
        if inter <= 0 or amistad < 10:
            nivel = 'alguien que recién conocés'
        elif amistad < 30:
            nivel = 'un conocido'
        elif amistad < 60:
            nivel = 'un amigo'
        else:
            nivel = 'un amigo cercano, de confianza'
        return (nivel, amistad, inter)

    def agregar_episodio(self, pid: int, texto: str) -> None:
        if not texto:
            return
        with self._lock:
            self._con.execute('INSERT INTO episodios(persona_id, texto, fecha) VALUES(?,?,?)',
                              (pid, texto, self._ahora()))
            self._con.commit()

    # ── Voz ─────────────────────────────────────────────────────────────────
    def agregar_voz(self, pid: int, embedding, origen: str = 'pasivo') -> None:
        """Guarda una muestra de voz; si pasa MAX_VOCES borra la pasiva más vieja
        (o la más vieja de todas si no quedan pasivas)."""
        if embedding is None:
            return
        with self._lock:
            self._con.execute(
                'INSERT INTO voces(persona_id, embedding, fecha, origen) VALUES(?,?,?,?)',
                (pid, np.asarray(embedding, np.float32).tobytes(), self._ahora(), origen))
            n = self._con.execute('SELECT COUNT(*) FROM voces WHERE persona_id=?',
                                  (pid,)).fetchone()[0]
            if n > MAX_VOCES:
                self._con.execute(
                    'DELETE FROM voces WHERE id IN (SELECT id FROM voces WHERE persona_id=? '
                    "ORDER BY (origen='pasivo') DESC, id ASC LIMIT ?)",
                    (pid, n - MAX_VOCES))
            self._con.commit()

    def total_voces(self, pid: int) -> int:
        with self._lock:
            return self._con.execute('SELECT COUNT(*) FROM voces WHERE persona_id=?',
                                     (pid,)).fetchone()[0]

    def centroide_voz(self, pid: int):
        """Promedio L2-normalizado de las muestras de voz de la persona, o None."""
        with self._lock:
            filas = self._con.execute('SELECT embedding FROM voces WHERE persona_id=?',
                                      (pid,)).fetchall()
        if not filas:
            return None
        c = np.mean([np.frombuffer(b, dtype=np.float32) for (b,) in filas], axis=0)
        return (c / (np.linalg.norm(c) + 1e-9)).astype(np.float32)

    def verificar_voz(self, pid: int, embedding):
        """Similitud coseno de la frase contra el promedio de voz de la persona, o
        None si no hay muestras o no hay embedding. El umbral lo decide quien llama."""
        if embedding is None:
            return None
        c = self.centroide_voz(pid)
        return None if c is None else float(np.dot(embedding, c))

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
        nivel, amistad, inter = self.nivel_relacion(pid)
        l.append(f'Para vos es {nivel} (lo viste {inter} veces, amistad {amistad}/100).')
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
