"""Backward-compatibility shim.

Historically each ``hbctool.hbc.hbc<v>`` package exposed a ``disassemble``
and ``assemble`` function. After the collapse refactor these live on
:class:`hbctool.hbc._base.HBCBase`. This module keeps the old import
paths working.
"""
from . import HBC84
from hbctool.hbc._base import (
    _assemble as _assemble_ops,
    _disassemble as _disassemble_ops,
    _load_opcodes,
    _make_operand_types,
)

_HBC_CLS = HBC84
_OPCODE_OPERAND, _OPCODE_MAPPER, _OPCODE_MAPPER_INV = _load_opcodes(_HBC_CLS.DATA_DIR)
_OPERAND_TYPE = _make_operand_types(_HBC_CLS.IMM32_SIGNED)


def disassemble(bc):
    """Disassemble a raw bytecode stream for HBC v84."""
    return _disassemble_ops(bc, _OPCODE_MAPPER, _OPCODE_OPERAND, _OPERAND_TYPE)


def assemble(insts):
    """Assemble a structured op list for HBC v84 into a raw bytecode stream."""
    return _assemble_ops(insts, _OPCODE_MAPPER_INV, _OPCODE_OPERAND, _OPERAND_TYPE)


__all__ = ["assemble", "disassemble"]
