// Atajos del CLI → qué hacer. Función pura (sin Ink) para poder probarla.
// Las mismas letras que en la ventana de video (robot_bob/main.py):
//   Q salir · S micrófono · D cortar y escuchar · J cortar · P presentación · T foto · E enrolar · ENTER turno

/** Lo que importa de una tecla de Ink (useInput). */
export interface Tecla {
  input: string;
  ctrl?: boolean;
  return?: boolean;
  escape?: boolean;
  backspace?: boolean;
  delete?: boolean;
}

export type Accion =
  | { tipo: 'nada' }
  | { tipo: 'salir' }                                   // Q o Ctrl+C
  | { tipo: 'enviar'; tecla: string; nombre?: string }  // → {"cmd":"tecla",...}
  | { tipo: 'nombre'; texto: string | null };           // línea del nombre (null = cerrada)

const LETRAS = ['s', 'd', 'j', 'p', 't'];

/** nombre: lo escrito en la línea del nombre, o null si no está abierta. */
export function interpretar(t: Tecla, nombre: string | null): Accion {
  if (t.ctrl && t.input === 'c') return { tipo: 'salir' };
  if (nombre !== null) return editarNombre(t, nombre);
  if (t.ctrl) return { tipo: 'nada' };
  if (t.return) return { tipo: 'enviar', tecla: 'turno' };
  const letra = t.input.toLowerCase();
  if (letra === 'q') return { tipo: 'salir' };
  if (letra === 'e') return { tipo: 'nombre', texto: '' };
  if (LETRAS.includes(letra)) return { tipo: 'enviar', tecla: letra };
  return { tipo: 'nada' };
}

function editarNombre(t: Tecla, nombre: string): Accion {
  if (t.escape) return { tipo: 'nombre', texto: null };
  if (t.return) {
    const limpio = nombre.trim();
    return limpio ? { tipo: 'enviar', tecla: 'e', nombre: limpio } : { tipo: 'nombre', texto: null };
  }
  // En Windows la tecla Borrar llega como delete (\x7f), no como backspace.
  if (t.backspace || t.delete) return { tipo: 'nombre', texto: [...nombre].slice(0, -1).join('') };
  if (t.ctrl) return { tipo: 'nada' };
  const imprimible = [...t.input].filter((c) => c >= ' ' && c !== '\x7f').join('');
  return imprimible ? { tipo: 'nombre', texto: nombre + imprimible } : { tipo: 'nada' };
}
