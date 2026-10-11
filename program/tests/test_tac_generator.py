from antlr4 import InputStream, CommonTokenStream, ParseTreeWalker

from CompiscriptLexer import CompiscriptLexer
from CompiscriptParser import CompiscriptParser
from analyzer import SemanticAnalyzer
from tac_generator import TACGenerator
from ir import tac
from ir.tac import Quad


def generate(source: str):
    parser = CompiscriptParser(CommonTokenStream(CompiscriptLexer(InputStream(source))))
    tree = parser.program()
    analyzer = SemanticAnalyzer()
    ParseTreeWalker.DEFAULT.walk(analyzer, tree)
    assert analyzer.errors == []
    generator = TACGenerator(analyzer)
    return generator.generate(tree), analyzer


def lines(source: str):
    program, _ = generate(source)
    return [line.strip() for line in program.lines()]


def main_body(source: str):
    """Instrucciones de `main`, sin begin_func/end_func."""
    result = lines(source)
    start = result.index(next(l for l in result if l.startswith("begin_func main")))
    return result[start + 1:-1]


def function_body(source: str, label: str):
    result = lines(source)
    start = next(i for i, l in enumerate(result) if l.startswith(f"begin_func {label},"))
    end = result.index(f"end_func {label}")
    return result[start + 1:end]


# --- expresiones y temporales ------------------------------------------------

def test_declaration_with_literal_is_a_copy():
    assert main_body("let a: integer = 5;") == ["a = 5"]


def test_declaration_without_initializer_emits_nothing():
    assert main_body("let a: integer;") == []


def test_arithmetic_respects_precedence_and_recycles_temps():
    body = main_body("""
        let a: integer = 1;
        let b: integer = 2;
        let c: integer = (a * b) + (a - b) * 4;
    """)

    assert body[2:] == [
        "t1 = a * b",
        "t2 = a - b",
        "t2 = t2 * 4",
        "t1 = t1 + t2",
        "c = t1",
    ]


def test_temps_restart_after_each_statement():
    body = main_body("""
        let a: integer = 1;
        let x: integer = a + 1;
        let y: integer = a + 2;
    """)

    assert body[1:] == ["t1 = a + 1", "x = t1", "t1 = a + 2", "y = t1"]


def test_unary_operators():
    body = main_body("""
        let a: integer = 1;
        let b: boolean = true;
        let c: integer = -a;
        let d: boolean = !b;
    """)

    assert body[2:] == ["t1 = -a", "c = t1", "t1 = !b", "d = t1"]


def test_string_concatenation_uses_plus():
    body = main_body('let s: string = "a" + "b";')

    assert body == ['t1 = "a" + "b"', "s = t1"]


def test_logical_and_short_circuits():
    body = main_body("""
        let a: boolean = true;
        let b: boolean = false;
        let c: boolean = a && b;
    """)

    assert body[2:] == ["t1 = a", "ifFalse t1 goto L1", "t1 = b", "L1:", "c = t1"]


def test_logical_or_short_circuits():
    body = main_body("""
        let a: boolean = true;
        let b: boolean = false;
        let c: boolean = a || b;
    """)

    assert body[2:] == ["t1 = a", "if t1 goto L1", "t1 = b", "L1:", "c = t1"]


def test_ternary_uses_both_branches_into_one_temp():
    body = main_body("""
        let a: integer = 1;
        let m: integer = a > 0 ? a : 0;
    """)

    assert body[1:] == [
        "t1 = a > 0",
        "ifFalse t1 goto L1",
        "t1 = a",
        "goto L2",
        "L1:",
        "t1 = 0",
        "L2:",
        "m = t1",
    ]


def test_chained_assignment():
    body = main_body("""
        let a: integer = 0;
        let b: integer = 0;
        a = b = 7;
    """)

    assert body[2:] == ["b = 7", "a = b"]


def test_print():
    assert main_body("print(1 + 2);") == ["t1 = 1 + 2", "print t1"]


def test_shadowed_variable_gets_unique_name():
    body = main_body("""
        let x: integer = 1;
        {
            let x: integer = 2;
            print(x);
        }
        print(x);
    """)

    assert body == ["x = 1", "x_1 = 2", "print x_1", "print x"]


