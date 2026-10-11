"""Representacion intermedia: codigo de tres direcciones (TAC) en cuadruplos.

Cada instruccion es un cuadruplo (op, arg1, arg2, result). Los operandos son
cadenas ya resueltas: nombres de variables, temporales (t1, t2, ...),
etiquetas (L1, L2, ...) o literales tal cual aparecen en el codigo fuente.
La forma textual de cada instruccion se documenta en TAC.md.

Este modulo no conoce ANTLR ni la tabla de simbolos; solo modela el
programa intermedio y los recursos que se asignan al generarlo
(temporales y etiquetas).
"""

from dataclasses import dataclass


# --- codigos de operacion ----------------------------------------------------

ASSIGN = "="          # result = arg1
BINARY = "binary"     # result = arg1 <op> arg2   (op en Quad.operator)
UNARY = "unary"       # result = <op> arg1
LABEL = "label"       # result:
GOTO = "goto"         # goto result
IF = "if"             # if arg1 goto result
IF_FALSE = "ifFalse"  # ifFalse arg1 goto result
PARAM = "param"       # param arg1
CALL = "call"         # [result =] call arg1, arg2(n)
RETURN = "return"     # return [arg1]
FUNC_BEGIN = "begin_func"  # begin_func arg1, arg2(tamano del registro de activacion)
FUNC_END = "end_func"      # end_func arg1
LOAD_INDEX = "load_index"    # result = arg1[arg2]
STORE_INDEX = "store_index"  # result[arg1] = arg2
LOAD_FIELD = "load_field"    # result = arg1.arg2
STORE_FIELD = "store_field"  # result.arg1 = arg2
NEW = "new"            # result = new arg1, arg2(tamano)
NEW_ARRAY = "new_array"  # result = new_array arg1(longitud)
LENGTH = "len"         # result = len arg1
PRINT = "print"        # print arg1
TRY_BEGIN = "try_begin"  # try_begin result(etiqueta del catch)
TRY_END = "try_end"      # try_end
CATCH = "catch"          # result = catch
ACCESS_LINK = "access_link"  # result = access_link arg1(saltos): fp del registro arg1 niveles arriba
LOAD_OUTER = "load_outer"    # result = arg1->arg2   (variable arg2 del registro al que apunta arg1)
STORE_OUTER = "store_outer"  # result->arg1 = arg2
LINK = "link"                # link arg1: enlace de acceso para la proxima llamada
THROW = "throw"              # throw arg1: lanza un error hacia el manejador activo


@dataclass
class Quad:
    op: str
    arg1: str = None
    arg2: str = None
    result: str = None
    operator: str = None  # operador concreto para BINARY / UNARY
    offset: int = None  # offset en bytes del campo (LOAD/STORE_FIELD) o de la variable (LOAD/STORE_OUTER)

    def __str__(self):
        op = self.op
        if op == ASSIGN:
            return f"{self.result} = {self.arg1}"
        if op == BINARY:
            return f"{self.result} = {self.arg1} {self.operator} {self.arg2}"
        if op == UNARY:
            return f"{self.result} = {self.operator}{self.arg1}"
        if op == LABEL:
            return f"{self.result}:"
        if op == GOTO:
            return f"goto {self.result}"
        if op in (IF, IF_FALSE):
            return f"{op} {self.arg1} goto {self.result}"
        if op == PARAM:
            return f"param {self.arg1}"
        if op == CALL:
            call = f"call {self.arg1}, {self.arg2}"
            return f"{self.result} = {call}" if self.result is not None else call
        if op == RETURN:
            return "return" if self.arg1 is None else f"return {self.arg1}"
        if op == FUNC_BEGIN:
            return f"begin_func {self.arg1}, {self.arg2}"
        if op == FUNC_END:
            return f"end_func {self.arg1}"
        if op == LOAD_INDEX:
            return f"{self.result} = {self.arg1}[{self.arg2}]"
        if op == STORE_INDEX:
            return f"{self.result}[{self.arg1}] = {self.arg2}"
        if op == LOAD_FIELD:
            return f"{self.result} = {self.arg1}.{self.arg2}"
        if op == STORE_FIELD:
            return f"{self.result}.{self.arg1} = {self.arg2}"
        if op == NEW:
            return f"{self.result} = new {self.arg1}, {self.arg2}"
        if op == NEW_ARRAY:
            return f"{self.result} = new_array {self.arg1}"
        if op == LENGTH:
            return f"{self.result} = len {self.arg1}"
        if op == PRINT:
            return f"print {self.arg1}"
        if op == TRY_BEGIN:
            return f"try_begin {self.result}"
        if op == TRY_END:
            return "try_end"
        if op == CATCH:
            return f"{self.result} = catch"
        if op == ACCESS_LINK:
            return f"{self.result} = access_link {self.arg1}"
        if op == LOAD_OUTER:
            return f"{self.result} = {self.arg1}->{self.arg2}"
        if op == STORE_OUTER:
            return f"{self.result}->{self.arg1} = {self.arg2}"
        if op == LINK:
            return f"link {self.arg1}"
        if op == THROW:
            return f"throw {self.arg1}"
        raise ValueError(f"operacion TAC desconocida: {op}")

    def as_row(self):
        """Fila (op, arg1, arg2, result) para mostrar el programa como tabla de cuadruplos."""
        op = self.operator if self.op in (BINARY, UNARY) else self.op
        return (op, self.arg1 or "", self.arg2 or "", self.result or "")


# --- temporales y etiquetas --------------------------------------------------

def is_temp(operand) -> bool:
    return isinstance(operand, str) and len(operand) > 1 and operand[0] == "t" and operand[1:].isdigit()


class TempAllocator:
    """Asigna temporales reciclando los que ya fueron consumidos.

    Un temporal se libera en cuanto la instruccion que lo usa como operando
    se emite (cada temporal tiene un unico uso dentro de una expresion), y
    vuelve a la lista de libres. `new` siempre entrega el temporal libre de
    menor numero, asi que expresiones como (a*b) + (c*d) usan solo t1 y t2.
    """

    def __init__(self):
        self._free: list[int] = []
        self._next = 1
        self.max_live = 0  # maximo de temporales vivos a la vez (para el registro de activacion)
        self._live = 0

    def new(self) -> str:
        if self._free:
            self._free.sort()
            number = self._free.pop(0)
        else:
            number = self._next
            self._next += 1
        self._live += 1
        self.max_live = max(self.max_live, self._live)
        return f"t{number}"

    def release(self, *operands):
        for operand in operands:
            if is_temp(operand):
                number = int(operand[1:])
                if number not in self._free:
                    self._free.append(number)
                    self._live -= 1

    def reset(self):
        """Se llama al entrar a una funcion: sus temporales viven en su propio registro."""
        self.__init__()


class LabelAllocator:
    def __init__(self):
        self._next = 1

    def new(self) -> str:
        label = f"L{self._next}"
        self._next += 1
        return label


class TACProgram:
    """Secuencia de cuadruplos emitidos, en orden."""

    def __init__(self):
        self.code: list[Quad] = []

    def emit(self, quad: Quad) -> Quad:
        self.code.append(quad)
        return quad

    def __iter__(self):
        return iter(self.code)

    def __len__(self):
        return len(self.code)

    def lines(self) -> list[str]:
        result = []
        for quad in self.code:
            text = str(quad)
            # Las etiquetas y los limites de funcion van sin sangria.
            if quad.op in (LABEL, FUNC_BEGIN, FUNC_END):
                result.append(text)
            else:
                result.append("    " + text)
        return result

    def __str__(self):
        return "\n".join(self.lines())
