"""A command-line interface for disassembling and assembling Hermes Bytecode."""
from hbctool import metadata, hbc, hasm
import argparse
import logging
import os
import sys

DEFAULT_HASM_PATH = "hasm"
DEFAULT_HBC_FILE = "index.android.bundle"

log = logging.getLogger("hbctool")


def _is_unsafe_output_path(path):
    abs_path = os.path.abspath(path)
    return abs_path in ("/", os.path.abspath(os.path.expanduser("~")))


def _confirm_overwrite(path, force=False):
    if not os.path.exists(path):
        return False

    if _is_unsafe_output_path(path):
        raise hasm.HASMError(f"Refusing to remove unsafe output directory: {path}")

    if force:
        return True

    if not sys.stdin.isatty():
        raise FileExistsError(
            f"Output directory already exists: {path} "
            f"(rerun with --force to overwrite, or remove the directory yourself)"
        )

    c = input(f"'{path}' exists. Do you want to remove it ? (y/n): ").lower().strip()
    if c[:1] != "y":
        raise FileExistsError(f"Output directory already exists: {path}")

    return True


def disasm(hbcfile, hasmpath, force=False):
    if not os.path.isfile(hbcfile):
        raise FileNotFoundError(f"HBC file not found: {hbcfile}")

    log.info("Disassemble '%s' to '%s' path", hbcfile, hasmpath)
    with open(hbcfile, "rb") as f:
        hbco = hbc.load(f)

    header = hbco.getHeader()
    sourceHash = bytes(header["sourceHash"]).hex()
    version = header["version"]
    log.info("Hermes Bytecode [ Source Hash: %s, HBC Version: %s ]", sourceHash, version)

    overwrite = _confirm_overwrite(hasmpath, force=force)
    hasm.dump(hbco, hasmpath, force=overwrite)
    log.info("Done")


def asm(hasmpath, hbcfile):
    log.info("Assemble '%s' to '%s' path", hasmpath, hbcfile)
    hbco = hasm.load(hasmpath)

    header = hbco.getHeader()
    sourceHash = bytes(header["sourceHash"]).hex()
    version = header["version"]
    log.info("Hermes Bytecode [ Source Hash: %s, HBC Version: %s ]", sourceHash, version)

    with open(hbcfile, "wb") as f:
        hbc.dump(hbco, f)
    log.info("Done")


def info(hbcfile):
    if not os.path.isfile(hbcfile):
        raise FileNotFoundError(f"HBC file not found: {hbcfile}")

    with open(hbcfile, "rb") as f:
        hbco = hbc.load(f)

    header = hbco.getHeader()
    sourceHash = bytes(header["sourceHash"]).hex()
    version = header["version"]
    function_count = hbco.getFunctionCount()
    string_count = hbco.getStringCount()

    print(f"file:           {hbcfile}")
    print(f"size:           {os.path.getsize(hbcfile)} bytes")
    print(f"version:        {version}")
    print(f"source_hash:    {sourceHash}")
    print(f"function_count: {function_count}")
    print(f"string_count:   {string_count}")


def _build_parser():
    parser = argparse.ArgumentParser(
        prog="hbctool",
        description="A command-line interface for disassembling and assembling "
        "the Hermes Bytecode.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "    hbctool disasm index.android.bundle test_hasm\n"
            "    hbctool asm test_hasm index.android.bundle\n"
            "    hbctool d index.android.bundle test_hasm\n"
            "    hbctool a test_hasm index.android.bundle\n"
            "    hbctool info index.android.bundle\n"
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"{metadata.project} {metadata.version}",
    )

    verbosity = parser.add_mutually_exclusive_group()
    verbosity.add_argument(
        "-q", "--quiet", action="store_true", help="Only print warnings and errors."
    )
    verbosity.add_argument(
        "-v", "--verbose", action="store_true", help="Print debug-level logging."
    )

    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True

    p_disasm = sub.add_parser(
        "disasm",
        aliases=["d"],
        help="Disassemble a Hermes bytecode bundle into a HASM directory.",
        description="Disassemble a Hermes bytecode bundle into a HASM directory.",
    )
    p_disasm.add_argument(
        "-y", "--force",
        action="store_true",
        help="Overwrite an existing HASM output directory without prompting.",
    )
    p_disasm.add_argument("hbc_file", metavar="HBC_FILE", help="Target HBC file")
    p_disasm.add_argument(
        "hasm_path",
        metavar="HASM_PATH",
        nargs="?",
        default=DEFAULT_HASM_PATH,
        help=f"Target HASM directory path (default: {DEFAULT_HASM_PATH})",
    )

    p_asm = sub.add_parser(
        "asm",
        aliases=["a"],
        help="Assemble a HASM directory back into a Hermes bytecode bundle.",
        description="Assemble a HASM directory back into a Hermes bytecode bundle.",
    )
    p_asm.add_argument(
        "hasm_path",
        metavar="HASM_PATH",
        nargs="?",
        default=DEFAULT_HASM_PATH,
        help=f"Source HASM directory path (default: {DEFAULT_HASM_PATH})",
    )
    p_asm.add_argument(
        "hbc_file",
        metavar="HBC_FILE",
        nargs="?",
        default=DEFAULT_HBC_FILE,
        help=f"Target HBC file (default: {DEFAULT_HBC_FILE})",
    )

    p_info = sub.add_parser(
        "info",
        help="Print metadata for a Hermes bytecode bundle without disassembling it.",
        description="Print metadata for a Hermes bytecode bundle without disassembling it.",
    )
    p_info.add_argument("hbc_file", metavar="HBC_FILE", help="Target HBC file")

    return parser


def _configure_logging(args):
    level = logging.INFO
    if args.quiet:
        level = logging.WARNING
    elif args.verbose:
        level = logging.DEBUG
    logging.basicConfig(level=level, format="[*] %(message)s", stream=sys.stderr)


def main(argv=None):
    parser = _build_parser()
    args = parser.parse_args(argv)
    _configure_logging(args)

    try:
        if args.command in ("disasm", "d"):
            disasm(args.hbc_file, args.hasm_path, force=args.force)
        elif args.command in ("asm", "a"):
            asm(args.hasm_path, args.hbc_file)
        elif args.command == "info":
            info(args.hbc_file)
        else:
            parser.error(f"unknown command: {args.command}")
    except (FileNotFoundError, FileExistsError, hasm.HASMError, ValueError) as exc:
        log.error("%s", exc)
        raise SystemExit(1)


def entry_point():
    """Zero-argument entry point for use with setuptools/distribute."""
    main()


if __name__ == "__main__":
    main()
