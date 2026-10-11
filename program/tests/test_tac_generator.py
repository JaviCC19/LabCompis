from antlr4 import InputStream, CommonTokenStream, ParseTreeWalker

from CompiscriptLexer import CompiscriptLexer
from CompiscriptParser import CompiscriptParser
from analyzer import SemanticAnalyzer
from tac_generator import TACGenerator


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
