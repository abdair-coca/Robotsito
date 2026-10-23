import { test } from 'node:test';
import assert from 'node:assert/strict';

import { Lineas, parsearLinea } from './protocolo.ts';

test('evento log de stdout', () => {
  const e = parsearLinea('{"t":"log","origen":"bob","nivel":"error","ansi":"\\u001b[31mx\\u001b[0m"}', 'stdout');
  assert.deepEqual(e, { t: 'log', origen: 'bob', nivel: 'error', ansi: '\x1b[31mx\x1b[0m' });
});

test('nivel desconocido o ausente → info', () => {
  const e = parsearLinea('{"t":"log","ansi":"hola","nivel":"raro"}', 'stdout');
  assert.deepEqual(e, { t: 'log', origen: 'bob', nivel: 'info', ansi: 'hola' });
});

test('log con turno; turno desconocido se ignora', () => {
  assert.deepEqual(parsearLinea('{"t":"log","ansi":"Usuario: hola","turno":"usuario"}', 'stdout'),
    { t: 'log', origen: 'bob', nivel: 'info', ansi: 'Usuario: hola', turno: 'usuario' });
  assert.deepEqual(parsearLinea('{"t":"log","ansi":"x","turno":"otro"}', 'stdout'),
    { t: 'log', origen: 'bob', nivel: 'info', ansi: 'x' });
});

test('estado: solo los campos conocidos y con su tipo', () => {
  assert.deepEqual(
    parsearLinea('{"t":"estado","fsm":"THINKING","mic":false,"emocion":"FELIZ","baile":"si","raro":1}', 'stdout'),
    { t: 'estado', cambios: { fsm: 'THINKING', emocion: 'FELIZ', mic: false } });
});

test('stdout que no es JSON va crudo', () => {
  assert.deepEqual(parsearLinea('INFO: Created TensorFlow Lite delegate', 'stdout'),
    { t: 'crudo', de: 'stdout', texto: 'INFO: Created TensorFlow Lite delegate' });
  assert.deepEqual(parsearLinea('{roto', 'stdout'), { t: 'crudo', de: 'stdout', texto: '{roto' });
  assert.deepEqual(parsearLinea('{"t":"otro"}', 'stdout'), { t: 'crudo', de: 'stdout', texto: '{"t":"otro"}' });
});

test('stderr siempre crudo, aunque parezca JSON', () => {
  const linea = '{"t":"log","ansi":"x"}';
  assert.deepEqual(parsearLinea(linea, 'stderr'), { t: 'crudo', de: 'stderr', texto: linea });
});

test('líneas cortadas entre chunks y \\r\\n', () => {
  const l = new Lineas();
  const b = (s: string) => new TextEncoder().encode(s);
  assert.deepEqual(l.agregar(b('uno\r\ndo')), ['uno']);
  assert.deepEqual(l.agregar(b('s\ntres')), ['dos']);
  assert.deepEqual(l.cerrar(), ['tres']);
  assert.deepEqual(l.cerrar(), []);
});

test('carácter UTF-8 partido entre chunks', () => {
  const l = new Lineas();
  const bytes = new TextEncoder().encode('✖ cámara\n');   // ✖ son 3 bytes
  assert.deepEqual(l.agregar(bytes.slice(0, 1)), []);
  assert.deepEqual(l.agregar(bytes.slice(1, 5)), []);
  assert.deepEqual(l.agregar(bytes.slice(5)), ['✖ cámara']);
});
