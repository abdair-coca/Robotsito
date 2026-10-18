"""
busqueda.py — Cliente de la búsqueda web del modo ingeniero (Fase S, S0).

Serper es una API HTTP de búsqueda de Google: un POST con la consulta en JSON y la
clave en el header X-API-KEY. Sin dependencias: urllib (stdlib).

buscar() devuelve:
  · lista de resultados {titulo, url, snippet, dominio, fecha, posicion}  (puede ser [])
  · None si no se pudo buscar (sin clave, sin red, error HTTP, respuesta inválida)
La diferencia importa: "no encontré nada" y "no pude buscar" se dicen distinto en voz.

La clave sale de robot_bob/.env (SERPER_API_KEY). config.py carga ese archivo en el
entorno; la clave nunca se imprime.
"""

import json
import os
import re
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional
from urllib.parse import urlparse

import config  # noqa: F401  (carga robot_bob/.env en el entorno)

SERPER_URL = 'https://google.serper.dev/search'
SERPER_NOTICIAS_URL = 'https://google.serper.dev/news'
BUSQUEDA_TIMEOUT_S = 3.0
GL, HL = 'bo', 'es'          # región y idioma de los resultados


def _dominio(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith('www.') else host


def buscar(consulta: str, n: int = 10, noticias: bool = False,
           timeout: float = BUSQUEDA_TIMEOUT_S,
           clave: Optional[str] = None) -> Optional[List[dict]]:
    """Resultados de Serper para `consulta`, o None si no se pudo buscar."""
    clave = clave or os.environ.get('SERPER_API_KEY', '')
    if not clave:
        print('[busqueda] sin SERPER_API_KEY en .env')
        return None
    url = SERPER_NOTICIAS_URL if noticias else SERPER_URL
    cuerpo = json.dumps({'q': consulta, 'gl': GL, 'hl': HL, 'num': n}).encode('utf-8')
    peticion = urllib.request.Request(url, data=cuerpo, method='POST', headers={
        'X-API-KEY': clave, 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(peticion, timeout=timeout) as r:
            datos = json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:          # 401 clave mala, 429 cuota agotada...
        print(f'[busqueda] HTTP {e.code} en "{consulta}"')
        return None
    except (urllib.error.URLError, OSError, ValueError) as e:   # red, timeout, JSON roto
        print(f'[busqueda] falló "{consulta}": {e}')
        return None

    items = datos.get('news' if noticias else 'organic') or []
    resultados = []
    for it in items:
        link = (it.get('link') or '').strip()
        if not link:
            continue
        resultados.append({
            'titulo': (it.get('title') or '').strip(),
            'url': link,
            'snippet': (it.get('snippet') or '').strip(),
            'dominio': _dominio(link),
            'fecha': it.get('date') or '',
            'posicion': it.get('position') or len(resultados) + 1,
        })
    return resultados


# ── S2. Búsqueda en paralelo y ranking (sin LLM) ──────────────────────────────────
MAX_FUENTES = 8
MAX_POR_DOMINIO = 2


def _horas(fecha: str) -> Optional[float]:
    """'hace 3 horas' → 3.0, 'hace 2 días' → 48.0. None si no es una fecha relativa."""
    m = re.search(r'hace\s+(\d+)\s+(\w+)', fecha or '', re.IGNORECASE)
    if not m:
        return None
    cantidad, unidad = int(m.group(1)), m.group(2).lower()
    for prefijo, horas in (('minut', 1 / 60), ('hora', 1), ('d', 24), ('seman', 168),
                           ('mes', 720), ('a', 8760)):
        if unidad.startswith(prefijo):
            return cantidad * horas
    return None


def _clave_orden(r: dict):
    """Noticias: las más recientes primero; sin fecha, al final. Después, la posición."""
    h = _horas(r.get('fecha', ''))
    return (h is None, h or 0.0, r.get('posicion', 99))


def _clave_url(url: str) -> str:
    """Igual nota aunque cambie www, la barra final o el fragmento."""
    p = urlparse(url.lower())
    host = p.netloc[4:] if p.netloc.startswith('www.') else p.netloc
    return host + p.path.rstrip('/') + (f'?{p.query}' if p.query else '')


def rankear(listas: List[List[dict]], maximo: int = MAX_FUENTES) -> List[dict]:
    """Une los resultados de varias consultas: quita URLs repetidas, máximo
    MAX_POR_DOMINIO por dominio, y devuelve como mucho `maximo` fuentes."""
    vistos, por_dominio, aceptados = set(), {}, []
    for r in sorted((r for lista in listas for r in lista), key=_clave_orden):
        clave = _clave_url(r['url'])
        if clave in vistos or por_dominio.get(r['dominio'], 0) >= MAX_POR_DOMINIO:
            continue
        vistos.add(clave)
        por_dominio[r['dominio']] = por_dominio.get(r['dominio'], 0) + 1
        aceptados.append(r)
        if len(aceptados) == maximo:
            break
    return aceptados


def buscar_todas(consultas: List[str], noticias: bool = False,
                 timeout: float = BUSQUEDA_TIMEOUT_S) -> Optional[List[dict]]:
    """Una búsqueda por consulta, en paralelo, y el ranking de todo lo que llegó.
    Lista (puede ser []), o None si ninguna consulta pudo buscarse."""
    if not consultas:
        return []
    with ThreadPoolExecutor(max_workers=len(consultas)) as pool:
        listas = list(pool.map(lambda q: buscar(q, noticias=noticias, timeout=timeout),
                               consultas))
    if all(lista is None for lista in listas):
        return None
    return rankear([lista for lista in listas if lista])


# ── S1. Descompositor: pregunta → 0 a 4 consultas (0 = no hace falta buscar) ───────
DESCOMPONER_MODELO = 'qwen/qwen3.8-27b'     # medido: 20/20 aciertos; gpt-oss-20b, 18/20
MAX_CONSULTAS = 4
DESCOMPONER_PROMPT = (
    'Eres el planificador de búsqueda de Bob, un robot. Decide si la pregunta necesita '
    'información de internet actual o verificable. Responde SOLO con JSON: '
    '{"buscar": true o false, "consultas": ["...", "..."]}. '
    'buscar = false para conceptos generales, matemáticas, charla, opiniones y cosas que no '
    'cambian; entonces consultas = []. buscar = true para noticias, hechos recientes, precios, '
    'lanzamientos, cargos o eventos actuales; entonces da de 1 a 4 consultas cortas, en '
    'español, cada una buscable en Google.')


def _razonamiento(modelo: str) -> dict:
    """Mismo criterio que voice_pipeline: que el modelo no gaste tokens razonando."""
    if modelo.startswith('openai/gpt-oss-'):
        return {'reasoning_effort': 'low'}
    if modelo.startswith('qwen/qwen3'):
        return {'reasoning_effort': 'none'}
    return {}


def _json_de(texto: Optional[str]):
    """El objeto JSON del texto del modelo (acepta bloques ```json), o None."""
    t = (texto or '').strip()
    if t.startswith('```'):
        t = t.strip('`').removeprefix('json').strip()
    i, j = t.find('{'), t.rfind('}')
    if i < 0 or j < i:
        return None
    try:
        return json.loads(t[i:j + 1])
    except ValueError:
        return None


def descomponer(pregunta: str, cliente=None, modelo: str = DESCOMPONER_MODELO,
                timeout: float = 3.0) -> Optional[dict]:
    """{'buscar': bool, 'consultas': [...]} o None si el modelo falló o no devolvió
    un JSON válido. Con buscar=True y sin consultas, la respuesta es inválida (None)."""
    if cliente is None:
        from groq import Groq
        cliente = Groq(api_key=config.GROQ_API_KEY, timeout=timeout)
    try:
        r = cliente.chat.completions.create(
            model=modelo, temperature=0, max_tokens=300,
            messages=[{'role': 'system', 'content': DESCOMPONER_PROMPT},
                      {'role': 'user', 'content': pregunta}],
            **_razonamiento(modelo))
    except Exception as e:
        print(f'[busqueda] descomposición falló ({modelo}): {e}')
        return None
    datos = _json_de(r.choices[0].message.content)
    if not isinstance(datos, dict):
        return None
    if datos.get('buscar') is not True:
        return {'buscar': False, 'consultas': []}
    consultas = [str(c).strip() for c in (datos.get('consultas') or []) if str(c).strip()]
    if not consultas:
        return None
    return {'buscar': True, 'consultas': consultas[:MAX_CONSULTAS]}


if __name__ == '__main__':
    import sys
    consulta = ' '.join(sys.argv[1:]) or 'noticias de tecnología hoy'
    res = buscar(consulta, n=5)
    if res is None:
        print('falló la búsqueda')
    else:
        print(f'{len(res)} resultados para "{consulta}"')
        for r in res:
            print(f"{r['posicion']}. {r['titulo']} | {r['dominio']} | {r['fecha']}")
