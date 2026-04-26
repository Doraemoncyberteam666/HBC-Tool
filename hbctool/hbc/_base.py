"""Shared parser, translator, and HBC* base class for every Hermes bytecode version.

Each ``hbctool.hbc.hbc<v>`` package only needs to declare:

* ``VERSION`` (int) -- e.g. ``74``
* ``DATA_DIR`` (``pathlib.Path``) -- directory containing ``structure.json``
  and ``opcode.json``
* optionally ``IDENTIFIER_FIELD`` -- ``"identifierHashes"`` (default) or
  ``"identifierTranslations"`` (HBC 59 / 62)
* optionally ``IMM32_SIGNED`` -- ``False`` (default) or ``True`` (HBC 95+)

and inherit from :class:`HBCBase`. The ~13k lines of duplicated parser /
translator / wrapper code that used to live in 18 near-identical
``hbc<v>/{__init__,parser,translator}.py`` files now live here exactly
once.
"""
from __future__ import annotations

import copy
import json
import pathlib
from struct import unpack

from hbctool import util as _util
from hbctool.util import (
    from_double,
    from_int8,
    from_int32,
    from_uint8,
    from_uint16,
    from_uint32,
    memcpy,
    read,
    readuint,
    to_double,
    to_int8,
    to_int32,
    to_uint8,
    to_uint16,
    to_uint32,
    write,
    writeuint,
)


# Tag bits in object-value buffers (SLP encoding).
NullTag = 0
TrueTag = 1 << 4
FalseTag = 2 << 4
NumberTag = 3 << 4
LongStringTag = 4 << 4
ShortStringTag = 5 << 4
ByteStringTag = 6 << 4
IntegerTag = 7 << 4
TagMask = 0x70

# Hermes magic constants.
MAGIC = 2240826417119764422
BYTECODE_ALIGNMENT = 4

# Sentinels for string-table entries.
INVALID_OFFSET = (1 << 23)
INVALID_LENGTH = (1 << 8) - 1

# Identifier-segment field name.
IDENT_HASHES = "identifierHashes"
IDENT_TRANSLATIONS = "identifierTranslations"


# ---------------------------------------------------------------------------
# structure.json / opcode.json loading (cached per directory).
# ---------------------------------------------------------------------------

_STRUCTURE_CACHE: dict[pathlib.Path, dict] = {}
_OPCODE_CACHE: dict[pathlib.Path, dict] = {}


def _load_structure(data_dir: pathlib.Path) -> dict:
    cached = _STRUCTURE_CACHE.get(data_dir)
    if cached is not None:
        # Return a deep copy so per-call mutation of size slots
        # (e.g. ``stringStorageS[2] = ...``) doesn't leak across versions.
        return copy.deepcopy(cached)
    with open(data_dir / "structure.json", "r") as f:
        loaded = json.load(f)
    _STRUCTURE_CACHE[data_dir] = loaded
    return copy.deepcopy(loaded)


def _load_opcodes(data_dir: pathlib.Path) -> tuple[dict, list, dict]:
    cached = _OPCODE_CACHE.get(data_dir)
    if cached is not None:
        return cached["operand"], cached["mapper"], cached["mapper_inv"]
    with open(data_dir / "opcode.json", "r") as f:
        opcode_operand = json.load(f)
    opcode_mapper = list(opcode_operand.keys())
    opcode_mapper_inv = {v: i for i, v in enumerate(opcode_mapper)}
    _OPCODE_CACHE[data_dir] = {
        "operand": opcode_operand,
        "mapper": opcode_mapper,
        "mapper_inv": opcode_mapper_inv,
    }
    return opcode_operand, opcode_mapper, opcode_mapper_inv


def _make_operand_types(imm32_signed: bool) -> dict:
    """Return the operand-type table.

    HBC versions <=94 encode ``Imm32`` as ``uint32``; HBC 95+ encodes it
    as ``int32`` (signed).
    """
    imm32 = (4, to_int32, from_int32) if imm32_signed else (4, to_uint32, from_uint32)
    return {
        "Reg8":   (1, to_uint8,  from_uint8),
        "Reg32":  (4, to_uint32, from_uint32),
        "UInt8":  (1, to_uint8,  from_uint8),
        "UInt16": (2, to_uint16, from_uint16),
        "UInt32": (4, to_uint32, from_uint32),
        "Addr8":  (1, to_int8,   from_int8),
        "Addr32": (4, to_int32,  from_int32),
        "Imm32":  imm32,
        "Double": (8, to_double, from_double),
    }


