# Arquitectura

Este documento describe cómo está organizado el compilador (fase de análisis
léxico/sintáctico/semántico) y cómo encajan sus piezas.

## Vista general

```
Compiscript.g4  --ANTLR-->  CompiscriptLexer.py / CompiscriptParser.py / CompiscriptListener.py
                                          |
                                          v
                                  ParseTree (ANTLR)
                                          |
                 +------------------------+------------------------+
                 |                        |                        |
                 v                        v                        v
           analyzer.py            tree_export.py            Driver.py / lsp_server.py
     (SemanticAnalyzer, un              (arbol -> dict/DOT          (puntos de entrada:
      CompiscriptListener)               para visualizarlo)          CLI y editor)
                 |
                 v
            semantic/ (agnóstico de ANTLR)
       symbol_table.py + types_system.py + errors.py
```

La regla de diseño principal es que **`semantic/` no conoce ANTLR**: no
importa nada de `antlr4`, no recibe contextos (`ctx`) ni sabe de líneas o
columnas. Sólo modela conceptos del lenguaje (tipos, símbolos, ámbitos,
errores). Todo lo que sí conoce el árbol de ANTLR vive en `analyzer.py`,
que actúa de puente: recorre el `ParseTree` con un `Listener` y, en cada
nodo relevante, llama a la tabla de símbolos o construye tipos.

Esta separación es la que permite reutilizar `semantic/` desde tests
unitarios sin generar ningún parser (`tests/test_symbol_table.py`), y es
la misma razón por la que `Driver.py` y `lsp_server.py` pueden compartir
`analyzer.py` sin duplicar lógica: ambos son "drivers" delgados que arman
el lexer/parser, corren el listener, y hacen algo distinto con el
resultado (imprimir por stdout, o publicar diagnósticos LSP).

## Componentes

### `program/Compiscript.g4`

Gramática ANTLR (lexer + parser) del lenguaje. Se compila con la
herramienta `antlr` (Java) hacia Python3; los archivos generados
(`CompiscriptLexer.py`, `CompiscriptParser.py`, `CompiscriptListener.py`,
`*.tokens`, `*.interp`) no se versionan (ver `.gitignore`) porque son
artefactos derivados — se regeneran con el comando documentado en el
`README.md`.

Puntos de precedencia de expresiones: la gramática codifica la
precedencia de operadores como una cadena de reglas
(`assignmentExpr > conditionalExpr > logicalOrExpr > logicalAndExpr >
equalityExpr > relationalExpr > additiveExpr > multiplicativeExpr >
unaryExpr > primaryExpr`), en vez de usar declaraciones de precedencia
implícitas de ANTLR. Esto hace explícito en la gramática misma qué
operador liga más fuerte, y es lo que produce árboles como el de la
sección "Árbol sintáctico visual" más abajo.

### `program/semantic/` — tabla de símbolos y sistema de tipos

- **`types_system.py`**: jerarquía de `Type` (`IntegerType`, `FloatType`,
  `StringType`, `BooleanType`, `NullType`, `VoidType`, `ErrorType`,
  `ArrayType`, `ClassType`, `FunctionType`) más las funciones de relación
  entre tipos: `is_numeric`, `is_error`, `is_assignable`. `ErrorType` es
  un tipo comodín: una vez que una expresión falla, se le asigna
  `ErrorType` para que los errores no se dupliquen en cascada hacia sus
  usos posteriores.
- **`symbol_table.py`**: `Symbol` y sus subclases (`VariableSymbol`,
  `ParameterSymbol`, `FunctionSymbol`, `ClassSymbol`), `Scope` (un
  ambiente con un diccionario nombre→símbolo y un puntero a su padre) y
  `SymbolTable` (mantiene el `Scope` global y el `current_scope`, y
  expone `enter_scope`/`exit_scope`/`define`/`resolve`). La resolución de
  nombres (`Scope.resolve`) camina hacia arriba por la cadena de padres
  — así es como una función anidada ve las variables del ámbito que la
  contiene (closures a nivel estático).
