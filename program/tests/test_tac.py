from ir import tac
from ir.tac import Quad, TACProgram, TempAllocator, LabelAllocator, is_temp


def test_binary_quad_renders_as_three_address_instruction():
    quad = Quad(tac.BINARY, "a", "b", "t1", operator="+")

    assert str(quad) == "t1 = a + b"
    assert quad.as_row() == ("+", "a", "b", "t1")


def test_conditional_jump_and_label_render():
    assert str(Quad(tac.IF_FALSE, "t1", result="L2")) == "ifFalse t1 goto L2"
    assert str(Quad(tac.LABEL, result="L2")) == "L2:"


def test_call_with_and_without_result():
    assert str(Quad(tac.CALL, "suma", "2", "t1")) == "t1 = call suma, 2"
    assert str(Quad(tac.CALL, "imprimir", "0")) == "call imprimir, 0"


def test_memory_access_quads_render():
    assert str(Quad(tac.LOAD_INDEX, "arr", "t1", "t2")) == "t2 = arr[t1]"
    assert str(Quad(tac.STORE_INDEX, "t1", "5", "arr")) == "arr[t1] = 5"
    assert str(Quad(tac.LOAD_FIELD, "this", "nombre", "t1")) == "t1 = this.nombre"
    assert str(Quad(tac.STORE_FIELD, "nombre", "t1", "this")) == "this.nombre = t1"


def test_temps_are_recycled_after_release():
    temps = TempAllocator()
    t1 = temps.new()
    t2 = temps.new()
    temps.release(t1, t2)
    t3 = temps.new()

    assert (t1, t2, t3) == ("t1", "t2", "t1")
    assert temps.max_live == 2


def test_release_ignores_non_temporaries():
    temps = TempAllocator()
    temps.release("x", "10", "L1")

    assert temps.new() == "t1"


def test_releasing_twice_does_not_duplicate_free_temp():
    temps = TempAllocator()
    t1 = temps.new()
    temps.release(t1)
    temps.release(t1)

    assert temps.new() == "t1"
    assert temps.new() == "t2"


def test_labels_are_unique():
    labels = LabelAllocator()

    assert [labels.new() for _ in range(3)] == ["L1", "L2", "L3"]


def test_is_temp():
    assert is_temp("t12")
    assert not is_temp("total")
    assert not is_temp("t")


def test_program_indents_everything_but_labels_and_function_bounds():
    program = TACProgram()
    program.emit(Quad(tac.FUNC_BEGIN, "f", "8"))
    program.emit(Quad(tac.LABEL, result="L1"))
    program.emit(Quad(tac.RETURN, "x"))
    program.emit(Quad(tac.FUNC_END, "f"))

    assert str(program) == "begin_func f, 8\nL1:\n    return x\nend_func f"
