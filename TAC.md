# Código intermedio: TAC de Compiscript

Este documento describe la representación intermedia (IR) que genera el
compilador: un **código de tres direcciones (TAC)** almacenado como
**cuádruplos** `(op, arg1, arg2, result)`. Incluye el conjunto de
instrucciones, la organización de memoria (tabla de símbolos con
direcciones y registros de activación), el algoritmo de temporales,
ejemplos de traducción de cada construcción y los supuestos tomados.

Para generarlo:

```bash
python3 Driver.py programa.cps --tac            # TAC a stdout
python3 Driver.py programa.cps --tac salida.tac # TAC a un archivo
python3 Driver.py programa.cps --symbols        # tabla de símbolos con direcciones
```

El TAC solo se genera si el programa no tiene errores léxicos,
sintácticos ni semánticos.

## 1. Instrucciones

Cada instrucción es un cuádruplo (`ir/tac.py`, clase `Quad`). Los operandos
son nombres de variables, temporales (`t1`, `t2`, …), etiquetas (`L1`,
`L2`, …) o literales tal como aparecen en el fuente (`10`, `3.14`,
`"hola"`, `true`, `false`, `null`).

| Forma textual | op | arg1 | arg2 | result | Significado |
|---|---|---|---|---|---|
| `x = y` | `=` | y | | x | copia |
| `x = y op z` | `op` | y | z | x | binaria: `+ - * / %`, `< <= > >=`, `== !=` |
| `x = op y` | `op` | y | | x | unaria: `-` (negación), `!` (not) |
| `L:` | `label` | | | L | define una etiqueta |
| `goto L` | `goto` | | | L | salto incondicional |
| `if x goto L` | `if` | x | | L | salta si `x` es verdadero |
| `ifFalse x goto L` | `ifFalse` | x | | L | salta si `x` es falso |
| `param x` | `param` | x | | | apila un argumento |
| `x = call f, n` | `call` | f | n | x | llama a `f` con los últimos `n` params (sin `x =` si es `void`) |
| `return [x]` | `return` | x | | | retorna de la función |
| `begin_func f, k` | `begin_func` | f | k | | inicio de la función `f`; `k` = bytes de su registro de activación |
| `end_func f` | `end_func` | f | | | fin de la función `f` |
| `x = a[i]` | `load_index` | a | i | x | lee un elemento de un arreglo |
| `a[i] = x` | `store_index` | i | x | a | escribe un elemento de un arreglo |
| `x = o.f` | `load_field` | o | f | x | lee un campo de un objeto |
| `o.f = x` | `store_field` | f | x | o | escribe un campo de un objeto |
| `x = new C, k` | `new` | C | k | x | reserva `k` bytes para un objeto de la clase `C` y guarda la vtable de `C` en su offset 0 |
| `x = new_array n` | `new_array` | n | | x | reserva un arreglo de `n` elementos |
| `x = len a` | `len` | a | | x | longitud de un arreglo |
| `print x` | `print` | x | | | imprime un valor |
| `try_begin L` | `try_begin` | | | L | registra `L` como manejador de errores |
| `try_end` | `try_end` | | | | quita el manejador registrado |
| `x = catch` | `catch` | | | x | obtiene el error capturado |

`TACProgram.lines()` imprime la forma textual, y `Quad.as_row()` da la
fila del cuádruplo (para mostrarlo como tabla).

## 2. Organización de memoria

La tabla de símbolos (`semantic/`) se amplió para que cada variable tenga
una **dirección relativa** dentro de un área de memoria (`storage`,
`offset`, `size` en `Symbol`).

### Tamaños (`semantic/runtime.py`, `size_of`)

| Tipo | Bytes |
|---|---|
| `integer`, `boolean` | 4 |
| `float` | 8 |
| `string`, arreglos, objetos, `null` (referencias) | 8 (un puntero) |

### Áreas

- **Segmento global** (`storage = global`): las variables de nivel
  superior, con offsets crecientes en orden de declaración. Incluye las
  variables de `for`/`foreach`/bloques que estén fuera de una función.
- **Registro de activación** (`ActivationRecord`, uno por función o
  método):

  ```
  [ this (solo métodos) | parámetros | locales | temporales ]
    offset 0 ──────────────────────────────────────────────▶
  ```

  Las variables declaradas en bloques anidados (`if`, `while`, `for`, …)
  se ubican en el registro de la función que las contiene. El tamaño
  total, que se emite en `begin_func f, k`, es
  `variables + temporales × 8`. La cantidad de temporales es el máximo
  de temporales vivos a la vez dentro de la función.
