# Reglas del Proyecto — Robot Bob

## Regla #0: Uso Obligatorio de Graphify para Lectura y Navegación (Ahorro de Tokens)

**SIEMPRE que se pida a un modelo de IA / agente leer, comprender o investigar el proyecto o su arquitectura (incluso si el usuario NO menciona "graphify" explícitamente), el agente DEBE usar Graphify como primera opción:**
1. Para entender la arquitectura o responder preguntas de código, consultar primero `graphify-out/GRAPH_REPORT.md` o ejecutar `graphify query "<pregunta>"` / `graphify explain "<concepto>"`.
2. Usar `graphify path "<nodoA>" "<nodoB>"` para rastrear relaciones entre módulos antes de abrir código fuente.
3. Evitar la lectura masiva de archivos `.py` / `.md` completos salvo que sea indispensable para hacer una edición precisa.
4. Tras realizar modificaciones en el código, ejecutar `graphify update .` para mantener el grafo actualizado.
