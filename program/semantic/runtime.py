"""Organizacion de la memoria en tiempo de ejecucion.

Cada variable recibe una direccion relativa (offset) dentro de un area de
memoria:

- `global`: el segmento de datos globales del programa.
- `param` / `local`: el registro de activacion de la funcion que la declara.
- `field`: el bloque de memoria de un objeto (layout de su clase).

El registro de activacion de una funcion tiene la forma

    [ this (si es metodo) | parametros... | locales... | temporales... ]

con offsets crecientes desde su base. Las variables declaradas en bloques
anidados (if, while, for, ...) se ubican en el registro de la funcion que
las contiene, no en uno propio.

Cada registro de activacion lleva ademas una cabecera fija de 24 bytes, en
offsets negativos respecto a `fp`, que arma la secuencia de llamada:

    fp[-24]  direccion de retorno
    fp[-16]  enlace de control (fp del llamador)
    fp[-8]   enlace de acceso (fp del registro de la funcion que contiene
             lexicamente a esta; solo lo usan las funciones anidadas)

Nivel de anidamiento: el segmento global es el nivel 0; `main`, las
funciones de nivel superior y los metodos son nivel 1; una funcion
declarada dentro de otra de nivel n es nivel n + 1.
"""

from .types_system import Type, IntegerType, FloatType, BooleanType

WORD = 4
POINTER = 8

RETURN_ADDRESS_OFFSET = -24
CONTROL_LINK_OFFSET = -16
ACCESS_LINK_OFFSET = -8
HEADER_SIZE = 3 * POINTER

# Registro base de cada area: gp (segmento global), fp (registro de
# activacion actual) y this (el objeto receptor de un metodo).
_BASE = {"global": "gp", "param": "fp", "local": "fp", "field": "this"}


def format_address(base: str, offset: int) -> str:
    """Direccion relativa a un registro base: gp[+0], fp[+8], this[+16]."""
    return f"{base}[{offset:+d}]"


def address_of(symbol):
    """Direccion de un simbolo ya ubicado, o None si aun no tiene una."""
    if symbol.storage not in _BASE or symbol.offset is None:
        return None
    return format_address(_BASE[symbol.storage], symbol.offset)


def size_of(type_: Type) -> int:
    """Tamano en bytes de un valor del tipo dado.

    Los tipos por referencia (string, arreglos, objetos, null) y los
    tipos aun desconocidos ocupan un puntero.
    """
    if isinstance(type_, (IntegerType, BooleanType)):
        return WORD
    if isinstance(type_, FloatType):
        return 8
    return POINTER


class MemoryArea:
    """Area contigua donde se asignan variables con offsets crecientes."""

    def __init__(self, name: str, storage: str, level: int = 0):
        self.name = name  # etiqueta del area: 'global' o la etiqueta de la funcion
        self.storage = storage
        self.level = level  # nivel de anidamiento (0 = global)
        self.size = 0
        self.symbols = []

    def allocate(self, symbol, storage: str = None):
        symbol.storage = storage or self.storage
        symbol.offset = self.size
        symbol.size = size_of(symbol.type)
        symbol.level = self.level
        symbol.area = self.name
        self.size += symbol.size
        self.symbols.append(symbol)
        return symbol.offset

    def describe(self) -> list[str]:
        rows = []
        for s in self.symbols:
            type_name = s.type.name if s.type is not None else "?"
            rows.append(
                f"  {s.storage:<6} {s.name:<12} {type_name:<12} offset={s.offset:<4} size={s.size:<2} "
                f"nivel={s.level}  dir={address_of(s)}"
            )
        return rows


class ThisSlot:
    """Pseudo-simbolo para el parametro implicito `this` de un metodo."""

    def __init__(self, class_type):
        self.name = "this"
        self.type = class_type
        self.storage = None
        self.offset = None
        self.size = None
        self.level = None
        self.area = None

    @property
    def address(self):
        return address_of(self)


class ActivationRecord(MemoryArea):
    """Registro de activacion de una funcion, metodo o de `main`.

    `enclosing` es el registro de la funcion que contiene lexicamente a
    esta: a el apunta el enlace de acceso (fp[-8]). Es None para las
    funciones de nivel 1, que solo ven variables globales (via gp).
    """

    def __init__(self, label: str, level: int = 1, enclosing: "ActivationRecord" = None):
        super().__init__(label, "local", level)
        self.enclosing = enclosing
        self.temp_count = 0  # lo llena el generador de TAC (max. de temporales vivos a la vez)

    @property
    def params(self):
        return [s for s in self.symbols if s.storage == "param"]

    @property
    def locals(self):
        return [s for s in self.symbols if s.storage == "local"]

    @property
    def params_size(self) -> int:
        return sum(s.size for s in self.params)

    @property
    def locals_size(self) -> int:
        return sum(s.size for s in self.locals)

    @property
    def temps_size(self) -> int:
        return self.temp_count * POINTER

    @property
    def data_size(self) -> int:
        """Bytes que reserva `begin_func`: variables + temporales (un puntero cada uno)."""
        return self.size + self.temps_size

    @property
    def total_size(self) -> int:
        """Tamano completo del registro: cabecera (retorno + enlaces) + variables + temporales."""
        return HEADER_SIZE + self.data_size

    def temp_offset(self, number: int) -> int:
        """Offset (respecto a fp) del temporal t<number>: los temporales van despues de las variables."""
        return self.size + (number - 1) * POINTER

    def describe(self) -> list[str]:
        access = (
            f"fp de {self.enclosing.name} (nivel {self.enclosing.level})"
            if self.enclosing is not None else "no se usa (solo ve globales, via gp)"
        )
        rows = [
            f"  cabecera fp[{RETURN_ADDRESS_OFFSET:+d}] direccion de retorno",
            f"  cabecera fp[{CONTROL_LINK_OFFSET:+d}] enlace de control: fp del llamador",
            f"  cabecera fp[{ACCESS_LINK_OFFSET:+d}]  enlace de acceso: {access}",
        ]
        rows += super().describe()
        for n in range(1, self.temp_count + 1):
            offset = self.temp_offset(n)
            rows.append(
                f"  {'temp':<6} {'t' + str(n):<12} {'temporal':<12} offset={offset:<4} size={POINTER:<2} "
                f"nivel={self.level}  dir={format_address('fp', offset)}"
            )
        rows.append(
            f"  tamano: params={self.params_size} + locales={self.locals_size} + "
            f"temporales={self.temp_count}x{POINTER} = {self.data_size} bytes (begin_func) "
            f"+ {HEADER_SIZE} de cabecera = {self.total_size} bytes"
        )
        return rows
