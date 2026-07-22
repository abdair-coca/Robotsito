"""Tests unitarios para memory.py (indice en RAM + SQLite)."""

import os
import tempfile
import time

import numpy as np
import pytest

from memory import Memoria


@pytest.fixture
def mem():
    db = os.path.join(tempfile.gettempdir(), 'test_memory_unit.db')
    try:
        os.remove(db)
    except FileNotFoundError:
        pass
    time.sleep(0.1)
    m = Memoria(db)
    yield m
    m.cerrar()
    time.sleep(0.1)
    try:
        os.remove(db)
    except FileNotFoundError:
        pass


def test_idx_vacia(mem):
    assert len(mem._idx) == 0
    assert mem.total_personas() == 0


def test_registrar_actualiza_idx(mem):
    emb = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
    pid = mem.registrar('Alice', emb, 25)
    assert pid == 1
    assert len(mem._idx) == 1
    assert pid in mem._idx
    assert np.allclose(mem._idx[pid], emb)


def test_reconocer_match(mem):
    emb = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
    pid = mem.registrar('Alice', emb)
    res = mem.reconocer(emb)
    assert res is not None
    assert res[0] == pid
    assert res[1] == 'Alice'
    assert res[2] >= 0.99


def test_reconocer_sin_match(mem):
    emb1 = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
    mem.registrar('Alice', emb1)
    emb2 = np.array([0.0, 1.0, 0.0, 0.0], dtype=np.float32)
    res = mem.reconocer(emb2)
    assert res is None


def test_reconocer_empty(mem):
    assert mem.reconocer(None) is None
    assert mem.reconocer(np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)) is None


def test_actualizar_embedding_sync(mem):
    emb1 = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
    pid = mem.registrar('Alice', emb1)
    emb2 = np.array([0.0, 1.0, 0.0, 0.0], dtype=np.float32)
    mem.actualizar_embedding(pid, emb2)
    assert np.allclose(mem._idx[pid], emb2)
    res = mem.reconocer(emb2)
    assert res is not None
    assert res[0] == pid


def test_reconstruir_idx_desde_db(mem):
    emb1 = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
    pid_a = mem.registrar('Alice', emb1)
    emb2 = np.array([0.0, 1.0, 0.0, 0.0], dtype=np.float32)
    pid_b = mem.registrar('Bob', emb2)
    mem._idx.clear()
    assert len(mem._idx) == 0
    mem._reconstruir_idx()
    assert len(mem._idx) == 2
    assert pid_a in mem._idx and pid_b in mem._idx
    assert np.allclose(mem._idx[pid_a], emb1)
    assert np.allclose(mem._idx[pid_b], emb2)
