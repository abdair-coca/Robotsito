"""
fabrica/backlog.py — F1 de la fase F: elegir la siguiente tarea de .dev/PLAN.md.

Una tarea pendiente es una línea `- [ ] **ID. Título.** ...` con una línea
`  - Criterio: ...` debajo. Entran solo las tareas que cumplen F-D2: tienen criterio
y el criterio no pide un paso en vivo ni la confirmación del dueño.

Solo lee: no escribe ni cambia nada.
"""

import re
from dataclasses import dataclass
from typing import Iterable, List, Optional

_TAREA = re.compile(r'^- \[ \] \*\*(?P<id>[^.*]+?)\.\s*(?P<titulo>[^*]+?)\.?\*\*')
_CRITERIO = re.compile(r'^\s+- Criterio:\s*(?P<texto>.+)$')
# Un criterio con alguna de estas palabras no se puede verificar solo (F-D2).
_NO_AUTOMATICO = ('en vivo', 'confirmación', 'usuario')


@dataclass
class Tarea:
    id: str
    titulo: str
    criterio: str
    detalle: str = ''    # lo que sigue al título en la línea de la tarea


def leer(texto: str) -> List[Tarea]:
    """Tareas pendientes ([ ]) que tienen criterio, en el orden del plan."""
    tareas: List[Tarea] = []
    actual: Optional[Tarea] = None
    for linea in texto.splitlines():
        m = _TAREA.match(linea)
        if m:
            actual = Tarea(m.group('id').strip(), m.group('titulo').strip(), '',
                           linea[m.end():].strip())
            tareas.append(actual)
            continue
        if linea.startswith('#') or linea.startswith('- '):
            actual = None  # otro título o viñeta: ya no es de la tarea
            continue
        c = _CRITERIO.match(linea)
        if c and actual is not None and not actual.criterio:
            actual.criterio = c.group('texto').strip()
    return [t for t in tareas if t.criterio]


def automatica(tarea: Tarea) -> bool:
    """True si la tarea se puede verificar sin el dueño ni hardware en vivo."""
    criterio = tarea.criterio.lower()
    return not any(palabra in criterio for palabra in _NO_AUTOMATICO)


def siguiente(texto: str, saltar: Iterable[str] = ()) -> Optional[Tarea]:
    """La primera tarea pendiente y automática, en el orden del plan. saltar: ids que no
    se eligen (la fábrica pasa las que ya tienen rama: ya se intentaron)."""
    saltar = {s.lower() for s in saltar}
    for tarea in leer(texto):
        if automatica(tarea) and tarea.id.lower() not in saltar:
            return tarea
    return None
