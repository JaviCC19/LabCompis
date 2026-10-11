# Código intermedio: TAC de Compiscript

Este documento describe la representación intermedia (IR) que genera el
compilador: un **código de tres direcciones (TAC)** almacenado como
**cuádruplos** `(op, arg1, arg2, result)`. Incluye el conjunto de
instrucciones, los dos modos de salida (por nombre y con direcciones), la
organización de memoria (tabla de símbolos con direcciones y registros de
activación), el algoritmo de temporales, ejemplos de traducción de cada
construcción y los supuestos tomados.

Para generarlo (desde `program/`):

```bash
python3 Driver.py programa.cps --tac              # TAC por nombre, a stdout
python3 Driver.py programa.cps --tac --addr       # TAC con direcciones (gp/fp/this)
python3 Driver.py programa.cps --tac salida.tac   # TAC a un archivo (se puede combinar con --addr)
python3 Driver.py programa.cps --symbols          # tabla de símbolos con direcciones y registros
```

El TAC solo se genera si el programa no tiene errores léxicos,
sintácticos ni semánticos. Si los tiene, se imprime cada error con su
`línea:columna` (p.ej. `Error semantico (linea 2:0): ...`) y el Driver
termina con código 1 sin emitir TAC.

Todos los ejemplos de este documento son salida real del compilador
(varios están fijados en `tests/test_tac_generator.py`,
`tests/test_tac_programs.py` y `tests/test_tac_addresses.py`).

## 1. Instrucciones

Cada instrucción es un cuádruplo (`ir/tac.py`, clase `Quad`). Los operandos
son nombres de variables, temporales (`t1`, `t2`, …), etiquetas (`L1`,
`L2`, …) o literales tal como aparecen en el fuente (`10`, `3.14`,
`"hola"`, `true`, `false`, `null`). Los cuádruplos de campos y de
variables externas llevan además el **offset** en bytes (campo `offset`),
que es lo que usa el modo con direcciones.

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
| `link a` | `link` | a | | | fija el enlace de acceso de la próxima llamada (`fp` o un temporal con un `access_link`) |
| `x = call f, n` | `call` | f | n | x | llama a `f` con los últimos `n` params (sin `x =` si es `void`); `f` puede ser un temporal (llamada por vtable) |
| `return [x]` | `return` | x | | | retorna de la función |
| `begin_func f, k` | `begin_func` | f | k | | inicio de `f`; `k` = bytes de variables + temporales de su registro de activación |
| `end_func f` | `end_func` | f | | | fin de la función `f` |
| `x = a[i]` | `load_index` | a | i | x | lee el elemento `i` de un arreglo |
| `a[i] = x` | `store_index` | i | x | a | escribe el elemento `i` de un arreglo |
| `x = o.f` | `load_field` | o | f | x | lee un campo de un objeto (offset del layout de la clase) |
| `o.f = x` | `store_field` | f | x | o | escribe un campo de un objeto |
| `x = new C, k` | `new` | C | k | x | reserva `k` bytes para un objeto de `C` y guarda la vtable de `C` en su offset 0 |
| `x = new_array n` | `new_array` | n | | x | reserva un arreglo de `n` elementos |
| `x = len a` | `len` | a | | x | longitud de un arreglo |
| `print x` | `print` | x | | | imprime un valor |
| `try_begin L` | `try_begin` | | | L | registra `L` como manejador de errores (pila de manejadores) |
| `try_end` | `try_end` | | | | quita el último manejador registrado |
| `x = catch` | `catch` | | | x | obtiene el error capturado |
| `throw x` | `throw` | x | | | lanza el error `x` al manejador activo |
| `t = access_link n` | `access_link` | n | | t | `fp` del registro que está `n` niveles arriba en la cadena de acceso |
| `x = t->v` | `load_outer` | t | v | x | lee la variable `v` del registro al que apunta `t` (offset de `v` en ese registro) |
| `t->v = x` | `store_outer` | v | x | t | escribe la variable `v` del registro al que apunta `t` |

