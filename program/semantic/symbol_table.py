"""Tabla de simbolos con manejo de ambitos (entornos) anidados.

Este modulo es agnostico del arbol de ANTLR: no conoce contextos ni
numeros de linea. El visitor que recorre el arbol es responsable de
capturar esa informacion y de reportar los errores usando las
excepciones que aqui se definen.
"""

from enum import Enum, auto

from .types_system import FunctionType, ClassType, Type
from .runtime import size_of, address_of, POINTER


class ScopeKind(Enum):
    GLOBAL = auto()
    FUNCTION = auto()
    CLASS = auto()
    BLOCK = auto()


class Symbol:
    def __init__(self, name: str, type_: Type = None):
        self.name = name
        self.type = type_
        # Direccion en tiempo de ejecucion (ver runtime.py): area donde vive
        # ('global', 'param', 'local' o 'field'), offset dentro de ella y tamano.
        self.storage = None
        self.offset = None
        self.size = None
        # Nivel de anidamiento del area que lo declara (0 = global, 1 = main /
        # funcion de nivel superior / metodo, ...) y etiqueta de esa area
        # ('global', la etiqueta de la funcion o el nombre de la clase).
        self.level = None
        self.area = None

    @property
    def address(self):
        """Direccion relativa a su registro base (gp, fp o this), p.ej. 'fp[+8]'."""
        return address_of(self)

    def __repr__(self):
        return f"{self.__class__.__name__}({self.name}: {self.type})"


class VariableSymbol(Symbol):
    def __init__(self, name: str, type_: Type = None, is_const: bool = False, initialized: bool = False):
        super().__init__(name, type_)
        self.is_const = is_const
        self.initialized = initialized


class ParameterSymbol(VariableSymbol):
    def __init__(self, name: str, type_: Type = None):
        super().__init__(name, type_, is_const=False, initialized=True)


class FunctionSymbol(Symbol):
    def __init__(self, name: str, param_types, return_type: Type, params=None):
        super().__init__(name, FunctionType(param_types, return_type))
        self.params = params or []  # list[ParameterSymbol], en orden posicional
        self.return_type = return_type
        self.scope = None  # Scope propio de la funcion, asignado al crearlo (closures)
        self.owner_class = None  # ClassSymbol si es un metodo, None si es funcion libre
        self.label = name  # etiqueta de entrada en el TAC ('f', 'Clase.metodo', 'externa.interna')
        self.frame = None  # ActivationRecord con params, locales y temporales

    @property
    def param_types(self):
        return self.type.param_types


class ClassSymbol(Symbol):
    def __init__(self, name: str, superclass: "ClassSymbol" = None):
        super().__init__(name, ClassType(name, class_symbol=self))
        self.superclass = superclass
        self.fields: dict[str, VariableSymbol] = {}
        self.methods: dict[str, FunctionSymbol] = {}
        self.scope = None  # Scope propio de la clase, asignado al crearla
        self.size = 0  # bytes que ocupa una instancia (incluye los campos heredados)

    def compute_layout(self):
        """Asigna offsets a los campos: primero los heredados, luego los propios.

        El offset 0 de todo objeto guarda el puntero a la vtable de su clase.
        """
        offset = self.superclass.size if self.superclass is not None else POINTER
        for field in self.fields.values():
            field.storage = "field"
            field.area = self.name
            field.offset = offset
            field.size = size_of(field.type)
            offset += field.size
        self.size = offset

    def vtable(self) -> list:
        """Metodos en el orden de sus slots: un metodo que sobrescribe a otro hereda su slot."""
        table = list(self.superclass.vtable()) if self.superclass is not None else []
        for method in self.methods.values():
            if method.name == "constructor":
                continue
            slot = next((i for i, m in enumerate(table) if m.name == method.name), None)
            if slot is None:
                table.append(method)
            else:
                table[slot] = method
        return table

    def is_subclass_of(self, other: "ClassSymbol") -> bool:
        current = self
        while current is not None:
            if current is other:
                return True
            current = current.superclass
        return False

    def all_fields(self) -> list:
        inherited = self.superclass.all_fields() if self.superclass is not None else []
        return inherited + list(self.fields.values())

    def resolve_member(self, name: str):
        if name in self.fields:
            return self.fields[name]
        if name in self.methods:
            return self.methods[name]
        if self.superclass is not None:
            return self.superclass.resolve_member(name)
        return None

    def define_field(self, symbol: VariableSymbol):
        if symbol.name in self.fields or symbol.name in self.methods:
            raise DuplicateSymbolError(symbol.name, self.fields.get(symbol.name) or self.methods.get(symbol.name))
        self.fields[symbol.name] = symbol

    def define_method(self, symbol: FunctionSymbol):
        if symbol.name in self.fields or symbol.name in self.methods:
            raise DuplicateSymbolError(symbol.name, self.fields.get(symbol.name) or self.methods.get(symbol.name))
        symbol.owner_class = self
        self.methods[symbol.name] = symbol


class DuplicateSymbolError(Exception):
    """Se intento redeclarar un identificador ya definido en el mismo ambito."""

    def __init__(self, name: str, existing: Symbol):
        super().__init__(f"'{name}' ya fue declarado en este ambito")
        self.name = name
        self.existing = existing


class Scope:
    def __init__(self, kind: ScopeKind, parent: "Scope" = None, owner=None):
        self.kind = kind
        self.parent = parent
        self.owner = owner  # FunctionSymbol o ClassSymbol al que pertenece este scope, si aplica
        self.symbols: dict[str, Symbol] = {}

    def define(self, symbol: Symbol):
        if symbol.name in self.symbols:
            raise DuplicateSymbolError(symbol.name, self.symbols[symbol.name])
        self.symbols[symbol.name] = symbol

    def resolve_local(self, name: str):
        return self.symbols.get(name)

    def resolve(self, name: str):
        scope = self
        while scope is not None:
            if name in scope.symbols:
                return scope.symbols[name]
            scope = scope.parent
        return None

    def nearest_enclosing(self, kind: ScopeKind) -> "Scope":
        scope = self
        while scope is not None:
            if scope.kind == kind:
                return scope
            scope = scope.parent
        return None

    def is_inside(self, kind: ScopeKind) -> bool:
        return self.nearest_enclosing(kind) is not None


class SymbolTable:
    def __init__(self):
        self.global_scope = Scope(ScopeKind.GLOBAL)
        self.current_scope = self.global_scope

    def enter_scope(self, kind: ScopeKind, owner=None) -> Scope:
        self.current_scope = Scope(kind, parent=self.current_scope, owner=owner)
        return self.current_scope

    def exit_scope(self):
        if self.current_scope.parent is None:
            raise RuntimeError("no se puede salir del ambito global")
        self.current_scope = self.current_scope.parent

    def define(self, symbol: Symbol):
        self.current_scope.define(symbol)

    def resolve(self, name: str):
        return self.current_scope.resolve(name)

    def resolve_local(self, name: str):
        return self.current_scope.resolve_local(name)
