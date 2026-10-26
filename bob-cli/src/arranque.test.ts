import { test } from 'node:test';
import assert from 'node:assert/strict';

import { aplicar, arranqueInicial, clasificar, panelTexto, quitarAnsi, type Arranque } from './arranque.ts';

// Las líneas de arranque tal como llegan del Bob real (pegado del 2026-10-23).
// Rich se come los [etiquetas] de sus mensajes; por eso algunas empiezan con espacio.
const ARRANQUE = [
  ' perfil=normal · LLM=rotacion · TTS=piper · clima=True · noticias=True · musica=True',
  '═'.repeat(60),
  '  Robot Bob — Sistema Integrado',
  '═'.repeat(60),
  '[serial] Conectado WiFi 192.168.0.16:5007',
  'Conectando al stream de la cámara...',
  'Esperando primer frame (máx 15 s)...',
  '[stream] Conectado.',
  '[tracker] Cámara lista: 320x240',
  '[memoria] activa. Personas en la base: 23',
  '[voice_id] WeSpeaker listo (resnet34_LM).',
  ...[1, 2, 3, 4, 5].flatMap((n) => [
    "Applied providers: ['CPUExecutionProvider'], with options: {'CPUExecutionProvider': {}}",
    `find model: C:/Users/abdai/.insightface/models/buffalo_l/m${n}.onnx detection [1, 3, 640, 640] 127.5 128.0`,
  ]),
  'set det-size: (320, 320)',
  '[face_id] InsightFace listo (buffalo_l).',
  ' rotación → gpt-oss-20b → qwen3.8-27b → gpt-oss-120b → ollama',
  ' LLM=rotación (nube) · TTS=piper · STT=groq · clima=True · noticias=True · musica=True',
  'pygame 2.6.1 (SDL 2.28.4, Python 3.11.9)',
  'Hello from the pygame community. https://www.pygame.org/contribute.html',
  'Robot Bob activo. Presiona Q para salir, S para silenciar o activar el micrófono.',
  ' micrófono continuo + Silero VAD',
];

function correr(lineas: string[], desde: Arranque = arranqueInicial()): Arranque {
  return lineas.reduce((a, l) => aplicar(a, clasificar(l.trim())), desde);
}

test('clasificación: cada línea del arranque real', () => {
  const tipos = ARRANQUE.map((l) => clasificar(l.trim()).tipo);
  assert.deepEqual(tipos.slice(0, 4), ['config', 'ignorar', 'ignorar', 'ignorar']);
  assert.equal(tipos[4], 'paso');                         // serial
  assert.equal(tipos[5], 'ignorar');                      // conectando al stream
  assert.equal(tipos[6], 'ignorar');                      // esperando primer frame
  assert.equal(tipos[7], 'camara-stream');
  assert.equal(tipos[8], 'paso');                         // cámara
  assert.equal(tipos[9], 'paso');                         // memoria
  assert.equal(tipos[10], 'paso');                        // voz
  for (let i = 11; i <= 20; i++) assert.equal(tipos[i], 'libreria', `línea ${i}`);
  assert.equal(tipos[21], 'libreria');                    // set det-size
  assert.equal(tipos[22], 'paso');                        // caras
  assert.equal(tipos[23], 'paso');                        // cerebro
  assert.equal(tipos[24], 'config');
  assert.equal(tipos[25], 'libreria');                    // pygame 2.6.1
  assert.equal(tipos[26], 'libreria');                    // Hello from the pygame
  assert.equal(tipos[27], 'activo');
  assert.equal(tipos[28], 'normal');                      // micrófono: sale como log normal
});

test('datos del panel tomados de las líneas reales', () => {
  const a = correr(ARRANQUE.slice(0, 27));                // hasta "activo" sin incluirla
  assert.deepEqual(a.datos.serial, ['WiFi 192.168.0.16:5007']);
  assert.deepEqual(a.datos.camara, ['320×240', 'stream conectado']);
  assert.deepEqual(a.datos.memoria, ['23 personas en la base']);
  assert.deepEqual(a.datos.voz, ['WeSpeaker resnet34_LM']);
  assert.deepEqual(a.datos.caras, ['InsightFace buffalo_l']);
  assert.deepEqual(a.datos.cerebro, ['rotación de 4 modelos']);
  assert.deepEqual(a.datos.audio, ['piper', 'groq']);
  assert.equal(a.ocultos, 13, 'cuenta las 13 líneas de librerías');
});

