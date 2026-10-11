"""Generacion de codigo de tres direcciones a partir del arbol ya analizado.

Corre despues de `SemanticAnalyzer`: no vuelve a resolver nombres ni tipos,
sino que consulta lo que el analizador dejo anotado (`symbol_of`, `types`,
`suffix_base`) y las direcciones de la tabla de simbolos. Es un Visitor:
cada `visitX` de una sentencia emite cuadruplos, y cada `visitX` de una
expresion los emite y devuelve el operando (variable, temporal o literal)
donde queda su valor.

Cada funcion se genera en su propio bufer con su propio asignador de
temporales; al final se concatenan las funciones y luego `main`, que
contiene las sentencias de nivel superior.
"""

from antlr4.tree.Tree import ParseTreeVisitor, TerminalNode

from CompiscriptParser import CompiscriptParser
from analyzer import SemanticAnalyzer
from ir import tac
from ir.tac import Quad, TACProgram, TempAllocator, LabelAllocator
from semantic import FunctionSymbol, ClassSymbol, FunctionType, VOID


class _FunctionContext:
    """Estado de generacion de la funcion en curso (o de `main`)."""

    def __init__(self, label: str, symbol: FunctionSymbol = None):
        self.label = label
        self.symbol = symbol
        self.code = TACProgram()
        self.temps = TempAllocator()
        # (etiqueta de break, etiqueta de continue) de los bucles/switch abiertos;
        # un switch no tiene continue (None), asi que 'continue' busca el bucle mas cercano.
        self.jumps: list[tuple[str, str]] = []