- **Objetos** (`ClassSymbol.compute_layout`):

  ```
  [ vtable | campos heredados | campos propios ]
    offset 0
  ```

  Un objeto de una subclase empieza con el mismo layout que su
  superclase, así que un `Perro` se puede usar donde se espera un
  `Animal`.
- **Vtable** (`ClassSymbol.vtable()`): un slot por cada método. Un método
  que sobrescribe a otro conserva el slot del método original. El
  constructor no está en la vtable porque siempre se llama directo.

Ejemplo (`python3 Driver.py program.cps --symbols`):

```
== clase Animal (16 bytes por instancia)
  field  vtable       puntero      offset=0    size=8
  field  nombre       string       offset=8    size=8
  vtable: [0] Animal.hablar
== registro de activacion de Animal.constructor (16 bytes de variables + 0 temporales = 16 bytes)
  param  this         Animal       offset=0    size=8
  param  nombre       string       offset=8    size=8
```

## 3. Temporales

`TempAllocator` (`ir/tac.py`) mantiene una lista de temporales libres:

1. Cuando una instrucción consume un temporal como operando, ese temporal
   se **libera** (cada temporal de una expresión tiene un único uso).
2. Al pedir un temporal nuevo se entrega el **libre de menor número**, y
   solo se crea uno nuevo si no hay libres.
3. Los operandos se liberan **antes** de pedir el resultado, así que una
   operación puede reutilizar uno de sus operandos (`t1 = t1 + t2`).
4. Cada función tiene su propio asignador. `max_live`, el máximo de
   temporales vivos a la vez, define cuánto espacio de temporales lleva
   su registro de activación.

Hay casos en que un temporal debe sobrevivir a varias instrucciones: el
objeto recién creado por `new` mientras se evalúan los argumentos del
constructor, y el índice y la longitud de un `foreach` durante todo el
bucle. Esos temporales se liberan de forma explícita al terminar.

```
let c: integer = (a * b) + (a - b) * 4;
```
```
t1 = a * b
t2 = a - b
t2 = t2 * 4
t1 = t1 + t2
c = t1
```

## 4. Traducción de cada construcción

### Expresiones lógicas (cortocircuito)

`a && b`: si `a` es falso, `b` no se evalúa.
`a || b`: si `a` es verdadero, `b` no se evalúa.

```
t1 = a                 t1 = a
ifFalse t1 goto L1     if t1 goto L1
t1 = b                 t1 = b
L1:                    L1:
```

### Ternario `c ? x : y`

```
ifFalse c goto L1
t1 = x
goto L2
L1:
t1 = y
L2:
```

### if / else, while, do-while, for

```
if (c) {A} else {B}     while (c) {A}        do {A} while (c);     for (init; c; upd) {A}
  ifFalse c goto L2     L1:                  L1:                   init
  A                       ifFalse c goto L2    A                   L1:
  goto L1                 A                  L2:  (continue)         ifFalse c goto L3
L2:                       goto L1              if c goto L1          A
  B                     L2:                  L3:  (break)          L2:  (continue)
L1:                                                                  upd
                                                                     goto L1
                                                                   L3:  (break)
```

`break` salta a la etiqueta de salida del bucle o `switch` más interno, y
`continue` a la etiqueta de continuación del bucle más interno (que en un
`for` es la actualización). El generador omite un `goto` que quedaría
justo después de otro salto incondicional, porque nunca se ejecutaría.

### switch

Se compara el valor contra cada `case` en orden y se salta al primero que
coincida. Los cuerpos se emiten uno después del otro, así que **sin
`break` la ejecución continúa en el caso siguiente** (fall-through, como
en TypeScript).

```
t1 = i == 1
if t1 goto L1
t1 = i == 2
if t1 goto L2
goto L3           ; default (o fin, si no hay default)
L1: ...  goto L4  ; break
L2: ...
L3: ...           ; default
L4:
```

### foreach

`foreach (x in arr)` se traduce a un recorrido por índice:

```
t1 = 0
t2 = len arr
L1:
t3 = t1 < t2
ifFalse t3 goto L3
x = arr[t1]
...cuerpo...
L2:               ; continue
t1 = t1 + 1
goto L1
L3:               ; break
```

### Funciones y llamadas

Primero se evalúan **todos** los argumentos y después se emiten los
`param` seguidos, para que una llamada anidada no intercale sus `param`
con los de la llamada externa. Una función sin `return` final recibe un
`return` implícito.

```
function fact(n: integer): integer {
  if (n <= 1) { return 1; }
  return n * fact(n - 1);
}
let x: integer = (2 + 3) * (4 - 1);
print(fact(x));
```
```
begin_func fact, 12
    t1 = n <= 1
    ifFalse t1 goto L1
    return 1
L1:
    t1 = n - 1
    param t1
    t1 = call fact, 1
    t1 = n * t1
    return t1
end_func fact
begin_func main, 16
    t1 = 2 + 3
    t2 = 4 - 1
    t1 = t1 * t2
    x = t1
    param x
    t1 = call fact, 1
    print t1
end_func main
```