`TACProgram.lines()` / `TACProgram.text()` imprimen la forma textual, y
`Quad.as_row()` da la fila del cuádruplo (para mostrarlo como tabla).

## 2. Modos de salida: por nombre y con direcciones

El mismo programa intermedio se puede imprimir de dos formas:

- **Por nombre** (`--tac`, por defecto): cada variable aparece con su
  nombre (desambiguado con sufijos si hace falta, ver supuesto 2) y cada
  temporal como `tN`.
- **Con direcciones** (`--tac --addr`, `TACProgram.text(addresses=True)`):
  cada variable y temporal se reemplaza por su dirección según la tabla de
  símbolos:

  | Operando | Dirección | Ejemplo |
  |---|---|---|
  | variable global | `gp[+offset]` en el segmento global | `gp[+0]` |
  | parámetro / local de la función actual | `fp[+offset]` en su registro de activación | `fp[+8]` |
  | temporal `tN` | `fp[+(variables + (N-1)·8)]`, después de las variables | `fp[+12]` |
  | campo de `this` | `this[+offset]` (offset del layout de la clase) | `this[+16]` |
  | campo de otro objeto | `base[+offset]`, donde `base` es la dirección que guarda el puntero | `gp[+0][+8]` |
  | variable de una función externa | `base[+offset]`, donde `base` tiene el resultado de `access_link` | `fp[+12][+4]` |

  Notación: `X[+k]` con signo es **contenido de (valor de X) + k bytes**;
  `fp`, `gp` y `this` son registros (`this` es el parámetro en `fp[+0]` de
  un método). En `a[i]` sin signo, `i` es el **índice del elemento**, no
  un desplazamiento en bytes (supuesto 3). Las etiquetas, los nombres de
  funciones y los literales no se reescriben.

```
function fact(n: integer): integer {
  if (n <= 1) { return 1; }
  return n * fact(n - 1);
}
let x: integer = (2 + 3) * (4 - 1);
print(fact(x));
```

| `--tac` | `--tac --addr` |
|---|---|
| <pre>begin_func fact, 12<br>    t1 = n <= 1<br>    ifFalse t1 goto L1<br>    return 1<br>L1:<br>    t1 = n - 1<br>    param t1<br>    t1 = call fact, 1<br>    t1 = n * t1<br>    return t1<br>end_func fact<br>begin_func main, 16<br>    t1 = 2 + 3<br>    t2 = 4 - 1<br>    t1 = t1 * t2<br>    x = t1<br>    param x<br>    t1 = call fact, 1<br>    print t1<br>end_func main</pre> | <pre>begin_func fact, 12<br>    fp[+4] = fp[+0] <= 1<br>    ifFalse fp[+4] goto L1<br>    return 1<br>L1:<br>    fp[+4] = fp[+0] - 1<br>    param fp[+4]<br>    fp[+4] = call fact, 1<br>    fp[+4] = fp[+0] * fp[+4]<br>    return fp[+4]<br>end_func fact<br>begin_func main, 16<br>    fp[+0] = 2 + 3<br>    fp[+8] = 4 - 1<br>    fp[+0] = fp[+0] * fp[+8]<br>    gp[+0] = fp[+0]<br>    param gp[+0]<br>    fp[+0] = call fact, 1<br>    print fp[+0]<br>end_func main</pre> |

En `fact`, `n` es el parámetro en `fp[+0]` (4 bytes) y el único temporal
va en `fp[+4]`: `begin_func fact, 12`. En `main`, `x` es global (`gp[+0]`)
y los dos temporales ocupan `fp[+0]` y `fp[+8]`: `begin_func main, 16`.

## 3. Organización de memoria

La tabla de símbolos (`semantic/`) se amplió para que cada símbolo exponga
los datos que necesita el backend:

