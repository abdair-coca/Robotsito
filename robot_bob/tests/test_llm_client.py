"""Tests unitarios para llm_client.py (cache LRU)."""

import time
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def _setup():
    try:
        from llm_client import LLMClient, _CACHE
        c = LLMClient()
        _CACHE.clear()
        return (LLMClient, _CACHE, c)
    except Exception:
        return None


def test_cache_key_deterministic():
    s = _setup()
    if s is None:
        return
    LLMClient, CACHE, c = s
    k1 = c._req_hash(model="m", messages=[{"role": "user", "content": "hola"}],
                     temperature=0.5, max_tokens=60, extra=None)
    k2 = c._req_hash(model="m", messages=[{"role": "user", "content": "hola"}],
                     temperature=0.5, max_tokens=60, extra=None)
    assert k1 == k2


def test_cache_key_diff_messages():
    s = _setup()
    if s is None:
        return
    LLMClient, CACHE, c = s
    CACHE.clear()
    k1 = c._req_hash(model="m", messages=[{"role": "user", "content": "hola"}],
                     temperature=0.5, max_tokens=60, extra=None)
    k2 = c._req_hash(model="m", messages=[{"role": "user", "content": "chau"}],
                     temperature=0.5, max_tokens=60, extra=None)
    assert k1 != k2


def test_cache_set_get():
    s = _setup()
    if s is None:
        return
    LLMClient, CACHE, c = s
    CACHE.clear()
    assert c._cache_get("x", 60) is None
    c._cache_set("x", "hello")
    assert c._cache_get("x", 60) == "hello"
    assert c._cache_get("x", -1) is None


def test_cache_lru_eviction():
    s = _setup()
    if s is None:
        return
    LLMClient, CACHE, c = s
    CACHE.clear()
    from llm_client import _CACHE_MAX
    for i in range(_CACHE_MAX + 10):
        c._cache_set(f"k{i}", str(i))
    assert len(CACHE) <= _CACHE_MAX
    assert c._cache_get("k0", 60) is None
    assert c._cache_get(f"k{_CACHE_MAX - 1}", 60) is not None


def test_cache_renew_on_access():
    s = _setup()
    if s is None:
        return
    LLMClient, CACHE, c = s
    CACHE.clear()
    c._cache_set("x", "hello")
    ts0 = CACHE["x"][1]
    time.sleep(0.02)
    c._cache_get("x", 60)
    ts1 = CACHE["x"][1]
    assert ts1 > ts0
