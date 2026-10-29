// Cómo se ve cada cosa en el CLI. Funciones puras (sin Ink) para poder probarlas.

import type { CambiosEstado, Evento } from './protocolo.ts';

const GRIS = '\x1b[2m';
const CIAN = '\x1b[36m';
const VERDE = '\x1b[32m';
const NORMAL = '\x1b[0m';

/** Una línea del historial. Los turnos llevan una marca: › el usuario (con una línea
 *  en blanco antes, para separar cada intercambio) y ● Bob. */
export function aTexto(e: Evento): string {
  if (e.t === 'crudo') return e.de === 'stderr' ? `${GRIS}${e.texto}${NORMAL}` : e.texto;
  if (e.t !== 'log') return '';
  if (e.turno === 'usuario') return `\n${CIAN}›${NORMAL} ${e.ansi}`;
  if (e.turno === 'bob') return `${VERDE}●${NORMAL} ${e.ansi}`;
  return e.ansi;
}

export type EstadoBob = CambiosEstado;

export function fusionar(estado: EstadoBob, cambios: CambiosEstado): EstadoBob {
  return { ...estado, ...cambios };
}

const FASES: Record<string, string> = {
  IDLE: 'en reposo',
  PRESENCE: 'viendo a alguien',
  LISTENING: 'escuchando',
  THINKING: 'pensando',
  SPEAKING: 'hablando',
  CONVERSATION_IDLE: 'en charla',
};

export interface Parte {
  texto: string;
  color?: string;
}

/** La barra de estado como partes (la vista les pone color). Lo que Bob todavía no
 *  informó no aparece. */
export function partesEstado(e: EstadoBob): Parte[] {
  const partes: Parte[] = [];
  if (e.fsm) partes.push({ texto: FASES[e.fsm] ?? e.fsm.toLowerCase(), color: colorFase(e.fsm) });
  if (e.emocion) partes.push({ texto: `cara ${e.emocion.toLowerCase()}` });
  if (e.cabeza) partes.push({ texto: `cabeza pan ${e.cabeza.pan}° tilt ${e.cabeza.tilt}°` });
  if (e.red === false) partes.push({ texto: 'sin internet: solo teclas', color: 'red' });
  if (e.averias?.length) partes.push({ texto: `sin ${e.averias.join(', ')}`, color: 'red' });
  if (e.mic === false) partes.push({ texto: 'micrófono silenciado', color: 'red' });
  if (e.musica) partes.push({ texto: e.baile ? '♪ bailando' : '♪ música', color: 'magenta' });
  if (e.ingeniero) partes.push({ texto: 'modo ingeniero', color: 'yellow' });
  return partes;
}

function colorFase(fsm: string): string {
  if (fsm === 'LISTENING') return 'cyan';
  if (fsm === 'THINKING') return 'yellow';
  if (fsm === 'SPEAKING') return 'green';
  return 'white';
}

export const SPINNER = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'];