# --- funciones ----------------------------------------------------------------

def test_function_is_emitted_with_frame_size_and_return():
    source = """
        function suma(x: integer, y: integer): integer {
            return x + y;
        }
    """
    result = lines(source)

    # params (4 + 4) + un temporal (8)
    assert result[0] == "begin_func suma, 16"
    assert function_body(source, "suma") == ["t1 = x + y", "return t1"]


def test_void_function_gets_implicit_return():
    source = "function f() { print(1); }"

    assert function_body(source, "f") == ["print 1", "return"]


def test_call_evaluates_arguments_before_params():
    body = main_body("""
        function suma(x: integer, y: integer): integer { return x + y; }
        let a: integer = 1;
        print(suma(a, suma(2, 3)));
    """)

    assert body[1:] == [
        "param 2",
        "param 3",
        "t1 = call suma, 2",
        "param a",
        "param t1",
        "t1 = call suma, 2",
        "print t1",
    ]


def test_void_call_as_statement_has_no_result():
    body = main_body("""
        function f() { print(1); }
        f();
    """)

    assert body == ["call f, 0"]


def test_recursive_call():
    source = """
        function fact(n: integer): integer {
            if (n <= 1) { return 1; }
            return n * fact(n - 1);
        }
    """
    body = function_body(source, "fact")

    assert body[-5:] == ["t1 = n - 1", "param t1", "t1 = call fact, 1", "t1 = n * t1", "return t1"]


def test_functions_are_emitted_before_main():
    result = lines("""
        print(1);
        function f() { print(2); }
    """)

    assert result.index("begin_func f, 0") < result.index("begin_func main, 0")


# --- control de flujo ---------------------------------------------------------

def test_if_without_else():
    body = main_body("""
        let a: integer = 1;
        if (a > 0) { print(a); }
    """)

    assert body[1:] == ["t1 = a > 0", "ifFalse t1 goto L1", "print a", "L1:"]


def test_if_with_else():
    body = main_body("""
        let a: integer = 1;
        if (a > 0) { print(1); } else { print(2); }
    """)

    assert body[1:] == [
        "t1 = a > 0",
        "ifFalse t1 goto L2",
        "print 1",
        "goto L1",
        "L2:",
        "print 2",
        "L1:",
    ]


def test_while_loop():
    body = main_body("""
        let i: integer = 0;
        while (i < 3) { i = i + 1; }
    """)

    assert body[1:] == [
        "L1:",
        "t1 = i < 3",
        "ifFalse t1 goto L2",
        "t1 = i + 1",
        "i = t1",
        "goto L1",
        "L2:",
    ]


def test_do_while_runs_body_before_condition():
    body = main_body("""
        let i: integer = 3;
        do { i = i - 1; } while (i > 0);
    """)

    assert body[1:] == [
        "L1:",
        "t1 = i - 1",
        "i = t1",
        "L2:",
        "t1 = i > 0",
        "if t1 goto L1",
        "L3:",
    ]


def test_for_loop_with_update_and_continue():
    body = main_body("""
        for (let j: integer = 0; j < 3; j = j + 1) {
            if (j == 1) { continue; }
            print(j);
        }
    """)

    assert body == [
        "j = 0",
        "L1:",
        "t1 = j < 3",
        "ifFalse t1 goto L3",
        "t1 = j == 1",
        "ifFalse t1 goto L4",
        "goto L2",
        "L4:",
        "print j",
        "L2:",
        "t1 = j + 1",
        "j = t1",
        "goto L1",
        "L3:",
    ]


def test_for_without_condition_loops_until_break():
    body = main_body("""
        for (let j: integer = 0; ; j = j + 1) { break; }
    """)

    assert body == ["j = 0", "L1:", "goto L3", "L2:", "t1 = j + 1", "j = t1", "goto L1", "L3:"]


def test_break_jumps_out_of_innermost_loop_only():
    body = main_body("""
        let i: integer = 0;
        while (i < 2) {
            while (true) { break; }
            i = i + 1;
        }
    """)

    assert "goto L4" in body  # break del while interno -> fin del interno (L4), no del externo (L2)
    assert body.index("goto L4") < body.index("L4:")


