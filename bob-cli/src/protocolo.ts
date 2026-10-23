// Protocolo Bob → CLI (ver robot_bob/nucleo/puente_cli.py).
// stdout de Bob trae NDJSON: un evento por línea. Lo que no sea JSON (una librería
// que escribió directo) y todo stderr se muestran crudos: no se pierde nada.

export type Nivel = 'info' | 'aviso' | 'error';

export type Evento =
  | { t: 'log'; origen: string; nivel: Nivel; ansi: string }
  | { t: 'crudo'; de: 'stdout' | 'stderr'; texto: string };

const NIVELES: readonly string[] = ['info', 'aviso', 'error'];

/** Una línea de stdout o stderr de Bob → evento para mostrar. */
export function parsearLinea(linea: string, de: 'stdout' | 'stderr'): Evento {
  if (de === 'stdout' && linea.startsWith('{')) {
    try {
      const e = JSON.parse(linea);
      if (e && e.t === 'log' && typeof e.ansi === 'string') {
        return {
          t: 'log',
          origen: String(e.origen ?? 'bob'),
          nivel: NIVELES.includes(e.nivel) ? e.nivel : 'info',
          ansi: e.ansi,
        };
      }
    } catch {
      // no era JSON: va cruda
    }
  }
  return { t: 'crudo', de, texto: linea };
}

/** Arma líneas completas con los chunks de un stream (un chunk puede cortar una
 *  línea o un carácter UTF-8 a la mitad). */
export class Lineas {
  private resto = '';
  private decoder = new TextDecoder('utf-8');

  /** Agrega un chunk y devuelve las líneas que quedaron completas. */
  agregar(chunk: Uint8Array): string[] {
    const partes = (this.resto + this.decoder.decode(chunk, { stream: true })).split('\n');
    this.resto = partes.pop() ?? '';
    return partes.map((l) => (l.endsWith('\r') ? l.slice(0, -1) : l));
  }

  /** Al cerrarse el stream: lo que quedó sin salto de línea. */
  cerrar(): string[] {
    const ultima = this.resto + this.decoder.decode();
    this.resto = '';
    return ultima ? [ultima] : [];
  }
}
