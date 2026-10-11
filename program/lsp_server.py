"""Servidor de Language Server Protocol (LSP) para Compiscript.

Envuelve el lexer/parser generados por ANTLR y el `SemanticAnalyzer` para
ofrecerle a un editor (VS Code, via `vscode-extension/`) dos cosas:

  1. Diagnosticos en vivo: errores de sintaxis y errores semanticos se
     publican como `textDocument/publishDiagnostics` cada vez que se
     abre, edita o guarda un archivo `.cps`.
  2. Un request custom `compiscript/parseTree` que devuelve el arbol
     sintactico del ultimo parseo exitoso como JSON, para pintarlo en un
     webview (ver `vscode-extension/src/parseTreePanel.js`).
  3. Un request custom `compiscript/tac` que compila el documento y
     devuelve su codigo de tres direcciones y la tabla de simbolos con
     direcciones (o los errores, si los hay).

No se agrega logica semantica aqui: este archivo es solo el adaptador
entre `analyzer.py` y el protocolo LSP.
"""

from antlr4 import InputStream, CommonTokenStream, ParseTreeWalker
from antlr4.error.ErrorListener import ErrorListener
from lsprotocol import types
from pygls.server import LanguageServer

from CompiscriptLexer import CompiscriptLexer
from CompiscriptParser import CompiscriptParser
from analyzer import SemanticAnalyzer
from compiler import compile_source, describe_memory
from tree_export import tree_to_dict

server = LanguageServer("compiscript-language-server", "v1")

# uri -> (parse tree, rule_names) del ultimo parseo, para atender
# `compiscript/parseTree` sin volver a leer el documento.
_last_tree: dict[str, tuple] = {}


class _CollectingErrorListener(ErrorListener):
    def __init__(self):
        super().__init__()
        self.errors = []

    def syntaxError(self, recognizer, offendingSymbol, line, column, msg, e):
        self.errors.append((line, column, msg))


def _diagnostic(line: int, column: int, message: str, severity) -> types.Diagnostic:
    line0 = max(line - 1, 0)
    return types.Diagnostic(
        range=types.Range(
            start=types.Position(line=line0, character=column),
            end=types.Position(line=line0, character=column + 1),
        ),
        message=message,
        severity=severity,
        source="compiscript",
    )


def _analyze(uri: str, source: str):
    lexer = CompiscriptLexer(InputStream(source))
    syntax_listener = _CollectingErrorListener()
    lexer.removeErrorListeners()
    lexer.addErrorListener(syntax_listener)

    tokens = CommonTokenStream(lexer)

    parser = CompiscriptParser(tokens)
    parser.removeErrorListeners()
    parser.addErrorListener(syntax_listener)

    tree = parser.program()
    _last_tree[uri] = (tree, parser.ruleNames)

    diagnostics = [
        _diagnostic(line, column, msg, types.DiagnosticSeverity.Error)
        for line, column, msg in syntax_listener.errors
    ]

    if not syntax_listener.errors:
        analyzer = SemanticAnalyzer()
        ParseTreeWalker.DEFAULT.walk(analyzer, tree)
        diagnostics.extend(
            _diagnostic(error.line, error.column, error.message, types.DiagnosticSeverity.Error)
            for error in analyzer.errors
        )

    return diagnostics


def _validate(ls: LanguageServer, uri: str, source: str):
    diagnostics = _analyze(uri, source)
    ls.publish_diagnostics(uri, diagnostics)


@server.feature(types.TEXT_DOCUMENT_DID_OPEN)
def did_open(ls: LanguageServer, params: types.DidOpenTextDocumentParams):
    _validate(ls, params.text_document.uri, params.text_document.text)


@server.feature(types.TEXT_DOCUMENT_DID_CHANGE)
def did_change(ls: LanguageServer, params: types.DidChangeTextDocumentParams):
    doc = ls.workspace.get_text_document(params.text_document.uri)
    _validate(ls, params.text_document.uri, doc.source)


@server.feature(types.TEXT_DOCUMENT_DID_SAVE)
def did_save(ls: LanguageServer, params: types.DidSaveTextDocumentParams):
    doc = ls.workspace.get_text_document(params.text_document.uri)
    _validate(ls, params.text_document.uri, doc.source)


@server.command("compiscript/parseTree")
def parse_tree(ls: LanguageServer, params):
    uri = params[0] if params else None
    entry = _last_tree.get(uri)
    if entry is None:
        return {"error": f"no hay arbol parseado para '{uri}'; abre o guarda el archivo primero"}

    tree, rule_names = entry
    return tree_to_dict(tree, rule_names)


@server.command("compiscript/tac")
def tac(ls: LanguageServer, params):
    uri = params[0] if params else None
    if uri is None:
        return {"error": "no se indico el documento a compilar"}

    result = compile_source(ls.workspace.get_text_document(uri).source)
    if result.errors:
        return {"errors": result.errors}
    return {"tac": str(result.tac), "symbols": describe_memory(result.analyzer, result.init_frames)}


if __name__ == "__main__":
    server.start_io()