| Atributo (`Symbol`) | Contenido |
|---|---|
| `storage` | área donde vive: `global`, `param`, `local` o `field` |
| `offset`, `size` | desplazamiento en bytes dentro de su área y tamaño |
| `level` | nivel de anidamiento del área que lo declara (0 = global) |
| `area` | etiqueta del área: `global`, la etiqueta de la función (`f`, `Clase.metodo`, `externa.interna`) o el nombre de la clase |
| `address` | dirección relativa a su registro base: `gp[+k]`, `fp[+k]` o `this[+k]` |
| `label` (`FunctionSymbol`) | etiqueta de entrada en el TAC |
| `frame` (`FunctionSymbol`) | su `ActivationRecord` |

### Tamaños (`semantic/runtime.py`, `size_of`)

| Tipo | Bytes |
|---|---|
| `integer`, `boolean` | 4 |
| `float` | 8 |
| `string`, arreglos, objetos, `null` (referencias) | 8 (un puntero) |
| temporal | 8 (un slot por temporal) |

### Niveles de anidamiento

- **Nivel 0**: el segmento global.
- **Nivel 1**: `main`, las funciones de nivel superior, los métodos y los
  `Clase.__init`.
- **Nivel n + 1**: una función declarada dentro de otra de nivel n.

### Segmento global (`gp`)

Las variables de nivel superior, con offsets crecientes en orden de
declaración. Incluye las variables de `for`/`foreach`/bloques/`catch` que
estén fuera de toda función.

### Registro de activación (`ActivationRecord`, uno por función, método, `Clase.__init` y `main`)

```
fp[-24]  dirección de retorno          ┐
fp[-16]  enlace de control             │ cabecera (24 bytes): la arma la secuencia
fp[-8]   enlace de acceso              ┘ de llamada (call / link)
fp[+0]   this (solo métodos) | parámetros | locales | temporales
         ─────────────────────────────────────────────────────▶
```

- **Enlace de control**: el `fp` del llamador (para restaurarlo al
  retornar).
- **Enlace de acceso** (estático): el `fp` del registro de la función que
  contiene **léxicamente** a esta (`ActivationRecord.enclosing`). Solo lo
  usan las funciones de nivel ≥ 2; las de nivel 1 solo ven globales, que
  se acceden por `gp`.
- Las variables de bloques anidados (`if`, `while`, `for`, …) se ubican en
  el registro de la función que las contiene.
- Los temporales van después de las variables, 8 bytes cada uno. Hay
  tantos como `max_live` (máximo de temporales vivos a la vez, sección 4).
- `begin_func f, k`: **k = variables + temporales** (`data_size`), lo que
  reserva la función al entrar. El tamaño total (`total_size`) suma la
  cabecera de 24 bytes.

**`main`** sigue el mismo modelo: es un registro de nivel 1 sin variables
(las de nivel superior viven en `gp`), solo con temporales. Por eso
`begin_func main, k` es `k = max_live × 8`, y `--symbols` lo lista como un
registro más.

### Objetos (`ClassSymbol.compute_layout`)

```
[ vtable | campos heredados | campos propios ]
  offset 0
```

Un objeto de una subclase empieza con el mismo layout que su superclase,
así que un `Perro` se puede usar donde se espera un `Animal`.

### Vtable (`ClassSymbol.vtable()`)

Un slot por cada método. Un método que sobrescribe a otro conserva el slot
del método original. El constructor no está en la vtable porque siempre
se llama directo.

### Ejemplo de `--symbols`

