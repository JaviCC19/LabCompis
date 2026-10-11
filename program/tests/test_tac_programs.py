"""TAC de programas completos por construccion, y programas invalidos que no generan TAC."""

import pytest

from compiler import compile_source


def compile_ok(source: str):
    result = compile_source(source)
    assert result.errors == []
    return result


def body(source: str, label: str = "main"):
    out = [l.strip() for l in compile_ok(source).tac.lines()]
    start = next(i for i, l in enumerate(out) if l.startswith(f"begin_func {label},"))
    return out[start + 1:out.index(f"end_func {label}")]


# --- temporales -----------------------------------------------------------------

TEMPS_ACROSS_CALLS = """
    function f(x: integer): integer { return x + 1; }
    function g(x: integer): integer { return x * 2; }
    function h(a: integer, b: integer, c: integer): integer {
        return a * f(b) + g(c);
    }
"""


def test_temporary_stays_live_across_a_later_call_and_counts_in_the_frame():
    # t1 (a * f(b)) sigue vivo mientras se llama a g: g recibe t2, y luego t1 + t2
    assert body(TEMPS_ACROSS_CALLS, "h") == [
        "param b",
        "t1 = call f, 1",
        "t1 = a * t1",
        "param c",
        "t2 = call g, 1",
        "t1 = t1 + t2",
        "return t1",
    ]
    result = compile_ok(TEMPS_ACROSS_CALLS)
    h = result.analyzer.functions[2]
    assert h.frame.temp_count == 2  # max_live de h
    assert "begin_func h, 28" in str(result.tac)  # 12 bytes de params + 2 temporales x 8


def test_temporaries_are_recycled_after_the_call_consumes_them():
    out = body("""
        function f(x: integer, y: integer): integer { return x + y; }
        let r: integer = f(1 + 2, 3 * 4) + f(5 - 6, 7 / 7);
    """)

    assert out == [
        "t1 = 1 + 2",
        "t2 = 3 * 4",
        "param t1",
        "param t2",
        "t1 = call f, 2",
        "t2 = 5 - 6",
        "t3 = 7 / 7",
        "param t2",
        "param t3",
        "t2 = call f, 2",
        "t1 = t1 + t2",
        "r = t1",
    ]


def test_max_live_temporaries_define_main_frame_size():
    result = compile_ok("let a: integer = 1; let b: integer = (a + 1) * ((a + 2) * (a + 3));")

    assert result.analyzer.main_frame.temp_count == 3
    assert str(result.tac).splitlines()[0] == "begin_func main, 24"


# --- declaraciones, llamadas y switch -----------------------------------------

def test_const_declaration_is_a_copy_of_its_value():
    assert body("const PI: integer = 314; let r: integer = PI * 2;") == [
        "PI = 314", "t1 = PI * 2", "r = t1",
    ]


def test_nested_calls_as_arguments_evaluate_inner_calls_first():
    assert body("""
        function suma(p: integer, q: integer): integer { return p + q; }
        let r: integer = suma(suma(1, 2), suma(3, 4));
    """) == [
        "param 1", "param 2", "t1 = call suma, 2",
        "param 3", "param 4", "t2 = call suma, 2",
        "param t1", "param t2", "t1 = call suma, 2",
        "r = t1",
    ]


def test_switch_without_default_jumps_to_end_when_nothing_matches():
    assert body("""
        let x: integer = 2;
        switch (x) { case 1: print("uno"); break; case 2: print("dos"); }
    """) == [
        "x = 2",
        "t1 = x == 1", "if t1 goto L1",
        "t1 = x == 2", "if t1 goto L2",
        "goto L3",
        "L1:", 'print "uno"', "goto L3",
        "L2:", 'print "dos"',
        "L3:",
    ]


def test_switch_with_only_default_always_runs_it():
    assert body('let x: integer = 2; switch (x) { default: print("d"); }') == [
        "x = 2", "goto L1", "L1:", 'print "d"', "L2:",
    ]


# --- clases --------------------------------------------------------------------

THREE_LEVELS = """
    class A {
        let v: integer = 1;
        function constructor(v: integer) { this.v = v; }
        function get(): integer { return v; }
        function doble(): integer { return get() * 2; }
    }
    class B : A { function get(): integer { return v + 10; } }
    class C : B { let w: integer = 5; }
    let c: C = new C(3);
    print(c.doble());
"""


