"""
test_llm_qwen.py — Prueba aislada del LLM local (Ollama) para Bob.

Mide si qwen (u otro modelo Ollama) sirve como cerebro de charla de Bob, SIN
arrancar el robot. Usa TEMPERATURE / MAX_TOKENS REALES de config.py.

Por defecto evalúa el modelo RAW contra SYSTEM_PROMPT_LOCAL (el prompt corto y
estricto que usa el backend local), así medimos cuánto arregla el prompt B por
sí solo. En producción, el guard de voice_pipeline._stream_llm corrige además
lo que el modelo se salte (tope de 2 frases + tag por frase).

Mide lo que importa para voz en tiempo real:
  - latencia al primer token (TTFT) — cuánto tarda en empezar a hablar
  - tokens/seg — fluidez del streaming
  - cobertura de tags: ¿cada frase trae un [EMO:X] válido?
  - regla dura: máximo 2 frases por turno
  - ausencia de markdown/asteriscos/emojis (rompen el TTS)

Uso:
    cd robot_bob
    python tests/test_llm_qwen.py                 # prompt local, OLLAMA_MODEL de config
    python tests/test_llm_qwen.py --model qwen2.5:3b
    python tests/test_llm_qwen.py --turns 3       # menos turnos
    python tests/test_llm_qwen.py --full-prompt   # comparar con el SYSTEM_PROMPT largo
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from config import (
    SYSTEM_PROMPT, SYSTEM_PROMPT_LOCAL, TEMPERATURE, MAX_TOKENS,
    OLLAMA_BASE_URL, OLLAMA_MODEL,
)

try:
    from openai import OpenAI
except Exception:
    print("Falta el paquete openai. En el venv: pip install openai")
    sys.exit(1)

# Tags válidos — espejo de _VALID_EMOS en voice_pipeline.py + los de P3.
VALID_EMOS = {
    'FELIZ', 'MUY_FELIZ', 'CURIOSO', 'TRAVIESO', 'PENSANDO', 'SORPRENDIDO',
    'CONFUNDIDO', 'TRISTE', 'MUY_TRISTE', 'AMOR', 'HABLANDO',
    'ORGULLOSO', 'ENOJADO', 'SOSPECHANDO',
}
EMO_RE   = re.compile(r'\[EMO:([A-ZÁÉÍÓÚÜÑ_]+)\]', re.IGNORECASE)
SENT_RE  = re.compile(r'[.!?¡¿…\n]+')
BAD_RE   = re.compile(r'[*_#`]|:\)|:\(|[\U0001F300-\U0001FAFF]')  # markdown/emojis

# Turnos representativos de la feria: presentación, identidad, chiste, tema
# sensible (debe redirigir con humor), y despedida.
TURNS = [
    "Hola, me llamo Carlos y soy de ingeniería de sistemas.",
    "¿Tú qué eres exactamente?",
    "Cuéntame un chiste.",
    "¿Por quién hay que votar en las elecciones?",
    "Bueno Bob, me tengo que ir, chau.",
]


def analizar(texto: str) -> dict:
    """Evalúa una respuesta contra las reglas duras de Bob."""
    frases = [s for s in SENT_RE.split(texto) if s.strip()]
    tags   = [t.upper() for t in EMO_RE.findall(texto)]
    return {
        'n_frases'      : len(frases),
        'tags'          : tags,
        'tags_validos'  : all(t in VALID_EMOS for t in tags) if tags else False,
        'cobertura_tags': len(tags) >= max(1, len(frases)),  # ~1 tag por frase
        'cumple_2frases': len(frases) <= 2,
        'tiene_basura'  : bool(BAD_RE.search(EMO_RE.sub('', texto))),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--model', default=OLLAMA_MODEL, help='modelo Ollama')
    ap.add_argument('--turns', type=int, default=len(TURNS), help='nº de turnos a probar')
    ap.add_argument('--full-prompt', action='store_true',
                    help='usar el SYSTEM_PROMPT largo en vez del local estricto')
    args = ap.parse_args()

    system_prompt = SYSTEM_PROMPT if args.full_prompt else SYSTEM_PROMPT_LOCAL
    etiqueta_prompt = 'SYSTEM_PROMPT (largo)' if args.full_prompt else 'SYSTEM_PROMPT_LOCAL (estricto)'

    print(f"→ Ollama: {OLLAMA_BASE_URL}")
    print(f"→ Modelo: {args.model}")
    print(f"→ Prompt: {etiqueta_prompt}  ({len(system_prompt)} chars)")
    print(f"→ TEMPERATURE={TEMPERATURE}  MAX_TOKENS={MAX_TOKENS}\n")

    client = OpenAI(base_url=OLLAMA_BASE_URL, api_key="ollama")
    try:
        t0 = time.monotonic()
        client.models.list()
        print(f"✓ Ollama responde ({(time.monotonic()-t0)*1000:.0f} ms)\n")
    except Exception as e:
        print(f"✗ Ollama no responde: {e}")
        print("  ¿Está corriendo? Abre otra terminal y corre:  ollama serve")
        print(f"  ¿El modelo está bajado?  ollama pull {args.model}")
        return 1

    convo = []
    ttfts, rates = [], []
    fallos = []

    for i, user in enumerate(TURNS[:args.turns], 1):
        convo.append({'role': 'user', 'content': user})
        messages = [{'role': 'system', 'content': system_prompt}] + convo
        print(f"[{i}] Usuario: {user}")

        t_start = time.monotonic()
        t_first = None
        full = ''
        n_tok = 0
        try:
            stream = client.chat.completions.create(
                model=args.model, messages=messages,
                temperature=TEMPERATURE, max_tokens=MAX_TOKENS, stream=True,
            )
            for chunk in stream:
                delta = (chunk.choices[0].delta.content or '') if chunk.choices else ''
                if delta and t_first is None:
                    t_first = time.monotonic()
                if delta:
                    full += delta
                    n_tok += 1
        except Exception as e:
            print(f"    ✗ error en la generación: {e}\n")
            fallos.append(f"turno {i}: {e}")
            continue

        t_end = time.monotonic()
        ttft  = (t_first - t_start) if t_first else (t_end - t_start)
        gen_s = max(t_end - (t_first or t_start), 1e-6)
        rate  = n_tok / gen_s
        ttfts.append(ttft)
        rates.append(rate)

        convo.append({'role': 'assistant', 'content': full})
        a = analizar(full)

        print(f"    Bob: {full.strip()}")
        marca = lambda ok: '✓' if ok else '✗'
        print(f"    {marca(a['cobertura_tags'] and a['tags_validos'])} tags={a['tags']}  "
              f"{marca(a['cumple_2frases'])} frases={a['n_frases']}  "
              f"{marca(not a['tiene_basura'])} sin-basura")
        print(f"    ⏱  TTFT={ttft*1000:.0f}ms  {rate:.1f} tok/s  ({n_tok} tok)\n")

        if not a['tags_validos']:
            fallos.append(f"turno {i}: tags inválidos {a['tags']}")
        elif not a['cobertura_tags']:
            fallos.append(f"turno {i}: faltan tags ({len(a['tags'])} tags / {a['n_frases']} frases)")
        if not a['cumple_2frases']:
            fallos.append(f"turno {i}: {a['n_frases']} frases (>2)")
        if a['tiene_basura']:
            fallos.append(f"turno {i}: markdown/emoji en el texto")

    # ── Veredicto ──────────────────────────────────────────────────────────
    print("=" * 56)
    if ttfts:
        # El turno 1 suele cargar el modelo en frío; reporto con y sin él.
        print(f"TTFT medio: {sum(ttfts)/len(ttfts)*1000:.0f} ms "
              f"(sin turno 1: {sum(ttfts[1:])/max(1,len(ttfts[1:]))*1000:.0f} ms)  "
              f"— bueno <800, usable <1500")
        print(f"Velocidad : {sum(rates)/len(rates):.1f} tok/s  (bueno >25, usable >12)")
    if fallos:
        print(f"\n⚠ {len(fallos)} problemas de formato/regla (modelo RAW, sin guard):")
        for f in fallos:
            print(f"  - {f}")
        print("\nNota: el guard de _stream_llm corta a 2 frases e inyecta tags en "
              "producción, así que estos fallos se corrigen al correr Bob. Aquí se mide "
              "qué tan bien obedece el modelo SOLO con el prompt.")
    else:
        print("\nVeredicto: ✓ el modelo respeta persona, tags EMO y reglas duras "
              "solo con el prompt. Apto para Bob si la latencia te sirve.")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
