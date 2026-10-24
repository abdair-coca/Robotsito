// Prueba de punta a punta: la CLI real (App) con un Bob de mentira dentro de una
// terminal virtual. Cubre lo que las pruebas de funciones no ven: el panel de arranque
// queda una sola vez, el ruido de librerías no aparece y ningún log se pierde al
// redibujar la barra de estado.
//
//   npm run test:pantalla

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import React from 'react';
import { render } from 'ink';

import { App } from './app.tsx';
import type { Bob } from './bob.ts';
import { TerminalVirtual } from './pantalla-virtual.ts';

const AQUI = path.dirname(fileURLToPath(import.meta.url));
const FALSO = path.resolve(AQUI, '..', 'dev', 'bob_falso.py');

function esperar(condicion: () => boolean, ms: number): Promise<void> {
  return new Promise((ok, no) => {
    const t0 = Date.now();
    const intervalo = setInterval(() => {
      if (condicion()) {
        clearInterval(intervalo);
        ok();
      } else if (Date.now() - t0 > ms) {
        clearInterval(intervalo);
        no(new Error(`tiempo agotado (${ms} ms)`));
      }
    }, 50);
  });
}

test('arranque y charla en una terminal virtual', { timeout: 150_000 }, async () => {
  const salida = new TerminalVirtual(100, 40);
  const errores = new TerminalVirtual(100, 40);
  // stdin que no es terminal: la CLI no intenta leer teclas.
  const entrada = Object.assign(new EventEmitter(), { isTTY: false });
  const ref: { bob?: Bob } = {};

  const app = render(
    <App lanzamiento={{ script: FALSO, args: ['demo'], columnas: 100 }} registrar={(b) => { ref.bob = b; }} />,
    { stdout: salida as any, stderr: errores as any, stdin: entrada as any, exitOnCtrlC: false },
  );

  await esperar(() => salida.pantalla().includes('● Bob activo'), 60_000);
  await new Promise((r) => setTimeout(r, 20_000));    // dos ciclos de la charla simulada
  ref.bob?.detener();
  await app.waitUntilExit();

  const p = salida.pantalla();
  const veces = (texto: string) => p.split(texto).length - 1;

  // El panel de arranque, una sola vez, con sus pasos.
  assert.equal(veces('Robot Bob · arranque'), 1, 'el panel aparece una vez');
  assert.equal(veces('● Bob activo'), 1, 'el pie de activo aparece una vez');
  assert.ok(p.includes('✔ Serial') && p.includes('WiFi 192.168.0.16:5007'));
  assert.ok(p.includes('✔ Cámara') && p.includes('320×240 · stream conectado'));
  assert.ok(p.includes('✔ Audio') && p.includes('piper · groq'));
  assert.ok(p.includes('ocultos: 13 mensajes de librerías'), 'cuenta las 13 líneas ocultas');

  // Lo que no debe verse: ruido de librerías, configuración repetida, banner.
  for (const ruido of [
    'Applied providers', 'find model:', 'set det-size', 'pygame 2', 'Hello from the pygame',
    'perfil=', 'TTS=', '═══', 'Sistema Integrado', 'Conectando al stream', 'Esperando primer frame',
  ]) {
    assert.equal(veces(ruido), 0, `no debe verse: ${ruido}`);
  }

  // Lo que Bob dice y los avisos aparecen una sola vez: nada se borró al redibujar.
  for (const texto of [
    'Usuario: ¿Qué hora es? (charla 1)', 'Usuario: ¿Qué hora es? (charla 2)',
    'Bob (FELIZ): ¡Hola! Estoy muy bien', 'No respondió a tiempo', 'No llega video',
    'un print() a stderr', 'KeyError', 'INFO: Created TensorFlow', 'micrófono continuo + Silero VAD',
  ]) {
    assert.equal(veces(texto), 1, `una sola vez: ${texto}`);
  }

  // La barra de estado queda una vez al final.
  assert.equal(veces('Q salir · S micrófono · D corta y escucha'), 1, 'barra de atajos una vez');
  assert.ok(p.includes('Robot Bob detenido.'));
});
