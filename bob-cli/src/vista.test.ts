import { test } from 'node:test';
import assert from 'node:assert/strict';

import { aTexto, fusionar, partesEstado } from './vista.ts';

test('turnos marcados; el resto igual', () => {
  assert.equal(aTexto({ t: 'log', origen: 'bob', nivel: 'info', ansi: 'Usuario: hola', turno: 'usuario' }),
    '\n\x1b[36m›\x1b[0m Usuario: hola');
  assert.equal(aTexto({ t: 'log', origen: 'bob', nivel: 'info', ansi: 'Bob: hola', turno: 'bob' }),
    '\x1b[32m●\x1b[0m Bob: hola');
  assert.equal(aTexto({ t: 'log', origen: 'bob', nivel: 'info', ansi: 'x' }), 'x');
  assert.equal(aTexto({ t: 'crudo', de: 'stderr', texto: 'W0000' }), '\x1b[2mW0000\x1b[0m');
  assert.equal(aTexto({ t: 'crudo', de: 'stdout', texto: 'INFO' }), 'INFO');
});

test('fusionar: los cambios pisan, lo demás queda', () => {
  assert.deepEqual(fusionar({ fsm: 'IDLE', mic: true }, { fsm: 'LISTENING' }), { fsm: 'LISTENING', mic: true });
});

test('barra: fase en castellano y solo lo que se sabe', () => {
  assert.deepEqual(partesEstado({}), []);
  assert.deepEqual(partesEstado({ fsm: 'THINKING', mic: true }).map((p) => p.texto), ['pensando']);
  assert.deepEqual(partesEstado({ fsm: 'RARO' }).map((p) => p.texto), ['raro']);
});

test('barra: micrófono, música, baile y modo ingeniero', () => {
  const textos = partesEstado({
    fsm: 'SPEAKING', emocion: 'MUY_FELIZ', mic: false, musica: true, baile: true, ingeniero: true,
  }).map((p) => p.texto);
  assert.deepEqual(textos, ['hablando', 'cara muy_feliz', 'micrófono silenciado', '♪ bailando', 'modo ingeniero']);
  assert.deepEqual(partesEstado({ musica: true, baile: false }).map((p) => p.texto), ['♪ música']);
  assert.deepEqual(partesEstado({ musica: false, baile: true }).map((p) => p.texto), []);
});

test('barra: sin internet', () => {
  assert.deepEqual(partesEstado({ red: false, mic: false }).map((p) => p.texto),
    ['sin internet: solo teclas', 'micrófono silenciado']);
  assert.deepEqual(partesEstado({ red: true }), []);
});

test('barra: cabeza simulada (bob --alone)', () => {
  assert.deepEqual(partesEstado({ cabeza: { pan: 92, tilt: 81 } }).map((p) => p.texto), ['cabeza pan 92° tilt 81°']);
});
