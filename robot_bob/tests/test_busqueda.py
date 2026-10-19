"""
test_busqueda.py — Cliente de Serper (fase S0), sin red.

Respuestas simuladas: formato de la petición, parseo de resultados, y la diferencia entre
"no encontré nada" ([]) y "no pude buscar" (None).

  python tests/test_busqueda.py
"""
import json
import os
import sys
import time
import types
import urllib.error
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from asistente import busqueda  # noqa: E402

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


def _res(url: str, posicion: int, fecha: str = '') -> dict:
    return {'titulo': url, 'url': url, 'snippet': '', 'dominio': busqueda._dominio(url),
            'fecha': fecha, 'posicion': posicion}


def probar_paralelo_y_ranking() -> None:
    original = busqueda.buscar

    def lenta(q, **kw):
        time.sleep(1.0)
        return []
    busqueda.buscar = lenta
    try:
        t0 = time.monotonic()
        busqueda.buscar_todas(['a', 'b'])
        dt = time.monotonic() - t0
    finally:
        busqueda.buscar = original
    check('paralelo: dos consultas de 1 s tardan ~1 s, no 2 s', dt < 1.5, f'(dt={dt:.2f} s)')

    def mixta(q, **kw):
        return None if q == 'falla' else [_res('https://ok.example.com/x', 1)]
    busqueda.buscar = mixta
    try:
        res_parcial = busqueda.buscar_todas(['falla', 'bien'])
        res_nula = busqueda.buscar_todas(['falla'])
    finally:
        busqueda.buscar = original
    check('parcial: si una consulta falla, usa la otra', res_parcial and len(res_parcial) == 1)
    check('todas fallan: None', res_nula is None)

    listas = [[_res('https://www.diario.bo/nota/1/', 1), _res('https://diario.bo/nota/1', 2)],
              [_res('https://otro.com/a', 1)]]
    res = busqueda.rankear(listas)
    check('ranking: quita URLs repetidas (con o sin www y barra final)',
          [r['url'] for r in res] == ['https://www.diario.bo/nota/1/', 'https://otro.com/a'],
          f'(urls={[r["url"] for r in res]})')

    mismo = [_res(f'https://sitio.bo/{i}', i) for i in range(1, 5)]
    res = busqueda.rankear([mismo])
    check('ranking: máximo 2 fuentes por dominio', len(res) == 2, f'(n={len(res)})')

    muchas = [_res(f'https://d{i}.com/x', i) for i in range(1, 13)]
    check('ranking: como mucho 8 fuentes', len(busqueda.rankear([muchas])) == 8)

    por_posicion = [_res('https://c.com/3', 3), _res('https://a.com/1', 1), _res('https://b.com/2', 2)]
    check('ranking: orden por posición (sin fechas)',
          [r['posicion'] for r in busqueda.rankear([por_posicion])] == [1, 2, 3])

    noticias = [_res('https://n.com/sin', 1, ''), _res('https://n.com/dos', 2, 'hace 2 días'),
                _res('https://m.com/tres', 3, 'hace 3 horas')]
    orden = [r['url'] for r in busqueda.rankear([noticias])]
    check('ranking de noticias: más recientes primero, sin fecha al final',
          orden == ['https://m.com/tres', 'https://n.com/dos', 'https://n.com/sin'], f'({orden})')


def probar_fechas() -> None:
    check('fecha: "hace 11 horas" → 11',
          busqueda._horas('hace 11 horas') == 11)
    check('fecha: "hace 1 día" → 24', busqueda._horas('hace 1 día') == 24)
    check('fecha: "hace 2 semanas" → 336', busqueda._horas('hace 2 semanas') == 336)
    ahora = datetime(2026, 10, 18)
    check('fecha: "15 jul de 2026" (absoluta, español)',
          busqueda._horas('15 jul de 2026', ahora) == (ahora - datetime(2026, 7, 15)).total_seconds() / 3600)
    check('fecha: "Oct 17, 2026" (absoluta, inglés) → 24 h',
          busqueda._horas('Oct 17, 2026', ahora) == 24)
    check('fecha: texto sin fecha → None', busqueda._horas('ayer por la tarde') is None)
    absolutas = [_res('https://viejo.bo/x', 1, '15 jul de 2026'),
                 _res('https://nuevo.bo/y', 2, '10 oct de 2026')]
    orden = [r['url'] for r in busqueda.rankear([absolutas])]
    check('ranking: fechas absolutas, la más reciente primero',
          orden == ['https://nuevo.bo/y', 'https://viejo.bo/x'], f'({orden})')


