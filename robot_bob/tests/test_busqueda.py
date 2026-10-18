"""
test_busqueda.py — Cliente de Serper (fase S0), sin red.

Respuestas simuladas: formato de la petición, parseo de resultados, y la diferencia entre
"no encontré nada" ([]) y "no pude buscar" (None).

  python tests/test_busqueda.py
"""
import json
import os
import sys
import types
import urllib.error

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import busqueda  # noqa: E402

fallos = 0


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    global fallos
    fallos += 0 if ok else 1
    print(f"[{'OK' if ok else 'FALLA'}] {nombre} {detalle}", flush=True)


class RespuestaFalsa:
    def __init__(self, datos: dict):
        self._bytes = json.dumps(datos).encode('utf-8') if isinstance(datos, dict) else datos

    def read(self):
        return self._bytes

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def con_red_falsa(respuesta, capturas: list):
    """Reemplaza urlopen: guarda la petición y devuelve `respuesta` o la lanza."""
    def urlopen(peticion, timeout=None):
        capturas.append({'peticion': peticion, 'timeout': timeout})
        if isinstance(respuesta, BaseException):
            raise respuesta
        return RespuestaFalsa(respuesta)
    return urlopen


def probar(respuesta, **kwargs):
    capturas = []
    original = busqueda.urllib.request.urlopen
    busqueda.urllib.request.urlopen = con_red_falsa(respuesta, capturas)
    try:
        res = busqueda.buscar('consulta de prueba', clave='CLAVE_FALSA', **kwargs)
    finally:
        busqueda.urllib.request.urlopen = original
    return res, capturas


ORGANIC = {'organic': [
    {'title': 'Primera', 'link': 'https://www.ejemplo.com/a', 'snippet': 'Texto A', 'position': 1,
     'date': 'Oct 17, 2026'},
    {'title': 'Sin link', 'snippet': 'se descarta', 'position': 2},
    {'title': 'Segunda', 'link': 'https://noticias.dominio.bo/b', 'snippet': 'Texto B',
     'position': 3},
]}


def probar_sin_clave() -> None:
    original = os.environ.pop('SERPER_API_KEY', None)
    capturas = []
    orig_urlopen = busqueda.urllib.request.urlopen
    busqueda.urllib.request.urlopen = con_red_falsa(ORGANIC, capturas)
    try:
        res = busqueda.buscar('x')
    finally:
        busqueda.urllib.request.urlopen = orig_urlopen
        if original is not None:
            os.environ['SERPER_API_KEY'] = original
    check('sin clave: devuelve None sin llamar a la red', res is None and not capturas)


def probar_resultados() -> None:
    res, capturas = probar(ORGANIC)
    check('organic: descarta el resultado sin link', res is not None and len(res) == 2,
          f'(n={None if res is None else len(res)})')
    check('organic: campos del primer resultado',
          res[0]['titulo'] == 'Primera' and res[0]['snippet'] == 'Texto A'
          and res[0]['fecha'] == 'Oct 17, 2026' and res[0]['posicion'] == 1)
    check('organic: dominio sin www', res[0]['dominio'] == 'ejemplo.com',
          f'(dominio={res[0]["dominio"]!r})')
    check('organic: dominio de subdominio conservado', res[1]['dominio'] == 'noticias.dominio.bo')


def probar_peticion() -> None:
    _, capturas = probar(ORGANIC)
    p = capturas[0]['peticion']
    cuerpo = json.loads(p.data.decode('utf-8'))
    check('petición: POST a google.serper.dev/search',
          p.get_method() == 'POST' and p.full_url == busqueda.SERPER_URL)
    check('petición: clave en X-API-KEY', p.get_header('X-api-key') == 'CLAVE_FALSA')
    check('petición: JSON con q, gl, hl, num',
          cuerpo == {'q': 'consulta de prueba', 'gl': 'bo', 'hl': 'es', 'num': 10},
          f'(cuerpo={cuerpo})')
    check('petición: timeout de 3 s', capturas[0]['timeout'] == busqueda.BUSQUEDA_TIMEOUT_S)


