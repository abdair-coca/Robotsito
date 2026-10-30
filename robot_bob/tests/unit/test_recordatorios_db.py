"""
test_recordatorios_db.py — Recordatorios que sobreviven a un reinicio (reminders.db).

Qué prueba (sin hardware, sin LLM, sin red):
  1. Los pendientes siguen ahí al reabrir la base (reinicio de Bob).
  2. Un vencido se entrega una sola vez: al reabrir ya no vuelve a salir.
  3. Un recordatorio que venció con Bob apagado sigue pendiente y hay_vencidos() lo ve.
  4. vencidos() devuelve los vencidos en orden de vencimiento y deja los futuros.
  5. hay_vencidos() no marca nada como entregado.

Ejecutar:
  cd robot_bob
  python tests/unit/test_recordatorios_db.py
"""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from asistente import reminders as R


def main() -> None:
    print('═' * 60)
    print('  TEST Recordatorios en SQLite (reinicio de Bob)')
    print('═' * 60)
    fallos = 0
    carpeta = tempfile.mkdtemp(prefix='recordatorios_')
    ruta = os.path.join(carpeta, 'reminders.db')
    ahora = 1_000_000.0  # tiempo fijo: el test no depende del reloj

    try:
        # 1. Reinicio con pendientes
        s1 = R.ReminderStore(ruta)
        s1.agregar(R.Recordatorio(ahora + 60, 'tomar agua', 'en 1 minuto'))
        s1.agregar(R.Recordatorio(ahora + 600, 'la reunión', 'en 10 minutos'))
        s1.cerrar()
        s2 = R.ReminderStore(ruta)
        if s2.pendientes() != 2:
            print(f'  [FALLO] tras reabrir hay {s2.pendientes()} pendientes (esperado 2)'); fallos += 1
        else:
            print('  [OK] los 2 pendientes sobreviven al reinicio')

        # 2. Un vencido se entrega una sola vez
        venc = s2.vencidos(ahora + 100)
        s2.cerrar()
        s3 = R.ReminderStore(ruta)
        if [v.que for v in venc] != ['tomar agua'] or s3.vencidos(ahora + 100):
            print(f'  [FALLO] entrega: {[v.que for v in venc]} (esperado solo "tomar agua", y nada al repetir)')
            fallos += 1
        else:
            print('  [OK] el vencido se entrega una vez; al reabrir no vuelve a salir')

        # 3. Venció con Bob apagado: sigue pendiente y se ve al arrancar
        s3.agregar(R.Recordatorio(ahora + 50, 'llamar', 'en 50 segundos'))
        s3.agregar(R.Recordatorio(ahora + 99_999, 'futuro', 'dentro de días'))
        s3.cerrar()
        s4 = R.ReminderStore(ruta)
        dos_horas = ahora + 7200
        if not s4.hay_vencidos(dos_horas):
            print('  [FALLO] hay_vencidos no ve lo que venció con Bob apagado'); fallos += 1
        else:
            print('  [OK] al arrancar, hay_vencidos ve lo que venció con Bob apagado')

        # 4. Orden de vencimiento; el futuro se queda pendiente
        venc = s4.vencidos(dos_horas)
        orden = [v.que for v in venc]
        if orden != ['llamar', 'la reunión'] or s4.pendientes() != 1:
            print(f'  [FALLO] vencidos={orden} pendientes={s4.pendientes()} '
                  f'(esperado ["llamar", "la reunión"] y 1 pendiente)')
            fallos += 1
        else:
            print('  [OK] vencidos en orden de vencimiento; el futuro sigue pendiente')

        # 5. hay_vencidos no marca nada
        s4.agregar(R.Recordatorio(ahora - 1, 'ya', 'antes'))
        s4.hay_vencidos(ahora)
        s4.hay_vencidos(ahora)
        if s4.pendientes() != 2:
            print(f'  [FALLO] hay_vencidos marcó algo: pendientes={s4.pendientes()} (esperado 2)'); fallos += 1
        else:
            print('  [OK] hay_vencidos no marca ni quita nada')
        s4.cerrar()
    finally:
        shutil.rmtree(carpeta, ignore_errors=True)

    print('\n' + '═' * 60)
    print(f'  RESULTADO: {"TODO OK" if fallos == 0 else f"{fallos} FALLO(S)"}')
    print('═' * 60)
    sys.exit(1 if fallos else 0)


if __name__ == '__main__':
    main()
