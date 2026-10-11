"""Salida del TAC con direcciones (--tac --addr) en vez de nombres."""

from pathlib import Path

import Driver
from compiler import compile_source
from ir import tac
from ir.tac import Quad


def addr_lines(source: str):
    result = compile_source(source)
    assert result.errors == []
    return [l.strip() for l in result.tac.lines(addresses=True)]


def addr_body(source: str, label: str):
    result = addr_lines(source)
    start = next(i for i, l in enumerate(result) if l.startswith(f"begin_func {label},"))
    end = result.index(f"end_func {label}")
    return result[start + 1:end]


def test_globals_use_gp_and_main_temporaries_use_fp():
    assert addr_body("let a: integer = 1; let b: integer = a + 2;", "main") == [
        "gp[+0] = 1",
        "fp[+0] = gp[+0] + 2",
        "gp[+4] = fp[+0]",
    ]


def test_params_locals_and_temporaries_use_offsets_of_the_frame():
    # a: fp[+0], b: fp[+4], r: fp[+8]; los temporales van despues (fp[+12], fp[+20])
    assert addr_body("""
        function f(a: integer, b: integer): integer {
            let r: integer = a * b + (a - b);
            return r;
        }
    """, "f") == [
        "fp[+12] = fp[+0] * fp[+4]",
        "fp[+20] = fp[+0] - fp[+4]",
        "fp[+12] = fp[+12] + fp[+20]",
        "fp[+8] = fp[+12]",
        "return fp[+8]",
    ]


def test_fields_use_this_or_the_object_address_plus_the_field_offset():
    source = """
        class P { let x: integer; let y: integer;
                  function sumar(): integer { return x + this.y; } }
        let p: P = new P();
        p.y = 3;
        print(p.x);
    """

    assert addr_body(source, "P.sumar") == [
        "fp[+8] = this[+8]",
        "fp[+16] = this[+12]",
        "fp[+8] = fp[+8] + fp[+16]",
        "return fp[+8]",
    ]
    assert addr_body(source, "main")[2:] == ["gp[+0][+12] = 3", "fp[+0] = gp[+0][+8]", "print fp[+0]"]


def test_outer_variable_is_addressed_from_the_access_link():
    # x esta en fp[+4] del registro de 'externa'
    assert addr_body("""
        function externa(): integer {
            let a: integer = 1;
            let x: integer = 2;
            function interna(): integer { x = 5; return x; }
            return interna();
        }
    """, "externa.interna") == [
        "fp[+0] = access_link 1",
        "fp[+0][+4] = 5",
        "fp[+0] = access_link 1",
        "fp[+0] = fp[+0][+4]",
        "return fp[+0]",
    ]


def test_array_index_stays_an_element_index_and_labels_are_not_rewritten():
    body = addr_body("let a: integer[] = [1]; let i: integer = 0; print(a[i]);", "main")

    assert "fp[+0] = gp[+0][gp[+8]]" in body
    assert "if fp[+0] goto L1" in body
    assert body[-1] == 'throw "indice fuera de rango"'


def test_virtual_call_target_is_addressed_but_function_names_are_not():
    body = addr_body("""
        class A { function f(): integer { return 1; } }
        class B : A { function f(): integer { return 2; } }
        function g(): integer { return 3; }
        let a: A = new B();
        print(a.f());
        print(g());
    """, "main")

    assert "fp[+0] = gp[+0][+0]" in body  # puntero a la vtable (offset 0 del objeto)
    assert "fp[+0] = call fp[+0], 1" in body
    assert "fp[+0] = call g, 0" in body


def test_every_temporary_address_fits_in_its_frame():
    result = compile_source((Path(__file__).parent.parent / "program.cps").read_text(encoding="utf-8"))
    frames = {f.name: f for f in [s.frame for s in result.analyzer.functions] + [result.analyzer.main_frame]}

    current = None
    for quad in result.tac:
        if quad.op == tac.FUNC_BEGIN:
            current = frames.get(quad.arg1)
            assert int(quad.arg2) == current.data_size
        elif current is not None and quad.addresses:
            for name, address in quad.addresses.items():
                if name.startswith("t") and name[1:].isdigit():
                    offset = int(address[3:-1])
                    assert 0 <= offset < current.data_size


def test_name_mode_is_unchanged_by_addresses():
    result = compile_source("let a: integer = 1; print(a);")

    assert result.tac.text() == "begin_func main, 0\n    gp[+0] = 1\n    print gp[+0]\nend_func main".replace(
        "gp[+0]", "a"
    )


def test_quad_without_address_map_renders_names():
    assert Quad(tac.ASSIGN, "x", result="y").with_addresses() == "y = x"


def test_driver_addr_flag_prints_addresses(tmp_path, capsys):
    source = tmp_path / "p.cps"
    source.write_text("let a: integer = 1;\nprint(a);\n", encoding="utf-8")

    assert Driver.main(str(source), tac_output="-", addresses=True) == 0
    assert capsys.readouterr().out.splitlines() == [
        "begin_func main, 0", "    gp[+0] = 1", "    print gp[+0]", "end_func main",
    ]


def test_driver_reports_semantic_error_with_line_and_column_and_no_tac(tmp_path, capsys):
    source = tmp_path / "p.cps"
    source.write_text('let a: integer = 1;\nlet b: string = a;\n', encoding="utf-8")

    assert Driver.main(str(source), tac_output="-", addresses=True) == 1
    out = capsys.readouterr().out
    assert out.startswith("Error semantico (linea 2:0)")
    assert "begin_func" not in out
