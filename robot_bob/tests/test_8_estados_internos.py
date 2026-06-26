"""
test_8_estados_internos.py — Prueba aislada de P3 (estados internos).

Valida la deriva y los sesgos de energía/motivación/curiosidad/sociabilidad en
la StateMachine, sin hardware (serial dummy, sin cámara ni voz). Manipula los
timestamps internos para simular el paso del tiempo de forma determinista.

Ejecutar:
    cd robot_bob
    python tests/test_8_estados_internos.py
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from state_machine import StateMachine, RobotState


class DummySerial:
    """Serial no-op: la StateMachine solo necesita que existan estos métodos."""
    def cmd_estado(self, *_a, **_k): pass
    def cmd_servo(self, *_a, **_k): pass
    def cmd_siguiendo(self, *_a, **_k): pass
    def cmd_motor(self, *_a, **_k): pass


def _avanzar(sm, seg):
    """Simula que pasaron `seg` segundos desde el último tick de estados internos."""
    sm._t_ei_tick -= seg
    sm.tick_estados_internos()


def main() -> int:
    sm = StateMachine(DummySerial())
    ok = True
    def check(nombre, cond):
        nonlocal ok
        ok = ok and cond
        print(f"  {'✓' if cond else '✗'} {nombre}")

    # ── 1. Energía: se gasta hablando ────────────────────────────────────────
    print("1) Energía baja hablando:")
    sm.iniciar_hablando()                 # estado = SPEAKING
    e0 = sm.energia
    _avanzar(sm, 5.0)
    check(f"energía {e0:.3f} → {sm.energia:.3f} (baja)", sm.energia < e0)

    # ── 2. Energía: se recupera dormido ──────────────────────────────────────
    print("2) Energía sube dormida (IDLE + sueño):")
    sm._transicionar(RobotState.IDLE)
    sm._t_sin_presencia = time.monotonic() - 60.0   # forzar "dormido"
    sm.energia = 0.3
    e0 = sm.energia
    _avanzar(sm, 5.0)
    check(f"dormido={sm.is_asleep()} energía {e0:.3f} → {sm.energia:.3f} (sube)",
          sm.energia > e0)

    # ── 3. Cierre de charla buena: sube social/motiv, baja energía/curiosidad ─
    print("3) ei_evento_charla (5 turnos, mood +0.6):")
    sm.sociabilidad = sm.motivacion = sm.curiosidad = 0.5
    sm.energia = 0.8
    sm.ei_evento_charla(turnos=5, mood=0.6)
    check(f"sociabilidad sube → {sm.sociabilidad:.3f}", sm.sociabilidad > 0.5)
    check(f"motivación sube → {sm.motivacion:.3f}",     sm.motivacion > 0.5)
    check(f"curiosidad se sacia → {sm.curiosidad:.3f}", sm.curiosidad < 0.5)
    check(f"energía baja → {sm.energia:.3f}",           sm.energia < 0.8)

    # ── 4. factor_iniciativa en rango y monótono ─────────────────────────────
    print("4) factor_iniciativa:")
    sm.motivacion = sm.sociabilidad = sm.energia = 0.0
    f_bajo = sm.factor_iniciativa()
    sm.motivacion = sm.sociabilidad = sm.energia = 1.0
    f_alto = sm.factor_iniciativa()
    check(f"rango {f_bajo:.2f}..{f_alto:.2f} (bajo<alto, ≥0.2)",
          f_bajo < f_alto and f_bajo >= 0.2)

    # ── 5. Prompt menciona extremos, calla neutros ───────────────────────────
    print("5) estado_interno_prompt:")
    sm.energia, sm.motivacion, sm.curiosidad, sm.sociabilidad = 0.5, 0.5, 0.5, 0.5
    check("todo neutro → prompt vacío", sm.estado_interno_prompt() == '')
    sm.energia, sm.curiosidad = 0.1, 0.9
    p = sm.estado_interno_prompt()
    check(f"extremos → menciona cansado+curioso: {p[-70:]!r}",
          'cansado' in p and 'curioso' in p)

    # ── 6. Curiosidad sube al aparecer una cara (IDLE→PRESENCE) ───────────────
    print("6) Cara nueva sube curiosidad:")
    sm._transicionar(RobotState.IDLE)
    sm.curiosidad = 0.4
    c0 = sm.curiosidad
    sm.notificar_cara(True)               # IDLE → PRESENCE
    check(f"estado={sm.estado.value} curiosidad {c0:.3f} → {sm.curiosidad:.3f}",
          sm.curiosidad > c0 and sm.estado == RobotState.PRESENCE)

    print("\n" + "=" * 56)
    print("Veredicto: ✓ P3 OK" if ok else "Veredicto: ✗ revisar fallos arriba")
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
