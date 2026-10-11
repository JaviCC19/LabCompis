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
"""

from .types_system import Type, IntegerType, FloatType, BooleanType

WORD = 4
POINTER = 8


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

    def __init__(self, name: str, storage: str):
        self.name = name
        self.storage = storage
        self.size = 0
        self.symbols = []

    def allocate(self, symbol, storage: str = None):
        symbol.storage = storage or self.storage
        symbol.offset = self.size
        symbol.size = size_of(symbol.type)
        self.size += symbol.size
        self.symbols.append(symbol)
        return symbol.offset

    def describe(self) -> list[str]:
        rows = []
        for s in self.symbols:
            type_name = s.type.name if s.type is not None else "?"
            rows.append(f"  {s.storage:<6} {s.name:<12} {type_name:<12} offset={s.offset:<4} size={s.size}")
        return rows


class ThisSlot:
    """Pseudo-simbolo para el parametro implicito `this` de un metodo."""

    def __init__(self, class_type):
        self.name = "this"
        self.type = class_type
        self.storage = None
        self.offset = None
        self.size = None


class ActivationRecord(MemoryArea):
    def __init__(self, label: str):
        super().__init__(label, "local")
        self.temp_count = 0  # lo llena el generador de TAC

    @property
    def params(self):
        return [s for s in self.symbols if s.storage == "param"]

    @property
    def locals(self):
        return [s for s in self.symbols if s.storage == "local"]

    @property
    def total_size(self) -> int:
        """Tamano final del registro: variables + temporales (un puntero cada uno)."""
        return self.size + self.temp_count * POINTER
