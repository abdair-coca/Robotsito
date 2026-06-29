"""
test_9_asistente.py — Prueba aislada del P9 (asistente: hora/fecha/clima/noticias).

Qué prueba (sin hardware, sin LLM):
  1. Detección de intención (hora/fecha/clima/noticias) y NO-disparo en charla normal.
  2. Formato de hora/fecha en español con una fecha fija.
  3. contexto_asistente() inyecta solo lo pedido.
  4. (best-effort, red) obtener_clima() de la ciudad configurada.
  4b. (best-effort, red) obtener_noticias() del RSS de Google News.

Ejecutar:
  cd robot_bob
  python tests/test_9_asistente.py
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import assistant as A


def main() -> None:
    print('═' * 60)
    print('  TEST P9 — Asistente (hora / fecha / clima / noticias)')
    print('═' * 60)
    fallos = 0

    # 1. Intención
    casos = [
        ("¿qué hora es?",            True,  False, False),
        ("dime la hora",             True,  False, False),
        ("¿qué día es hoy?",         False, True,  False),
        ("¿en qué fecha estamos?",   False, True,  False),
        ("¿qué clima hace?",         False, False, True),
        ("¿cuántos grados hace?",    False, False, True),
        ("hola Bob, ¿cómo estás?",   False, False, False),
        ("cuéntame un chiste",       False, False, False),
    ]
    for texto, eh, ef, ec in casos:
        t = texto.lower()
        gh, gf, gc = A._quiere_hora(t), A._quiere_fecha(t), A._quiere_clima(t)
        ok = (gh, gf, gc) == (eh, ef, ec)
        print(f'  [{"OK" if ok else "FALLO"}] "{texto}" → hora={gh} fecha={gf} clima={gc}')
        if not ok:
            fallos += 1

    # 2. Formato hora/fecha con fecha fija (viernes 26 jun 2026, 14:35)
    fija = datetime(2026, 6, 26, 14, 35)
    h, f = A.hora_actual(fija), A.fecha_actual(fija)
    print(f'\n  hora_actual  → {h}')
    print(f'  fecha_actual → {f}')
    if h != "14:35":
        print('  [FALLO] hora esperada 14:35'); fallos += 1
    if f != "viernes 26 de junio de 2026":
        print('  [FALLO] fecha esperada "viernes 26 de junio de 2026"'); fallos += 1

    # 3. Inyección: solo lo pedido
    ctx = A.contexto_asistente("¿qué hora es?", fija)
    print(f'\n  contexto("¿qué hora es?"):\n{ctx}')
    if "Hora actual" not in ctx or "Fecha de hoy" in ctx:
        print('  [FALLO] debía inyectar solo la hora'); fallos += 1
    if A.contexto_asistente("hola, ¿cómo estás?", fija) != "":
        print('  [FALLO] charla normal NO debe inyectar'); fallos += 1

    # 4. Clima en vivo (best-effort: red puede fallar, no cuenta como fallo)
    print(f'\n  obtener_clima("{A.CLIMA_CIUDAD}") (red)...')
    clima = A.obtener_clima()
    print(f'  → {clima!r}  ' + ('(OK)' if clima else '(sin red / no disponible — no es fallo)'))

    # 4b. Noticias: intención + inyección + fetch en vivo (best-effort)
    print('\n  --- noticias ---')
    casos_n = [
        ("¿cuáles son las últimas noticias?", True),
        ("dame las noticias de hoy",          True),
        ("qué hay de nuevo en el mundo",      True),
        ("¿qué hora es?",                     False),
        ("cuéntame un chiste",                False),
    ]
    for texto, en in casos_n:
        gn = A._quiere_noticias(texto.lower())
        ok = gn == en
        print(f'  [{"OK" if ok else "FALLO"}] "{texto}" → noticias={gn}')
        if not ok:
            fallos += 1
    # Inyección: charla normal NO debe traer noticias
    if "Titulares" in A.contexto_asistente("hola, ¿cómo estás?", fija):
        print('  [FALLO] charla normal NO debe inyectar noticias'); fallos += 1
    # Fetch en vivo (red puede fallar → no es fallo)
    print(f'  obtener_noticias() (red)...')
    noticias = A.obtener_noticias()
    if noticias:
        print(f'  → {len(noticias)} titular(es) (OK):')
        for n in noticias:
            print(f'      • {n}')
    else:
        print('  → None (sin red / no disponible — no es fallo)')

    # 5. Recordatorios (parse + store)
    import reminders as R
    import time as _t
    print('\n  --- recordatorios ---')
    base = datetime(2026, 6, 26, 14, 0, 0)   # 14:00 fijo

    r1 = R.parse_recordatorio("recuérdame en 5 minutos que tome agua", base)
    print(f'  rel: {r1}')
    if not r1 or r1.que != "tome agua" or r1.cuando_str != "en 5 minutos":
        print('  [FALLO] relativo "en 5 minutos / tome agua"'); fallos += 1

    r2 = R.parse_recordatorio("despiértame a las 3 de la tarde", base)
    print(f'  abs: {r2}')
    if not r2 or r2.cuando_str != "a las 15:00":
        print('  [FALLO] absoluto "a las 3 de la tarde" → 15:00'); fallos += 1

    r3 = R.parse_recordatorio("a las 9 de la mañana avísame de la reunión", base)
    if not r3 or r3.cuando_str != "a las 09:00":
        print('  [FALLO] absoluto "a las 9 de la mañana" → 09:00'); fallos += 1

    if R.parse_recordatorio("cuéntame un chiste", base) is not None:
        print('  [FALLO] sin gatillo NO debe crear recordatorio'); fallos += 1
    if R.parse_recordatorio("recuérdame algo", base) is not None:
        print('  [FALLO] gatillo SIN tiempo NO debe crear recordatorio'); fallos += 1

    # Afinado: "media hora" (bug: antes daba 30 horas), "y media", "dentro de", mañana
    rh = R.parse_recordatorio("recuérdame en media hora que estire", base)
    print(f'  media hora: {rh.cuando_str if rh else None}')
    if not rh or rh.cuando_str != "en 30 minutos":
        print('  [FALLO] "en media hora" → "en 30 minutos"'); fallos += 1

    rhm = R.parse_recordatorio("avísame en una hora y media", base)
    if not rhm or rhm.cuando_str != "en 90 minutos":
        print('  [FALLO] "en una hora y media" → "en 90 minutos"'); fallos += 1

    rd = R.parse_recordatorio("recuérdame dentro de 10 minutos tomar agua", base)
    if not rd or rd.cuando_str != "en 10 minutos":
        print('  [FALLO] "dentro de 10 minutos"'); fallos += 1

    rmed = R.parse_recordatorio("despiértame a las 3 y media de la tarde", base)
    print(f'  3 y media tarde: {rmed.cuando_str if rmed else None}')
    if not rmed or rmed.cuando_str != "a las 15:30":
        print('  [FALLO] "a las 3 y media de la tarde" → 15:30'); fallos += 1

    # "mañana a las 9" desde las 14:00 → día siguiente 09:00 (no hoy, ya pasó)
    rman = R.parse_recordatorio("recuérdame mañana a las 9 la reunión", base)
    if not rman:
        print('  [FALLO] "mañana a las 9" no parseó'); fallos += 1
    else:
        from datetime import datetime as _dt
        dia = _dt.fromtimestamp(rman.due_ts)
        if not (dia.day == 27 and dia.hour == 9):
            print(f'  [FALLO] "mañana a las 9" → {dia} (esperado día 27 09:00)'); fallos += 1
        else:
            print('  [OK] "mañana a las 9" → día siguiente 09:00')

    # Store: vence lo que toca, conserva lo futuro
    store = R.ReminderStore()
    ahora = _t.time()
    store.agregar(R.Recordatorio(ahora - 1, "ya", "antes"))
    store.agregar(R.Recordatorio(ahora + 999, "luego", "después"))
    venc = store.vencidos(ahora)
    if len(venc) != 1 or venc[0].que != "ya" or store.pendientes() != 1:
        print('  [FALLO] store.vencidos no separó vencido/futuro'); fallos += 1
    else:
        print('  [OK] store separa vencido (1) y deja pendiente (1)')

    print('\n' + '═' * 60)
    print(f'  RESULTADO: {"TODO OK" if fallos == 0 else f"{fallos} FALLO(S)"}')
    print('═' * 60)
    sys.exit(1 if fallos else 0)


if __name__ == '__main__':
    main()
