"""HBC version 59 bindings.

Thin shim that subclasses :class:`hbctool.hbc._base.HBCBase`. All real
parser / translator / wrapper logic lives in ``hbctool/hbc/_base.py``;
this file only declares the per-version configuration knobs.
"""
import pathlib

from hbctool.hbc._base import HBCBase, IDENT_TRANSLATIONS

_DATA_DIR = pathlib.Path(__file__).parent / "data"


class HBC59(HBCBase):
    VERSION = 59
    DATA_DIR = _DATA_DIR
    IDENTIFIER_FIELD = IDENT_TRANSLATIONS


__all__ = ["HBC59"]