# ---------------------------------------------------------------------------
# Parser / exporter.
# ---------------------------------------------------------------------------

def _parse(f, structure: dict, identifier_field: str) -> dict:
    headerS = structure["header"]
    smallFunctionHeaderS = structure["SmallFuncHeader"]
    functionHeaderS = structure["FuncHeader"]
    stringTableEntryS = structure["SmallStringTableEntry"]
    overflowStringTableEntryS = structure["OverflowStringTableEntry"]
    stringStorageS = structure["StringStorage"]
    arrayBufferS = structure["ArrayBuffer"]
    objKeyBufferS = structure["ObjKeyBuffer"]
    objValueBufferS = structure["ObjValueBuffer"]
    regExpTableEntryS = structure["RegExpTableEntry"]
    regExpStorageS = structure["RegExpStorage"]
    cjsModuleTableS = structure["CJSModuleTable"]

    obj: dict = {}

    # Segment 1: Header
    header: dict = {}
    for key in headerS:
        header[key] = read(f, headerS[key])
    obj["header"] = header
    f.pad(BYTECODE_ALIGNMENT)

    # Segment 2: Function Headers (with overflow follow-ups).
    functionHeaders: list = []
    for _ in range(header["functionCount"]):
        functionHeader: dict = {}
        for key in smallFunctionHeaderS:
            functionHeader[key] = read(f, smallFunctionHeaderS[key])

        if (functionHeader["flags"] >> 5) & 1:
            functionHeader["small"] = copy.deepcopy(functionHeader)
            saved_pos = f.tell()
            large_offset = (functionHeader["infoOffset"] << 16) | functionHeader["offset"]
            f.seek(large_offset)
            for key in functionHeaderS:
                functionHeader[key] = read(f, functionHeaderS[key])
            f.seek(saved_pos)

        functionHeaders.append(functionHeader)
    obj["functionHeaders"] = functionHeaders
    f.pad(BYTECODE_ALIGNMENT)

    # Segment 3: StringKind (skipped, just stored).
    stringKinds: list = []
    for _ in range(header["stringKindCount"]):
        stringKinds.append(readuint(f, bits=32))
    obj["stringKinds"] = stringKinds
    f.pad(BYTECODE_ALIGNMENT)

    # Segment 4: identifierHashes / identifierTranslations.
    identifiers: list = []
    for _ in range(header["identifierCount"]):
        identifiers.append(readuint(f, bits=32))
    obj[identifier_field] = identifiers
    f.pad(BYTECODE_ALIGNMENT)

    # Segment 5: StringTable
    stringTableEntries: list = []
    for _ in range(header["stringCount"]):
        entry: dict = {}
        for key in stringTableEntryS:
            entry[key] = read(f, stringTableEntryS[key])
        stringTableEntries.append(entry)
    obj["stringTableEntries"] = stringTableEntries
    f.pad(BYTECODE_ALIGNMENT)

    # Segment 6: StringTableOverflow
    stringTableOverflowEntries: list = []
    for _ in range(header["overflowStringCount"]):
        entry = {}
        for key in overflowStringTableEntryS:
            entry[key] = read(f, overflowStringTableEntryS[key])
        stringTableOverflowEntries.append(entry)
    obj["stringTableOverflowEntries"] = stringTableOverflowEntries
    f.pad(BYTECODE_ALIGNMENT)

    # Segment 7: StringStorage (variable-size blob).
    stringStorageS[2] = header["stringStorageSize"]
    obj["stringStorage"] = read(f, stringStorageS)
    f.pad(BYTECODE_ALIGNMENT)

    # Segment 8: ArrayBuffer
    arrayBufferS[2] = header["arrayBufferSize"]
    obj["arrayBuffer"] = read(f, arrayBufferS)
    f.pad(BYTECODE_ALIGNMENT)

    # Segment 9: ObjKeyBuffer
    objKeyBufferS[2] = header["objKeyBufferSize"]
    obj["objKeyBuffer"] = read(f, objKeyBufferS)
    f.pad(BYTECODE_ALIGNMENT)

    # Segment 10: ObjValueBuffer
    objValueBufferS[2] = header["objValueBufferSize"]
    obj["objValueBuffer"] = read(f, objValueBufferS)
    f.pad(BYTECODE_ALIGNMENT)

    # Segment 11: RegExpTable
    regExpTable: list = []
    for _ in range(header["regExpCount"]):
        entry = {}
        for key in regExpTableEntryS:
            entry[key] = read(f, regExpTableEntryS[key])
        regExpTable.append(entry)
    obj["regExpTable"] = regExpTable
    f.pad(BYTECODE_ALIGNMENT)

    # Segment 12: RegExpStorage
    regExpStorageS[2] = header["regExpStorageSize"]
    obj["regExpStorage"] = read(f, regExpStorageS)
    f.pad(BYTECODE_ALIGNMENT)

    # Segment 13: CJSModuleTable
    cjsModuleTable: list = []
    for _ in range(header["cjsModuleCount"]):
        entry = {}
        for key in cjsModuleTableS:
            entry[key] = read(f, cjsModuleTableS[key])
        cjsModuleTable.append(entry)
    obj["cjsModuleTable"] = cjsModuleTable
    f.pad(BYTECODE_ALIGNMENT)

    obj["instOffset"] = f.tell()
    obj["inst"] = f.readall()
    return obj