def test_three_level_inheritance_uses_grandparent_constructor_and_chained_inits():
    assert body(THREE_LEVELS, "C.__init") == ["param this", "call B.__init, 1", "this.w = 5", "return"]
    assert body(THREE_LEVELS, "B.__init") == ["param this", "call A.__init, 1", "return"]
    assert body(THREE_LEVELS)[:6] == [
        "t1 = new C, 16",
        "param t1", "call C.__init, 1",
        "param t1", "param 3", "call A.constructor, 2",
    ]


def test_implicit_this_call_inside_base_method_is_dispatched_dynamically():
    # A.doble llama a get() sin 'this.': como B lo sobrescribe, va por la vtable
    assert body(THREE_LEVELS, "A.doble") == [
        "t1 = this.vtable",
        "t1 = t1[0]",
        "param this",
        "t1 = call t1, 1",
        "t1 = t1 * 2",
        "return t1",
    ]


def test_object_layout_of_grandchild_keeps_inherited_offsets():
    result = compile_ok(THREE_LEVELS)
    c = result.analyzer.table.global_scope.resolve("C")

    assert [(f.name, f.offset) for f in c.all_fields()] == [("v", 8), ("w", 12)]
    assert [m.label for m in c.vtable()] == ["B.get", "A.doble"]


# --- arreglos ------------------------------------------------------------------

def test_foreach_over_matrix_nests_two_index_loops():
    out = body("""
        let m: integer[][] = [[1, 2], [3, 4]];
        foreach (fila in m) { foreach (x in fila) { print(x); } }
    """)
    start = out.index("m = t1") + 1

    assert out[start:] == [
        "t1 = 0", "t2 = len m",
        "L1:", "t3 = t1 < t2", "ifFalse t3 goto L3",
        "fila = m[t1]",
        "t3 = 0", "t4 = len fila",
        "L4:", "t5 = t3 < t4", "ifFalse t5 goto L6",
        "x = fila[t3]",
        "print x",
        "L5:", "t3 = t3 + 1", "goto L4",
        "L6:",
        "L2:", "t1 = t1 + 1", "goto L1",
        "L3:",
    ]


# --- programas invalidos: no generan TAC y reportan linea:columna --------------

INVALID = [
    ('let a: integer = "x";', "1:0", "no se puede inicializar"),
    ("print(y);", "1:6", "no declarada"),
    ("break;", "1:0", "'break' solo puede usarse"),
    ("let x: integer = 1;\nreturn x;", "2:0", "'return' fuera de una funcion"),
    ("const K: integer = 1;\nK = 2;", "2:0", "es una constante"),
    ("function f(a: integer): integer { return a; }\nlet r: integer = f(1, 2);", "2:18", "argumento"),
    ("let a: integer[] = [1];\nprint(a[\"0\"]);", "2:7", "el indice debe ser 'integer'"),
    ("if (1) { print(1); }", "1:4", "debe ser boolean"),
    ("print(this);", "1:6", "'this' solo puede usarse dentro de una clase"),
    ("foreach (x in 5) { print(x); }", "1:0", "'foreach' espera un arreglo"),
    ("class A { }\nlet a: A = new A();\nprint(a.nada);", "3:7", "no es un miembro"),
    ("function f(): integer {\n  function g(): integer { return z; }\n  return g();\n}", "2:33", "no declarada"),
    ("function f(): integer { let x: integer = 1; }", "1:0", "no tiene ninguna sentencia 'return'"),
    ("while (true) {\n  break;\n  print(1);\n}", "3:2", "codigo inalcanzable"),
]


@pytest.mark.parametrize("source, position, fragment", INVALID)
def test_invalid_program_does_not_generate_tac_and_reports_position(source, position, fragment):
    result = compile_source(source)

    assert result.tac is None
    assert result.errors, "se esperaba al menos un error"
    first = result.errors[0]
    assert f"(linea {position})" in first
    assert fragment in first


def test_syntax_error_reports_position_and_does_not_generate_tac():
    result = compile_source("let x: integer = 1\nprint(x);")

    assert result.tac is None
    assert result.errors[0].startswith("Error de sintaxis (linea 2:0)")