```
function externa(a: integer): integer {
  let x: integer = a * 2;
  function interna(b: integer): integer {
    x = x + b;
    return x + a;
  }
  return interna(1);
}
```
```
== segmento global gp (0 bytes, nivel 0)
== registro de activacion de externa (nivel 1)
  cabecera fp[-24] direccion de retorno
  cabecera fp[-16] enlace de control: fp del llamador
  cabecera fp[-8]  enlace de acceso: no se usa (solo ve globales, via gp)
  param  a            integer      offset=0    size=4  nivel=1  dir=fp[+0]
  local  x            integer      offset=4    size=4  nivel=1  dir=fp[+4]
  temp   t1           temporal     offset=8    size=8  nivel=1  dir=fp[+8]
  tamano: params=4 + locales=4 + temporales=1x8 = 16 bytes (begin_func) + 24 de cabecera = 40 bytes
== registro de activacion de externa.interna (nivel 2)
  cabecera fp[-24] direccion de retorno
  cabecera fp[-16] enlace de control: fp del llamador
  cabecera fp[-8]  enlace de acceso: fp de externa (nivel 1)
  param  b            integer      offset=0    size=4  nivel=2  dir=fp[+0]
  temp   t1           temporal     offset=4    size=8  nivel=2  dir=fp[+4]
  temp   t2           temporal     offset=12   size=8  nivel=2  dir=fp[+12]
  tamano: params=4 + locales=0 + temporales=2x8 = 20 bytes (begin_func) + 24 de cabecera = 44 bytes
== registro de activacion de main (nivel 1)
  ...
```

Las clases aparecen como `== clase Animal (16 bytes por instancia)` con
cada campo (`dir=this[+8]  (de Animal)`) y su vtable.

## 4. Temporales

`TempAllocator` (`ir/tac.py`) mantiene una lista de temporales libres:

1. Cuando una instrucción consume un temporal como operando, ese temporal
   se **libera** (cada temporal de una expresión tiene un único uso).
2. Al pedir un temporal nuevo se entrega el **libre de menor número**, y
   solo se crea uno nuevo si no hay libres.
3. Los operandos se liberan **antes** de pedir el resultado, así que una
   operación puede reutilizar uno de sus operandos (`t1 = t1 + t2`).
4. Cada función tiene su propio asignador. `max_live`, el máximo de
   temporales vivos a la vez, define cuánto espacio de temporales lleva
   su registro de activación (`temp_count`), y por lo tanto el `k` de
   `begin_func`.

Hay casos en que un temporal debe sobrevivir a varias instrucciones: el
objeto recién creado por `new` mientras se evalúan los argumentos del
constructor, y el índice y la longitud de un `foreach` durante todo el
bucle. Esos temporales se liberan de forma explícita al terminar.

**Temporales vivos a través de `call`.** Los temporales son slots del
registro de activación de la función, no registros de la máquina, así que
sobreviven a una llamada sin guardarlos: la función llamada tiene su
propio registro. En `a * f(b) + g(c)`, `t1` sigue vivo mientras se llama
a `g`, y `max_live = 2`:

```
begin_func h, 28          ; 12 bytes de params (a, b, c) + 2 temporales × 8
    param b
    t1 = call f, 1
    t1 = a * t1
    param c
    t2 = call g, 1        ; t1 sigue vivo (fp[+12]); el resultado va a t2 (fp[+20])
    t1 = t1 + t2
    return t1
end_func h
```

Una variable del programa que se llame como un temporal (`t1`) o como un
registro (`fp`, `gp`) se renombra (`t1_1`) para que no se confunda con
ellos.

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

## 5. Traducción de cada construcción

### Expresiones lógicas como valor (cortocircuito)

Cuando el resultado de `&&` / `||` se guarda o se usa como valor
(`let c = a || b`), se evalúa en cortocircuito sobre un temporal:

```
t1 = a                 t1 = a
ifFalse t1 goto L1     if t1 goto L1
t1 = b                 t1 = b
L1:                    L1:
```

### Condiciones de `if`/`while`/`do-while`/`for`/ternario: código de saltos

En una condición, `||`, `&&` y `!` no calculan un booleano: se traducen a
saltos. Cada operando salta a la salida verdadera o falsa en cuanto
decide el resultado, y el resto no se evalúa. `!` solo intercambia los
saltos. Las condiciones constantes no emiten prueba (`while (true)` no
evalúa nada, y `if (false)` es un `goto` al final).

```
if ((a > 0 || b > 0) && !(a == b)) { print(a); }
```
```
    t1 = a > 0
    if t1 goto L2        ; a > 0 ya hace verdadero el ||
    t1 = b > 0
    ifFalse t1 goto L1   ; || falso -> && falso -> salta el cuerpo
L2:
    t1 = a == b
    if t1 goto L1        ; !(a == b) es falso
    print a
L1:
```

