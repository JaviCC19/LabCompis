"""Puente entre el arbol de ANTLR y la tabla de simbolos.

`semantic/` es agnostico de ANTLR; este modulo si conoce el arbol y usa un
Listener para recorrerlo, resolviendo ambitos, declaraciones y tipos sobre
una SymbolTable.
"""

from antlr4.tree.Tree import TerminalNode

from CompiscriptListener import CompiscriptListener
from CompiscriptParser import CompiscriptParser

from semantic import (
    SymbolTable,
    ScopeKind,
    VariableSymbol,
    ParameterSymbol,
    FunctionSymbol,
    ClassSymbol,
    DuplicateSymbolError,
    SemanticError,
    MemoryArea,
    ActivationRecord,
    ThisSlot,
    INTEGER,
    FLOAT,
    STRING,
    BOOLEAN,
    NULL,
    VOID,
    ERROR,
    ArrayType,
    ClassType,
    FloatType,
    StringType,
    NullType,
    FunctionType,
    is_numeric,
    is_error,
    is_assignable,
)


class SemanticAnalyzer(CompiscriptListener):
    def __init__(self):
        self.table = SymbolTable()
        self.errors: list[SemanticError] = []
        self.function_stack: list[FunctionSymbol] = []
        self.returns_seen: list[bool] = []  # paralelo a function_stack: si ya se vio un 'return'
        self.class_stack: list[ClassSymbol] = []
        self.loop_depth = 0
        self.switch_depth = 0

        self.types = {}  # ctx de expresion -> Type ya inferido/verificado
        self.suffix_base = {}  # SuffixOpContext -> Type sobre el que se aplico
        # ctx -> simbolo que declara o al que se refiere (declaraciones, identificadores,
        # funciones, clases, foreach/catch). Lo usa el generador de TAC.
        self.symbol_of = {}
        self._decl_symbols = {}  # ctx de declaracion -> simbolo, mientras se visita su inicializador

        self.globals = MemoryArea("global", "global")  # segmento de datos de las variables globales
        self.functions: list[FunctionSymbol] = []  # todas las funciones y metodos, en orden de declaracion

    # ------------------------------------------------------------------
    # utilidades
    # ------------------------------------------------------------------

    def _error(self, ctx, message: str):
        token = ctx.start
        self.errors.append(SemanticError(token.line, token.column, message))

    def _define(self, symbol, ctx, kind_desc: str):
        try:
            self.table.define(symbol)
        except DuplicateSymbolError:
            self._error(ctx, f"'{symbol.name}' ya fue declarado en este ambito ({kind_desc})")

    @staticmethod
    def _terminates_flow(stmt_ctx: "CompiscriptParser.StatementContext") -> bool:
        return (
            stmt_ctx.returnStatement() is not None
            or stmt_ctx.breakStatement() is not None
            or stmt_ctx.continueStatement() is not None
        )

    def _check_dead_code(self, statements):
        for i, stmt in enumerate(statements):
            if self._terminates_flow(stmt) and i + 1 < len(statements):
                self._error(statements[i + 1], "codigo inalcanzable despues de 'return'/'break'/'continue'")
                break

    def _resolve_name(self, name: str):
        """Resuelve un nombre usado sin calificar.

        Recorre los ambitos hacia afuera; al pasar por el ambito de una clase
        tambien busca los miembros heredados de sus superclases, para que un
        campo o metodo heredado se pueda usar con `this` implicito.
        """
        scope = self.table.current_scope
        while scope is not None:
            if name in scope.symbols:
                return scope.symbols[name]
            if scope.kind == ScopeKind.CLASS and isinstance(scope.owner, ClassSymbol):
                member = scope.owner.resolve_member(name)
                if member is not None:
                    return member
            scope = scope.parent
        return None

    def _allocate(self, symbol):
        """Ubica una variable en el registro de activacion de su funcion, o en el segmento global.

        Los campos de clase no pasan por aqui: su offset lo fija ClassSymbol.compute_layout.
        """
        if self.function_stack:
            self.function_stack[-1].frame.allocate(symbol, "local")
        else:
            self.globals.allocate(symbol)

    def _resolve_type(self, type_ctx: "CompiscriptParser.TypeContext"):
        if type_ctx is None:
            return None

        base_ctx = type_ctx.baseType()
        base_text = base_ctx.getText()
        if base_text == "integer":
            base = INTEGER
        elif base_text == "float":
            base = FLOAT
        elif base_text == "string":
            base = STRING
        elif base_text == "boolean":
            base = BOOLEAN
        else:
            symbol = self.table.resolve(base_text)
            if not isinstance(symbol, ClassSymbol):
                self._error(base_ctx, f"tipo desconocido '{base_text}'")
                return None
            base = symbol.type

        dims = (type_ctx.getChildCount() - 1) // 2
        result = base
        for _ in range(dims):
            result = ArrayType(result)
        return result

    def _type(self, ctx):
        return self.types.get(ctx)

    def _set_type(self, ctx, type_):
        self.types[ctx] = type_

    # ------------------------------------------------------------------
    # bloques: cada uno abre su propio ambito
    # ------------------------------------------------------------------

    def enterBlock(self, ctx: CompiscriptParser.BlockContext):
        self.table.enter_scope(ScopeKind.BLOCK)

        parent = ctx.parentCtx
        if isinstance(parent, CompiscriptParser.TryCatchStatementContext) and parent.block(1) is ctx:
            name = parent.Identifier().getText()
            symbol = VariableSymbol(name, None, initialized=True)
            self._define(symbol, parent, "variable de catch")
            self.symbol_of[parent] = symbol
            self._allocate(symbol)
        elif isinstance(parent, CompiscriptParser.ForeachStatementContext):
            name = parent.Identifier().getText()
            iterable_type = self._type(parent.expression())
            if isinstance(iterable_type, ArrayType):
                element_type = iterable_type.element_type
            else:
                if not is_error(iterable_type):
                    self._error(parent, f"'foreach' espera un arreglo, se recibio '{iterable_type.name}'")
                element_type = None
            symbol = VariableSymbol(name, element_type, initialized=True)
            self._define(symbol, parent, "variable de foreach")
            self.symbol_of[parent] = symbol
            self._allocate(symbol)

    def exitBlock(self, ctx: CompiscriptParser.BlockContext):
        self._check_dead_code(ctx.statement())
        self.table.exit_scope()

    # ------------------------------------------------------------------
    # declaraciones de variables y constantes
    # ------------------------------------------------------------------

    def enterVariableDeclaration(self, ctx: CompiscriptParser.VariableDeclarationContext):
        if ctx in self._decl_symbols:
            return  # ya se predeclaro como campo de clase, ver enterClassDeclaration

        name = ctx.Identifier().getText()
        declared_type = self._resolve_type(ctx.typeAnnotation().type_()) if ctx.typeAnnotation() else None
        symbol = VariableSymbol(name, declared_type, is_const=False, initialized=ctx.initializer() is not None)
        self._decl_symbols[ctx] = symbol
        self._define(symbol, ctx, "variable")

    def exitVariableDeclaration(self, ctx: CompiscriptParser.VariableDeclarationContext):
        symbol = self._decl_symbols.pop(ctx, None)
        if symbol is None:
            return
        self.symbol_of[ctx] = symbol

        if ctx.initializer() is not None:
            init_type = self._type(ctx.initializer().expression())
            if symbol.type is None:
                symbol.type = init_type
            elif not is_assignable(symbol.type, init_type):
                self._error(
                    ctx,
                    f"no se puede inicializar '{symbol.name}' de tipo '{symbol.type.name}' "
                    f"con un valor de tipo '{init_type.name}'",
                )

        if self.table.current_scope.kind != ScopeKind.CLASS:
            self._allocate(symbol)

    def enterConstantDeclaration(self, ctx: CompiscriptParser.ConstantDeclarationContext):
        if ctx in self._decl_symbols:
            return  # ya se predeclaro como campo de clase, ver enterClassDeclaration

        name = ctx.Identifier().getText()
        declared_type = self._resolve_type(ctx.typeAnnotation().type_()) if ctx.typeAnnotation() else None
        symbol = VariableSymbol(name, declared_type, is_const=True, initialized=True)
        self._decl_symbols[ctx] = symbol
        self._define(symbol, ctx, "constante")

    def exitConstantDeclaration(self, ctx: CompiscriptParser.ConstantDeclarationContext):
        symbol = self._decl_symbols.pop(ctx, None)
        if symbol is None:
            return
        self.symbol_of[ctx] = symbol

        init_type = self._type(ctx.expression())
        if symbol.type is None:
            symbol.type = init_type
        elif not is_assignable(symbol.type, init_type):
            self._error(
                ctx,
                f"no se puede inicializar la constante '{symbol.name}' de tipo '{symbol.type.name}' "
                f"con un valor de tipo '{init_type.name}'",
            )

        if self.table.current_scope.kind != ScopeKind.CLASS:
            self._allocate(symbol)

    # ------------------------------------------------------------------
    # asignacion como sentencia (Identifier '=' expr ';'  /  expr '.' Id '=' expr ';')
    # ------------------------------------------------------------------

    def exitAssignment(self, ctx: CompiscriptParser.AssignmentContext):
        expressions = ctx.expression()
        member_name = ctx.Identifier().getText()

        if len(expressions) == 1:
            value_type = self._type(expressions[0])
            symbol = self._resolve_name(member_name)
            self.symbol_of[ctx] = symbol
            if symbol is None:
                self._error(ctx, f"variable '{member_name}' no declarada")
            elif getattr(symbol, "is_const", False):
                self._error(ctx, f"no se puede asignar a '{member_name}', es una constante")
            elif not is_assignable(symbol.type, value_type):
                self._error(
                    ctx,
                    f"no se puede asignar un valor de tipo '{value_type.name}' a '{member_name}' "
                    f"de tipo '{symbol.type.name}'",
                )
            return

        target_type = self._type(expressions[0])
        value_type = self._type(expressions[1])
        if is_error(target_type):
            return
        if not isinstance(target_type, ClassType) or target_type.class_symbol is None:
            self._error(ctx, f"no se puede acceder a una propiedad de un valor de tipo '{target_type.name}'")
            return
        member = target_type.class_symbol.resolve_member(member_name)
        if member is None:
            self._error(ctx, f"'{member_name}' no es un miembro de la clase '{target_type.class_symbol.name}'")
        elif isinstance(member, VariableSymbol) and member.is_const:
            self._error(ctx, f"no se puede asignar al campo constante '{member_name}'")
        elif not is_assignable(member.type, value_type):
            self._error(
                ctx,
                f"no se puede asignar un valor de tipo '{value_type.name}' a '{member_name}' "
                f"de tipo '{member.type.name}'",
            )

    # ------------------------------------------------------------------
    # uso de identificadores, 'new' y 'this' dentro de expresiones
    # ------------------------------------------------------------------

    def exitIdentifierExpr(self, ctx: CompiscriptParser.IdentifierExprContext):
        text = ctx.Identifier().getText()
        symbol = self._resolve_name(text)
        self.symbol_of[ctx] = symbol
        if symbol is None:
            self._error(ctx, f"variable '{text}' no declarada")
            self._set_type(ctx, ERROR)
        else:
            self._set_type(ctx, symbol.type if symbol.type is not None else ERROR)

    def exitNewExpr(self, ctx: CompiscriptParser.NewExprContext):
        text = ctx.Identifier().getText()
        symbol = self.table.resolve(text)
        if not isinstance(symbol, ClassSymbol):
            self._error(ctx, f"clase '{text}' no declarada")
            self._set_type(ctx, ERROR)
            return

        self._set_type(ctx, symbol.type)
        self.symbol_of[ctx] = symbol

        args = ctx.arguments().expression() if ctx.arguments() else []
        arg_types = [self._type(a) for a in args]
        constructor = symbol.resolve_member("constructor")
        if not isinstance(constructor, FunctionSymbol):
            if arg_types:
                self._error(ctx, f"la clase '{text}' no tiene constructor, no se esperaban argumentos")
            return

        expected = constructor.param_types
        if len(arg_types) != len(expected):
            self._error(
                ctx,
                f"el constructor de '{text}' espera {len(expected)} argumento(s) y se recibieron {len(arg_types)}",
            )
        else:
            for i, (arg_type, expected_type) in enumerate(zip(arg_types, expected), start=1):
                if not is_assignable(expected_type, arg_type):
                    self._error(
                        ctx,
                        f"el argumento {i} del constructor de '{text}' debe ser de tipo "
                        f"'{expected_type.name}', se recibio '{arg_type.name}'",
                    )

    def exitThisExpr(self, ctx: CompiscriptParser.ThisExprContext):
        if not self.class_stack:
            self._error(ctx, "'this' solo puede usarse dentro de una clase")
            self._set_type(ctx, ERROR)
        else:
            self._set_type(ctx, self.class_stack[-1].type)

    # ------------------------------------------------------------------
    # literales y arreglos
    # ------------------------------------------------------------------

    def exitLiteralExpr(self, ctx: CompiscriptParser.LiteralExprContext):
        if ctx.Literal() is not None:
            text = ctx.Literal().getText()
            if text.startswith('"'):
                result = STRING
            elif "." in text:
                result = FLOAT
            else:
                result = INTEGER
            self._set_type(ctx, result)
        elif ctx.arrayLiteral() is not None:
            self._set_type(ctx, self._type(ctx.arrayLiteral()))
        else:
            text = ctx.getText()
            self._set_type(ctx, {"null": NULL, "true": BOOLEAN, "false": BOOLEAN}[text])

    def exitArrayLiteral(self, ctx: CompiscriptParser.ArrayLiteralContext):
        elements = ctx.expression()
        if not elements:
            self._set_type(ctx, ArrayType(ERROR))
            return

        element_type = self._type(elements[0])
        for extra in elements[1:]:
            extra_type = self._type(extra)
            if not is_error(element_type) and not is_error(extra_type) and element_type != extra_type:
                self._error(
                    ctx,
                    f"los elementos del arreglo deben tener el mismo tipo: "
                    f"'{element_type.name}' y '{extra_type.name}'",
                )
                element_type = ERROR
                break
        self._set_type(ctx, ArrayType(element_type))

    # ------------------------------------------------------------------
    # operadores: unario, aritmeticos, relacionales, logicos, ternario
    # ------------------------------------------------------------------

    def exitUnaryExpr(self, ctx: CompiscriptParser.UnaryExprContext):
        inner = ctx.unaryExpr()
        if inner is None:
            self._set_type(ctx, self._type(ctx.primaryExpr()))
            return

        operand_type = self._type(inner)
        op = ctx.getChild(0).getText()
        if is_error(operand_type):
            result = ERROR
        elif op == "-":
            if is_numeric(operand_type):
                result = operand_type
            else:
                self._error(ctx, f"el operador unario '-' requiere un operando numerico, se recibio '{operand_type.name}'")
                result = ERROR
        else:  # '!'
            if operand_type == BOOLEAN:
                result = BOOLEAN
            else:
                self._error(ctx, f"el operador unario '!' requiere un operando boolean, se recibio '{operand_type.name}'")
                result = ERROR
        self._set_type(ctx, result)

    def _binary_chain(self, ctx, operands, validate):
        result = self._type(operands[0])
        for i in range(1, len(operands)):
            op = ctx.getChild(2 * i - 1).getText()
            right = self._type(operands[i])
            result = validate(ctx, result, op, right)
        self._set_type(ctx, result)

    @staticmethod
    def _numeric_result(left, right):
        return FLOAT if (isinstance(left, FloatType) or isinstance(right, FloatType)) else INTEGER

    def _validate_additive(self, ctx, left, op, right):
        if is_error(left) or is_error(right):
            return ERROR
        if op == "+":
            if is_numeric(left) and is_numeric(right):
                return self._numeric_result(left, right)
            if isinstance(left, StringType) and isinstance(right, StringType):
                return STRING
            self._error(ctx, f"el operador '+' no admite operandos de tipo '{left.name}' y '{right.name}'")
            return ERROR
        if is_numeric(left) and is_numeric(right):
            return self._numeric_result(left, right)
        self._error(ctx, f"el operador '{op}' requiere operandos numericos, se recibio '{left.name}' y '{right.name}'")
        return ERROR

    def _validate_multiplicative(self, ctx, left, op, right):
        if is_error(left) or is_error(right):
            return ERROR
        if is_numeric(left) and is_numeric(right):
            return self._numeric_result(left, right)
        self._error(ctx, f"el operador '{op}' requiere operandos numericos, se recibio '{left.name}' y '{right.name}'")
        return ERROR

    def _validate_relational(self, ctx, left, op, right):
        if not is_error(left) and not is_error(right) and not (is_numeric(left) and is_numeric(right)):
            self._error(ctx, f"el operador '{op}' requiere operandos numericos, se recibio '{left.name}' y '{right.name}'")
        return BOOLEAN

    @staticmethod
    def _comparable(a, b):
        if a == b:
            return True
        if is_numeric(a) and is_numeric(b):
            return True
        if isinstance(a, NullType) and isinstance(b, (ArrayType, ClassType)):
            return True
        if isinstance(b, NullType) and isinstance(a, (ArrayType, ClassType)):
            return True
        return False

    def _validate_equality(self, ctx, left, op, right):
        if not is_error(left) and not is_error(right) and not self._comparable(left, right):
            self._error(ctx, f"no se puede comparar '{left.name}' con '{right.name}' usando '{op}'")
        return BOOLEAN

    def _validate_logical(self, ctx, left, op, right):
        if not is_error(left) and left != BOOLEAN:
            self._error(ctx, f"el operando izquierdo de '{op}' debe ser boolean, se recibio '{left.name}'")
        if not is_error(right) and right != BOOLEAN:
            self._error(ctx, f"el operando derecho de '{op}' debe ser boolean, se recibio '{right.name}'")
        return BOOLEAN

    def exitMultiplicativeExpr(self, ctx: CompiscriptParser.MultiplicativeExprContext):
        self._binary_chain(ctx, ctx.unaryExpr(), self._validate_multiplicative)

    def exitAdditiveExpr(self, ctx: CompiscriptParser.AdditiveExprContext):
        self._binary_chain(ctx, ctx.multiplicativeExpr(), self._validate_additive)

    def exitRelationalExpr(self, ctx: CompiscriptParser.RelationalExprContext):
        self._binary_chain(ctx, ctx.additiveExpr(), self._validate_relational)

    def exitEqualityExpr(self, ctx: CompiscriptParser.EqualityExprContext):
        self._binary_chain(ctx, ctx.relationalExpr(), self._validate_equality)

    def exitLogicalAndExpr(self, ctx: CompiscriptParser.LogicalAndExprContext):
        self._binary_chain(ctx, ctx.equalityExpr(), self._validate_logical)

    def exitLogicalOrExpr(self, ctx: CompiscriptParser.LogicalOrExprContext):
        self._binary_chain(ctx, ctx.logicalAndExpr(), self._validate_logical)

    def exitTernaryExpr(self, ctx: CompiscriptParser.TernaryExprContext):
        cond_type = self._type(ctx.logicalOrExpr())
        branches = ctx.expression()
        if not branches:
            self._set_type(ctx, cond_type)
            return

        if not is_error(cond_type) and cond_type != BOOLEAN:
            self._error(ctx, f"la condicion del operador ternario debe ser boolean, se recibio '{cond_type.name}'")

        then_type = self._type(branches[0])
        else_type = self._type(branches[1])
        if is_error(then_type) or is_error(else_type):
            result = ERROR
        elif then_type == else_type:
            result = then_type
        elif is_numeric(then_type) and is_numeric(else_type):
            result = FLOAT
        else:
            self._error(
                ctx,
                f"las ramas del operador ternario tienen tipos incompatibles: "
                f"'{then_type.name}' y '{else_type.name}'",
            )
            result = ERROR
        self._set_type(ctx, result)

    def exitExprNoAssign(self, ctx: CompiscriptParser.ExprNoAssignContext):
        self._set_type(ctx, self._type(ctx.conditionalExpr()))

    def exitExpression(self, ctx: CompiscriptParser.ExpressionContext):
        self._set_type(ctx, self._type(ctx.assignmentExpr()))

    def exitPrimaryExpr(self, ctx: CompiscriptParser.PrimaryExprContext):
        if ctx.literalExpr() is not None:
            self._set_type(ctx, self._type(ctx.literalExpr()))
        elif ctx.leftHandSide() is not None:
            self._set_type(ctx, self._type(ctx.leftHandSide()))
        else:
            self._set_type(ctx, self._type(ctx.expression()))

    # ------------------------------------------------------------------
    # leftHandSide: primaryAtom seguido de llamadas, indices y propiedades
    # ------------------------------------------------------------------

    def _apply_call(self, suffix, callee_type):
        if not isinstance(callee_type, FunctionType):
            self._error(suffix, f"no se puede invocar un valor de tipo '{callee_type.name}'")
            return ERROR

        args = suffix.arguments().expression() if suffix.arguments() else []
        arg_types = [self._type(a) for a in args]
        expected = callee_type.param_types
        if len(arg_types) != len(expected):
            self._error(suffix, f"se esperaban {len(expected)} argumento(s) y se recibieron {len(arg_types)}")
        else:
            for i, (arg_type, expected_type) in enumerate(zip(arg_types, expected), start=1):
                if not is_assignable(expected_type, arg_type):
                    self._error(
                        suffix,
                        f"el argumento {i} debe ser de tipo '{expected_type.name}', se recibio '{arg_type.name}'",
                    )
        return callee_type.return_type

    def _apply_suffix(self, suffix, current):
        if is_error(current):
            return ERROR

        if isinstance(suffix, CompiscriptParser.IndexExprContext):
            if not isinstance(current, ArrayType):
                self._error(suffix, f"no se puede indexar un valor de tipo '{current.name}'")
                return ERROR
            index_type = self._type(suffix.expression())
            if not is_error(index_type) and index_type != INTEGER:
                self._error(suffix, f"el indice debe ser 'integer', se recibio '{index_type.name}'")
            return current.element_type

        if isinstance(suffix, CompiscriptParser.PropertyAccessExprContext):
            if not isinstance(current, ClassType) or current.class_symbol is None:
                self._error(suffix, f"no se puede acceder a una propiedad de un valor de tipo '{current.name}'")
                return ERROR
            member_name = suffix.Identifier().getText()
            member = current.class_symbol.resolve_member(member_name)
            if member is None:
                self._error(suffix, f"'{member_name}' no es un miembro de la clase '{current.class_symbol.name}'")
                return ERROR
            return member.type

        return self._apply_call(suffix, current)

    def exitLeftHandSide(self, ctx: CompiscriptParser.LeftHandSideContext):
        current = self._type(ctx.primaryAtom())
        for suffix in ctx.suffixOp():
            self.suffix_base[suffix] = current
            current = self._apply_suffix(suffix, current)
        self._set_type(ctx, current)

    def _lvalue_target(self, lhs_ctx: CompiscriptParser.LeftHandSideContext):
        """Simbolo al que apunta directamente un leftHandSide, si lo hay (para validar const)."""
        suffixes = lhs_ctx.suffixOp()
        if not suffixes:
            atom = lhs_ctx.primaryAtom()
            if isinstance(atom, CompiscriptParser.IdentifierExprContext):
                return self._resolve_name(atom.Identifier().getText())
            return None

        last = suffixes[-1]
        if isinstance(last, CompiscriptParser.PropertyAccessExprContext):
            base_type = self.suffix_base.get(last)
            if isinstance(base_type, ClassType) and base_type.class_symbol is not None:
                return base_type.class_symbol.resolve_member(last.Identifier().getText())
        return None

    # ------------------------------------------------------------------
    # asignacion dentro de una expresion (x = y = 5, arr[0] = 1, this.x = 1)
    # ------------------------------------------------------------------

    def exitAssignExpr(self, ctx: CompiscriptParser.AssignExprContext):
        value_type = self._type(ctx.assignmentExpr())
        target_type = self._type(ctx.leftHandSide())
        target_symbol = self._lvalue_target(ctx.leftHandSide())

        if target_symbol is not None and getattr(target_symbol, "is_const", False):
            self._error(ctx, f"no se puede asignar a '{target_symbol.name}', es una constante")
        elif not is_assignable(target_type, value_type):
            self._error(
                ctx,
                f"no se puede asignar un valor de tipo '{value_type.name}' a un destino de tipo '{target_type.name}'",
            )
        self._set_type(ctx, value_type)

    def exitPropertyAssignExpr(self, ctx: CompiscriptParser.PropertyAssignExprContext):
        value_type = self._type(ctx.assignmentExpr())
        base_type = self._type(ctx.leftHandSide())
        member_name = ctx.Identifier().getText()

        if is_error(base_type):
            self._set_type(ctx, value_type)
            return
        if not isinstance(base_type, ClassType) or base_type.class_symbol is None:
            self._error(ctx, f"no se puede acceder a una propiedad de un valor de tipo '{base_type.name}'")
            self._set_type(ctx, ERROR)
            return

        member = base_type.class_symbol.resolve_member(member_name)
        if member is None:
            self._error(ctx, f"'{member_name}' no es un miembro de la clase '{base_type.class_symbol.name}'")
            self._set_type(ctx, ERROR)
            return

        if isinstance(member, VariableSymbol) and member.is_const:
            self._error(ctx, f"no se puede asignar al campo constante '{member_name}'")
        elif not is_assignable(member.type, value_type):
            self._error(
                ctx,
                f"no se puede asignar un valor de tipo '{value_type.name}' a '{member_name}' "
                f"de tipo '{member.type.name}'",
            )
        self._set_type(ctx, value_type)

    # ------------------------------------------------------------------
    # condiciones de control de flujo: deben ser boolean
    # ------------------------------------------------------------------

    def _check_boolean_condition(self, expr_ctx, keyword: str):
        cond_type = self._type(expr_ctx)
        if not is_error(cond_type) and cond_type != BOOLEAN:
            self._error(expr_ctx, f"la condicion de '{keyword}' debe ser boolean, se recibio '{cond_type.name}'")

    def exitIfStatement(self, ctx: CompiscriptParser.IfStatementContext):
        self._check_boolean_condition(ctx.expression(), "if")

    def enterWhileStatement(self, ctx: CompiscriptParser.WhileStatementContext):
        self.loop_depth += 1

    def exitWhileStatement(self, ctx: CompiscriptParser.WhileStatementContext):
        self._check_boolean_condition(ctx.expression(), "while")
        self.loop_depth -= 1

    def enterDoWhileStatement(self, ctx: CompiscriptParser.DoWhileStatementContext):
        self.loop_depth += 1

    def exitDoWhileStatement(self, ctx: CompiscriptParser.DoWhileStatementContext):
        self._check_boolean_condition(ctx.expression(), "do-while")
        self.loop_depth -= 1

    def enterForeachStatement(self, ctx: CompiscriptParser.ForeachStatementContext):
        self.loop_depth += 1

    def exitForeachStatement(self, ctx: CompiscriptParser.ForeachStatementContext):
        self.loop_depth -= 1

    # ------------------------------------------------------------------
    # break / continue: solo dentro de bucles (break tambien en switch)
    # ------------------------------------------------------------------

    def exitBreakStatement(self, ctx: CompiscriptParser.BreakStatementContext):
        if self.loop_depth == 0 and self.switch_depth == 0:
            self._error(ctx, "'break' solo puede usarse dentro de un bucle o un switch")

    def exitContinueStatement(self, ctx: CompiscriptParser.ContinueStatementContext):
        if self.loop_depth == 0:
            self._error(ctx, "'continue' solo puede usarse dentro de un bucle")

    @staticmethod
    def _for_condition(ctx: CompiscriptParser.ForStatementContext):
        """La expresion de condicion del 'for', si fue escrita.

        `expression()` no distingue por si sola condicion de actualizacion
        cuando solo una de las dos esta presente, asi que se ubica primero
        el ';' que separa ambas (el ultimo ';' hijo directo del contexto).
        """
        children = ctx.children
        separator_index = None
        for i, child in enumerate(children):
            if isinstance(child, TerminalNode) and child.getText() == ";":
                separator_index = i

        for i, child in enumerate(children):
            if isinstance(child, CompiscriptParser.ExpressionContext) and i < separator_index:
                return child
        return None

    # ------------------------------------------------------------------
    # funciones
    # ------------------------------------------------------------------

    def enterFunctionDeclaration(self, ctx: CompiscriptParser.FunctionDeclarationContext):
        symbol = self._decl_symbols.pop(ctx, None)
        if symbol is not None:
            params = symbol.params
        else:
            name = ctx.Identifier().getText()
            return_type = self._resolve_type(ctx.type_()) if ctx.type_() else VOID

            params = []
            param_types = []
            if ctx.parameters():
                for param_ctx in ctx.parameters().parameter():
                    p_name = param_ctx.Identifier().getText()
                    p_type = self._resolve_type(param_ctx.type_()) if param_ctx.type_() else None
                    params.append(ParameterSymbol(p_name, p_type))
                    param_types.append(p_type)

            symbol = FunctionSymbol(name, param_types, return_type, params=params)
            if self.class_stack:
                symbol.owner_class = self.class_stack[-1]
            self._define(symbol, ctx, "funcion")

        # Una funcion anidada (incluso dentro de un metodo) se califica con la
        # etiqueta de la funcion que la contiene, para que sea unica.
        if self.function_stack:
            symbol.label = f"{self.function_stack[-1].label}.{symbol.name}"
        elif symbol.owner_class is not None:
            symbol.label = f"{symbol.owner_class.name}.{symbol.name}"
        symbol.frame = ActivationRecord(symbol.label)
        self.symbol_of[ctx] = symbol
        self.functions.append(symbol)
        if symbol.owner_class is not None:
            symbol.frame.allocate(ThisSlot(symbol.owner_class.type), "param")

        scope = self.table.enter_scope(ScopeKind.FUNCTION, owner=symbol)
        symbol.scope = scope
        self.function_stack.append(symbol)
        self.returns_seen.append(False)

        for param in params:
            self._define(param, ctx, "parametro")
            symbol.frame.allocate(param, "param")

    def exitFunctionDeclaration(self, ctx: CompiscriptParser.FunctionDeclarationContext):
        self.table.exit_scope()
        symbol = self.function_stack.pop()
        had_return = self.returns_seen.pop()

        if not had_return and symbol.return_type is not None and symbol.return_type != VOID:
            self._error(
                ctx,
                f"la funcion '{symbol.name}' declara tipo de retorno "
                f"'{symbol.return_type.name}' pero no tiene ninguna sentencia 'return'",
            )

    def exitReturnStatement(self, ctx: CompiscriptParser.ReturnStatementContext):
        if not self.function_stack:
            self._error(ctx, "'return' fuera de una funcion")
            return

        self.returns_seen[-1] = True
        function = self.function_stack[-1]
        expr = ctx.expression()
        value_type = self._type(expr) if expr is not None else VOID
        if not is_assignable(function.return_type, value_type):
            self._error(
                ctx,
                f"la funcion '{function.name}' debe retornar '{function.return_type.name}', "
                f"se recibio '{value_type.name}'",
            )

    # ------------------------------------------------------------------
    # clases
    # ------------------------------------------------------------------

    def enterClassDeclaration(self, ctx: CompiscriptParser.ClassDeclarationContext):
        identifiers = ctx.Identifier()
        name = identifiers[0].getText()

        superclass = None
        if len(identifiers) > 1:
            super_name = identifiers[1].getText()
            super_symbol = self.table.resolve(super_name)
            if not isinstance(super_symbol, ClassSymbol):
                self._error(ctx, f"clase base '{super_name}' no declarada")
            else:
                superclass = super_symbol

        symbol = ClassSymbol(name, superclass=superclass)
        self._define(symbol, ctx, "clase")
        self.symbol_of[ctx] = symbol

        scope = self.table.enter_scope(ScopeKind.CLASS, owner=symbol)
        symbol.scope = scope
        self.class_stack.append(symbol)

        # Los miembros se predeclaran para que un metodo pueda referirse a
        # 'this.campo' u otro metodo sin importar el orden en que aparecen
        # en el cuerpo de la clase.
        for member in ctx.classMember():
            if member.variableDeclaration():
                self._predeclare_field(symbol, member.variableDeclaration(), is_const=False)
            elif member.constantDeclaration():
                self._predeclare_field(symbol, member.constantDeclaration(), is_const=True)
            elif member.functionDeclaration():
                self._predeclare_method(symbol, member.functionDeclaration())

    def _predeclare_field(self, class_symbol: ClassSymbol, decl_ctx, is_const: bool):
        name = decl_ctx.Identifier().getText()
        declared_type = self._resolve_type(decl_ctx.typeAnnotation().type_()) if decl_ctx.typeAnnotation() else None
        symbol = VariableSymbol(name, declared_type, is_const=is_const, initialized=True)
        self._define(symbol, decl_ctx, "constante" if is_const else "variable")
        class_symbol.fields[name] = symbol
        self._decl_symbols[decl_ctx] = symbol

    def _predeclare_method(self, class_symbol: ClassSymbol, func_ctx: CompiscriptParser.FunctionDeclarationContext):
        name = func_ctx.Identifier().getText()
        return_type = self._resolve_type(func_ctx.type_()) if func_ctx.type_() else VOID

        params = []
        param_types = []
        if func_ctx.parameters():
            for param_ctx in func_ctx.parameters().parameter():
                p_name = param_ctx.Identifier().getText()
                p_type = self._resolve_type(param_ctx.type_()) if param_ctx.type_() else None
                params.append(ParameterSymbol(p_name, p_type))
                param_types.append(p_type)

        symbol = FunctionSymbol(name, param_types, return_type, params=params)
        symbol.owner_class = class_symbol
        self._define(symbol, func_ctx, "funcion")
        class_symbol.methods[name] = symbol
        self._decl_symbols[func_ctx] = symbol

    def exitClassDeclaration(self, ctx: CompiscriptParser.ClassDeclarationContext):
        self.class_stack.pop().compute_layout()
        self.table.exit_scope()

    # ------------------------------------------------------------------
    # for: la variable de control vive en su propio ambito
    # ------------------------------------------------------------------

    def enterForStatement(self, ctx: CompiscriptParser.ForStatementContext):
        self.table.enter_scope(ScopeKind.BLOCK)
        self.loop_depth += 1

    def exitForStatement(self, ctx: CompiscriptParser.ForStatementContext):
        condition = self._for_condition(ctx)
        if condition is not None:
            self._check_boolean_condition(condition, "for")
        self.table.exit_scope()
        self.loop_depth -= 1

    # ------------------------------------------------------------------
    # switch: el depth habilita 'break' dentro de sus casos
    # ------------------------------------------------------------------

    def enterSwitchStatement(self, ctx: CompiscriptParser.SwitchStatementContext):
        self.switch_depth += 1

    def exitSwitchStatement(self, ctx: CompiscriptParser.SwitchStatementContext):
        self.switch_depth -= 1

        switch_type = self._type(ctx.expression())
        if is_error(switch_type):
            return
        for case in ctx.switchCase():
            case_type = self._type(case.expression())
            if not is_error(case_type) and not self._comparable(switch_type, case_type):
                self._error(
                    case,
                    f"el valor del 'case' es de tipo '{case_type.name}', "
                    f"no compatible con el tipo del 'switch' ('{switch_type.name}')",
                )

    def exitSwitchCase(self, ctx: CompiscriptParser.SwitchCaseContext):
        self._check_dead_code(ctx.statement())

    def exitDefaultCase(self, ctx: CompiscriptParser.DefaultCaseContext):
        self._check_dead_code(ctx.statement())