def _export(obj: dict, f, structure: dict, identifier_field: str) -> None:
    headerS = structure["header"]
    smallFunctionHeaderS = structure["SmallFuncHeader"]
    functionHeaderS = structure["FuncHeader"]
    stringTableEntryS = structure["SmallStringTableEntry"]
    overflowStringTableEntryS = structure["OverflowStringTableEntry"]
    stringStorageS = structure["StringStorage"]
    arrayBufferS = structure["ArrayBuffer"]
    objKeyBufferS = structure["ObjKeyBuffer"]
    objValueBufferS = structure["ObjValueBuffer"]
    regExpTableEntryS = structure["RegExpTableEntry"]
    regExpStorageS = structure["RegExpStorage"]
    cjsModuleTableS = structure["CJSModuleTable"]

    header = obj["header"]
    for key in headerS:
        write(f, header[key], headerS[key])
    f.pad(BYTECODE_ALIGNMENT)

    overflowedFunctionHeaders: list = []
    overflowedFunctionHeaderPositions: list = []

    functionHeaders = obj["functionHeaders"]
    for i in range(header["functionCount"]):
        functionHeader = functionHeaders[i]
        if "small" in functionHeader:
            overflowedFunctionHeaderPositions.append(f.tell())
            for key in smallFunctionHeaderS:
                write(f, functionHeader["small"][key], smallFunctionHeaderS[key])
            overflowedFunctionHeaders.append(functionHeader)
        else:
            for key in smallFunctionHeaderS:
                write(f, functionHeader[key], smallFunctionHeaderS[key])
    f.pad(BYTECODE_ALIGNMENT)

    stringKinds = obj["stringKinds"]
    for i in range(header["stringKindCount"]):
        writeuint(f, stringKinds[i], bits=32)
    f.pad(BYTECODE_ALIGNMENT)

    identifiers = obj[identifier_field]
    for i in range(header["identifierCount"]):
        writeuint(f, identifiers[i], bits=32)
    f.pad(BYTECODE_ALIGNMENT)

    stringTableEntries = obj["stringTableEntries"]
    for i in range(header["stringCount"]):
        for key in stringTableEntryS:
            write(f, stringTableEntries[i][key], stringTableEntryS[key])
    f.pad(BYTECODE_ALIGNMENT)

    stringTableOverflowEntries = obj["stringTableOverflowEntries"]
    for i in range(header["overflowStringCount"]):
        for key in overflowStringTableEntryS:
            write(f, stringTableOverflowEntries[i][key], overflowStringTableEntryS[key])
    f.pad(BYTECODE_ALIGNMENT)

    stringStorageS[2] = header["stringStorageSize"]
    write(f, obj["stringStorage"], stringStorageS)
    f.pad(BYTECODE_ALIGNMENT)

    arrayBufferS[2] = header["arrayBufferSize"]
    write(f, obj["arrayBuffer"], arrayBufferS)
    f.pad(BYTECODE_ALIGNMENT)

    objKeyBufferS[2] = header["objKeyBufferSize"]
    write(f, obj["objKeyBuffer"], objKeyBufferS)
    f.pad(BYTECODE_ALIGNMENT)

    objValueBufferS[2] = header["objValueBufferSize"]
    write(f, obj["objValueBuffer"], objValueBufferS)
    f.pad(BYTECODE_ALIGNMENT)

    regExpTable = obj["regExpTable"]
    for i in range(header["regExpCount"]):
        for key in regExpTableEntryS:
            write(f, regExpTable[i][key], regExpTableEntryS[key])
    f.pad(BYTECODE_ALIGNMENT)

    regExpStorageS[2] = header["regExpStorageSize"]
    write(f, obj["regExpStorage"], regExpStorageS)
    f.pad(BYTECODE_ALIGNMENT)

    cjsModuleTable = obj["cjsModuleTable"]
    for i in range(header["cjsModuleCount"]):
        for key in cjsModuleTableS:
            write(f, cjsModuleTable[i][key], cjsModuleTableS[key])
    f.pad(BYTECODE_ALIGNMENT)

    f.writeall(obj["inst"])

    # Patch up overflowed function header pointers (large_offset goes at the tail).
    for overflowedFunctionHeader, smallHeaderPos in zip(
        overflowedFunctionHeaders, overflowedFunctionHeaderPositions
    ):
        large_offset = f.tell()
        smallFunctionHeader = overflowedFunctionHeader["small"]
        smallFunctionHeader["infoOffset"] = large_offset >> 16
        smallFunctionHeader["offset"] = large_offset & 0xFFFF

        for key in functionHeaderS:
            write(f, overflowedFunctionHeader[key], functionHeaderS[key])

        current_pos = f.tell()
        f.seek(smallHeaderPos)
        for key in smallFunctionHeaderS:
            write(f, smallFunctionHeader[key], smallFunctionHeaderS[key])
        f.seek(current_pos)


