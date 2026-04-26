"""Backward-compatibility shim.

Historically each ``hbctool.hbc.hbc<v>`` package exposed a ``parse`` and
``export`` function plus an ``INVALID_LENGTH`` constant. After the
collapse refactor these live in :mod:`hbctool.hbc._base`. This module
keeps the old import paths working.
"""
from . import HBC89
from hbctool.hbc._base import (
    INVALID_LENGTH,
    INVALID_OFFSET,
    MAGIC,
    BYTECODE_ALIGNMENT,
    _parse as _parse_obj,
    _export as _export_obj,
    _load_structure,
)

_HBC_CLS = HBC89


def parse(f):
    """Parse a Hermes bytecode bundle for HBC v89 from a ``BitReader``."""
    structure = _load_structure(_HBC_CLS.DATA_DIR)
    return _parse_obj(f, structure, _HBC_CLS.IDENTIFIER_FIELD)


def export(obj, f):
    """Serialize a Hermes bytecode bundle for HBC v89 to a ``BitWriter``."""
    structure = _load_structure(_HBC_CLS.DATA_DIR)
    _export_obj(obj, f, structure, _HBC_CLS.IDENTIFIER_FIELD)


__all__ = [
    "parse",
    "export",
    "INVALID_LENGTH",
    "INVALID_OFFSET",
    "MAGIC",
    "BYTECODE_ALIGNMENT",
]
