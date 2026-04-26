"""
A command-line interface for disassembling and assembling
the Hermes Bytecode.

Usage:
    hbctool (disasm|d) [-y | --force] <HBC_FILE> [<HASM_PATH>]
    hbctool (asm|a) [<HASM_PATH>] [<HBC_FILE>]
    hbctool --help
    hbctool --version

Operation:
    disasm, d           Disassemble Hermes Bytecode
    asm, a              Assemble Hermes Bytecode

Args:
    HBC_FILE            Target HBC file
    HASM_PATH           Target HASM directory path

Options:
    -y, --force         Overwrite an existing HASM output directory without prompting
    --version           Show hbctool version
    --help              Show hbctool help manual

Examples:
    hbctool disasm index.android.bundle test_hasm
    hbctool asm test_hasm index.android.bundle
    hbctool d index.android.bundle test_hasm
    hbctool a test_hasm index.android.bundle
"""
from hbctool import metadata, hbc, hasm
import os
import sys

DEFAULT_HASM_PATH = "hasm"
DEFAULT_HBC_FILE = "index.android.bundle"

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

    print(f"[*] Disassemble '{hbcfile}' to '{hasmpath}' path")
    with open(hbcfile, "rb") as f:
        hbco = hbc.load(f)

    header = hbco.getHeader()
    sourceHash = bytes(header["sourceHash"]).hex()
    version = header["version"]
    print(f"[*] Hermes Bytecode [ Source Hash: {sourceHash}, HBC Version: {version} ]")

    overwrite = _confirm_overwrite(hasmpath, force=force)
    hasm.dump(hbco, hasmpath, force=overwrite)
    print(f"[*] Done")

def asm(hasmpath, hbcfile):
    print(f"[*] Assemble '{hasmpath}' to '{hbcfile}' path")
    hbco = hasm.load(hasmpath)

    header = hbco.getHeader()
    sourceHash = bytes(header["sourceHash"]).hex()
    version = header["version"]
    print(f"[*] Hermes Bytecode [ Source Hash: {sourceHash}, HBC Version: {version} ]")

    with open(hbcfile, "wb") as f:
        hbc.dump(hbco, f)
    print(f"[*] Done")

def main():
    from docopt import docopt
    args = docopt(__doc__, version=f"{metadata.project} {metadata.version}")
    try:
        if args['disasm'] or args['d']:
            disasm(
                args['<HBC_FILE>'],
                args['<HASM_PATH>'] or DEFAULT_HASM_PATH,
                force=bool(args.get('--force') or args.get('-y')),
            )
        elif args['asm'] or args['a']:
            asm(args['<HASM_PATH>'] or DEFAULT_HASM_PATH, args['<HBC_FILE>'] or DEFAULT_HBC_FILE)
    except (FileNotFoundError, FileExistsError, hasm.HASMError, ValueError) as exc:
        print(f"[!] {exc}", file=sys.stderr)
        raise SystemExit(1)
    

def entry_point():
    """Zero-argument entry point for use with setuptools/distribute."""
    main()

if __name__ == "__main__":
    main()