class TACGenerator(ParseTreeVisitor):
    def __init__(self, analyzer):
        self.analyzer = analyzer
        self.labels = LabelAllocator()
        self.functions: list[_FunctionContext] = []  # funciones ya generadas, en orden
        self._stack: list[_FunctionContext] = []
        self._names = {}  # id(simbolo) -> nombre unico en el TAC
        self._used_names = {}  # etiqueta de funcion / 'global' -> nombres ya usados

    # ------------------------------------------------------------------
    # punto de entrada
    # ------------------------------------------------------------------

    def generate(self, tree) -> TACProgram:
        main = _FunctionContext("main")
        self._stack.append(main)
        begin = self._emit(Quad(tac.FUNC_BEGIN, "main", "0"))
        self.visit(tree)
        self._emit(Quad(tac.FUNC_END, "main"))
        begin.arg2 = str(main.temps.max_live * 8)
        self._stack.pop()

        program = TACProgram()
        for function in self.functions + [main]:
            for quad in function.code:
                program.emit(quad)
        return program

    # ------------------------------------------------------------------
    # utilidades
    # ------------------------------------------------------------------

    @property
    def _current(self) -> _FunctionContext:
        return self._stack[-1]

    def _emit(self, quad: Quad) -> Quad:
        code = self._current.code.code
        # Un 'goto' justo despues de otro salto incondicional nunca se ejecuta
        # (p.ej. el 'goto fin' de un if cuyo bloque termina en 'break').
        if quad.op == tac.GOTO and code and code[-1].op in (tac.GOTO, tac.RETURN):
            return quad
        return self._current.code.emit(quad)

    def _new_temp(self) -> str:
        return self._current.temps.new()

    def _release(self, *operands):
        self._current.temps.release(*operands)

    def _label(self) -> str:
        return self.labels.new()

    def _place_label(self, label: str):
        self._emit(Quad(tac.LABEL, result=label))

    def _name(self, symbol) -> str:
        """Nombre del simbolo en el TAC; se desambiguan variables que se ocultan entre si."""
        key = id(symbol)
        if key not in self._names:
            area = "global" if symbol.storage == "global" else self._current.label
            used = self._used_names.setdefault(area, set())
            global_used = self._used_names.setdefault("global", set())
            name = symbol.name
            suffix = 1
            while name in used or (area != "global" and name in global_used):
                name = f"{symbol.name}_{suffix}"
                suffix += 1
            used.add(name)
            self._names[key] = name
        return self._names[key]

    def _binary(self, operator: str, left: str, right: str) -> str:
        self._release(left, right)
        result = self._new_temp()
        self._emit(Quad(tac.BINARY, left, right, result, operator=operator))
        return result

    def _value_into(self, target: str, value: str):
        if target != value:
            self._emit(Quad(tac.ASSIGN, value, result=target))

    def _store_variable(self, symbol, value: str):
        """Guarda `value` en la variable `symbol` (un campo se guarda a traves de `this`)."""
        if symbol.storage == "field":
            self._emit(Quad(tac.STORE_FIELD, symbol.name, value, "this"))
        else:
            self._value_into(self._name(symbol), value)

    def visit(self, tree):
        # Despacho por nombre de contexto (FooContext -> visitFoo), para no depender
        # de que el parser se haya generado con la opcion -visitor de ANTLR.
        if isinstance(tree, TerminalNode):
            return None
        name = type(tree).__name__
        method = getattr(self, "visit" + name[: -len("Context")], None)
        if method is not None:
            return method(tree)
        return self.visitChildren(tree)

    def visitChildren(self, node):
        for child in node.getChildren():
            self.visit(child)
        return None

    # ------------------------------------------------------------------
    # sentencias simples
    # ------------------------------------------------------------------

    def visitVariableDeclaration(self, ctx: CompiscriptParser.VariableDeclarationContext):
        symbol = self.analyzer.symbol_of.get(ctx)
        if symbol is None or symbol.storage == "field" or ctx.initializer() is None:
            return
        value = self.visit(ctx.initializer().expression())
        self._release(value)
        self._store_variable(symbol, value)

    def visitConstantDeclaration(self, ctx: CompiscriptParser.ConstantDeclarationContext):
        symbol = self.analyzer.symbol_of.get(ctx)
        if symbol is None or symbol.storage == "field":
            return
        value = self.visit(ctx.expression())
        self._release(value)
        self._store_variable(symbol, value)

    def visitAssignment(self, ctx: CompiscriptParser.AssignmentContext):
        expressions = ctx.expression()
        if len(expressions) == 1:
            value = self.visit(expressions[0])
            self._release(value)
            self._store_variable(self.analyzer.symbol_of[ctx], value)
            return

        target = self.visit(expressions[0])
        value = self.visit(expressions[1])
        self._release(target, value)
        self._emit(Quad(tac.STORE_FIELD, ctx.Identifier().getText(), value, target))

    def visitExpressionStatement(self, ctx: CompiscriptParser.ExpressionStatementContext):
        self._release(self.visit(ctx.expression()))

    def visitPrintStatement(self, ctx: CompiscriptParser.PrintStatementContext):
        value = self.visit(ctx.expression())
        self._release(value)
        self._emit(Quad(tac.PRINT, value))

    # ------------------------------------------------------------------
    # control de flujo
    # ------------------------------------------------------------------

    def _jump_if_false(self, expr_ctx, label: str):
        condition = self.visit(expr_ctx)
        self._release(condition)
        self._emit(Quad(tac.IF_FALSE, condition, result=label))

    def visitIfStatement(self, ctx: CompiscriptParser.IfStatementContext):
        blocks = ctx.block()
        end_label = self._label()
        else_label = self._label() if len(blocks) > 1 else end_label

        self._jump_if_false(ctx.expression(), else_label)
        self.visit(blocks[0])
        if len(blocks) > 1:
            self._emit(Quad(tac.GOTO, result=end_label))
            self._place_label(else_label)
            self.visit(blocks[1])
        self._place_label(end_label)

    def _loop_body(self, block, break_label: str, continue_label: str):
        self._current.jumps.append((break_label, continue_label))
        self.visit(block)
        self._current.jumps.pop()

    def visitWhileStatement(self, ctx: CompiscriptParser.WhileStatementContext):
        start_label, end_label = self._label(), self._label()
        self._place_label(start_label)
        self._jump_if_false(ctx.expression(), end_label)
        self._loop_body(ctx.block(), end_label, start_label)
        self._emit(Quad(tac.GOTO, result=start_label))
        self._place_label(end_label)

    def visitDoWhileStatement(self, ctx: CompiscriptParser.DoWhileStatementContext):
        start_label, condition_label, end_label = self._label(), self._label(), self._label()
        self._place_label(start_label)
        self._loop_body(ctx.block(), end_label, condition_label)
        self._place_label(condition_label)
        condition = self.visit(ctx.expression())
        self._release(condition)
        self._emit(Quad(tac.IF, condition, result=start_label))
        self._place_label(end_label)

    def visitForStatement(self, ctx: CompiscriptParser.ForStatementContext):
        if ctx.variableDeclaration() is not None:
            self.visit(ctx.variableDeclaration())
        elif ctx.assignment() is not None:
            self.visit(ctx.assignment())

        condition = SemanticAnalyzer._for_condition(ctx)
        update = next((e for e in ctx.expression() if e is not condition), None)

        start_label, update_label, end_label = self._label(), self._label(), self._label()
        self._place_label(start_label)
        if condition is not None:
            self._jump_if_false(condition, end_label)
        self._loop_body(ctx.block(), end_label, update_label)
        self._place_label(update_label)
        if update is not None:
            self._release(self.visit(update))
        self._emit(Quad(tac.GOTO, result=start_label))
        self._place_label(end_label)

    def visitBreakStatement(self, ctx: CompiscriptParser.BreakStatementContext):
        self._emit(Quad(tac.GOTO, result=self._current.jumps[-1][0]))

    def visitContinueStatement(self, ctx: CompiscriptParser.ContinueStatementContext):
        target = next(cont for _, cont in reversed(self._current.jumps) if cont is not None)
        self._emit(Quad(tac.GOTO, result=target))

    def visitSwitchStatement(self, ctx: CompiscriptParser.SwitchStatementContext):
        """Se compara el valor contra cada 'case' en orden y se salta al primero que coincida.

        Los cuerpos se emiten uno tras otro, asi que sin 'break' la ejecucion
        continua en el siguiente caso (fall-through), como en TypeScript.
        """
        value = self.visit(ctx.expression())
        cases = ctx.switchCase()
        case_labels = [self._label() for _ in cases]
        default_label = self._label() if ctx.defaultCase() is not None else None
        end_label = self._label()

        for case, label in zip(cases, case_labels):
            case_value = self.visit(case.expression())
            self._release(case_value)
            test = self._new_temp()
            self._emit(Quad(tac.BINARY, value, case_value, test, operator="=="))
            self._release(test)
            self._emit(Quad(tac.IF, test, result=label))
        self._release(value)
        self._emit(Quad(tac.GOTO, result=default_label or end_label))

        self._current.jumps.append((end_label, None))
        for case, label in zip(cases, case_labels):
            self._place_label(label)
            for statement in case.statement():
                self.visit(statement)
        if default_label is not None:
            self._place_label(default_label)
            for statement in ctx.defaultCase().statement():
                self.visit(statement)
        self._current.jumps.pop()
        self._place_label(end_label)

    # ------------------------------------------------------------------
    # funciones
    # ------------------------------------------------------------------

    def visitFunctionDeclaration(self, ctx: CompiscriptParser.FunctionDeclarationContext):
        symbol: FunctionSymbol = self.analyzer.symbol_of[ctx]
        function = _FunctionContext(symbol.label, symbol)
        self._stack.append(function)

        begin = self._emit(Quad(tac.FUNC_BEGIN, symbol.label, "0"))
        for param in symbol.params:
            self._name(param)
        self.visit(ctx.block())
        if not function.code.code or function.code.code[-1].op != tac.RETURN:
            self._emit(Quad(tac.RETURN))
        self._emit(Quad(tac.FUNC_END, symbol.label))

        symbol.frame.temp_count = function.temps.max_live
        begin.arg2 = str(symbol.frame.total_size)
        self._stack.pop()
        self.functions.append(function)

    def visitReturnStatement(self, ctx: CompiscriptParser.ReturnStatementContext):
        if ctx.expression() is None:
            self._emit(Quad(tac.RETURN))
            return
        value = self.visit(ctx.expression())
        self._release(value)
        self._emit(Quad(tac.RETURN, value))

    def _call(self, label: str, args: list, this: str = None, returns_value: bool = True) -> str:
        """Evalua los argumentos, emite los `param` y la llamada; devuelve el operando del resultado."""
        values = [self.visit(a) for a in args]
        if this is not None:
            values.insert(0, this)
        for value in values:
            self._emit(Quad(tac.PARAM, value))
        self._release(*values)
        result = self._new_temp() if returns_value else None
        self._emit(Quad(tac.CALL, label, str(len(values)), result))
        return result

    # ------------------------------------------------------------------
    # expresiones: cada visit devuelve el operando con el valor
    # ------------------------------------------------------------------

    def visitExpression(self, ctx: CompiscriptParser.ExpressionContext):
        return self.visit(ctx.assignmentExpr())

    def visitExprNoAssign(self, ctx: CompiscriptParser.ExprNoAssignContext):
        return self.visit(ctx.conditionalExpr())

    def visitTernaryExpr(self, ctx: CompiscriptParser.TernaryExprContext):
        condition = self.visit(ctx.logicalOrExpr())
        branches = ctx.expression()
        if not branches:
            return condition

        else_label, end_label = self._label(), self._label()
        self._release(condition)
        self._emit(Quad(tac.IF_FALSE, condition, result=else_label))
        result = self._new_temp()
        then_value = self.visit(branches[0])
        self._release(then_value)
        self._value_into(result, then_value)
        self._emit(Quad(tac.GOTO, result=end_label))
        self._place_label(else_label)
        else_value = self.visit(branches[1])
        self._release(else_value)
        self._value_into(result, else_value)
        self._place_label(end_label)
        return result

    def _short_circuit(self, ctx, operands, jump_op):
        """`&&` / `||`: el operando derecho solo se evalua si el izquierdo no decide el resultado."""
        value = self.visit(operands[0])
        if len(operands) == 1:
            return value

        end_label = self._label()
        self._release(value)
        result = self._new_temp()
        self._value_into(result, value)
        for operand in operands[1:]:
            self._emit(Quad(jump_op, result, result=end_label))
            right = self.visit(operand)
            self._release(right)
            self._value_into(result, right)
        self._place_label(end_label)
        return result

    def visitLogicalOrExpr(self, ctx: CompiscriptParser.LogicalOrExprContext):
        return self._short_circuit(ctx, ctx.logicalAndExpr(), tac.IF)

    def visitLogicalAndExpr(self, ctx: CompiscriptParser.LogicalAndExprContext):
        return self._short_circuit(ctx, ctx.equalityExpr(), tac.IF_FALSE)

    def _binary_chain(self, ctx, operands):
        result = self.visit(operands[0])
        for i in range(1, len(operands)):
            operator = ctx.getChild(2 * i - 1).getText()
            right = self.visit(operands[i])
            result = self._binary(operator, result, right)
        return result

    def visitEqualityExpr(self, ctx: CompiscriptParser.EqualityExprContext):
        return self._binary_chain(ctx, ctx.relationalExpr())

    def visitRelationalExpr(self, ctx: CompiscriptParser.RelationalExprContext):
        return self._binary_chain(ctx, ctx.additiveExpr())

    def visitAdditiveExpr(self, ctx: CompiscriptParser.AdditiveExprContext):
        return self._binary_chain(ctx, ctx.multiplicativeExpr())

    def visitMultiplicativeExpr(self, ctx: CompiscriptParser.MultiplicativeExprContext):
        return self._binary_chain(ctx, ctx.unaryExpr())

    def visitUnaryExpr(self, ctx: CompiscriptParser.UnaryExprContext):
        if ctx.unaryExpr() is None:
            return self.visit(ctx.primaryExpr())
        operand = self.visit(ctx.unaryExpr())
        self._release(operand)
        result = self._new_temp()
        self._emit(Quad(tac.UNARY, operand, result=result, operator=ctx.getChild(0).getText()))
        return result

    def visitPrimaryExpr(self, ctx: CompiscriptParser.PrimaryExprContext):
        if ctx.literalExpr() is not None:
            return self.visit(ctx.literalExpr())
        if ctx.leftHandSide() is not None:
            return self.visit(ctx.leftHandSide())
        return self.visit(ctx.expression())

    def visitLiteralExpr(self, ctx: CompiscriptParser.LiteralExprContext):
        if ctx.arrayLiteral() is not None:
            return self.visit(ctx.arrayLiteral())
        return ctx.getText()

    # ------------------------------------------------------------------
    # leftHandSide: identificadores, llamadas, indices y propiedades
    # ------------------------------------------------------------------

    def visitIdentifierExpr(self, ctx: CompiscriptParser.IdentifierExprContext):
        symbol = self.analyzer.symbol_of.get(ctx)
        if symbol is not None and symbol.storage == "field":
            result = self._new_temp()
            self._emit(Quad(tac.LOAD_FIELD, "this", symbol.name, result))
            return result
        if symbol is None or isinstance(symbol, FunctionSymbol):
            return ctx.getText()
        return self._name(symbol)

    def visitThisExpr(self, ctx: CompiscriptParser.ThisExprContext):
        return "this"

    def visitLeftHandSide(self, ctx: CompiscriptParser.LeftHandSideContext):
        return self._eval_lhs(ctx, len(ctx.suffixOp()))

    def _eval_lhs(self, ctx: CompiscriptParser.LeftHandSideContext, count: int) -> str:
        """Evalua el atomo y los primeros `count` sufijos de un leftHandSide."""
        atom = ctx.primaryAtom()
        suffixes = ctx.suffixOp()

        if count > 0 and isinstance(atom, CompiscriptParser.IdentifierExprContext) \
                and isinstance(suffixes[0], CompiscriptParser.CallExprContext):
            current = self._call_function(atom, suffixes[0])
            start = 1
        else:
            current = self.visit(atom)
            start = 0

        i = start
        while i < count:
            suffix = suffixes[i]
            if isinstance(suffix, CompiscriptParser.PropertyAccessExprContext) and i + 1 < count \
                    and isinstance(suffixes[i + 1], CompiscriptParser.CallExprContext):
                current = self._call_method(current, suffix, suffixes[i + 1])
                i += 2
                continue
            current = self._apply_suffix(current, suffix)
            i += 1
        return current

    def _returns_value(self, function_type) -> bool:
        return not (isinstance(function_type, FunctionType) and function_type.return_type == VOID)

    def _call_function(self, atom, call: CompiscriptParser.CallExprContext) -> str:
        symbol = self.analyzer.symbol_of.get(atom)
        args = call.arguments().expression() if call.arguments() else []
        if isinstance(symbol, FunctionSymbol):
            # Un metodo invocado sin 'this.' dentro de su propia clase recibe el 'this' actual.
            this = "this" if symbol.owner_class is not None else None
            return self._call(symbol.label, args, this, self._returns_value(symbol.type))
        return self._call(atom.getText(), args)

    def _call_method(self, receiver: str, access, call) -> str:
        base_type = self.analyzer.suffix_base.get(access)
        method_name = access.Identifier().getText()
        method = None
        if base_type is not None and getattr(base_type, "class_symbol", None) is not None:
            method = base_type.class_symbol.resolve_member(method_name)
        label = method.label if isinstance(method, FunctionSymbol) else method_name
        args = call.arguments().expression() if call.arguments() else []
        returns_value = self._returns_value(method.type) if method is not None else True
        return self._call(label, args, receiver, returns_value)

    def _apply_suffix(self, current: str, suffix) -> str:
        if isinstance(suffix, CompiscriptParser.IndexExprContext):
            index = self.visit(suffix.expression())
            self._release(current, index)
            result = self._new_temp()
            self._emit(Quad(tac.LOAD_INDEX, current, index, result))
            return result

        if isinstance(suffix, CompiscriptParser.PropertyAccessExprContext):
            self._release(current)
            result = self._new_temp()
            self._emit(Quad(tac.LOAD_FIELD, current, suffix.Identifier().getText(), result))
            return result

        # llamada sobre un valor ya calculado (p.ej. el resultado de otra llamada)
        args = suffix.arguments().expression() if suffix.arguments() else []
        self._release(current)
        return self._call(current, args)

    # ------------------------------------------------------------------
    # asignacion como expresion: x = v, a[i] = v, o.f = v
    # ------------------------------------------------------------------

    def visitAssignExpr(self, ctx: CompiscriptParser.AssignExprContext):
        lhs = ctx.leftHandSide()
        suffixes = lhs.suffixOp()

        if not suffixes:
            value = self.visit(ctx.assignmentExpr())
            atom = lhs.primaryAtom()
            symbol = self.analyzer.symbol_of.get(atom)
            if symbol.storage == "field":
                self._store_variable(symbol, value)
                return value
            self._release(value)
            self._store_variable(symbol, value)
            return self._name(symbol)

        base = self._eval_lhs(lhs, len(suffixes) - 1)
        last = suffixes[-1]
        if isinstance(last, CompiscriptParser.IndexExprContext):
            index = self.visit(last.expression())
            value = self.visit(ctx.assignmentExpr())
            self._emit(Quad(tac.STORE_INDEX, index, value, base))
            self._release(base, index)
        else:
            value = self.visit(ctx.assignmentExpr())
            self._emit(Quad(tac.STORE_FIELD, last.Identifier().getText(), value, base))
            self._release(base)
        return value

    def visitPropertyAssignExpr(self, ctx: CompiscriptParser.PropertyAssignExprContext):
        base = self.visit(ctx.leftHandSide())
        value = self.visit(ctx.assignmentExpr())
        self._emit(Quad(tac.STORE_FIELD, ctx.Identifier().getText(), value, base))
        self._release(base)
        return value
