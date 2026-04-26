"""HBC version 95 bindings.

Thin shim that subclasses :class:`hbctool.hbc._base.HBCBase`. All real
parser / translator / wrapper logic lives in ``hbctool/hbc/_base.py``;
this file only declares the per-version configuration knobs.
"""
import pathlib

from hbctool.hbc._base import HBCBase

_DATA_DIR = pathlib.Path(__file__).parent / "data"


class HBC95(HBCBase):
    VERSION = 95
    DATA_DIR = _DATA_DIR
    IMM32_SIGNED = True


__all__ = ["HBC95"]
