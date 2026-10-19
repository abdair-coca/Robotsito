"""
test_agente_web.py — Agente web del modo dueño (V5d), sin red, navegador ni micrófono.

Detección de pedidos web, condiciones (flag, modo ingeniero, Claude Code), la
confirmación en voz (sí / no / silencio / voz ajena), el resultado dicho y
guardado como contexto del turno siguiente, y la configuración de la sesión
(Playwright MCP fijo, aislado, lista blanca y herramientas peligrosas ocultas).

  python tests/unit/test_agente_web.py
"""
import os
import sys
import threading
import types

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from voz import pipeline as vp   # noqa: E402
from agente import modo_dueno as md       # noqa: E402
from agente import claude_code            # noqa: E402

fallos = 0


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    global fallos
    fallos += 0 if ok else 1
    print(f"[{'OK' if ok else 'FALLA'}] {nombre} {detalle}", flush=True)


class FakeSM:
    mood = 0.0

    def __init__(self):
        self.ev_hablando = threading.Event()
        self.ev_escuchando = threading.Event()
        self.ev_pensando = threading.Event()
        self.bailando = threading.Event()

    def estado_interno_prompt(self):
        return ''

    def __getattr__(self, n):
        return lambda *a, **k: None


class FakeMgr:
    def __getattr__(self, n):
        return lambda *a, **k: None


class FakeMem:
    """Voz del dueño con sim fija; la del turno se cambia en .sim."""
    sim = 0.8

    def total_voces(self, pid):
        return 10

    def verificar_voz(self, pid, emb):
        return self.sim

    def persona(self, pid):
        return (1, 'Abdair')

    def contexto(self, pid):
        return ''


class FakeSesion:
    def __init__(self, resultado):
        self.resultado = resultado
        self.tareas = []
        self.kwargs_preguntar = None
        self.cerrada = False

    def preguntar(self, texto, **k):
        self.tareas.append(texto)
        self.kwargs_preguntar = k
        if k.get('al_usar'):
            k['al_usar']('mcp__playwright__browser_navigate', {'url': 'https://duckduckgo.com'})
        return self.resultado

    def iniciar_en_fondo(self):
        pass

    def cerrar(self):
        self.cerrada = True


VOZ = (np.ones(256, np.float32) / 16.0, 1.8)


def construir(respuesta_voz='sí, hazlo', sim_confirmacion=0.8, en_modo=True, resultado='ok'):
    v = vp.VoicePipeline(FakeMgr(), FakeSM())
    v._memoria = FakeMem()
    v._convo = []
    v.dichas = []
    v._hablar = lambda texto, emo=None: v.dichas.append(texto)
    v._grabar = lambda **k: b'audio' if respuesta_voz is not None else None
    v._transcribir = lambda audio: respuesta_voz or ''

    def analizar(audio):
        v._memoria.sim = sim_confirmacion       # la voz de la confirmación
        return (np.ones(256, np.float32) / 16.0, 1.5)
    v._analizar_voz = analizar
    v._modo_ingeniero = en_modo
    v._sesion_web = FakeSesion(resultado)
    return v