# ---------------------------------------------------------------------------
# Translator (instruction-stream <-> structured ops).
# ---------------------------------------------------------------------------

def _disassemble(bc, opcode_mapper, opcode_operand, operand_type):
    if _util._fastutil is not None:
        return _util._fastutil.disassemble_ops(bc, opcode_mapper, opcode_operand)

    i = 0
    insts = []
    while i < len(bc):
        opcode = opcode_mapper[bc[i]]
        i += 1
        inst = (opcode, [])
        operand_ts = opcode_operand[opcode]
        for oper_t in operand_ts:
            is_str = oper_t.endswith(":S")
            if is_str:
                oper_t = oper_t[:-2]
            size, conv_to, _ = operand_type[oper_t]
            val = conv_to(bc[i:i + size])
            inst[1].append((oper_t, is_str, val))
            i += size
        insts.append(inst)
    return insts


def _assemble(insts, opcode_mapper_inv, opcode_operand, operand_type):
    if _util._fastutil is not None:
        return _util._fastutil.assemble_ops(insts, opcode_mapper_inv, opcode_operand)

    bc: list = []
    for opcode, operands in insts:
        op = opcode_mapper_inv[opcode]
        bc.append(op)
        if not (len(opcode_operand[opcode]) == len(operands)):
            raise ValueError(f"malformed instruction: {op}, {operands}")
        for oper_t, _, val in operands:
            if not (oper_t in operand_type):
                raise ValueError(f"unknown operand type: {oper_t}")
            _, _, conv_from = operand_type[oper_t]
            bc += conv_from(val)
    return bc


# ---------------------------------------------------------------------------
# Per-version base class.
# ---------------------------------------------------------------------------

