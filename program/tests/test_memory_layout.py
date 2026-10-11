from compiler import compile_source, describe_memory
from semantic import VariableSymbol, ClassSymbol, MemoryArea, INTEGER, FLOAT, STRING, BOOLEAN, size_of, HEADER_SIZE
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


# --- nivel de anidamiento, etiqueta del area y direccion de cada simbolo ------

def test_each_symbol_exposes_storage_offset_size_level_area_and_address():
    analyzer = analyze("""
        let g: integer = 0;
        class A { let v: integer; function set(n: integer) { this.v = n; } }
        function f(p: integer): integer { let x: integer = p; return x; }
    """)
    scope = analyzer.table.global_scope
    g = scope.resolve("g")
    f = scope.resolve("f")
    p, x = f.frame.symbols
    a = scope.resolve("A")
    v = a.fields["v"]
    this, n = a.methods["set"].frame.symbols

    assert (g.storage, g.offset, g.size, g.level, g.area, g.address) == ("global", 0, 4, 0, "global", "gp[+0]")
    assert (p.storage, p.level, p.area, p.address) == ("param", 1, "f", "fp[+0]")
    assert (x.storage, x.level, x.area, x.address) == ("local", 1, "f", "fp[+4]")
    assert (v.storage, v.area, v.address) == ("field", "A", "this[+8]")
    assert (this.address, n.address) == ("fp[+0]", "fp[+8]")
    assert (f.level, f.area, f.label) == (0, "global", "f")


def test_symbol_without_address_reports_none():
    symbol = VariableSymbol("suelto", INTEGER)

    assert symbol.address is None
    assert symbol.level is None


def test_nested_function_frame_has_level_and_access_link_to_enclosing_frame():
    analyzer = analyze("""
        function externa(): integer {
            let x: integer = 1;
            function media(): integer {
                function interna(): integer { return x; }
                return interna();
            }
            return media();
        }
    """)
    externa, media, interna = analyzer.functions

    assert [fn.frame.level for fn in (externa, media, interna)] == [1, 2, 3]
    assert externa.frame.enclosing is None
    assert media.frame.enclosing is externa.frame
    assert interna.frame.enclosing is media.frame
    assert (media.level, media.area) == (1, "externa")


def test_frame_sizes_split_params_locals_temps_and_header():
    result = compile_source("""
        function f(a: integer, b: float): integer {
            let r: integer = a * 2 + a * 3;
            return r;
        }
    """)
    frame = result.analyzer.functions[0].frame

    assert (frame.params_size, frame.locals_size, frame.temp_count) == (12, 4, 2)
    assert frame.data_size == 16 + 2 * 8
    assert frame.total_size == HEADER_SIZE + frame.data_size
    assert frame.temp_offset(1) == 16 and frame.temp_offset(2) == 24
    assert f"begin_func f, {frame.data_size}" in str(result.tac)


def test_main_frame_holds_only_temporaries_and_globals_live_in_gp():
    result = compile_source("""
        let a: integer = 1;
        let b: integer = (a + 1) * (a + 2);
        { let c: integer = b; }
    """)
    analyzer = result.analyzer

    assert analyzer.main_frame.symbols == []
    assert analyzer.main_frame.temp_count == 2
    assert str(result.tac).splitlines()[0] == "begin_func main, 16"
    assert [s.address for s in analyzer.globals.symbols] == ["gp[+0]", "gp[+4]", "gp[+8]"]


def test_memory_report_shows_main_frame_header_and_temporaries():
    result = compile_source("let a: integer = 1 + 2;")
    report = describe_memory(result.analyzer, result.init_frames)

    assert "== registro de activacion de main (nivel 1)" in report
    assert "cabecera fp[-16] enlace de control: fp del llamador" in report
    assert "temp   t1           temporal     offset=0    size=8  nivel=1  dir=fp[+0]" in report


def test_class_init_frame_is_reported():
    result = compile_source("class A { let v: integer = 3; } let a: A = new A();")
    report = describe_memory(result.analyzer, result.init_frames)

    assert "== registro de activacion de A.__init (nivel 1)" in report
    assert "field  v            integer      offset=8    size=4   dir=this[+8]  (de A)" in report


def test_semantic_error_leaves_no_tac_and_reports_line_and_column():
    result = compile_source("function f(): integer {\n  let x: integer = 1;\n  return y;\n}")

    assert result.tac is None
    assert result.errors == ["Error semantico (linea 3:9): variable 'y' no declarada"]