def test_no_unreachable_goto_after_break():
    body = main_body("""
        let i: integer = 0;
        while (true) {
            if (i == 1) { break; } else { i = 1; }
        }
    """)

    assert body.count("goto L2") == 1


def test_switch_compares_cases_in_order_and_falls_through():
    body = main_body("""
        let i: integer = 2;
        switch (i) {
            case 1: print("uno"); break;
            case 2: print("dos");
            default: print("otro");
        }
    """)

    assert body[1:] == [
        "t1 = i == 1",
        "if t1 goto L1",
        "t1 = i == 2",
        "if t1 goto L2",
        "goto L3",
        "L1:",
        'print "uno"',
        "goto L4",
        "L2:",
        'print "dos"',
        "L3:",
        'print "otro"',
        "L4:",
    ]


def test_continue_inside_switch_targets_enclosing_loop():
    body = main_body("""
        let i: integer = 0;
        while (i < 3) {
            switch (i) { case 0: continue; }
            i = i + 1;
        }
    """)

    assert "goto L1" in body[body.index("L3:"):]


# --- clases y objetos ---------------------------------------------------------

ANIMALES = """
    class Animal {
        let nombre: string;
        function constructor(nombre: string) { this.nombre = nombre; }
        function hablar(): string { return this.nombre + " hace ruido."; }
    }
    class Perro : Animal {
        function hablar(): string { return this.nombre + " ladra."; }
    }
"""


def test_method_receives_this_and_accesses_fields_through_it():
    assert function_body(ANIMALES, "Animal.constructor") == ["this.nombre = nombre", "return"]
    assert function_body(ANIMALES, "Perro.hablar") == [
        "t1 = this.nombre",
        't1 = t1 + " ladra."',
        "return t1",
    ]


def test_new_allocates_object_and_calls_inherited_constructor():
    body = main_body(ANIMALES + 'let p: Perro = new Perro("Toby");')

    # vtable (8) + nombre (8)
    assert body == ["t1 = new Perro, 16", "param t1", 'param "Toby"', "call Animal.constructor, 2", "p = t1"]


def test_call_on_class_without_overrides_is_direct():
    body = main_body(ANIMALES + """
        let p: Perro = new Perro("Toby");
        print(p.hablar());
    """)

    assert body[-3:] == ["param p", "t1 = call Perro.hablar, 1", "print t1"]


def test_call_on_overridden_method_goes_through_vtable():
    body = main_body(ANIMALES + """
        let a: Animal = new Perro("Toby");
        print(a.hablar());
    """)

    assert body[-5:] == [
        "t1 = a.vtable",
        "t1 = t1[0]",
        "param a",
        "t1 = call t1, 1",
        "print t1",
    ]


def test_field_initializers_run_in_init_called_by_new():
    source = """
        class Contador {
            let valor: integer = 10;
            function constructor() {}
        }
        let c: Contador = new Contador();
    """

    assert function_body(source, "Contador.__init") == ["this.valor = 10", "return"]
    assert main_body(source) == [
        "t1 = new Contador, 12",
        "param t1",
        "call Contador.__init, 1",
        "param t1",
        "call Contador.constructor, 1",
        "c = t1",
    ]


def test_subclass_init_calls_superclass_init_first():
    source = """
        class A { let x: integer = 1; }
        class B : A { let y: integer = 2; }
    """

    assert function_body(source, "B.__init") == ["param this", "call A.__init, 1", "this.y = 2", "return"]


def test_property_assignment_and_access_on_object():
    body = main_body(ANIMALES + """
        let p: Perro = new Perro("Toby");
        p.nombre = "Rex";
        print(p.nombre);
    """)

    assert body[-3:] == ['p.nombre = "Rex"', "t1 = p.nombre", "print t1"]


def test_method_called_without_this_inside_class():
    source = """
        class A {
            function uno(): integer { return 1; }
            function dos(): integer { return uno() + 1; }
        }
    """

    assert function_body(source, "A.dos") == [
        "param this",
        "t1 = call A.uno, 1",
        "t1 = t1 + 1",
        "return t1",
    ]


# --- arreglos, foreach y try/catch -------------------------------------------

