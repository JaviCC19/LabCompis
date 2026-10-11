from antlr4 import InputStream, CommonTokenStream, ParseTreeWalker

from CompiscriptLexer import CompiscriptLexer
from CompiscriptParser import CompiscriptParser
from analyzer import SemanticAnalyzer


def analyze(source: str) -> SemanticAnalyzer:
    lexer = CompiscriptLexer(InputStream(source))
    tokens = CommonTokenStream(lexer)
    parser = CompiscriptParser(tokens)
    tree = parser.program()

    analyzer = SemanticAnalyzer()
    ParseTreeWalker.DEFAULT.walk(analyzer, tree)
    return analyzer


def messages(analyzer: SemanticAnalyzer):
    return [e.message for e in analyzer.errors]


def test_simple_variable_declaration_has_no_errors():
    analyzer = analyze("let x: integer = 5;")

    assert analyzer.errors == []


def test_using_undeclared_variable_is_reported():
    analyzer = analyze("print(y);")

    assert len(analyzer.errors) == 1
    assert "y" in messages(analyzer)[0]
    assert "no declarada" in messages(analyzer)[0]


def test_redeclaring_variable_in_same_scope_is_reported():
    analyzer = analyze("let x: integer = 1; let x: integer = 2;")

    assert len(analyzer.errors) == 1
    assert "'x'" in messages(analyzer)[0]


def test_shadowing_in_nested_block_is_allowed():
    analyzer = analyze("""
        let x: integer = 1;
        {
            let x: string = "hola";
            print(x);
        }
        print(x);
    """)

    assert analyzer.errors == []


def test_block_variable_not_visible_outside_block():
    analyzer = analyze("""
        {
            let x: integer = 1;
        }
        print(x);
    """)

    assert len(analyzer.errors) == 1
    assert "x" in messages(analyzer)[0]


def test_function_parameters_are_visible_in_its_body():
    analyzer = analyze("""
        function saludar(nombre: string): string {
            return nombre;
        }
    """)

    assert analyzer.errors == []


def test_function_parameters_not_visible_outside_function():
    analyzer = analyze("""
        function saludar(nombre: string): string {
            return nombre;
        }
        print(nombre);
    """)

    assert len(analyzer.errors) == 1
    assert "nombre" in messages(analyzer)[0]


def test_recursive_function_can_call_itself():
    analyzer = analyze("""
        function factorial(n: integer): integer {
            if (n <= 1) {
                return 1;
            }
            return n * factorial(n - 1);
        }
    """)

    assert analyzer.errors == []


def test_duplicate_function_declaration_is_reported():
    analyzer = analyze("""
        function f(): integer { return 1; }
        function f(): integer { return 2; }
    """)

    assert len(analyzer.errors) == 1
    assert "'f'" in messages(analyzer)[0]


def test_duplicate_parameter_names_are_reported():
    analyzer = analyze("""
        function f(a: integer, a: integer): integer { return a; }
    """)

    assert len(analyzer.errors) == 1
    assert "'a'" in messages(analyzer)[0]


def test_class_and_superclass_resolution():
    analyzer = analyze("""
        class Animal {
            let nombre: string;
        }
        class Perro : Animal {
            function ladrar(): string {
                return "guau";
            }
        }
        let p: Perro = new Perro();
    """)

    assert analyzer.errors == []


def test_undeclared_superclass_is_reported():
    analyzer = analyze("""
        class Perro : Animal {
        }
    """)

    assert len(analyzer.errors) == 1
    assert "Animal" in messages(analyzer)[0]


def test_instantiating_undeclared_class_is_reported():
    analyzer = analyze("let x = new Fantasma();")

    assert len(analyzer.errors) == 1
    assert "Fantasma" in messages(analyzer)[0]


def test_for_loop_counter_is_scoped_to_the_loop():
    analyzer = analyze("""
        for (let i: integer = 0; i < 3; i = i + 1) {
            print(i);
        }
        print(i);
    """)

    assert len(analyzer.errors) == 1
    assert "i" in messages(analyzer)[0]


def test_foreach_variable_is_visible_inside_the_body():
    analyzer = analyze("""
        let notas: integer[] = [1, 2, 3];
        foreach (nota in notas) {
            print(nota);
        }
    """)

    assert analyzer.errors == []


def test_catch_variable_is_visible_inside_catch_block():
    analyzer = analyze("""
        try {
            let x: integer = 1;
        } catch (err) {
            print(err);
        }
    """)

    assert analyzer.errors == []


def test_inherited_field_and_method_can_be_used_with_implicit_this():
    analyzer = analyze("""
        class A {
          let v: integer = 1;
          function get(): integer { return v; }
        }
        class B : A {
          function doble(): integer { return v + get(); }
          function poner(n: integer) { v = n; }
        }
        class C : B {
          function triple(): integer { return v * 3; }
        }
    """)

    assert analyzer.errors == []


def test_inherited_field_is_not_visible_outside_the_class():
    analyzer = analyze("""
        class A { let v: integer = 1; }
        class B : A { }
        v = 2;
    """)

    assert len(analyzer.errors) == 1
    assert analyzer.errors[0].line == 4
    assert "no declarada" in messages(analyzer)[0]


def test_inherited_constant_field_cannot_be_assigned_with_implicit_this():
    analyzer = analyze("""
        class A { const LIMITE: integer = 10; }
        class B : A { function f() { LIMITE = 3; } }
    """)

    assert len(analyzer.errors) == 1
    assert "constante" in messages(analyzer)[0]
