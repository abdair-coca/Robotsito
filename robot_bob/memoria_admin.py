"""
memoria_admin.py — Ver y editar la base de memoria de Bob (memory.db).

Uso (desde robot_bob/, con el venv):
  python memoria_admin.py                      lista todas las personas
  python memoria_admin.py show <id>            ficha + recuerdos de una persona
  python memoria_admin.py rename <id> <nombre> cambia el nombre
  python memoria_admin.py set <id> <campo> <valor>   campo: edad|gustos|temas|relacion
  python memoria_admin.py addep <id> <texto>   agrega un recuerdo a mano
  python memoria_admin.py del <id>             borra la persona y sus recuerdos
  python memoria_admin.py delep <id_episodio>  borra un recuerdo
  python memoria_admin.py clear                BORRA TODO (pide confirmar)

El embedding facial NO se muestra ni se edita (es el vector de la cara).
"""

import os
import sqlite3
import sys

_DB = os.path.join(os.path.dirname(__file__), 'memory.db')
_CAMPOS = {'edad', 'gustos', 'temas', 'relacion'}


def _con():
    if not os.path.exists(_DB):
        print(f'No existe la base: {_DB} (¿corriste main.py o el test alguna vez?)')
        sys.exit(1)
    return sqlite3.connect(_DB)


def listar(con):
    filas = con.execute(
        'SELECT id, nombre, edad, amistad, interacciones, ultima_vez, gustos '
        'FROM personas ORDER BY id').fetchall()
    if not filas:
        print('(base vacía)')
        return
    print(f'{"id":<4}{"nombre":<16}{"edad":<6}{"amist":<7}{"vistas":<8}{"última vez":<18}gustos')
    print('-' * 90)
    for pid, nom, edad, amistad, inter, ult, gus in filas:
        print(f'{pid:<4}{(nom or "(sin nombre)"):<16}{str(edad or "-"):<6}'
              f'{str(amistad or 0):<7}{str(inter or 0):<8}{(ult or "-"):<18}{(gus or "")[:30]}')
    print(f'\nTotal: {len(filas)} persona(s).')


def mostrar(con, pid):
    p = con.execute('SELECT id,nombre,edad,gustos,temas,relacion,primera_vez,ultima_vez,'
                    'amistad,confianza,interacciones FROM personas WHERE id=?',
                    (pid,)).fetchone()
    if not p:
        print(f'No existe persona id={pid}')
        return
    campos = ['id', 'nombre', 'edad', 'gustos', 'temas', 'relacion', 'primera_vez',
              'ultima_vez', 'amistad', 'confianza', 'interacciones']
    print('── Persona ──')
    for k, v in zip(campos, p):
        print(f'  {k:<12}: {v}')
    eps = con.execute('SELECT id, fecha, texto FROM episodios WHERE persona_id=? ORDER BY id',
                      (pid,)).fetchall()
    print(f'── Recuerdos ({len(eps)}) ──')
    for eid, fecha, texto in eps:
        print(f'  [{eid}] ({fecha}) {texto}')


def main():
    args = sys.argv[1:]
    con = _con()
    try:
        if not args or args[0] in ('list', 'ls'):
            listar(con)
        elif args[0] == 'show':
            mostrar(con, int(args[1]))
        elif args[0] == 'rename':
            con.execute('UPDATE personas SET nombre=? WHERE id=?', (args[2], int(args[1])))
            con.commit(); print('OK'); mostrar(con, int(args[1]))
        elif args[0] == 'set':
            campo = args[2]
            if campo not in _CAMPOS:
                print(f'Campo inválido. Usá uno de: {sorted(_CAMPOS)}'); return
            con.execute(f'UPDATE personas SET {campo}=? WHERE id=?', (args[3], int(args[1])))
            con.commit(); print('OK'); mostrar(con, int(args[1]))
        elif args[0] == 'addep':
            from datetime import datetime
            con.execute('INSERT INTO episodios(persona_id, texto, fecha) VALUES(?,?,?)',
                        (int(args[1]), args[2], datetime.now().strftime('%Y-%m-%d %H:%M')))
            con.commit(); print('OK'); mostrar(con, int(args[1]))
        elif args[0] == 'del':
            con.execute('DELETE FROM episodios WHERE persona_id=?', (int(args[1]),))
            con.execute('DELETE FROM personas WHERE id=?', (int(args[1]),))
            con.commit(); print(f'Borrada persona id={args[1]}')
        elif args[0] == 'delep':
            con.execute('DELETE FROM episodios WHERE id=?', (int(args[1]),))
            con.commit(); print(f'Borrado recuerdo id={args[1]}')
        elif args[0] == 'clear':
            if input('¿Borrar TODA la memoria? escribí SI: ').strip() == 'SI':
                con.execute('DELETE FROM episodios'); con.execute('DELETE FROM personas')
                con.commit(); print('Base vaciada.')
            else:
                print('Cancelado.')
        else:
            print(__doc__)
    finally:
        con.close()


if __name__ == '__main__':
    main()