def test_array_literal_allocates_and_stores_each_element():
    assert main_body("let a: integer[] = [1, 2];") == [
        "t1 = new_array 2",
        "t1[0] = 1",
        "t1[1] = 2",
        "a = t1",
    ]


def test_index_read_and_write():
    body = main_body("""
        let a: integer[] = [1, 2];
        let i: integer = 0;
        a[i + 1] = a[i] * 2;
    """)

    assert body[5:] == [
        "t1 = i + 1",
        # lectura a[i]: 0 <= i < len a
        "t2 = i < 0",
        "if t2 goto L1",
        "t2 = len a",
        "t2 = i >= t2",
        "if t2 goto L1",
        "t2 = a[i]",
        "t2 = t2 * 2",
        # escritura a[i + 1]
        "t3 = t1 < 0",
        "if t3 goto L1",
        "t3 = len a",
        "t3 = t1 >= t3",
        "if t3 goto L1",
        "a[t1] = t2",
        "return",
        "L1:",
        'throw "indice fuera de rango"',
    ]


def test_nested_array_access():
    body = main_body("""
        let m: integer[][] = [[1, 2], [3, 4]];
        print(m[1][0]);
    """)

    # con indice literal no negativo solo se verifica el limite superior
    assert body[-12:] == [
        "t1 = len m",
        "t1 = 1 >= t1",
        "if t1 goto L1",
        "t1 = m[1]",
        "t2 = len t1",
        "t2 = 0 >= t2",
        "if t2 goto L1",
        "t1 = t1[0]",
        "print t1",
        "return",
        "L1:",
        'throw "indice fuera de rango"',
    ]


def test_foreach_iterates_by_index_up_to_length():
    body = main_body("""
        let a: integer[] = [1];
        foreach (x in a) { print(x); }
    """)

    assert body[3:] == [
        "t1 = 0",
        "t2 = len a",
        "L1:",
        "t3 = t1 < t2",
        "ifFalse t3 goto L3",
        "x = a[t1]",
        "print x",
        "L2:",
        "t1 = t1 + 1",
        "goto L1",
        "L3:",
    ]


def test_try_catch_registers_handler_and_binds_error():
    body = main_body("""
        let a: integer[] = [1];
        try { print(a[5]); } catch (err) { print(err); }
    """)

    assert body[3:] == [
        "try_begin L1",
        "t1 = len a",
        "t1 = 5 >= t1",
        "if t1 goto L3",  # fuera de rango: el stub lanza el error al manejador activo (L1)
        "t1 = a[5]",
        "print t1",
        "try_end",
        "goto L2",
        "L1:",
        "err = catch",
        "print err",
        "L2:",
        "return",
        "L3:",
        'throw "indice fuera de rango"',
    ]


def test_inherited_field_with_implicit_this_reads_through_this():
    body = function_body("""
        class A { let v: integer = 1; }
        class B : A { function get(): integer { return v + 10; } }
    """, "B.get")

    assert body == ["t1 = this.v", "t1 = t1 + 10", "return t1"]


def test_nested_functions_in_different_methods_get_unique_labels():
    source = """
        class A {
          let v: integer = 1;
          function f(): integer {
            function aux(): integer { return v; }
            return aux();
          }
          function g(): integer {
            function aux(): integer { return 2; }
            return aux();
          }
        }
        class B : A { function aux(): integer { return 3; } }
    """
    result = lines(source)

    assert "begin_func A.f.aux, 16" in result
    assert "begin_func A.g.aux, 8" in result
    # aux no es un metodo: se llama directo (no por la vtable) y recibe el 'this' del metodo
    assert function_body(source, "A.f") == ["param this", "link fp", "t1 = call A.f.aux, 1", "return t1"]
    assert function_body(source, "A.f.aux") == ["t1 = this.v", "return t1"]


def test_method_with_same_name_as_nested_function_is_still_virtual():
    source = """
        class A { function aux(): integer { return 1; } function h(): integer { return aux(); } }
        class B : A { function aux(): integer { return 3; } }
    """

    assert function_body(source, "A.h")[:2] == ["t1 = this.vtable", "t1 = t1[0]"]


# --- funciones anidadas: enlace de acceso explicito ---------------------------