test('cámara: el orden del stream y del tracker no cambia el resultado', () => {
  const stream = clasificar('[stream] Conectado.');
  const tracker = clasificar('[tracker] Cámara lista: 320x240');
  const antes = correr([], aplicar(aplicar(arranqueInicial(), stream), tracker));
  const despues = correr([], aplicar(aplicar(arranqueInicial(), tracker), stream));
  assert.deepEqual(antes.datos.camara, ['320×240', 'stream conectado']);
  assert.deepEqual(despues.datos.camara, ['320×240', 'stream conectado']);
});

test('audio: la línea sin STT no tapa el STT de la siguiente', () => {
  const a = correr([' perfil=normal · TTS=piper · musica=True']);
  assert.deepEqual(a.datos.audio, ['piper']);
  const b = correr([' LLM=rotación (nube) · TTS=piper · STT=groq'], a);
  assert.deepEqual(b.datos.audio, ['piper', 'groq']);
});

test('panel en curso: pasos hechos con ✔ y pendientes con ○', () => {
  const a = correr(ARRANQUE.slice(0, 5));                 // solo el serial
  const lineas = panelTexto(a, 'en-curso').map(quitarAnsi);
  assert.ok(lineas.some((l) => l.includes('✔ Serial') && l.includes('WiFi 192.168.0.16:5007')));
  assert.ok(lineas.some((l) => l.includes('○ Cámara') && l.includes('esperando…')));
  assert.ok(lineas.at(-1)?.includes('arrancando…'));
  assert.ok(!lineas.some((l) => l.includes('ocultos')), 'el contador aparece al cerrar, no en curso');
});

test('panel: todas las filas de la caja miden lo mismo', () => {
  const a = correr(ARRANQUE.slice(0, 27));
  const caja = panelTexto(a, 'activo').map(quitarAnsi).filter((l) => /^[╭│╰]/.test(l));
  const anchos = new Set(caja.map((l) => [...l].length));
  assert.equal(anchos.size, 1, `anchos distintos: ${[...anchos].join(', ')}`);
  assert.ok(caja[0].startsWith('╭') && caja.at(-1)?.startsWith('╰'));
});

test('panel activo: pie y contador de ocultos', () => {
  const a = correr(ARRANQUE.slice(0, 27));
  const lineas = panelTexto(a, 'activo').map(quitarAnsi);
  assert.equal(lineas.filter((l) => l.includes('● Bob activo')).length, 1);
  assert.ok(lineas.includes('  ocultos: 13 mensajes de librerías'));
});

test('contador: en singular cuando es uno', () => {
  const a = correr(["find model: C:/x/m.onnx detection [1, 3] 1.0 1.0"]);
  assert.ok(panelTexto(a, 'activo').map(quitarAnsi).includes('  ocultos: 1 mensaje de librerías'));
});

test('panel detenido antes de activo: avisa, sin ✔ de más', () => {
  const a = correr(ARRANQUE.slice(0, 11));
  const lineas = panelTexto(a, 'detenido').map(quitarAnsi);
  assert.ok(lineas.some((l) => l.includes('se detuvo antes de quedar activo')));
});

test('panel cerrado: ya no cambia con más líneas', () => {
  const cerrado = { ...correr(ARRANQUE.slice(0, 5)), abierto: false };
  const despues = aplicar(cerrado, clasificar('[memoria] activa. Personas en la base: 9'));
  assert.equal(despues, cerrado);
});

test('líneas que no son del arranque siguen como log', () => {
  assert.equal(clasificar('⚠ CLAUDE     No respondió a tiempo.').tipo, 'normal');
  assert.equal(clasificar('[mic] micrófono continuo').tipo, 'normal');
});

test('quitarAnsi saca los colores de Rich', () => {
  assert.equal(quitarAnsi('\x1b[1;31m✖ CÁMARA\x1b[0m'), '✖ CÁMARA');
});

test('panel: sin ESP32 (bob --alone) el serial no queda esperando', () => {
  const a = correr(['[serial] Simulado (sin ESP32)']);
  assert.deepEqual(a.datos.serial, ['Simulado (sin ESP32)']);
});