- **`errors.py`**: `SemanticError(line, column, message)`, sin dependencia
  de ANTLR — el analizador es quien traduce un `ctx.start` a línea/columna
  antes de construir el error.

### `program/analyzer.py` — el puente

`SemanticAnalyzer` extiende `CompiscriptListener` (generado por ANTLR) y
sobreescribe los métodos `enterX`/`exitX` de las reglas que necesitan
lógica semántica. Usa dos pilas (`function_stack`, `class_stack`) y dos
contadores (`loop_depth`, `switch_depth`) para saber, en cualquier punto
del recorrido, si un `return` está dentro de una función, si un `break`
está dentro de un bucle/switch, o a qué clase pertenece un `this`. Los
tipos ya calculados de cada expresión se guardan en `self.types` (un
`dict[ctx -> Type]`) para que las reglas de más arriba en el árbol (p.ej.
una asignación) puedan consultar el tipo de sus subexpresiones sin
recalcularlo.

Además de validar, el analizador deja anotado lo que necesita la
generación de código: `symbol_of` (`dict[ctx -> símbolo]` de cada
declaración, identificador, función, clase y variable de
`foreach`/`catch`), `suffix_base` (el tipo sobre el que se aplica cada
`.campo`/`[i]`/`(...)`) y la lista `functions`. También asigna la
**dirección** de cada variable (`_allocate`): segmento global, registro de
activación de su función o, para los campos, el layout de su clase.

### `program/semantic/runtime.py` — memoria en tiempo de ejecución

`size_of(tipo)`, `MemoryArea` (área contigua con offsets crecientes:
segmento global) y `ActivationRecord` (registro de activación de una
función: `this`, parámetros, locales y temporales). `ClassSymbol` calcula
el layout de sus instancias (`compute_layout`: puntero a vtable, campos
heredados y propios) y su `vtable()`. Al igual que el resto de
`semantic/`, no depende de ANTLR.

### `program/ir/tac.py` — representación intermedia

Define el TAC como cuádruplos (`Quad`), el contenedor `TACProgram`, el
asignador de temporales con reciclaje (`TempAllocator`) y el de etiquetas
(`LabelAllocator`). No conoce ANTLR ni la tabla de símbolos. El formato
está documentado en [`TAC.md`](TAC.md).

### `program/tac_generator.py` — generación de TAC

`TACGenerator` es un Visitor que recorre el árbol **después** del
análisis semántico. Cada `visitX` de una sentencia emite cuádruplos, y
cada `visitX` de una expresión los emite y devuelve el operando donde
quedó su valor (variable, temporal o literal). No vuelve a resolver
nombres: usa `symbol_of`, `suffix_base` y las direcciones que dejó el
analizador. Cada función se genera en su propio búfer
(`_FunctionContext`), con su propio asignador de temporales y su propia
pila de etiquetas de `break`/`continue`. Al cerrar la función, el tamaño
de su registro de activación se completa con los temporales usados.

### `program/compiler.py` — pipeline

`compile_source(fuente)` corre lexer → parser → `SemanticAnalyzer` →
`TACGenerator` y devuelve un `CompileResult` (árbol, errores, analizador,
TAC). El TAC solo se genera si no hubo errores. `describe_memory` arma el
reporte de la tabla de símbolos con direcciones. Tanto `Driver.py` como
los tests usan este módulo.

### `program/tree_export.py` — representación visual del árbol

Dos funciones puras, sin estado: `tree_to_dict` (arbol → `dict` JSON,
usado por el panel de VS Code) y `tree_to_dot` (arbol → grafo Graphviz
DOT, para generar una imagen sin depender de ningún editor). Ninguna de
las dos conoce reglas semánticas — sólo recorren la forma del
`ParseTree` que ya construyó ANTLR.

### `program/Driver.py` — CLI

Punto de entrada por línea de comandos (sobre `compiler.py`): compila un
`.cps` e imprime los errores de sintaxis o de semántica. Opciones:
`--tree <archivo.json|archivo.dot>` vuelca el árbol sintáctico, `--tac
[archivo]` emite el código de tres direcciones y `--symbols` muestra la
tabla de símbolos con direcciones y registros de activación.

