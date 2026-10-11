# Compiscript — Análisis Semántico y Generación de Código Intermedio

Implementación del compilador de Compiscript con ANTLR4 y target Python3.
Cubre el análisis léxico, sintáctico y semántico, y la generación de
código intermedio (TAC).

# Link al video:
https://drive.google.com/drive/folders/1oJakUhT_LeHLsataZ9m95zVwSX6Z9hN2?usp=sharing
   

Ver [Instrucciones.md](Instrucciones.md) para el enunciado completo de la tarea
y [ARCHITECTURE.md](ARCHITECTURE.md) para el detalle de la arquitectura, y
[TAC.md](TAC.md) para la documentación del código intermedio.

## Estructura del repositorio

```
program/
  Compiscript.g4        Gramática ANTLR (lexer + parser)
  Driver.py              Punto de entrada CLI: compila un .cps, reporta errores y opcionalmente
                          exporta el arbol (--tree), el TAC (--tac) y la tabla de simbolos (--symbols)
  compiler.py            Pipeline: parser -> analisis semantico -> generacion de TAC
  analyzer.py            Analisis semantico (Listener) + asignacion de direcciones
  tac_generator.py       Generacion de codigo de tres direcciones (Visitor)
  ir/                     Representacion intermedia: cuadruplos, temporales y etiquetas
  lsp_server.py           Servidor LSP (pygls) que expone el analizador a un editor
  tree_export.py          Arbol de ANTLR -> dict JSON / grafo Graphviz DOT
  program.cps            Programa de ejemplo usado para pruebas manuales
  semantic/               Paquete Python con la tabla de simbolos y el sistema de tipos
  tests/                  Bateria de tests (casos exitosos y fallidos por regla semantica)
  requirements.txt        Dependencias de Python para correr el servidor LSP en el host

vscode-extension/        Extension de VS Code (cliente LSP) que actua como el "IDE" del proyecto
ARCHITECTURE.md           Documentacion de arquitectura
```

## Entorno de desarrollo (Docker)

El proyecto se construye y ejecuta dentro de un contenedor Docker que trae Java
(requerido por la herramienta ANTLR) y el runtime de Python para ANTLR.

1. Construir la imagen:

   ```bash
   docker build --rm . -t csp-image
   ```

2. Levantar un contenedor interactivo con el directorio `program` montado:

   ```bash
   docker run --rm -ti -v "$(pwd)/program":/program csp-image
   ```

3. Dentro del contenedor, generar el lexer/parser a partir de la gramática:

   ```bash
   antlr -Dlanguage=Python3 Compiscript.g4
   ```

4. Ejecutar el analizador sobre el programa de ejemplo:

   ```bash
   python3 Driver.py program.cps
   ```

5. Generar el código intermedio (TAC) y ver la tabla de símbolos con
   direcciones y registros de activación:

   ```bash
   python3 Driver.py program.cps --tac              # a stdout
   python3 Driver.py program.cps --tac salida.tac   # a un archivo
   python3 Driver.py program.cps --symbols
   ```

6. Correr la batería de tests:

   ```bash
   pytest
   ```

Los archivos generados por ANTLR (`CompiscriptLexer.py`, `CompiscriptParser.py`, etc.)
no se versionan — se regeneran a partir de `Compiscript.g4` en cada build.

## Árbol sintáctico visual

`Driver.py` puede volcar el árbol sintáctico de un programa, ya sea como
JSON o como un grafo Graphviz DOT:

```bash
python3 Driver.py program.cps --tree tree.dot
dot -Tpng tree.dot -o tree.png   # requiere graphviz (brew install graphviz / apt install graphviz)
```

La misma representación (como JSON) es la que usa la extensión de VS Code
para el comando **"Compiscript: Mostrar árbol sintáctico"** (ver abajo).

## Extensión de VS Code (editor)

