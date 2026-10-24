// Terminal virtual para probar lo que Ink dibuja, sin una terminal real.
// Entiende lo que usan Ink y log-update: subir y borrar líneas, columna 0, saltos de
// línea y ocultar el cursor. Lo demás se ignora. Modela una pantalla sin límite de
// altura, así que sirve para ver qué líneas sobreviven al redibujar.

import { EventEmitter } from 'node:events';

const SECUENCIA = /^\x1b\[([0-9;?]*)([A-Za-z])/;

export class TerminalVirtual extends EventEmitter {
  readonly isTTY = true;
  private lineas: string[] = [''];
  private fila = 0;
  private col = 0;
  private pendiente = '';

  constructor(public columns = 100, public rows = 40) {
    super();
  }

  write(datos: string | Uint8Array, cb?: () => void): boolean {
    const bruto = typeof datos === 'string' ? datos : Buffer.from(datos).toString('utf8');
    const texto = this.pendiente + bruto;
    this.pendiente = '';
    let i = 0;
    while (i < texto.length) {
      const c = texto[i];
      if (c === '\x1b') {
        const m = SECUENCIA.exec(texto.slice(i));
        if (!m) {
          this.pendiente = texto.slice(i);   // secuencia partida: se completa en el próximo write
          break;
        }
        this.secuencia(m[1], m[2]);
        i += m[0].length;
        continue;
      }
      if (c === '\r') this.col = 0;
      else if (c === '\n') {
        this.fila += 1;
        this.asegurar();
        this.col = 0;
      } else this.poner(c);
      i += 1;
    }
    cb?.();
    return true;
  }

  /** Lo que se ve, de arriba abajo, sin espacios ni líneas vacías al final. */
  pantalla(): string {
    return this.lineas.map((l) => l.trimEnd()).join('\n').replace(/\n+$/, '');
  }

  private asegurar(): void {
    while (this.lineas.length <= this.fila) this.lineas.push('');
  }

  private poner(c: string): void {
    this.asegurar();
    const linea = this.lineas[this.fila].padEnd(this.col, ' ');
    this.lineas[this.fila] = linea.slice(0, this.col) + c + linea.slice(this.col + 1);
    this.col += 1;
  }

  private secuencia(params: string, cmd: string): void {
    if (params.startsWith('?')) return;          // cursor oculto/visible, modos privados
    const n = Math.max(1, parseInt(params || '1', 10) || 1);
    switch (cmd) {
      case 'A': this.fila = Math.max(0, this.fila - n); break;          // subir n líneas
      case 'B': this.fila += n; this.asegurar(); break;                 // bajar n líneas
      case 'G': this.col = n - 1; break;                                // columna absoluta
      case 'H': this.fila = 0; this.col = 0; break;                     // inicio
      case 'K': {                                                       // borrar en la línea
        const actual = this.lineas[this.fila] ?? '';
        if (params === '2') this.lineas[this.fila] = '';
        else if (params === '' || params === '0') this.lineas[this.fila] = actual.slice(0, this.col);
        break;
      }
      case 'J':                                                         // borrar la pantalla
        if (params === '2' || params === '3') {
          this.lineas = [''];
          this.fila = 0;
          this.col = 0;
        }
        break;
      default: break;
    }
  }
}