### `program/lsp_server.py` — servidor para el editor

Servidor [LSP](https://microsoft.github.io/language-server-protocol/)
construido con [`pygls`](https://github.com/openlawlibrary/pygls). Reutiliza
`analyzer.py` tal cual: cada vez que el editor abre, edita o guarda un
`.cps`, el servidor vuelve a parsear y a correr el `SemanticAnalyzer`, y
publica los errores (de sintaxis y semánticos) como diagnósticos LSP —
esto es lo que hace que aparezcan subrayados en rojo en el editor sin
que el usuario ejecute nada manualmente. También expone un comando
custom, `compiscript/parseTree`, que devuelve el árbol del último parseo
exitoso como JSON (vía `tree_export.tree_to_dict`).

### `vscode-extension/` — el "IDE"

En vez de construir un IDE propio, el punto de entrada del compilador es
una extensión de VS Code que actúa de cliente LSP:

- `syntaxes/compiscript.tmLanguage.json` + `language-configuration.json`:
  resaltado de sintaxis y pares de brackets/comentarios para archivos
  `.cps`.
- `src/extension.js`: arranca `program/lsp_server.py` como proceso hijo
  (vía `vscode-languageclient`) y lo conecta a VS Code por stdio. De ahí
  en más, los diagnósticos semánticos aparecen solos en el editor.
- `src/parseTreePanel.js`: implementa el comando **"Compiscript: Mostrar
  árbol sintáctico"**, que le pide al servidor el árbol del archivo activo
  y lo pinta como una lista anidada colapsable en un panel webview — esta
  es la representación visual del árbol sintáctico integrada al editor.

Ver `README.md` para los pasos de instalación y uso.

## Árbol sintáctico visual (ejemplo)

Para `let x: integer = 1 + 2 * 3;`, `tree_to_dot` produce un árbol que
respeta la precedencia de operadores codificada en la gramática (`*` liga
más fuerte que `+`, por eso queda más profundo):

```
program
└─ statement
   └─ variableDeclaration
      ├─ 'let'  'x'  ':'  'integer'
      └─ initializer
         └─ expression → … → additiveExpr
                           ├─ multiplicativeExpr → … → literalExpr → '1'
                           ├─ '+'
                           └─ multiplicativeExpr
                              ├─ unaryExpr → … → literalExpr → '2'
                              ├─ '*'
                              └─ unaryExpr → … → literalExpr → '3'
```

(La imagen real, generada con Graphviz a partir de `tree_to_dot`, se
obtiene con los comandos de la sección correspondiente en `README.md`.)

## Flujo de un error semántico, de punta a punta

1. `analyzer.py` detecta, por ejemplo, `let x: integer = "texto";` en
   `exitVariableDeclaration` — `is_assignable(INTEGER, STRING)` es
   `False`.
2. Llama a `self._error(ctx, mensaje)`, que arma un `SemanticError(line,
   column, mensaje)` a partir de `ctx.start` y lo agrega a `self.errors`.
3. **CLI**: `Driver.py` imprime `str(error)` por stdout y retorna
   código de salida 1.
4. **Editor**: `lsp_server.py` convierte cada `SemanticError` en un
   `lsprotocol.types.Diagnostic` y llama a `ls.publish_diagnostics`; VS
   Code lo muestra como un subrayado rojo en la línea/columna exacta,
   sin que el usuario tenga que correr nada.

## Por qué no hay un IDE propio

Construir un editor de texto desde cero no agrega valor didáctico sobre
las fases del compilador — el trabajo real (gramática, tabla de
símbolos, reglas semánticas) es el mismo si el editor es VS Code o uno
hecho a mano. Conectar el analizador a VS Code vía LSP tiene además la
ventaja de ser el mismo protocolo que usan compiladores de producción
(rust-analyzer, pyright, gopls), así que la arquitectura de
`lsp_server.py` es transferible más allá de este curso.