class ClienteStreamFalso:
    """Imita chat.completions.create(stream=True): emite `chunks` y, si `error`, falla al final."""

    def __init__(self, chunks, error=None):
        self.chunks, self.error, self.llamadas = chunks, error, []
        self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.llamadas.append(kw)
        return self._emitir()

    def _emitir(self):
        for c in self.chunks:
            yield types.SimpleNamespace(choices=[types.SimpleNamespace(
                delta=types.SimpleNamespace(content=c))])
        if self.error:
            raise self.error


FUENTES = [
    {'titulo': 'Dólar sube', 'url': 'https://a.bo/1', 'snippet': 'Cotización oficial 6,96.',
     'dominio': 'a.bo', 'fecha': 'hace 2 horas', 'posicion': 1},
    {'titulo': 'Otra nota', 'url': 'https://b.bo/2', 'snippet': 'Dato dos.', 'dominio': 'b.bo',
     'fecha': '', 'posicion': 2},
    {'titulo': 'Tercera', 'url': 'https://c.bo/3', 'snippet': 'Dato tres.', 'dominio': 'c.bo',
     'fecha': '', 'posicion': 3},
]


def probar_respuesta_fuentes() -> None:
    chunks = ['El dólar', ' está en 6,96 [1].', ' Otro dato [9] tomado.', ' Y [2, 3] más', '.']
    cliente = ClienteStreamFalso(chunks)
    registro = {}
    frases = list(busqueda.responder_con_fuentes(
        '¿Cuál es el dólar?', FUENTES, cliente=cliente, modelo='openai/gpt-oss-20b',
        registro=registro))
    check('streaming: arma frases a partir de fragmentos y las limpia',
          frases == ['El dólar está en 6,96.', 'Otro dato tomado.', 'Y más.'], f'({frases})')
    check('citas: guarda las fuentes citadas; el [9] fuera de rango no cuenta',
          registro['usadas'] == {1, 2, 3}, f'(usadas={registro["usadas"]})')
    check('voz: ninguna frase contiene corchetes ni URLs',
          not any(('[' in f or ']' in f or 'http' in f) for f in frases))
    usuario = cliente.llamadas[0]['messages'][1]['content']
    check('prompt: fuentes numeradas y la pregunta',
          '[1] Dólar sube (a.bo, hace 2 horas)' in usuario and 'Pregunta: ¿Cuál es el dólar?'
          in usuario, f'({usuario[:80]!r}...)')
    kw = cliente.llamadas[0]
    check('petición: stream=True, modelo y razonamiento bajo para gpt-oss',
          kw['stream'] is True and kw['model'] == 'openai/gpt-oss-20b'
          and kw['reasoning_effort'] == 'low')

    abreviada = ClienteStreamFalso(['BrainChip Holdings Ltd. ha presentado', ' su núcleo. Otra',
                                    ' frase.'])
    frases = list(busqueda.responder_con_fuentes('p', FUENTES, cliente=abreviada,
                                                 modelo='openai/gpt-oss-20b'))
    check('frases: no corta en una abreviatura ("Ltd.") seguida de minúscula',
          frases == ['BrainChip Holdings Ltd. ha presentado su núcleo.', 'Otra frase.'],
          f'({frases})')

    cortado = ClienteStreamFalso(['Primera frase.', ' Segunda'], error=RuntimeError('stream roto'))
    registro = {}
    frases = list(busqueda.responder_con_fuentes('p', FUENTES, cliente=cortado,
                                                 modelo='openai/gpt-oss-20b', registro=registro))
    check('stream cortado: conserva lo ya dicho y registra el error',
          frases == ['Primera frase.'] and 'stream roto' in registro.get('error', ''),
          f'(frases={frases}, error={registro.get("error")})')


def main() -> int:
    probar_sin_clave()
    probar_resultados()
    probar_peticion()
    probar_noticias()
    probar_fallos()
    probar_sin_resultados()
    probar_descompositor()
    probar_paralelo_y_ranking()
    probar_fechas()
    probar_respuesta_fuentes()
    print(f"\n{'OK' if fallos == 0 else f'{fallos} FALLA(S)'}", flush=True)
    return 1 if fallos else 0


if __name__ == '__main__':
    sys.exit(main())