CLOSURE = """
    function externa(a: integer): integer {
      let x: integer = a * 2;
      function interna(b: integer): integer {
        x = x + b;
        return x + a;
      }
      return interna(1);
    }
"""


def test_outer_variable_is_read_and_written_through_the_access_link():
    assert function_body(CLOSURE, "externa.interna") == [
        "t1 = access_link 1",
        "t1 = t1->x",
        "t1 = t1 + b",
        "t2 = access_link 1",
        "t2->x = t1",
        "t1 = access_link 1",
        "t1 = t1->x",
        "t2 = access_link 1",
        "t2 = t2->a",
        "t1 = t1 + t2",
        "return t1",
    ]


def test_call_to_nested_function_passes_current_fp_as_access_link():
    assert function_body(CLOSURE, "externa") == [
        "t1 = a * 2",
        "x = t1",
        "param 1",
        "link fp",
        "t1 = call externa.interna, 1",
        "return t1",
    ]


def test_outer_variable_quads_carry_the_offset_in_the_outer_frame():
    program, analyzer = generate(CLOSURE)
    loads = [q for q in program if q.op == tac.LOAD_OUTER]
    x = analyzer.functions[0].frame.locals[0]

    assert {(q.arg2, q.offset) for q in loads} == {("x", x.offset), ("a", 0)}


def test_access_link_climbs_several_levels_and_recursion_reuses_enclosing_link():
    source = """
        function a(): integer {
          let x: integer = 1;
          function h(): integer { return x; }
          function b(n: integer): integer {
            function c(): integer { return x + n; }
            if (n <= 0) { return c(); }
            return b(n - 1) + h();
          }
          return b(2);
        }
    """

    assert function_body(source, "a.b.c")[:4] == [
        "t1 = access_link 2", "t1 = t1->x", "t2 = access_link 1", "t2 = t2->n",
    ]
    body = function_body(source, "a.b")
    assert body[2:4] == ["link fp", "t1 = call a.b.c, 0"]
    # b se llama a si misma y a su hermana h: ambas estan declaradas en 'a', un nivel arriba
    assert body[8:11] == ["t1 = access_link 1", "link t1", "t1 = call a.b, 1"]
    assert body[11:14] == ["t2 = access_link 1", "link t2", "t2 = call a.h, 0"]


def test_top_level_functions_and_globals_do_not_use_access_link():
    result = lines("""
        let g: integer = 1;
        function f(): integer { return g; }
        function k(): integer { return f(); }
    """)

    assert not any("access_link" in l or l.startswith("link") for l in result)


# --- try_end en todos los caminos de salida -----------------------------------

def test_return_inside_try_removes_the_handler_first():
    body = function_body("""
        function f(n: integer): integer {
          try { return n * 2; } catch (e) { print(e); }
          return 0;
        }
    """, "f")

    assert body[:4] == ["try_begin L1", "t1 = n * 2", "try_end", "return t1"]
    # el final normal del try es inalcanzable (termina en return): no se emite
    assert body[4] == "L1:"


def test_return_inside_nested_try_removes_every_open_handler():
    body = function_body("""
        function f(): integer {
          try {
            try { return 1; } catch (a) { print(a); }
          } catch (b) { print(b); }
          return 0;
        }
    """, "f")

    assert body[:5] == ["try_begin L1", "try_begin L3", "try_end", "try_end", "return 1"]


def test_return_inside_catch_only_closes_the_enclosing_try():
    body = function_body("""
        function f(): integer {
          try {
            try { print(1); } catch (a) { return 2; }
          } catch (b) { print(b); }
          return 0;
        }
    """, "f")
    catch_start = body.index("a = catch")

    assert body[catch_start + 1:catch_start + 3] == ["try_end", "return 2"]


def test_break_and_continue_inside_try_remove_the_handler():
    body = main_body("""
        let i: integer = 0;
        while (i < 10) {
          try {
            i = i + 1;
            if (i == 2) { continue; }
            if (i == 5) { break; }
          } catch (e) { print(e); }
        }
    """)
    cont = body.index("goto L1")
    brk = body.index("goto L2")

    assert body[cont - 1] == "try_end"
    assert body[brk - 1] == "try_end"


