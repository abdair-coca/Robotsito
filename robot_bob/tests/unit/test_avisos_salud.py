"""
test_avisos_salud.py — Avisos del vigilante (nucleo/avisos_salud.py), S3 y S4 de la Fase S.

Sin hardware ni voz (decir y libre son de prueba; la bitácora va a una carpeta temporal):
  1. Cada caída, vuelta, rendición y arreglo deja su línea en la bitácora.
  2. Bob dice la frase de la parte solo cuando está libre; la red no se dice.
  3. Se cae y vuelve antes de poder decirlo: no dice nada.
  4. La última noticia de una parte reemplaza a la vieja.
  5. Con el Vigilante: un arreglo que lanza queda en la bitácora.
  6. La barra del CLI recibe las partes caídas (sin la red).
  7. "Bob, diagnostícate": el comando se reconoce, el resumen nombra lo caído y el
     pipeline revisa todo, pone la cara y lo dice (S4).

  python tests/unit/test_avisos_salud.py
"""
import json
import os
import sys
import tempfile
import threading

RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, RAIZ)

from nucleo import avisos_salud, estado_cli, salud   # noqa: E402
from nucleo.salud import Chequeo, Resultado, Vigilante   # noqa: E402

fallos = 0


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    global fallos
    fallos += 0 if ok else 1
    print(f"[{'OK' if ok else 'FALLA'}] {nombre} {detalle}", flush=True)


tmp = tempfile.mkdtemp()
log = os.path.join(tmp, 'logs', 'salud.jsonl')


def lineas():
    with open(log, encoding='utf-8') as f:
        return [json.loads(x) for x in f]


dichas = []
libre = [False]
av = avisos_salud.Avisador(decir=dichas.append, libre=lambda: libre[0], ruta_log=log,
                           reloj=lambda: 1000.0)
MEM = avisos_salud.FRASES['memoria']

# ── 1 y 2. Bitácora y voz cuando está libre ─────────────────────────────────────
av.al_cambiar('memoria', Resultado(False, 'unable to open database file'))
check('caída: línea en la bitácora',
      lineas() == [{'t': 1000.0, 'parte': 'memoria', 'evento': 'cayo',
                    'detalle': 'unable to open database file'}], f'{lineas()}')
check('ocupado: no dice nada todavía', not av.entregar() and dichas == [])
check('la frase espera', av.pendientes() == [MEM['cayo']])
libre[0] = True
check('libre: dice la caída', av.entregar() and dichas == [MEM['cayo']])
check('sin pendientes: no repite', not av.entregar() and len(dichas) == 1)
av.al_cambiar('memoria', Resultado(True))
av.entregar()
check('vuelve: lo dice', dichas[-1] == MEM['volvio'] and lineas()[-1]['evento'] == 'volvio')
av.al_cambiar('red', Resultado(False, 'sin internet'))
check('red: a la bitácora pero no por voz',
      lineas()[-1]['parte'] == 'red' and av.pendientes() == [])

# ── 3. Se cae y vuelve sin que Bob lo diga ──────────────────────────────────────
libre[0] = False
av.al_cambiar('hilo-wake-monitor', Resultado(False, 'no corre: wake-monitor'))
av.al_cambiar('hilo-wake-monitor', Resultado(True))
check('caída y vuelta sin decir: nada pendiente', av.pendientes() == [])
check('pero las dos quedan en la bitácora',
      [x['evento'] for x in lineas()[-2:]] == ['cayo', 'volvio'])

# ── 4. La última noticia reemplaza ──────────────────────────────────────────────
av.al_cambiar('audio', Resultado(False))
av.al_rendirse('audio', Resultado(False, 'connect: timed out'))
check('rendirse reemplaza la caída pendiente',
      av.pendientes() == [avisos_salud.FRASES['audio']['rota']])
check('la rendición queda en la bitácora', lineas()[-1]['evento'] == 'rota')
av.al_cambiar('audio', Resultado(True))
check('vuelve antes de decir la rendición: nada pendiente', av.pendientes() == [])
av.al_cambiar('ollama', Resultado(False))
check('parte sin frases: no se dice', av.pendientes() == [])

# ── 5. Con el Vigilante ─────────────────────────────────────────────────────────
def _falla():
    raise OSError('sin respuesta')


