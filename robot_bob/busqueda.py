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
from datetime import datetime
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


_MESES_ES = {'ene': 1, 'feb': 2, 'mar': 3, 'abr': 4, 'may': 5, 'jun': 6, 'jul': 7,
             'ago': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dic': 12}
_RE_FECHA_ES = re.compile(r'(\d{1,2})\s+([a-z]{3})\w*\.?\s+(?:de\s+)?(\d{4})', re.IGNORECASE)
_RE_FECHA_EN = re.compile(r'([a-z]{3})\w*\.?\s+(\d{1,2}),?\s+(\d{4})', re.IGNORECASE)


def _horas(fecha: str, ahora: Optional[datetime] = None) -> Optional[float]:
    """Horas desde la publicación. 'hace 3 horas' → 3.0; '15 jul de 2026' o
    'Oct 17, 2026' → horas desde esa fecha. None si no hay fecha reconocible."""
    fecha = fecha or ''
    m = re.search(r'hace\s+(\d+)\s+(\w+)', fecha, re.IGNORECASE)
    if m:
        cantidad, unidad = int(m.group(1)), m.group(2).lower()
        for prefijo, horas in (('minut', 1 / 60), ('hora', 1), ('d', 24), ('seman', 168),
                               ('mes', 720), ('a', 8760)):
            if unidad.startswith(prefijo):
                return cantidad * horas
        return None
    m = _RE_FECHA_ES.search(fecha)
    if m and m.group(2).lower() in _MESES_ES:
        dia, mes, anio = int(m.group(1)), _MESES_ES[m.group(2).lower()], int(m.group(3))
    else:
        m = _RE_FECHA_EN.search(fecha)
        if not m or m.group(1).lower() not in _MESES_ES:
            return None
        mes, dia, anio = _MESES_ES[m.group(1).lower()], int(m.group(2)), int(m.group(3))
    try:
        publicado = datetime(anio, mes, dia)
    except ValueError:
        return None
    return max(0.0, ((ahora or datetime.now()) - publicado).total_seconds() / 3600)


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
    'cambian; entonces consultas = []. Ante la duda, busca: una búsqueda de más cuesta poco, '
    'y responder de memoria algo que pudo cambiar sale mal. buscar = true para noticias, '
    'hechos recientes, precios, '
    'lanzamientos, cargos o eventos actuales; entonces da de 1 a 4 consultas cortas, en '
    'español, cada una buscable en Google. No agregues años ni fechas que la pregunta no '
    'mencione.')


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
        cliente = _cliente_groq(timeout)
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


# ── S3. Respuesta con citas, en streaming (frases para la voz) ─────────────────────
RESPUESTA_TEMPERATURA = 0.3
RESPUESTA_MODELO = getattr(config, 'GROQ_LLM_MODEL', 'openai/gpt-oss-20b')
SISTEMA_FUENTES = (
    'Eres Bob, un robot que responde en voz alta en español. Responde SOLO con lo que dicen '
    'las fuentes numeradas que te doy. Cita cada dato con su número entre corchetes, por '
    'ejemplo [2]. Cada frase repite SOLO lo que dice la fuente que cita: si la fuente dice '
    '"en análisis" o "propuesta", no digas "aprobó"; si no lo dice, no lo agregues. No '
    'inventes datos, cifras ni nombres. Si las fuentes no alcanzan, dilo en una frase. '
    'Máximo 3 frases cortas, sin listas, sin markdown, sin URLs ni nombres de fuentes largos.')
_RE_CITA = re.compile(r'\[\s*(\d+(?:\s*,\s*\d+)*)\s*\]')
_RE_URL_VOZ = re.compile(r'https?://\S+')
# Solo corta si la frase siguiente empieza con mayúscula o signo de apertura: así
# "BrainChip Holdings Ltd. ha presentado…" no se parte en "Ltd." (abreviatura).
_RE_FIN_FRASE = re.compile(r'(?<=[.!?…])\s+(?=[A-ZÁÉÍÓÚÑ¿¡"“«\[])')


def _cliente_groq(timeout: float = 3.0):
    from groq import Groq
    return Groq(api_key=config.GROQ_API_KEY, timeout=timeout)


def _prompt_fuentes(pregunta: str, fuentes: List[dict]):
    bloques = []
    for i, f in enumerate(fuentes, 1):
        meta = ', '.join(x for x in (f['dominio'], f.get('fecha', '')) if x)
        bloques.append(f'[{i}] {f["titulo"]} ({meta})\n    {f["snippet"]}')
    return SISTEMA_FUENTES, 'Fuentes:\n' + '\n'.join(bloques) + f'\n\nPregunta: {pregunta}'


def _limpiar_frase(frase: str, n_fuentes: int, usadas: set) -> str:
    """Quita las citas del texto (no se leen en voz alta) y guarda las que existen.
    Un [n] fuera de rango se quita y no cuenta como cita."""
    def cita(m):
        for k in re.findall(r'\d+', m.group(1)):
            if 1 <= int(k) <= n_fuentes:
                usadas.add(int(k))
        return ''
    texto = _RE_CITA.sub(cita, frase)
    texto = re.sub(r'[\[\]]', '', texto)              # corchetes sueltos
    texto = _RE_URL_VOZ.sub('', texto)
    texto = re.sub(r'[*#_`>]+', '', texto)
    texto = re.sub(r'\s+([.,;:!?])', r'\1', texto)    # "dato ." → "dato."
    return ' '.join(texto.split())


def responder_con_fuentes(pregunta: str, fuentes: List[dict], cliente=None,
                          modelo: str = RESPUESTA_MODELO,
                          registro: Optional[dict] = None):
    """Frases para la voz, en streaming, redactadas solo con `fuentes` y citadas [n].
    Al terminar, registro['usadas'] tiene las fuentes citadas y registro['error'] el
    motivo si el stream se cortó. Sin frases, el llamador usa su respaldo."""
    registro = registro if registro is not None else {}
    usadas = registro['usadas'] = set()
    if cliente is None:
        cliente = _cliente_groq()
    sistema, usuario = _prompt_fuentes(pregunta, fuentes)
    buffer = ''
    try:
        stream = cliente.chat.completions.create(
            model=modelo, temperature=RESPUESTA_TEMPERATURA, max_tokens=400, stream=True,
            messages=[{'role': 'system', 'content': sistema},
                      {'role': 'user', 'content': usuario}],
            **_razonamiento(modelo))
        for chunk in stream:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if not delta:
                continue
            buffer += delta
            *completas, buffer = _RE_FIN_FRASE.split(buffer)
            for frase in completas:
                limpia = _limpiar_frase(frase, len(fuentes), usadas)
                if limpia:
                    yield limpia
        if buffer.strip():
            limpia = _limpiar_frase(buffer, len(fuentes), usadas)
            if limpia:
                yield limpia
    except Exception as e:
        registro['error'] = str(e)
        print(f'[busqueda] respuesta cortada ({modelo}): {e}')
    print(f'[busqueda] fuentes citadas: {sorted(usadas)} de {len(fuentes)}')


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