class HBCBase:
    """Base class for every HBC<v> wrapper.

    Subclasses declare:

    * ``VERSION`` (int)
    * ``DATA_DIR`` (``pathlib.Path``) -- where ``structure.json`` /
      ``opcode.json`` live for this version
    * ``IDENTIFIER_FIELD`` (default ``IDENT_HASHES``)
    * ``IMM32_SIGNED`` (default ``False``)
    """

    VERSION: int = -1
    DATA_DIR: pathlib.Path | None = None
    IDENTIFIER_FIELD: str = IDENT_HASHES
    IMM32_SIGNED: bool = False

    def __init__(self, f=None):
        if self.DATA_DIR is None:
            raise RuntimeError(
                f"{type(self).__name__} did not set DATA_DIR; cannot load structure / opcode tables"
            )
        # Cache once per subclass.
        cls = type(self)
        if not getattr(cls, "_BOUND", False):
            cls._STRUCTURE = _load_structure(cls.DATA_DIR)
            (
                cls._OPCODE_OPERAND,
                cls._OPCODE_MAPPER,
                cls._OPCODE_MAPPER_INV,
            ) = _load_opcodes(cls.DATA_DIR)
            cls._OPERAND_TYPE = _make_operand_types(cls.IMM32_SIGNED)
            cls._BOUND = True

        if f is not None:
            self.obj = _parse(f, copy.deepcopy(cls._STRUCTURE), cls.IDENTIFIER_FIELD)
        else:
            self.obj = None

    # ------------------------------------------------------------------
    # Object lifecycle.
    # ------------------------------------------------------------------

    def export(self, f):
        cls = type(self)
        _export(self.getObj(), f, copy.deepcopy(cls._STRUCTURE), cls.IDENTIFIER_FIELD)

    def getObj(self):
        if not self.obj:
            raise RuntimeError("Obj is not set.")
        return self.obj

    def setObj(self, obj):
        self.obj = obj

    def getVersion(self):
        return self.VERSION

    def getHeader(self):
        return self.getObj()["header"]

    # ------------------------------------------------------------------
    # Translator pass-throughs.
    # ------------------------------------------------------------------

    def disassemble(self, bc):
        cls = type(self)
        return _disassemble(bc, cls._OPCODE_MAPPER, cls._OPCODE_OPERAND, cls._OPERAND_TYPE)

    def assemble(self, insts):
        cls = type(self)
        return _assemble(insts, cls._OPCODE_MAPPER_INV, cls._OPCODE_OPERAND, cls._OPERAND_TYPE)

    # ------------------------------------------------------------------
    # Functions.
    # ------------------------------------------------------------------

    def getFunctionCount(self):
        return self.getObj()["header"]["functionCount"]

    def getFunction(self, fid, disasm=True):
        if not (fid >= 0 and fid < self.getFunctionCount()):
            raise IndexError("Invalid function ID")

        functionHeader = self.getObj()["functionHeaders"][fid]
        offset = functionHeader["offset"]
        paramCount = functionHeader["paramCount"]
        registerCount = functionHeader["frameSize"]
        symbolCount = functionHeader["environmentSize"]
        bytecodeSizeInBytes = functionHeader["bytecodeSizeInBytes"]
        functionName = functionHeader["functionName"]

        instOffset = self.getObj()["instOffset"]
        start = offset - instOffset
        end = start + bytecodeSizeInBytes
        bc = self.getObj()["inst"][start:end]
        insts = bc
        if disasm:
            insts = self.disassemble(bc)

        functionNameStr, _ = self.getString(functionName)
        return functionNameStr, paramCount, registerCount, symbolCount, insts, functionHeader

    def setFunction(self, fid, func, disasm=True, offset_shift=0, string_id_cache=None):
        if not (fid >= 0 and fid < self.getFunctionCount()):
            raise IndexError("Invalid function ID")

        functionName, paramCount, registerCount, symbolCount, insts, _ = func
        functionHeader = self.getObj()["functionHeaders"][fid]
        functionHeader["paramCount"] = paramCount
        functionHeader["frameSize"] = registerCount
        functionHeader["environmentSize"] = symbolCount
        functionHeader["functionName"] = self.getStringId(
            functionName, string_id_cache=string_id_cache
        )

        offset = functionHeader["offset"]
        bytecodeSizeInBytes = functionHeader["bytecodeSizeInBytes"]
        instOffset = self.getObj()["instOffset"]
        start = offset - instOffset + offset_shift

        bc = insts
        if disasm:
            bc = self.assemble(insts)

        if len(bc) > bytecodeSizeInBytes:
            self.getObj()["inst"][start:start + bytecodeSizeInBytes] = bc
        else:
            memcpy(self.getObj()["inst"], bc, start, len(bc))
            if len(bc) < bytecodeSizeInBytes:
                del self.getObj()["inst"][start + len(bc):start + bytecodeSizeInBytes]

        functionHeader["bytecodeSizeInBytes"] = len(bc)
        return len(bc) - bytecodeSizeInBytes

    def _rebuild_function_offsets(self):
        function_headers = self.getObj()["functionHeaders"]
        chunks = []
        for function_header in function_headers:
            offset = function_header["offset"]
            bytecode_size = function_header["bytecodeSizeInBytes"]
            start = offset - self.getObj()["instOffset"]
            end = start + bytecode_size
            chunks.append(self.getObj()["inst"][start:end])

        new_inst: list = []
        current_offset = self.getObj()["instOffset"]
        for function_header, chunk in zip(function_headers, chunks):
            function_header["offset"] = current_offset
            function_header["bytecodeSizeInBytes"] = len(chunk)
            new_inst.extend(chunk)
            current_offset += len(chunk)
        self.getObj()["inst"] = new_inst

    def _shift_function_offsets(self, delta):
        if delta == 0:
            return
        for function_header in self.getObj()["functionHeaders"]:
            function_header["offset"] += delta

    def _allocate_string_slot(self, byte_length):
        header = self.getObj()["header"]
        old_size = header["stringStorageSize"]
        new_size = old_size + byte_length
        old_aligned_size = (old_size + 3) & ~0x03
        new_aligned_size = (new_size + 3) & ~0x03
        delta = new_aligned_size - old_aligned_size

        string_storage = self.getObj()["stringStorage"]
        offset = len(string_storage)
        string_storage.extend([0] * byte_length)

        header["stringStorageSize"] = len(string_storage)
        if delta:
            self.getObj()["instOffset"] += delta
            self._shift_function_offsets(delta)

        return offset

    # ------------------------------------------------------------------
    # Strings.
    # ------------------------------------------------------------------

    def getStringId(self, string_value, string_id_cache=None):
        count = self.getStringCount()
        if string_id_cache is not None:
            sid = string_id_cache.get(string_value)
            if sid is not None:
                return sid
        for i in range(count):
            s, _ = self.getString(i)
            if s == string_value:
                if string_id_cache is not None:
                    string_id_cache[string_value] = i
                return i

        isUTF16 = 0
        s = string_value.encode("utf-8")
        l = len(s)

        offset = self._allocate_string_slot(len(s))

        stringTableEntry = {"isUTF16": isUTF16}
        stringTableOverflowEntries = self.getObj()["stringTableOverflowEntries"]
        if l >= INVALID_LENGTH:
            stringTableEntry["length"] = INVALID_LENGTH
            stringTableEntry["offset"] = len(stringTableOverflowEntries)
            stringTableOverflowEntries.append({"offset": offset, "length": l})
            self.getObj()["header"]["overflowStringCount"] = len(stringTableOverflowEntries)
        else:
            stringTableEntry["length"] = l
            stringTableEntry["offset"] = offset

        self.getObj()["stringTableEntries"].append(stringTableEntry)
        self.getObj()["header"]["stringCount"] += 1

        memcpy(self.getObj()["stringStorage"], s, offset, len(s))

        if string_id_cache is not None:
            string_id_cache[string_value] = count
        return count

    def getStringCount(self):
        return self.getObj()["header"]["stringCount"]

    def getString(self, sid):
        if not (sid >= 0 and sid < self.getStringCount()):
            raise IndexError("Invalid string ID")

        stringTableEntry = self.getObj()["stringTableEntries"][sid]
        stringStorage = self.getObj()["stringStorage"]
        stringTableOverflowEntries = self.getObj()["stringTableOverflowEntries"]

        isUTF16 = stringTableEntry["isUTF16"]
        offset = stringTableEntry["offset"]
        length = stringTableEntry["length"]

        if length >= INVALID_LENGTH:
            stringTableOverflowEntry = stringTableOverflowEntries[offset]
            offset = stringTableOverflowEntry["offset"]
            length = stringTableOverflowEntry["length"]

        if isUTF16:
            length *= 2

        s = bytes(stringStorage[offset:offset + length])
        return s.hex() if isUTF16 else s.decode("utf-8"), (isUTF16, offset, length)

    def setString(self, sid, val):
        if not (sid >= 0 and sid < self.getStringCount()):
            raise IndexError("Invalid string ID")

        stringTableEntry = self.getObj()["stringTableEntries"][sid]
        stringStorage = self.getObj()["stringStorage"]
        stringTableOverflowEntries = self.getObj()["stringTableOverflowEntries"]

        isUTF16 = stringTableEntry["isUTF16"]
        offset = stringTableEntry["offset"]
        length = stringTableEntry["length"]

        if length >= INVALID_LENGTH:
            stringTableOverflowEntry = stringTableOverflowEntries[offset]
            offset = stringTableOverflowEntry["offset"]
            length = stringTableOverflowEntry["length"]

        if isUTF16:
            s = list(bytes.fromhex(val))
            l = len(s) // 2
        else:
            l = len(val)
            s = val.encode("utf-8")

        if l > length:
            offset = self._allocate_string_slot(len(s))
            if stringTableEntry["length"] >= INVALID_LENGTH:
                stringTableOverflowEntries[stringTableEntry["offset"]]["offset"] = offset
                stringTableOverflowEntries[stringTableEntry["offset"]]["length"] = l
            else:
                stringTableEntry["length"] = INVALID_LENGTH
                stringTableEntry["offset"] = len(stringTableOverflowEntries)
                stringTableOverflowEntries.append({"offset": offset, "length": l})
                self.getObj()["header"]["overflowStringCount"] = len(stringTableOverflowEntries)
        else:
            if isUTF16:
                length *= 2

        memcpy(stringStorage, s, offset, len(s))

    # ------------------------------------------------------------------
    # SLP buffers (array / objKey / objValue).
    # ------------------------------------------------------------------

    def _checkBufferTag(self, buf, iid):
        keyTag = buf[iid]
        if keyTag & 0x80:
            return (((keyTag & 0x0f) << 8) | (buf[iid + 1]), keyTag & TagMask)
        return (keyTag & 0x0f, keyTag & TagMask)

    def _SLPToString(self, tag, buf, iid, ind):
        start = iid + ind
        if tag == ByteStringTag:
            type = "String"
            val = buf[start]
            ind += 1
        elif tag == ShortStringTag:
            type = "String"
            val = unpack("<H", bytes(buf[start:start + 2]))[0]
            ind += 2
        elif tag == LongStringTag:
            type = "String"
            val = unpack("<L", bytes(buf[start:start + 4]))[0]
            ind += 4
        elif tag == NumberTag:
            type = "Number"
            val = unpack("<d", bytes(buf[start:start + 8]))[0]
            ind += 8
        elif tag == IntegerTag:
            type = "Integer"
            val = unpack("<L", bytes(buf[start:start + 4]))[0]
            ind += 4
        elif tag == NullTag:
            type = "Null"
            val = None
        elif tag == TrueTag:
            type = "Boolean"
            val = True
        elif tag == FalseTag:
            type = "Boolean"
            val = False
        else:
            type = "Empty"
            val = None
        return type, val, ind

    def getArrayBufferSize(self):
        return self.getObj()["header"]["arrayBufferSize"]

    def getArray(self, aid):
        if not (aid >= 0 and aid < self.getArrayBufferSize()):
            raise IndexError("Invalid Array ID")
        tag = self._checkBufferTag(self.getObj()["arrayBuffer"], aid)
        ind = 2 if tag[0] > 0x0f else 1
        arr: list = []
        t = None
        for _ in range(tag[0]):
            t, val, ind = self._SLPToString(tag[1], self.getObj()["arrayBuffer"], aid, ind)
            arr.append(val)
        return t, arr

    def getObjKeyBufferSize(self):
        return self.getObj()["header"]["objKeyBufferSize"]

    def getObjKey(self, kid):
        if not (kid >= 0 and kid < self.getObjKeyBufferSize()):
            raise IndexError("Invalid ObjKey ID")
        tag = self._checkBufferTag(self.getObj()["objKeyBuffer"], kid)
        ind = 2 if tag[0] > 0x0f else 1
        keys: list = []
        t = None
        for _ in range(tag[0]):
            t, val, ind = self._SLPToString(tag[1], self.getObj()["objKeyBuffer"], kid, ind)
            keys.append(val)
        return t, keys

    def getObjValueBufferSize(self):
        return self.getObj()["header"]["objValueBufferSize"]

    def getObjValue(self, vid):
        if not (vid >= 0 and vid < self.getObjValueBufferSize()):
            raise IndexError("Invalid ObjValue ID")
        tag = self._checkBufferTag(self.getObj()["objValueBuffer"], vid)
        ind = 2 if tag[0] > 0x0f else 1
        keys: list = []
        t = None
        for _ in range(tag[0]):
            t, val, ind = self._SLPToString(tag[1], self.getObj()["objValueBuffer"], vid, ind)
            keys.append(val)
        return t, keys


__all__ = [
    "HBCBase",
    "MAGIC",
    "BYTECODE_ALIGNMENT",
    "INVALID_OFFSET",
    "INVALID_LENGTH",
    "IDENT_HASHES",
    "IDENT_TRANSLATIONS",
    "NullTag", "TrueTag", "FalseTag", "NumberTag",
    "LongStringTag", "ShortStringTag", "ByteStringTag", "IntegerTag",
    "TagMask",
]