### Ternario `c ? x : y`

```
ifFalse c goto L1      ; con && / || en c se usa el código de saltos de arriba
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
`for` es la actualización). Lo que queda justo después de un salto
incondicional (`goto`, `return`, `throw`) sin una etiqueta de por medio no
se emite, porque nunca se ejecutaría.

### switch

Se compara el valor contra cada `case` en orden y se salta al primero que
coincida. Los cuerpos se emiten uno después del otro, así que **sin
`break` la ejecución continúa en el caso siguiente** (fall-through, como
en TypeScript). Sin `default`, si nada coincide se salta al final.

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

`foreach (x in arr)` se traduce a un recorrido por índice. No lleva
chequeo de límites: el índice va de `0` a `len arr - 1` por construcción.
Sobre una matriz, `x` es cada fila y se puede anidar otro `foreach`.

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
`param` seguidos, para que una llamada anidada como argumento
(`suma(suma(1, 2), suma(3, 4))`) no intercale sus `param` con los de la
llamada externa. Una función sin `return` final recibe un `return`
implícito. Una llamada a función `void` no tiene `x =`. Ver el ejemplo de
`fact` (recursión) en la sección 2.

### Funciones anidadas (closures): enlace de acceso explícito

Una función anidada recibe una etiqueta calificada (`externa.interna`) y
su propio registro, de nivel uno más que la función que la contiene. Para
leer o escribir una variable de una función externa se sube por la
cadena de **enlaces de acceso**: `access_link n` da el `fp` del registro
`n` niveles arriba (`n` = nivel actual − nivel de la variable), y `->`
accede a la variable con su offset en ese registro.

**Secuencia de llamada.** Antes de `call` a una función anidada se emite
`link a`, con el `fp` del registro que **declara** a la función llamada:

- si la declara la función actual: `link fp`;
- si la declara una función externa (llamada a sí misma o a una hermana):
  `t = access_link n` y `link t`, con `n` = nivel actual − nivel de la
  declaración.

`call` arma la cabecera del registro nuevo: guarda la dirección de
retorno, el `fp` actual como enlace de control y el valor de `link` como
enlace de acceso. Las funciones de nivel 1 y los métodos no reciben `link`.

```
function externa(a: integer): integer {
  let x: integer = a * 2;
  function interna(b: integer): integer {
    x = x + b;
    return x + a;
  }
  return interna(1);
}
```

| `--tac` | `--tac --addr` |
|---|---|
| <pre>begin_func externa.interna, 20<br>    t1 = access_link 1<br>    t1 = t1->x<br>    t1 = t1 + b<br>    t2 = access_link 1<br>    t2->x = t1<br>    t1 = access_link 1<br>    t1 = t1->x<br>    t2 = access_link 1<br>    t2 = t2->a<br>    t1 = t1 + t2<br>    return t1<br>end_func externa.interna<br>begin_func externa, 16<br>    t1 = a * 2<br>    x = t1<br>    param 1<br>    link fp<br>    t1 = call externa.interna, 1<br>    return t1<br>end_func externa</pre> | <pre>begin_func externa.interna, 20<br>    fp[+4] = access_link 1<br>    fp[+4] = fp[+4][+4]<br>    fp[+4] = fp[+4] + fp[+0]<br>    fp[+12] = access_link 1<br>    fp[+12][+4] = fp[+4]<br>    fp[+4] = access_link 1<br>    fp[+4] = fp[+4][+4]<br>    fp[+12] = access_link 1<br>    fp[+12] = fp[+12][+0]<br>    fp[+4] = fp[+4] + fp[+12]<br>    return fp[+4]<br>end_func externa.interna<br>begin_func externa, 16<br>    fp[+8] = fp[+0] * 2<br>    fp[+4] = fp[+8]<br>    param 1<br>    link fp<br>    fp[+8] = call externa.interna, 1<br>    return fp[+8]<br>end_func externa</pre> |

Con tres niveles (`a` → `b` → `c`), `c` lee una variable de `a` con
`access_link 2`, y una llamada recursiva de `b` a sí misma pasa
`t = access_link 1` / `link t` (ver
`test_access_link_climbs_several_levels_and_recursion_reuses_enclosing_link`).

Una función anidada dentro de un **método** se etiqueta
`Clase.metodo.aux`, recibe el mismo `this` como primer parámetro (`param
this`), se llama directo (no está en la vtable) y usa el enlace de acceso
para las locales del método.

### Clases, objetos y métodos

- Un método recibe `this` como primer parámetro implícito (`fp[+0]`). Su
  etiqueta es `Clase.metodo`.
- `new C(args)` reserva el objeto, llama a `C.__init` si la clase o una
  de sus superclases tiene campos con valor inicial, y luego llama al
  constructor (propio o heredado, aunque venga de varios niveles arriba)
  con `this` y los argumentos.
- `C.__init` llama primero al `__init` de la superclase y después asigna
  los campos propios que tienen valor inicial.
- **Llamada directa o virtual:** si ninguna subclase del tipo estático
  del receptor sobrescribe el método, se llama directo
  (`call Perro.hablar`). Si alguna lo sobrescribe, la llamada pasa por la
  vtable del objeto (despacho dinámico): `t = o.vtable` (offset 0),
  `t = t[slot]`, `call t, n`.
- Dentro de un método, un campo o método escrito sin `this.` (propio o
  **heredado**) se traduce igual que si llevara `this.`.

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
    this.patas = 4                 ; --addr: this[+16] = 4
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
    t1 = a.vtable                  ; --addr: fp[+0] = gp[+0][+0]
    t1 = t1[0]                     ; slot 0 de la vtable
    param a
    t1 = call t1, 1                ; --addr: fp[+0] = call fp[+0], 1
    print t1
end_func main
```

