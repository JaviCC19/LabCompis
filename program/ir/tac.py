"""Representacion intermedia: codigo de tres direcciones (TAC) en cuadruplos.

Cada instruccion es un cuadruplo (op, arg1, arg2, result). Los operandos son
cadenas ya resueltas: nombres de variables, temporales (t1, t2, ...),
etiquetas (L1, L2, ...) o literales tal cual aparecen en el codigo fuente.
La forma textual de cada instruccion se documenta en TAC.md.

Este modulo no conoce ANTLR ni la tabla de simbolos; solo modela el
programa intermedio y los recursos que se asignan al generarlo
(temporales y etiquetas).
"""

from dataclasses import dataclass, field, replace


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
    # nombre -> direccion (gp[+k], fp[+k]) de los operandos de la funcion a la
    # que pertenece el cuadruplo; lo llena el generador para el modo con direcciones.
    addresses: dict = field(default=None, repr=False, compare=False)

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

    # Posiciones que son datos (variables o temporales) en cada operacion; las
    # demas son etiquetas, nombres de funcion/clase/campo o tamanos.
    _DATA_SLOTS = {
        ASSIGN: ("arg1", "result"),
        BINARY: ("arg1", "arg2", "result"),
        UNARY: ("arg1", "result"),
        IF: ("arg1",),
        IF_FALSE: ("arg1",),
        PARAM: ("arg1",),
        CALL: ("result",),
        RETURN: ("arg1",),
        LOAD_INDEX: ("arg1", "arg2", "result"),
        STORE_INDEX: ("arg1", "arg2", "result"),
        LOAD_FIELD: ("arg1", "result"),
        STORE_FIELD: ("arg2", "result"),
        NEW: ("result",),
        NEW_ARRAY: ("arg1", "result"),
        LENGTH: ("arg1", "result"),
        PRINT: ("arg1",),
        CATCH: ("result",),
        ACCESS_LINK: ("result",),
        LOAD_OUTER: ("arg1", "result"),
        STORE_OUTER: ("arg2", "result"),
        LINK: ("arg1",),
        THROW: ("arg1",),
    }

    def with_addresses(self) -> str:
        """Forma textual con cada variable y temporal reemplazado por su direccion.

        Las variables globales se ven como gp[+k], las del registro actual y
        los temporales como fp[+k]. Un campo o una variable de una funcion
        externa se ve como base[+k] (contenido de la direccion base + k), con
        el offset del layout de la clase o del registro externo.
        """
        addresses = self.addresses or {}
        changes = {
            slot: addresses.get(getattr(self, slot), getattr(self, slot))
            for slot in self._DATA_SLOTS.get(self.op, ())
        }
        if self.op == CALL and is_temp(self.arg1):
            changes["arg1"] = addresses.get(self.arg1, self.arg1)  # llamada indirecta (vtable)
        quad = replace(self, **changes)
        if self.offset is not None:
            if self.op in (LOAD_FIELD, LOAD_OUTER):
                return f"{quad.result} = {quad.arg1}[{self.offset:+d}]"
            if self.op == STORE_FIELD:
                return f"{quad.result}[{self.offset:+d}] = {quad.arg2}"
            if self.op == STORE_OUTER:
                return f"{quad.result}[{self.offset:+d}] = {quad.arg2}"
        return str(quad)

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

    def lines(self, addresses: bool = False) -> list[str]:
        """Forma textual, por nombre (por defecto) o con direcciones (ver Quad.with_addresses)."""
        result = []
        for quad in self.code:
            text = quad.with_addresses() if addresses else str(quad)
            # Las etiquetas y los limites de funcion van sin sangria.
            if quad.op in (LABEL, FUNC_BEGIN, FUNC_END):
                result.append(text)
            else:
                result.append("    " + text)
        return result

    def text(self, addresses: bool = False) -> str:
        return "\n".join(self.lines(addresses))

    def __str__(self):
        return self.text()