En vez de un IDE propio, el proyecto se usa desde VS Code vía una extensión
que conecta un servidor [LSP](https://microsoft.github.io/language-server-protocol/)
(`program/lsp_server.py`) al editor. Esto da errores semánticos subrayados
en vivo mientras se escribe, resaltado de sintaxis para `.cps`, y el panel
de árbol sintáctico mencionado arriba.

### 1. Preparar el entorno de Python (host, fuera de Docker)

El servidor LSP corre directamente en tu máquina (VS Code necesita
hablarle por stdio), así que necesita el parser ya generado y las
dependencias instaladas fuera del contenedor:

```bash
# 1. Generar el parser (usando el mismo Docker de siempre)
docker run --rm -v "$(pwd)/program":/program csp-image antlr -Dlanguage=Python3 Compiscript.g4

# 2. Crear un entorno virtual e instalar dependencias del servidor
cd program
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Instalar dependencias de la extensión

```bash
cd vscode-extension
npm install
```

### 3. Correr la extensión

1. Abre la carpeta `vscode-extension/` en VS Code.
2. Presiona `F5` (o "Run and Debug" → "Run Extension"). Se abre una nueva
   ventana de VS Code ("Extension Development Host") con la extensión
   cargada.
3. En esa ventana, abre la carpeta raíz de este repo y luego un archivo
   `.cps` (por ejemplo `program/program.cps`).
4. Si el intérprete de Python del paso 1 no es el `python3` por defecto
   de tu PATH, ajusta `compiscript.pythonPath` en la configuración de VS
   Code (Settings → busca "compiscript") para que apunte al `.venv` que
   creaste (p.ej. `program/.venv/bin/python3`).
5. Deberías ver errores semánticos subrayados en rojo al guardar. Corre
   el comando **"Compiscript: Mostrar árbol sintáctico"** (paleta de
   comandos, `Cmd+Shift+P` / `Ctrl+Shift+P`) para ver el árbol del
   archivo activo en un panel lateral.

Para compilar el archivo activo, corre **"Compiscript: Compilar y mostrar
codigo intermedio (TAC)"**. El TAC y la tabla de símbolos con direcciones
se abren en un documento al lado del editor. Si el programa tiene errores,
la extensión los indica y no genera el TAC.

Si algo se traba, **"Compiscript: Reiniciar servidor de análisis"**
reinicia el proceso del servidor sin cerrar VS Code.

## Estado actual

- [x] Gramática ANTLR del lenguaje (incluye `integer`, `float` y `string`).
- [x] Entorno Docker para generar el parser y correr el analizador.
- [x] Tabla de símbolos con ámbitos anidados (global, función, clase, bloque).
- [x] Visitor de análisis semántico: sistema de tipos, ámbitos, funciones, clases,
      control de flujo (`if`/`while`/`do-while`/`for`/`foreach`/`switch`,
      `break`/`continue`, código inalcanzable), constructores.
- [x] Batería de tests (pytest) por regla semántica.
- [x] Representación visual del árbol sintáctico (`--tree` en `Driver.py` +
      panel de VS Code).
- [x] Editor: extensión de VS Code con LSP en vez de un IDE propio.
- [x] Documentación de arquitectura (`ARCHITECTURE.md`).

### Fase de generación de código intermedio

- [x] Diseño del TAC en cuádruplos (`ir/tac.py`, documentado en `TAC.md`).
- [x] Tabla de símbolos con direcciones, tamaños, registros de activación,
      layout de objetos y vtables.
- [x] Asignación y reciclaje de temporales.
- [x] Generación de TAC: expresiones (con cortocircuito), asignaciones, control
      de flujo, `switch`, funciones y recursión, clases/objetos con despacho
      dinámico, arreglos, `foreach`, `try/catch`.
- [x] `--tac` y `--symbols` en `Driver.py`.
- [x] Tests del TAC (`tests/test_tac*.py`, `tests/test_memory_layout.py`, `tests/test_compiler.py`).
- [x] Comando **"Compiscript: Compilar y mostrar codigo intermedio (TAC)"** en la extensión de VS Code.