### Arreglos: chequeo explícito de límites

Cada lectura o escritura `a[i]` verifica `0 <= i < len a` en el propio
TAC antes del acceso. Si falla, salta al stub de error de la función, que
lanza `"indice fuera de rango"`. Si el índice es un literal no negativo
se omite la comprobación `i < 0`. Sobre una matriz (`m[i][j]`) se
verifican las dos dimensiones.

### División y módulo: chequeo de cero

Antes de `/` y `%` se verifica que el divisor no sea cero (salvo que sea
un literal distinto de cero), con un stub que lanza `"division entre
cero"`.

```
let a: integer[] = [1, 2];
let i: integer = 1;
a[i] = a[0] / i;
```
```
begin_func main, 16
    t1 = new_array 2
    t1[0] = 1                      ; literal de arreglo: sin chequeo
    t1[1] = 2
    a = t1
    i = 1
    t1 = len a                     ; a[0]: índice literal, solo el límite superior
    t1 = 0 >= t1
    if t1 goto L1
    t1 = a[0]
    t2 = i == 0                    ; divisor
    if t2 goto L2
    t1 = t1 / i
    t2 = i < 0                     ; a[i] = ...
    if t2 goto L1
    t2 = len a
    t2 = i >= t2
    if t2 goto L1
    a[i] = t1
    return                         ; el flujo normal no cae en los stubs
L1:
    throw "indice fuera de rango"
L2:
    throw "division entre cero"
end_func main
```

Los stubs se emiten una vez por función y por tipo de error, después del
último `return`. Como `throw` usa el manejador activo **en tiempo de
ejecución**, el mismo stub sirve para accesos dentro y fuera de un `try`.

### try / catch / throw

```
try_begin L1      ; registra el manejador
...cuerpo del try...
try_end           ; lo quita al terminar normalmente
goto L2
L1:
err = catch       ; la variable del catch recibe el error
...cuerpo del catch...
L2:
```

**`try_end` en todos los caminos de salida.** Cada función lleva la
cuenta de los `try` abiertos en cada punto:

