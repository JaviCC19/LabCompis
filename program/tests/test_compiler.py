from compiler import compile_source, describe_memory


def test_valid_program_produces_tac():
    result = compile_source("let a: integer = 1 + 2; print(a);")

    assert result.errors == []
    assert str(result.tac).splitlines() == [
        "begin_func main, 8",
        "    t1 = 1 + 2",
        "    a = t1",
        "    print a",
        "end_func main",
    ]


def test_syntax_error_stops_before_semantic_analysis_and_tac():
    result = compile_source("let a: integer = ;")

    assert result.syntax_errors
    assert result.analyzer is None
    assert result.tac is None


def test_semantic_error_prevents_tac_generation():
    result = compile_source('let a: integer = "hola";')

    assert len(result.errors) == 1
    assert result.tac is None


def test_undeclared_variable_prevents_tac_generation():
    result = compile_source("print(x);")

    assert "no declarada" in result.errors[0]
    assert result.tac is None


def test_memory_report_lists_globals_classes_and_frames():
    result = compile_source("""
        let g: integer = 0;
        class A { let v: integer; function get(): integer { return this.v; } }
        function f(n: integer): integer { let r: integer = n * 2; return r; }
    """)
    report = describe_memory(result.analyzer)

    assert "global g            integer      offset=0    size=4" in report
    assert "== clase A (12 bytes por instancia)" in report
    assert "vtable: [0] A.get" in report
    assert "== registro de activacion de f (8 bytes de variables + 1 temporales = 16 bytes)" in report
    assert "local  r            integer      offset=4    size=4" in report
