from semantic import VariableSymbol, ClassSymbol, MemoryArea, INTEGER, FLOAT, STRING, BOOLEAN, size_of
from tests.test_analyzer_types import analyze


def test_sizes_by_type():
    assert size_of(INTEGER) == 4
    assert size_of(BOOLEAN) == 4
    assert size_of(FLOAT) == 8
    assert size_of(STRING) == 8
    assert size_of(None) == 8


def test_memory_area_assigns_increasing_offsets():
    area = MemoryArea("global", "global")
    a = VariableSymbol("a", INTEGER)
    b = VariableSymbol("b", FLOAT)
    c = VariableSymbol("c", BOOLEAN)
    for s in (a, b, c):
        area.allocate(s)

    assert [(s.offset, s.size) for s in (a, b, c)] == [(0, 4), (4, 8), (12, 4)]
    assert area.size == 16
    assert a.storage == "global"


def test_global_variables_live_in_global_segment():
    analyzer = analyze("""
        let a: integer = 1;
        let s: string = "x";
        const PI: float = 3.14;
    """)
    a, s, pi = (analyzer.table.global_scope.resolve(n) for n in ("a", "s", "PI"))

    assert (a.storage, a.offset) == ("global", 0)
    assert (s.storage, s.offset) == ("global", 4)
    assert (pi.storage, pi.offset) == ("global", 12)
    assert analyzer.globals.size == 20


def test_inferred_variable_gets_size_of_its_inferred_type():
    analyzer = analyze("let x = 2.5; let y = 1;")
    y = analyzer.table.global_scope.resolve("y")

    assert y.offset == 8


def test_function_activation_record_has_params_then_locals():
    analyzer = analyze("""
        function f(a: integer, b: string): integer {
            let x: integer = a;
            if (true) {
                let y: boolean = false;
            }
            return x;
        }
    """)
    f = analyzer.table.global_scope.resolve("f")
    frame = f.frame

    assert [(s.name, s.storage, s.offset) for s in frame.symbols] == [
        ("a", "param", 0),
        ("b", "param", 4),
        ("x", "local", 12),
        ("y", "local", 16),
    ]
    assert frame.size == 20
    assert f.label == "f"


def test_function_locals_do_not_use_global_segment():
    analyzer = analyze("""
        let g: integer = 0;
        function f() { let x: integer = 1; }
    """)

    assert analyzer.globals.size == 4


def test_method_record_starts_with_this_and_has_qualified_label():
    analyzer = analyze("""
        class A {
            let v: integer;
            function set(n: integer) { this.v = n; }
        }
    """)
    method = analyzer.table.global_scope.resolve("A").methods["set"]

    assert method.label == "A.set"
    assert [(s.name, s.offset) for s in method.frame.params] == [("this", 0), ("n", 8)]


def test_nested_function_label_is_qualified_by_outer_function():
    analyzer = analyze("""
        function outer(): integer {
            function inner(): integer { return 1; }
            return inner();
        }
    """)

    assert [f.label for f in analyzer.functions] == ["outer", "outer.inner"]


def test_class_layout_places_inherited_fields_first():
    analyzer = analyze("""
        class Animal { let nombre: string; let edad: integer; }
        class Perro : Animal { let raza: string; }
    """)
    animal: ClassSymbol = analyzer.table.global_scope.resolve("Animal")
    perro: ClassSymbol = analyzer.table.global_scope.resolve("Perro")

    # offset 0: puntero a la vtable
    assert [(f.name, f.offset) for f in animal.all_fields()] == [("nombre", 8), ("edad", 16)]
    assert animal.size == 20
    assert [(f.name, f.offset) for f in perro.all_fields()] == [("nombre", 8), ("edad", 16), ("raza", 20)]
    assert perro.size == 28


def test_vtable_keeps_slot_of_overridden_method():
    analyzer = analyze("""
        class Animal {
            function constructor() {}
            function hablar(): string { return "..."; }
            function comer() {}
        }
        class Perro : Animal {
            function hablar(): string { return "guau"; }
            function correr() {}
        }
    """)
    perro: ClassSymbol = analyzer.table.global_scope.resolve("Perro")

    assert [m.label for m in perro.vtable()] == ["Perro.hablar", "Animal.comer", "Perro.correr"]


def test_foreach_and_catch_variables_are_allocated():
    analyzer = analyze("""
        function f() {
            foreach (n in [1, 2]) { print(n); }
            try { print(1); } catch (e) { print(e); }
        }
    """)
    names = [s.name for s in analyzer.table.global_scope.resolve("f").frame.locals]

    assert names == ["n", "e"]
