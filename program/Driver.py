import argparse
import json
import sys

from compiler import compile_source, describe_memory
from tree_export import tree_to_dict, tree_to_dot


def main(path: str, tree_output: str = None, tac_output: str = None, show_symbols: bool = False,
         addresses: bool = False) -> int:
    with open(path, encoding="utf-8-sig") as f:  # -sig: ignora el BOM que agregan algunos editores
        result = compile_source(f.read())

    if tree_output is not None:
        if tree_output.endswith(".dot"):
            content = tree_to_dot(result.tree, result.parser.ruleNames)
        else:
            content = json.dumps(tree_to_dict(result.tree, result.parser.ruleNames), ensure_ascii=False, indent=2)
        with open(tree_output, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"Arbol sintactico escrito en '{tree_output}'")

    if result.errors:
        for error in result.errors:
            print(error)
        return 1

    if show_symbols:
        print(describe_memory(result.analyzer, result.init_frames))

    if tac_output == "-":
        print(result.tac.text(addresses))
    elif tac_output is not None:
        with open(tac_output, "w", encoding="utf-8") as f:
            f.write(result.tac.text(addresses) + "\n")
        print(f"Codigo intermedio escrito en '{tac_output}'")

    return 0


if __name__ == "__main__":
    arg_parser = argparse.ArgumentParser(description="Compilador de Compiscript")
    arg_parser.add_argument("archivo", help="programa .cps a compilar")
    arg_parser.add_argument("--tree", metavar="SALIDA", help="exporta el arbol sintactico (.json o .dot)")
    arg_parser.add_argument(
        "--tac", metavar="SALIDA", nargs="?", const="-",
        help="genera el codigo de tres direcciones (a stdout, o al archivo indicado)",
    )
    arg_parser.add_argument(
        "--symbols", action="store_true",
        help="muestra la tabla de simbolos con direcciones y registros de activacion",
    )
    arg_parser.add_argument(
        "--addr", action="store_true",
        help="con --tac: muestra los operandos como direcciones (gp[+k], fp[+k], this[+k]) en vez de nombres",
    )
    args = arg_parser.parse_args()
    tac_output = args.tac if args.tac is not None or not args.addr else "-"  # --addr solo implica --tac
    sys.exit(main(args.archivo, args.tree, tac_output, args.symbols, args.addr))
