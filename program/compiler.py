"""Pipeline completo: fuente -> parser -> analisis semantico -> TAC.

`Driver.py` (CLI) y `lsp_server.py` (editor) son clientes delgados de este
modulo. El TAC solo se genera si el programa no tiene errores lexicos,
sintacticos ni semanticos.
"""

from dataclasses import dataclass, field

from antlr4 import InputStream, CommonTokenStream, ParseTreeWalker
from antlr4.error.ErrorListener import ErrorListener

from CompiscriptLexer import CompiscriptLexer
from CompiscriptParser import CompiscriptParser
from analyzer import SemanticAnalyzer
from tac_generator import TACGenerator
from semantic import ClassSymbol


class SyntaxErrorListener(ErrorListener):
    def __init__(self):
        super().__init__()
        self.errors = []

    def syntaxError(self, recognizer, offendingSymbol, line, column, msg, e):
        self.errors.append(f"Error de sintaxis (linea {line}:{column}): {msg}")


@dataclass
class CompileResult:
    tree: object
    parser: CompiscriptParser
    syntax_errors: list = field(default_factory=list)
    analyzer: SemanticAnalyzer = None
    tac: object = None  # TACProgram, solo si no hubo errores
    init_frames: list = field(default_factory=list)  # registros de Clase.__init (los crea el generador)

    @property
    def errors(self) -> list[str]:
        semantic = [str(e) for e in self.analyzer.errors] if self.analyzer else []
        return self.syntax_errors + semantic


def compile_source(source: str) -> CompileResult:
    lexer = CompiscriptLexer(InputStream(source))
    listener = SyntaxErrorListener()
    lexer.removeErrorListeners()
    lexer.addErrorListener(listener)

    parser = CompiscriptParser(CommonTokenStream(lexer))
    parser.removeErrorListeners()
    parser.addErrorListener(listener)

    tree = parser.program()
    result = CompileResult(tree, parser, listener.errors)
    if listener.errors:
        return result

    analyzer = SemanticAnalyzer()
    ParseTreeWalker.DEFAULT.walk(analyzer, tree)
    result.analyzer = analyzer
    if analyzer.errors:
        return result

    generator = TACGenerator(analyzer)
    result.tac = generator.generate(tree)
    result.init_frames = generator.init_frames
    return result


def describe_memory(analyzer: SemanticAnalyzer, extra_frames=()) -> str:
    """Tabla de simbolos con sus direcciones: segmento global, layout de clases y registros de activacion.

    `extra_frames` agrega registros que no vienen del fuente (los `Clase.__init`
    que crea el generador de TAC, ver `CompileResult.init_frames`).
    """
    out = [f"== segmento global gp ({analyzer.globals.size} bytes, nivel 0)"]
    out += analyzer.globals.describe()

    for symbol in analyzer.table.global_scope.symbols.values():
        if isinstance(symbol, ClassSymbol):
            out.append(f"== clase {symbol.name} ({symbol.size} bytes por instancia)")
            out.append(f"  field  {'vtable':<12} {'puntero':<12} offset=0    size=8   dir=this[+0]")
            for f in symbol.all_fields():
                type_name = f.type.name if f.type is not None else "?"
                out.append(
                    f"  field  {f.name:<12} {type_name:<12} offset={f.offset:<4} size={f.size:<2}  "
                    f"dir={f.address}  (de {f.area})"
                )
            vtable = ", ".join(f"[{i}] {m.label}" for i, m in enumerate(symbol.vtable()))
            out.append(f"  vtable: {vtable or '(vacia)'}")

    for frame in [f.frame for f in analyzer.functions] + list(extra_frames) + [analyzer.main_frame]:
        out.append(f"== registro de activacion de {frame.name} (nivel {frame.level})")
        out += frame.describe()
    return "\n".join(out)