def test_break_of_loop_fully_inside_try_does_not_remove_the_handler():
    body = main_body("""
        try {
          while (true) { break; }
        } catch (e) { print(e); }
    """)

    assert body.count("try_end") == 1
    assert body[:5] == ["try_begin L1", "L3:", "ifFalse true goto L4", "goto L4", "L4:"]


def test_break_out_of_switch_from_inside_try_removes_the_handler():
    body = main_body("""
        let x: integer = 1;
        switch (x) {
          case 1:
            try { break; } catch (e) { print(e); }
          default:
            print(0);
        }
    """)
    start = next(i for i, l in enumerate(body) if l.startswith("try_begin"))

    # break sale del switch (L3) quitando antes el manejador del try
    assert body[start + 1:start + 3] == ["try_end", "goto L3"]
    assert body[-1] == "L3:"


def test_void_return_inside_try_also_removes_handler():
    assert function_body("""
        function f() { try { return; } catch (e) { print(e); } }
    """, "f")[:3] == ["try_begin L1", "try_end", "return"]


# --- chequeos explicitos en tiempo de ejecucion y throw -----------------------

def test_out_of_range_access_inside_function_throws_to_active_handler():
    body = function_body("""
        function leer(a: integer[], i: integer): integer {
          try { return a[i]; } catch (e) { return -1; }
        }
    """, "leer")

    assert body[:8] == [
        "try_begin L1",
        "t1 = i < 0",
        "if t1 goto L3",
        "t1 = len a",
        "t1 = i >= t1",
        "if t1 goto L3",
        "t1 = a[i]",
        "try_end",
    ]
    # el stub queda despues del ultimo return, fuera del flujo normal
    assert body[-2:] == ["L3:", 'throw "indice fuera de rango"']


def test_function_without_array_access_has_no_error_stub():
    assert "throw" not in "\n".join(lines("function f(x: integer): integer { return x + 1; }"))


def test_foreach_and_array_literal_do_not_need_bounds_checks():
    result = lines("""
        let a: integer[] = [1, 2, 3];
        foreach (x in a) { print(x); }
    """)

    assert not any(l.startswith("throw") or " >= " in l for l in result)


def test_matrix_write_checks_both_dimensions():
    body = main_body("""
        let m: integer[][] = [[1, 2], [3, 4]];
        let i: integer = 1;
        let j: integer = 0;
        m[i][j] = 9;
    """)
    store = body.index("t1[j] = 9")

    assert body[store - 11:store] == [
        "t1 = i < 0", "if t1 goto L1", "t1 = len m", "t1 = i >= t1", "if t1 goto L1",
        "t1 = m[i]",
        "t2 = j < 0", "if t2 goto L1", "t2 = len t1", "t2 = j >= t2", "if t2 goto L1",
    ]


def test_division_and_modulo_by_variable_check_for_zero():
    body = main_body("""
        let a: integer = 10;
        let b: integer = 2;
        let c: integer = a / b % b;
    """)

    assert body[2:] == [
        "t1 = b == 0",
        "if t1 goto L1",
        "t1 = a / b",
        "t2 = b == 0",
        "if t2 goto L1",
        "t1 = t1 % b",
        "c = t1",
        "return",
        "L1:",
        'throw "division entre cero"',
    ]


def test_division_by_nonzero_literal_is_not_checked():
    body = main_body("let a: integer = 10; let c: integer = a / 2; let d: float = 1.0 / 0.5;")

    assert "throw" not in "\n".join(body)


def test_division_by_literal_zero_is_checked():
    body = main_body("let a: integer = 10; let c: integer = a / 0;")

    assert body[1:3] == ["t1 = 0 == 0", "if t1 goto L1"]
    assert body[-1] == 'throw "division entre cero"'


def test_each_error_kind_has_a_single_stub_per_function():
    body = main_body("""
        let a: integer[] = [1];
        let i: integer = 0;
        print(a[i] / i);
        print(a[i] % i);
    """)

    assert body.count('throw "indice fuera de rango"') == 1
    assert body.count('throw "division entre cero"') == 1


def test_throw_quad_renders():
    assert str(Quad(tac.THROW, '"x"')) == 'throw "x"'