v = Vigilante([Chequeo('memoria', lambda: Resultado(False, 'muda'), _falla)],
              al_cambiar=av.al_cambiar, al_rendirse=av.al_rendirse, al_intentar=av.al_intentar,
              lanzar=lambda n, tarea: tarea())
v.revisar_todo()
ultima = lineas()[-1]
check('arreglo que lanza: en la bitácora',
      ultima['evento'] == 'arreglo' and ultima['intento'] == 1 and ultima['lanzo'] is True
      and 'OSError' in ultima['detalle'], f'{ultima}')
v2 = Vigilante([Chequeo('memoria', lambda: Resultado(False), lambda: None)],
               al_intentar=av.al_intentar, lanzar=lambda n, tarea: tarea())
v2.revisar_todo()
check('arreglo que no lanza: lanzo False', lineas()[-1]['lanzo'] is False)
av_sin_log = avisos_salud.Avisador(ruta_log=None)
av_sin_log.al_cambiar('memoria', Resultado(False))
check('sin bitácora ni voz: no falla', av_sin_log.pendientes() == [])

# ── 6. Barra del CLI ────────────────────────────────────────────────────────────
class _SM:
    class estado:
        name = 'IDLE'
    musica_sonando = threading.Event()
    bailando = threading.Event()


class _Voz:
    microfono_activo = True


v3 = Vigilante([Chequeo('audio', lambda: Resultado(False)), Chequeo('red', lambda: Resultado(False)),
                Chequeo('serial', lambda: Resultado(True))])
v3.revisar_todo()
f = estado_cli.foto(_SM(), _Voz(), object(), v3)
check('barra: partes caídas sin la red', f.get('averias') == ['audio del DevKit'], f'{f.get("averias")}')
check('barra sin vigilante: sin el campo', 'averias' not in estado_cli.foto(_SM(), _Voz(), object()))

# ── 7. Diagnóstico (S4) ─────────────────────────────────────────────────────────
for frase in ('Bob, diagnostícate', 'bob diagnosticate', 'hazte un diagnóstico', 'revísate',
              'Bob, chequéate', 'haz un autodiagnóstico'):
    check(f'comando: "{frase}"', avisos_salud.es_comando_diagnostico(frase))
for frase in ('el diagnóstico del médico fue bueno', 'revisa la tarea', ''):
    check(f'no es comando: "{frase}"', not avisos_salud.es_comando_diagnostico(frase))
check('resumen: todo bien', avisos_salud.resumen({'serial': Resultado(True)})
      == avisos_salud.DIAGNOSTICO_BIEN)
r1 = avisos_salud.resumen({'serial': Resultado(False), 'red': Resultado(True)})
check('resumen: una parte, en singular y sin paréntesis',
      'no funciona cuerpo.' in r1 and '(' not in r1, r1)
r2 = avisos_salud.resumen({'audio': Resultado(False), 'red': Resultado(False),
                           'memoria': Resultado(True)})
check('resumen: varias, en plural y con "y"',
      'no funcionan audio del DevKit y internet.' in r2, r2)

from voz.pipeline import VoicePipeline   # noqa: E402


class _VozFalsa:
    def __init__(self, vigilante):
        self._vigilante, self.caras, self.dicho = vigilante, [], []
        self._serial = self

    def cmd_estado(self, emo):
        self.caras.append(emo)

    def _hablar(self, texto, emo=None):
        self.dicho.append((texto, emo))


pasadas = []
v4 = Vigilante([Chequeo('audio', lambda: pasadas.append(1) or Resultado(False))])
vf = _VozFalsa(v4)
VoicePipeline._diagnosticar(vf)
check('diagnosticar: revisa ahora', pasadas == [1])
check('diagnosticar: pone la cara y lo dice sin el tag',
      vf.caras == ['PENSANDO'] and vf.dicho and vf.dicho[0][1] == 'PENSANDO'
      and vf.dicho[0][0].startswith('Me revisé: no funciona audio del DevKit'), f'{vf.dicho}')
vf2 = _VozFalsa(None)
VoicePipeline._diagnosticar(vf2)
check('diagnosticar sin vigilante: no dice nada', vf2.dicho == [])

print(f"\n{'TODO OK' if fallos == 0 else f'{fallos} FALLA(S)'}")
sys.exit(1 if fallos else 0)