def main() -> None:
    md.MODO_DUENO_ENABLED = True
    md.AGENTE_WEB_ENABLED = True
    md.MOTOR_INGENIERO = 'claude'
    original_disp = claude_code.disponible
    claude_code.disponible = lambda: True
    try:
        # 1) Detección: el navegador solo si se pide explícitamente; buscar va al pipeline
        for t in ('Bob, en el navegador busca el precio del ESP32-CAM',
                  'abre la página de wikipedia', 'entra a wikipedia.org',
                  'haz clic en el primer resultado', 'navega a wikipedia.org',
                  'Quiero que abras el navegador y busques inspiración para la aplicación',
                  'abre el navegador y busca ideas'):
            d = md.detectar(t)
            check(f'web: "{t}"', d is not None and d[0] == 'web', f'-> {d}')
        for t in ('Bob, busca en internet el precio del ESP32-CAM',
                  'busca el clima de Potosí en la web', 'en internet busca robots baratos',
                  'investiga en google qué es un servo MG996R'):
            d = md.detectar(t)
            check(f'buscar (pipeline): "{t}"', d is not None and d[0] == 'buscar_web', f'-> {d}')
        check('web: quita el "Bob," de la tarea',
              md.detectar('Bob, en el navegador busca X')[1]['tarea'] == 'en el navegador busca X')
        for t in ('abre los ojos', 'busca una idea para los servos', 'en internet hay de todo',
                  'me gusta navegar'):
            d = md.detectar(t)
            check(f'no es navegador ni búsqueda: "{t}"',
                  d is None or d[0] not in ('web', 'buscar_web'), f'-> {d}')

        # 2) Confirmado por el dueño → ejecuta, dice el resultado y lo guarda de contexto
        v = construir(resultado='El ESP32-CAM cuesta unos 8 dólares. Lo venden en varias '
                                'tiendas. Hay modelos con antena. Cuarta frase de más.')
        md.atender(v, 'Bob, en el navegador busca el precio del ESP32-CAM', VOZ)
        s = v._sesion_web
        check('dueño + sí: ejecuta la tarea',
              s.tareas == ['en el navegador busca el precio del ESP32-CAM'])
        check('dueño: no pide confirmación',
              not any('Dime sí o no' in d or 'Hago esto' in d for d in v.dichas))
        check('dueño + sí: con tope de pasos y tiempo',
              s.kwargs_preguntar and s.kwargs_preguntar.get('max_herramientas') == md.WEB_MAX_HERRAMIENTAS
              and s.kwargs_preguntar.get('timeout') == md.WEB_TIMEOUT_S)
        dicho = ' '.join(v.dichas)
        check('dueño + sí: dice el resultado (máx 3 frases)',
              'unos 8 dólares' in dicho and 'Cuarta frase' not in dicho)
        check('dueño + sí: queda de contexto', v._contexto_web and '8 dólares' in v._contexto_web)
        check('dueño: anuncia "Abro el navegador." una sola vez',
              v.dichas.count('Abro el navegador.') == 1)

        # 3) El siguiente turno del modo ingeniero recibe el resultado, una sola vez
        ing = FakeSesion('Con 8 dólares entra en el presupuesto.')
        v._sesion_ing = ing
        md.responder_claude(v, '¿y eso nos conviene?', ing)
        check('contexto web en el turno siguiente', 'Resultado de la última tarea web' in ing.tareas[-1])
        md.responder_claude(v, 'otra cosa', ing)
        check('contexto web solo una vez', 'Resultado de la última tarea web' not in ing.tareas[-1])

        # 4) La voz del dueño da la orden → abre el navegador directo, sin preguntar otra vez
        v = construir()
        md.atender(v, 'en el navegador busca algo', VOZ)
        check('dueño: abre el navegador directo, sin confirmación',
              v._sesion_web.tareas == ['en el navegador busca algo']
              and not any('Dime sí o no' in d or 'Hago esto' in d for d in v.dichas))

        # 5) Otra voz da la orden → rechazada, no ejecuta
        v = construir()
        v._memoria.sim = 0.1
        md.atender(v, 'en el navegador busca algo', VOZ)
        check('orden de otra voz: rechazada, no ejecuta',
              not v._sesion_web.tareas and bool(v.dichas), f'-> {v.dichas[-1:]}')

        # 7) Falla la tarea → lo dice
        v = construir(resultado=None)
        md.atender(v, 'en el navegador busca algo', VOZ)
        check('tarea fallida: lo dice', any('No pude terminar' in d for d in v.dichas))

        # 8) Fuera del modo ingeniero / flag apagado / sin Claude Code
        v = construir(en_modo=False)
        md.atender(v, 'en el navegador busca algo', VOZ)
        check('fuera del modo ingeniero: lo indica', not v._sesion_web.tareas
              and any('modo ingeniero' in d for d in v.dichas))
        md.AGENTE_WEB_ENABLED = False
        v = construir()
        md.atender(v, 'en el navegador busca algo', VOZ)
        check('AGENTE_WEB_ENABLED=False: no ejecuta', not v._sesion_web.tareas)
        md.AGENTE_WEB_ENABLED = True
        claude_code.disponible = lambda: False
        v = construir()
        md.atender(v, 'en el navegador busca algo', VOZ)
        check('sin Claude Code: no ejecuta', not v._sesion_web.tareas
              and any('Claude Code' in d for d in v.dichas))
        claude_code.disponible = lambda: True


        # 9b) Búsqueda en internet: la responde el pipeline, el navegador no se abre
        v = construir()
        check('buscar en modo ingeniero: no es comando (el turno va al LLM)',
              md.atender(v, 'Bob, busca en internet el precio del ESP32-CAM', VOZ) is False
              and not v._sesion_web.tareas and not any('Dime sí o no' in d for d in v.dichas))
        v = construir(en_modo=False)
        md.atender(v, 'en el navegador busca algo', VOZ)
        check('buscar fuera del modo ingeniero: lo indica y no abre el navegador',
              any('modo ingeniero' in d for d in v.dichas) and not v._sesion_web.tareas)

        # 10) Limpieza para la voz: respuesta real de WebSearch (2026-10-17)
        real = ('El ESP32-CAM cuesta unos 7 a 11 € en tiendas europeas, y más en Latinoamérica. '
                'El servo MG996R da unos 9 a 11 kg·cm de par (ver https://servodatabase.com/x).\n\n'
                'Fuentes: [OpenELAB](https://openelab.io/es/c), [Waveshare](https://www.waveshare.com/m)')
        limpio = ' '.join(md.frases_para_decir(real, 3))
        check('voz: sin la lista de fuentes ni URLs', 'Fuentes' not in limpio and 'http' not in limpio
              and 'OpenELAB' not in limpio, f'-> {limpio}')
        check('voz: conserva el contenido', '7 a 11 €' in limpio and 'MG996R' in limpio)
        check('voz: enlace markdown → solo el texto',
              md.frases_para_decir('Mira [la ficha](https://x.y/z) del servo.', 2) == ['Mira la ficha del servo.'])

        # 11) Configuración de la sesión real (sin ejecutarla)
        v = construir()
        v._sesion_web = None
        s = md._sesion_web(v)
        args = s.mcp['playwright']['args']
        check('sesión: Playwright MCP fijo en 0.0.83', md.PLAYWRIGHT_MCP == '@playwright/mcp@0.0.83'
              and md.PLAYWRIGHT_MCP in args)
        check('sesión: perfil aislado y navegador visible', '--isolated' in args and '--headless' not in args)
        check('sesión: Haiku 5.5', s.modelo == 'claude-haiku-5-5')
        check('sesión: busca con WebSearch (sin código local)',
              s.herramientas == 'WebSearch' and 'WebSearch' in s.permitidas)
        peligrosas = {md._WEB + h for h in ('evaluate', 'run_code_unsafe', 'file_upload', 'drop')}
        check('sesión: las peligrosas ocultas', peligrosas <= set(s.prohibidas))
        check('sesión: ninguna permitida es peligrosa', not (set(s.permitidas) & set(s.prohibidas)))
        check('sesión: prompt con reglas fijas', md._REGLAS_FIJAS in s.system_prompt)
        check('sesión: se reutiliza', md._sesion_web(v) is s)
        md.salir_ingeniero(v)
        check('salir del modo ingeniero cierra el navegador', v._sesion_web is None)
    finally:
        claude_code.disponible = original_disp

    print(f"\n{'TODO OK' if fallos == 0 else f'{fallos} FALLA(S)'}")
    sys.exit(1 if fallos else 0)


if __name__ == '__main__':
    main()