def probar_noticias() -> None:
    news = {'news': [{'title': 'Nota', 'link': 'https://medio.bo/n', 'snippet': 's',
                      'date': '2 weeks ago', 'source': 'Medio', 'position': 1}]}
    res, capturas = probar(news, noticias=True)
    check('noticias: usa el endpoint /news y lee el campo news',
          capturas[0]['peticion'].full_url == busqueda.SERPER_NOTICIAS_URL
          and res and res[0]['titulo'] == 'Nota')


def probar_fallos() -> None:
    res, _ = probar(urllib.error.HTTPError('u', 401, 'Unauthorized', None, None))
    check('HTTP 401 (clave mala): None', res is None)
    res, _ = probar(urllib.error.HTTPError('u', 429, 'Too Many', None, None))
    check('HTTP 429 (cuota agotada): None', res is None)
    res, _ = probar(urllib.error.URLError('timed out'))
    check('red caída o timeout: None', res is None)
    res, _ = probar(b'no es json')
    check('respuesta que no es JSON: None', res is None)


def probar_sin_resultados() -> None:
    res, _ = probar({'organic': []})
    check('sin resultados: lista vacía, no None', res == [], f'(res={res!r})')


class ClienteGroqFalso:
    """Imita chat.completions.create de Groq: devuelve `contenido` o lanza `error`."""

    def __init__(self, contenido=None, error=None):
        self.contenido, self.error, self.llamadas = contenido, error, []
        self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.llamadas.append(kw)
        if self.error:
            raise self.error
        msg = types.SimpleNamespace(content=self.contenido)
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])


def descomponer_con(contenido=None, error=None, modelo=None):
    cliente = ClienteGroqFalso(contenido, error)
    kwargs = {'cliente': cliente}
    if modelo:
        kwargs['modelo'] = modelo
    return busqueda.descomponer('pregunta de prueba', **kwargs), cliente


def probar_descompositor() -> None:
    res, _ = descomponer_con('{"buscar": true, "consultas": ["dólar hoy Bolivia", "tipo de cambio"]}')
    check('descomponer: buscar=true con consultas', res == {'buscar': True,
          'consultas': ['dólar hoy Bolivia', 'tipo de cambio']}, f'(res={res})')
    res, _ = descomponer_con('{"buscar": false, "consultas": ["algo"]}')
    check('descomponer: buscar=false descarta las consultas', res == {'buscar': False,
          'consultas': []}, f'(res={res})')
    res, _ = descomponer_con('{"buscar": true, "consultas": ["a", "b", "c", "d", "e", "f"]}')
    check('descomponer: máximo 4 consultas', res and len(res['consultas']) == 4)
    res, _ = descomponer_con('{"buscar": true, "consultas": ["  ", "", "uno"]}')
    check('descomponer: descarta consultas vacías', res == {'buscar': True, 'consultas': ['uno']},
          f'(res={res})')
    res, _ = descomponer_con('{"buscar": true, "consultas": []}')
    check('descomponer: buscar=true sin consultas es inválido (None)', res is None)
    res, _ = descomponer_con('```json\n{"buscar": false, "consultas": []}\n```')
    check('descomponer: acepta bloque ```json', res == {'buscar': False, 'consultas': []})
    res, _ = descomponer_con('no sé, quizá sí')
    check('descomponer: texto sin JSON es None', res is None)
    res, _ = descomponer_con(error=RuntimeError('timeout'))
    check('descomponer: error del cliente es None', res is None)
    res, cliente = descomponer_con('{"buscar": false, "consultas": []}', modelo='qwen/qwen3.8-27b')
    kw = cliente.llamadas[0]
    check('descomponer: envía el modelo, el prompt y reasoning none para qwen',
          kw['model'] == 'qwen/qwen3.8-27b' and kw['reasoning_effort'] == 'none'
          and kw['messages'][0]['content'] == busqueda.DESCOMPONER_PROMPT)


def main() -> int:
    probar_sin_clave()
    probar_resultados()
    probar_peticion()
    probar_noticias()
    probar_fallos()
    probar_sin_resultados()
    probar_descompositor()
    print(f"\n{'OK' if fallos == 0 else f'{fallos} FALLA(S)'}", flush=True)
    return 1 if fallos else 0


if __name__ == '__main__':
    sys.exit(main())
