"""
test_7_opener_memoria.py — Prueba aislada de P7 (conversación autónoma sobre P1).

Valida VoicePipeline._opener_memoria SIN arrancar el pipeline (mic/cámara/serial):
arma una memoria temporal con una persona y un recuerdo, y comprueba que Bob
genera un saludo que RETOMA ese tema ("la última vez me hablabas de tu robot...").

Llama al LLM real (Ollama/Groq según config). Requiere Ollama corriendo si
LLM_BACKEND="ollama".

Ejecutar:
    cd robot_bob
    python tests/test_7_opener_memoria.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from openai import OpenAI
from config import LLM_BACKEND, OLLAMA_BASE_URL, OLLAMA_MODEL, GROQ_LLM_MODEL, GROQ_API_KEY
from memory import Memoria
from voice_pipeline import VoicePipeline


def _llm_de_config():
    """Mismo criterio que VoicePipeline: Ollama si está configurado, si no Groq."""
    if LLM_BACKEND == "ollama":
        return OpenAI(base_url=OLLAMA_BASE_URL, api_key="ollama"), OLLAMA_MODEL
    from groq import Groq
    return Groq(api_key=GROQ_API_KEY), GROQ_LLM_MODEL


def _stub(mem, pid, nombre, llm, model):
    """Objeto mínimo con lo que _opener_memoria toca de self."""
    return SimpleNamespace(
        _convo_persona=pid, _memoria=mem, _persona_nombre=nombre,
        _llm=llm, _llm_model=model)


def main() -> int:
    llm, model = _llm_de_config()
    print(f"→ LLM: {LLM_BACKEND} → {model}\n")

    db = os.path.join(tempfile.gettempdir(), 'bob_test_p7.db')
    if os.path.exists(db):
        os.remove(db)
    mem = Memoria(db)

    # ── Caso 1: persona conocida CON recuerdo → debe retomar el tema ──────────
    pid = mem.registrar('Carlos', None, 22)
    mem.registrar_interaccion(pid, 45, 30)   # amistad ~ amigo
    mem.actualizar(pid, temas='robótica y su proyecto de fin de carrera',
                   gustos='programar en Python')
    mem.agregar_episodio(pid, 'Carlos contó que está construyendo un robot '
                              'humanoide para la feria de la UATF')

    stub = _stub(mem, pid, 'Carlos', llm, model)
    res = VoicePipeline._opener_memoria(stub)
    print("CASO 1 — conocido con recuerdo (espera: retoma robot/proyecto):")
    if res is None:
        print("  ✗ devolvió None (debería haber generado un opener)\n")
        ok1 = False
    else:
        texto, emo = res
        print(f"  Bob ({emo}): {texto}")
        low = texto.lower()
        ok1 = any(k in low for k in ('robot', 'proyecto', 'feria', 'carrera', 'humanoide'))
        print(f"  {'✓' if ok1 else '⚠'} {'retoma el tema' if ok1 else 'no menciona el tema esperado'}\n")

    # ── Caso 2: persona conocida SIN recuerdos → debe devolver None ───────────
    pid2 = mem.registrar('Ana', None, None)
    stub2 = _stub(mem, pid2, 'Ana', llm, model)
    res2 = VoicePipeline._opener_memoria(stub2)
    print("CASO 2 — conocida sin recuerdos (espera: None → cae a saludo normal):")
    ok2 = res2 is None
    print(f"  {'✓' if ok2 else '✗'} devolvió {res2!r}\n")

    mem.cerrar()
    os.remove(db)

    print("=" * 56)
    if ok1 and ok2:
        print("Veredicto: ✓ P7 funcionando — Bob retoma temas y cae limpio sin recuerdos.")
        return 0
    print("Veredicto: ⚠ revisar — ver casos marcados arriba.")
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
