import { test } from 'node:test';
import assert from 'node:assert/strict';

import { interpretar } from './teclas.ts';

const t = (input: string, extra: object = {}) => ({ input, ...extra });

test('Q y Ctrl+C salen (mayúscula también)', () => {
  assert.deepEqual(interpretar(t('q'), null), { tipo: 'salir' });
  assert.deepEqual(interpretar(t('Q'), null), { tipo: 'salir' });
  assert.deepEqual(interpretar(t('c', { ctrl: true }), null), { tipo: 'salir' });
});

test('S, D y J se mandan a Bob', () => {
  for (const l of ['s', 'd', 'j', 'S', 'D', 'J']) {
    assert.deepEqual(interpretar(t(l), null), { tipo: 'enviar', tecla: l.toLowerCase() });
  }
});

test('ENTER dispara un turno', () => {
  assert.deepEqual(interpretar(t('', { return: true }), null), { tipo: 'enviar', tecla: 'turno' });
});

test('E abre la línea del nombre', () => {
  assert.deepEqual(interpretar(t('e'), null), { tipo: 'nombre', texto: '' });
  assert.deepEqual(interpretar(t('E'), null), { tipo: 'nombre', texto: '' });
});

test('otras teclas no hacen nada', () => {
  assert.deepEqual(interpretar(t('x'), null), { tipo: 'nada' });
  assert.deepEqual(interpretar(t('s', { ctrl: true }), null), { tipo: 'nada' });
});

test('línea del nombre: escribir, borrar, ENTER manda, ESC cancela', () => {
  assert.deepEqual(interpretar(t('A'), ''), { tipo: 'nombre', texto: 'A' });
  // dentro de la línea las letras de atajo son texto, no atajos
  assert.deepEqual(interpretar(t('q'), 'Abdai'), { tipo: 'nombre', texto: 'Abdaiq' });
  assert.deepEqual(interpretar(t('', { delete: true }), 'Abdair'), { tipo: 'nombre', texto: 'Abdai' });
  assert.deepEqual(interpretar(t('', { backspace: true }), 'José'), { tipo: 'nombre', texto: 'Jos' });
  assert.deepEqual(interpretar(t('', { return: true }), '  Abdair '),
    { tipo: 'enviar', tecla: 'e', nombre: 'Abdair' });
  assert.deepEqual(interpretar(t('', { return: true }), '   '), { tipo: 'nombre', texto: null });
  assert.deepEqual(interpretar(t('', { escape: true }), 'Abd'), { tipo: 'nombre', texto: null });
  assert.deepEqual(interpretar(t('c', { ctrl: true }), 'Abd'), { tipo: 'salir' });
});

test('línea del nombre: pegar texto y caracteres de control', () => {
  assert.deepEqual(interpretar(t('María José'), ''), { tipo: 'nombre', texto: 'María José' });
  assert.deepEqual(interpretar(t('\x1b'), 'A'), { tipo: 'nada' });
});