### Clases, objetos y métodos

- Un método recibe `this` como primer parámetro implícito. Su etiqueta es
  `Clase.metodo`.
- `new C(args)` reserva el objeto, llama a `C.__init` si la clase o una
  de sus superclases tiene campos con valor inicial, y luego llama al
  constructor (propio o heredado) con `this` y los argumentos.
- `C.__init` llama primero al `__init` de la superclase y después asigna
  los campos propios que tienen valor inicial.
- **Llamada directa o virtual:** si ninguna subclase del tipo estático
  del receptor sobrescribe el método, se llama directo
  (`call Perro.hablar`). Si alguna lo sobrescribe, la llamada pasa por la
  vtable del objeto (despacho dinámico).
- Dentro de un método, un campo o método escrito sin `this.` se traduce
  igual que si llevara `this.`.

```
class Animal {
  let nombre: string;
  let patas: integer = 4;
  function constructor(nombre: string) { this.nombre = nombre; }
  function hablar(): string { return this.nombre + " hace ruido."; }
}
class Perro : Animal {
  function hablar(): string { return this.nombre + " ladra."; }
}
let a: Animal = new Perro("Toby");
print(a.hablar());
```
```
begin_func Animal.__init, 8
    this.patas = 4
    return
end_func Animal.__init
begin_func Perro.__init, 8
    param this
    call Animal.__init, 1
    return
end_func Perro.__init
...
begin_func main, 8
    t1 = new Perro, 20
    param t1
    call Perro.__init, 1
    param t1
    param "Toby"
    call Animal.constructor, 2
    a = t1
    t1 = a.vtable       ; despacho dinámico: Perro sobrescribe hablar
    t1 = t1[0]          ; slot 0 de la vtable
    param a
    t1 = call t1, 1
    print t1
end_func main
```

### Arreglos

```
let m: integer[][] = [[1, 2], [3, 4]];
print(m[1][0]);
```
```
t1 = new_array 2
t2 = new_array 2
t2[0] = 1
t2[1] = 2
t1[0] = t2
...
m = t1
t1 = m[1]
t1 = t1[0]
print t1
```

### try / catch

```
try_begin L1      ; un error en tiempo de ejecución salta a L1
...cuerpo del try...
try_end
goto L2
L1:
err = catch       ; la variable del catch recibe el error
...cuerpo del catch...
L2:
```

## 5. Supuestos y decisiones de diseño

1. **Programa = funciones + `main`.** Las funciones y métodos se emiten
   primero, cada uno entre `begin_func` y `end_func`. Las sentencias de
   nivel superior forman `main`, que se emite al final.
2. **Nombres.** Las variables aparecen en el TAC por su nombre. Si una
   variable de un bloque o función oculta a otra con el mismo nombre, se
   le agrega un sufijo (`x_1`) para que el TAC no sea ambiguo. La
   dirección real de cada nombre está en la tabla de símbolos
   (`--symbols`).
3. **Índices de arreglo.** `a[i]` usa el índice del elemento, no un
   desplazamiento en bytes. El backend multiplica por el tamaño del
   elemento y verifica los límites. Un acceso fuera de rango es un error
   en tiempo de ejecución que transfiere el control al manejador activo
   (`try_begin`).
4. **Campos por nombre.** `o.f` usa el nombre del campo. Su offset es fijo
   y está en el layout de la clase (sección 2), así que el backend lo
   resuelve sin ambigüedad.
5. **Tipos en las operaciones.** Los operadores no llevan el tipo
   (`t1 = a + b` sirve para `integer`, `float` y concatenación de
   `string`). El tipo de cada operando ya fue verificado en el análisis
   semántico y está en la tabla de símbolos. Un `integer` mezclado con un
   `float` se promueve a `float`.
6. **Booleanos.** Las condiciones se evalúan a un valor booleano y se
   usan con `if`/`ifFalse`. Los operadores `&&` y `||` se evalúan en
   cortocircuito.
7. **Paso de parámetros por valor.** Para objetos y arreglos lo que se
   copia es la referencia.
8. **Funciones anidadas.** Reciben una etiqueta calificada
   (`externa.interna`) y su propio registro de activación. El acceso a
   variables de la función externa se representa por su nombre, y se
   asume un enlace de acceso (*access link*) en tiempo de ejecución.
9. **`switch`** tiene fall-through entre casos (supuesto, el enunciado no
   lo define).