- `return` dentro de uno o más `try` emite un `try_end` por cada uno
  (después de evaluar el valor de retorno, que todavía puede fallar
  dentro del `try`);
- `break` / `continue` emiten un `try_end` por cada `try` que abandonan
  (un bucle completamente dentro del `try` no lo cierra);
- al saltar al `catch` el runtime ya quitó ese manejador, así que el
  cuerpo del `catch` no cuenta como dentro de su `try` (pero sí de los
  `try` externos).

```
function f(n: integer): integer {
  try {
    if (n > 0) { return n; }
  } catch (e) {
    print(e);
  }
  while (true) {
    try { break; } catch (e2) { print(e2); }
  }
  return 0;
}
```
```
begin_func f, 28
    try_begin L1
    t1 = n > 0
    ifFalse t1 goto L3
    try_end            ; return dentro del try
    return n
L3:
    try_end            ; fin normal del try
    goto L2
L1:
    e = catch
    print e
L2:
L4:
    try_begin L6
    try_end            ; break dentro del try
    goto L5
L6:
    e2 = catch
    print e2
L7:
    goto L4
L5:
    return 0
end_func f
```

## 6. Supuestos y decisiones de diseño

1. **Programa = funciones + `main`.** Las funciones y métodos se emiten
   primero, cada uno entre `begin_func` y `end_func`. Las sentencias de
   nivel superior forman `main`, que se emite al final y tiene su propio
   registro de activación (solo temporales; sus variables están en `gp`).
2. **Nombres.** En el modo por nombre, las variables aparecen por su
   nombre. Si una variable de un bloque o función oculta a otra con el
   mismo nombre, o se llama como un temporal o un registro, se le agrega
   un sufijo (`x_1`, `t1_1`) para que el TAC no sea ambiguo. La dirección
   de cada nombre está en la tabla de símbolos (`--symbols`) y se ve
   directamente con `--addr`.
3. **Índices de arreglo.** `a[i]` usa el índice del elemento, no un
   desplazamiento en bytes. El backend multiplica por el tamaño del
   elemento (4 u 8 bytes según su tipo) y salta la cabecera del arreglo,
   donde guarda la longitud que lee `len`. **La verificación de límites
   no se delega**: está explícita en el TAC (sección 5).
4. **Campos.** `o.f` muestra el nombre del campo por legibilidad, pero el
   cuádruplo lleva su offset (layout de la clase), que es lo que imprime
   `--addr`.
5. **Tipos en las operaciones.** Los operadores no llevan el tipo
   (`t1 = a + b` sirve para `integer`, `float` y concatenación de
   `string`). El tipo de cada operando ya fue verificado en el análisis
   semántico y está en la tabla de símbolos. Un `integer` mezclado con un
   `float` se promueve a `float`.
6. **Booleanos.** En condiciones de control de flujo, `&&`, `||` y `!` son
   código de saltos; como valor, se evalúan en cortocircuito sobre un
   temporal.
7. **Paso de parámetros por valor.** Para objetos y arreglos lo que se
   copia es la referencia.
8. **Funciones anidadas.** El acceso a variables de funciones externas es
   explícito con `access_link`/`->`, y el enlace de cada llamada se pasa
   con `link`. Compiscript no tiene valores de tipo función (no se puede
   retornar ni guardar una función), así que una función anidada nunca
   sobrevive al registro de la función que la contiene: alcanza con la
   cadena estática, sin clausuras en el heap.
9. **`switch`** tiene fall-through entre casos (supuesto, el enunciado no
   lo define).
10. **Errores en tiempo de ejecución.** `throw x` salta al manejador en la
    cima de la pila de manejadores (el último `try_begin` sin `try_end`),
    quitándolo, y deja `x` disponible para `catch`. Si la función actual
    no tiene manejador, el error se propaga a la función que la llamó
    (desapilando registros) hasta encontrar uno. Si no hay ninguno, el
    programa termina con el mensaje. Los errores que genera el compilador
    son `"indice fuera de rango"` y `"division entre cero"`.
