#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <stdint.h>
#include <string.h>

static int get_u8(PyObject* obj, Py_ssize_t idx, uint8_t* out) {
    if (PyBytes_Check(obj)) {
        Py_ssize_t size = PyBytes_GET_SIZE(obj);
        if (idx < 0 || idx >= size) return -1;
        *out = (uint8_t)PyBytes_AS_STRING(obj)[idx];
        return 0;
    }

    if (PyByteArray_Check(obj)) {
        Py_ssize_t size = PyByteArray_GET_SIZE(obj);
        if (idx < 0 || idx >= size) return -1;
        *out = (uint8_t)PyByteArray_AS_STRING(obj)[idx];
        return 0;
    }

    PyObject* item = PySequence_GetItem(obj, idx);
    if (!item) return -1;
    long v = PyLong_AsLong(item);
    Py_DECREF(item);
    if (PyErr_Occurred()) return -1;
    if (v < 0 || v > 255) {
        PyErr_SetString(PyExc_ValueError, "byte value out of range");
        return -1;
    }
    *out = (uint8_t)v;
    return 0;
}

static Py_ssize_t get_buffer_length(PyObject* obj) {
    if (PyBytes_Check(obj)) return PyBytes_GET_SIZE(obj);
    if (PyByteArray_Check(obj)) return PyByteArray_GET_SIZE(obj);
    return PySequence_Size(obj);
}

static int read_bytes(PyObject* obj, Py_ssize_t offset, uint8_t* out, Py_ssize_t count) {
    Py_ssize_t size = get_buffer_length(obj);
    if (size < 0) return -1;
    if (offset < 0 || count < 0 || offset > size || count > size - offset) {
        PyErr_SetString(PyExc_IndexError, "operand read out of range");
        return -1;
    }

    if (PyBytes_Check(obj)) {
        memcpy(out, PyBytes_AS_STRING(obj) + offset, (size_t)count);
        return 0;
    }

    if (PyByteArray_Check(obj)) {
        memcpy(out, PyByteArray_AS_STRING(obj) + offset, (size_t)count);
        return 0;
    }

    for (Py_ssize_t i = 0; i < count; ++i) {
        if (get_u8(obj, offset + i, &out[i]) != 0) return -1;
    }
    return 0;
}

static int operand_size(const char* oper_t) {
    if (strcmp(oper_t, "Reg8") == 0 || strcmp(oper_t, "UInt8") == 0 || strcmp(oper_t, "Addr8") == 0) return 1;
    if (strcmp(oper_t, "UInt16") == 0) return 2;
    if (strcmp(oper_t, "Reg32") == 0 || strcmp(oper_t, "UInt32") == 0 || strcmp(oper_t, "Addr32") == 0 || strcmp(oper_t, "Imm32") == 0) return 4;
    if (strcmp(oper_t, "Double") == 0) return 8;
    return -1;
}

static PyObject* parse_operand_value(const char* oper_t, PyObject* bc, Py_ssize_t offset) {
    int sz = operand_size(oper_t);
    if (sz < 0) {
        PyErr_SetString(PyExc_ValueError, "unknown operand type");
        return nullptr;
    }

    uint8_t b[8] = {0};
    if (read_bytes(bc, offset, b, sz) != 0) {
        if (!PyErr_Occurred()) PyErr_SetString(PyExc_IndexError, "operand read out of range");
        return nullptr;
    }

    if (strcmp(oper_t, "Double") == 0) {
        double d;
        memcpy(&d, b, 8);
        return PyFloat_FromDouble(d);
    }

    if (strcmp(oper_t, "Addr8") == 0) {
        int8_t v = (int8_t)b[0];
        return PyLong_FromLong((long)v);
    }

    if (strcmp(oper_t, "Addr32") == 0 || strcmp(oper_t, "Imm32") == 0) {
        int32_t v = (int32_t)((uint32_t)b[0] | ((uint32_t)b[1] << 8) | ((uint32_t)b[2] << 16) | ((uint32_t)b[3] << 24));
        return PyLong_FromLong((long)v);
    }

    if (sz == 1) {
        return PyLong_FromUnsignedLong((unsigned long)b[0]);
    }

    if (sz == 2) {
        uint16_t v = (uint16_t)b[0] | ((uint16_t)b[1] << 8);
        return PyLong_FromUnsignedLong((unsigned long)v);
    }

    uint32_t v = (uint32_t)b[0] | ((uint32_t)b[1] << 8) | ((uint32_t)b[2] << 16) | ((uint32_t)b[3] << 24);
    return PyLong_FromUnsignedLong((unsigned long)v);
}

// Write the byte representation of a single operand directly into ``dst``.
// Returns the number of bytes written, or -1 with a Python exception set.
//
// This used to be ``append_operand_bytes`` which built a Python ``list`` of
// ``PyLong`` objects -- one allocation + one PyLong box per byte. For an
// "instruction-heavy" bundle that's ~5 PyLong allocations per opcode times
// ~1M opcodes = ~5M PyLong allocations per round-trip, all of which were
// then unboxed back to bytes by the Python caller. Writing straight into
// the output ``bytearray`` removes that entire round-trip.
static int write_operand_bytes(uint8_t* dst, const char* oper_t, PyObject* val_obj) {
    int sz = operand_size(oper_t);
    if (sz < 0) {
        PyErr_SetString(PyExc_ValueError, "unknown operand type");
        return -1;
    }

    if (strcmp(oper_t, "Double") == 0) {
        double d = PyFloat_AsDouble(val_obj);
        if (PyErr_Occurred()) return -1;
        memcpy(dst, &d, 8);
        return 8;
    }

    long lv = PyLong_AsLong(val_obj);
    if (PyErr_Occurred()) return -1;

    if (strcmp(oper_t, "Addr8") == 0) {
        dst[0] = (uint8_t)((int8_t)lv);
        return 1;
    }

    uint32_t u;
    if (strcmp(oper_t, "Addr32") == 0 || strcmp(oper_t, "Imm32") == 0) {
        u = (uint32_t)(int32_t)lv;
    } else {
        u = (uint32_t)lv;
    }
    for (int i = 0; i < sz; ++i) {
        dst[i] = (uint8_t)((u >> (8 * i)) & 0xFFu);
    }
    return sz;
}

// Compute the byte width of one instruction given the operand-type list
// from ``opcode_operand`` (an opaque list of strings like "Reg8", "UInt32",
// "UInt32:S", "Double"). The trailing ":S" marker means "this operand is
// a string id"; it does not affect the byte width. Returns -1 on error.
static Py_ssize_t inst_size_from_operand_types(PyObject* operand_ts) {
    Py_ssize_t m = PySequence_Size(operand_ts);
    if (m < 0) return -1;
    Py_ssize_t total = 1;  // opcode byte
    for (Py_ssize_t k = 0; k < m; ++k) {
        PyObject* spec = PySequence_GetItem(operand_ts, k);
        if (!spec) return -1;
        const char* s = PyUnicode_AsUTF8(spec);
        if (!s) { Py_DECREF(spec); return -1; }
        size_t len = strlen(s);
        char base[32];
        if (len >= sizeof(base)) {
            Py_DECREF(spec);
            PyErr_SetString(PyExc_ValueError, "operand type too long");
            return -1;
        }
        if (len >= 2 && s[len - 2] == ':' && s[len - 1] == 'S') {
            memcpy(base, s, len - 2);
            base[len - 2] = '\0';
        } else {
            memcpy(base, s, len + 1);
        }
        Py_DECREF(spec);
        int sz = operand_size(base);
        if (sz < 0) {
            PyErr_SetString(PyExc_ValueError, "unknown operand type");
            return -1;
        }
        total += sz;
    }
    return total;
}

static PyObject* fu_disassemble_ops(PyObject*, PyObject* args) {
    PyObject* bc;
    PyObject* opcode_mapper;
    PyObject* opcode_operand;
    if (!PyArg_ParseTuple(args, "OOO", &bc, &opcode_mapper, &opcode_operand)) return nullptr;

    Py_ssize_t n = PySequence_Size(bc);
    if (n < 0) return nullptr;

    PyObject* insts = PyList_New(0);
    if (!insts) return nullptr;

    Py_ssize_t i = 0;
    while (i < n) {
        uint8_t opv;
        if (get_u8(bc, i, &opv) != 0) {
            Py_DECREF(insts);
            return nullptr;
        }
        i += 1;

        PyObject* opcode = PyList_GetItem(opcode_mapper, (Py_ssize_t)opv);  // borrowed
        if (!opcode) {
            Py_DECREF(insts);
            return nullptr;
        }

        PyObject* operand_ts = PyDict_GetItem(opcode_operand, opcode);  // borrowed
        if (!operand_ts) {
            Py_DECREF(insts);
            PyErr_SetString(PyExc_KeyError, "opcode missing in opcode_operand");
            return nullptr;
        }

        PyObject* operands = PyList_New(0);
        if (!operands) {
            Py_DECREF(insts);
            return nullptr;
        }

        Py_ssize_t m = PySequence_Size(operand_ts);
        if (m < 0) {
            Py_DECREF(operands);
            Py_DECREF(insts);
            return nullptr;
        }

        for (Py_ssize_t k = 0; k < m; ++k) {
            PyObject* oper_spec = PySequence_GetItem(operand_ts, k);
            if (!oper_spec) {
                Py_DECREF(operands);
                Py_DECREF(insts);
                return nullptr;
            }

            const char* oper_c = PyUnicode_AsUTF8(oper_spec);
            if (!oper_c) {
                Py_DECREF(oper_spec);
                Py_DECREF(operands);
                Py_DECREF(insts);
                return nullptr;
            }

            bool is_str = false;
            char base[32];
            size_t len = strlen(oper_c);
            if (len >= sizeof(base)) {
                Py_DECREF(oper_spec);
                Py_DECREF(operands);
                Py_DECREF(insts);
                PyErr_SetString(PyExc_ValueError, "operand type too long");
                return nullptr;
            }

            if (len >= 2 && oper_c[len - 2] == ':' && oper_c[len - 1] == 'S') {
                is_str = true;
                memcpy(base, oper_c, len - 2);
                base[len - 2] = '\0';
            } else {
                memcpy(base, oper_c, len + 1);
            }

            int sz = operand_size(base);
            if (sz < 0) {
                Py_DECREF(oper_spec);
                Py_DECREF(operands);
                Py_DECREF(insts);
                PyErr_SetString(PyExc_ValueError, "unknown operand type");
                return nullptr;
            }

            PyObject* val = parse_operand_value(base, bc, i);
            if (!val) {
                Py_DECREF(oper_spec);
                Py_DECREF(operands);
                Py_DECREF(insts);
                return nullptr;
            }

            PyObject* base_name = PyUnicode_FromString(base);
            if (!base_name) {
                Py_DECREF(val);
                Py_DECREF(oper_spec);
                Py_DECREF(operands);
                Py_DECREF(insts);
                return nullptr;
            }

            PyObject* tup = PyTuple_New(3);
            if (!tup) {
                Py_DECREF(base_name);
                Py_DECREF(val);
                Py_DECREF(oper_spec);
                Py_DECREF(operands);
                Py_DECREF(insts);
                return nullptr;
            }
            PyTuple_SET_ITEM(tup, 0, base_name);
            PyTuple_SET_ITEM(tup, 1, PyBool_FromLong(is_str ? 1 : 0));
            PyTuple_SET_ITEM(tup, 2, val);

            if (PyList_Append(operands, tup) != 0) {
                Py_DECREF(tup);
                Py_DECREF(oper_spec);
                Py_DECREF(operands);
                Py_DECREF(insts);
                return nullptr;
            }
            Py_DECREF(tup);
            Py_DECREF(oper_spec);
            i += sz;
        }

        PyObject* inst = PyTuple_New(2);
        if (!inst) {
            Py_DECREF(operands);
            Py_DECREF(insts);
            return nullptr;
        }
        Py_INCREF(opcode);
        PyTuple_SET_ITEM(inst, 0, opcode);
        PyTuple_SET_ITEM(inst, 1, operands);

        if (PyList_Append(insts, inst) != 0) {
            Py_DECREF(inst);
            Py_DECREF(insts);
            return nullptr;
        }
        Py_DECREF(inst);
    }

    return insts;
}

// Returns a freshly-allocated ``bytearray`` containing the assembled bytes.
//
// Pre-PR: this returned ``list[int]`` of length ~= 5..10 MB on a real
// React-Native bundle, with one ``PyLong`` allocation per byte. Round-trip
// peak memory was dominated by the ~8x overhead of ``list[int]`` vs the
// equivalent ``bytearray`` plus ~5M short-lived ``PyLong`` boxes.
//
// Post-PR: a single ``PyByteArray_FromStringAndSize`` allocation of the
// exact final size, then a write-straight-into-the-buffer loop. Caller
// (``HBCBase.setFunction`` / ``hasm.asm``) treats the return as a
// bytes-like, so switching from ``list[int]`` to ``bytearray`` is
// drop-in.
static PyObject* fu_assemble_ops(PyObject*, PyObject* args) {
    PyObject* insts;
    PyObject* opcode_mapper_inv;
    PyObject* opcode_operand = nullptr;
    if (!PyArg_ParseTuple(args, "OO|O", &insts, &opcode_mapper_inv, &opcode_operand)) return nullptr;

    Py_ssize_t n = PySequence_Size(insts);
    if (n < 0) return nullptr;

    // Pass 1: compute the exact total size by summing per-instruction
    // widths. Requires ``opcode_operand`` (the opcode -> [operand-type]
    // dict). This is cheap because the dict is already built and the
    // operand-type list per opcode is short (typically 0-5 entries).
    Py_ssize_t total_size = 0;
    if (opcode_operand) {
        for (Py_ssize_t i = 0; i < n; ++i) {
            PyObject* inst = PySequence_GetItem(insts, i);
            if (!inst) return nullptr;
            PyObject* opcode = PySequence_GetItem(inst, 0);
            Py_DECREF(inst);
            if (!opcode) return nullptr;
            PyObject* operand_ts = PyDict_GetItem(opcode_operand, opcode);  // borrowed
            if (!operand_ts) {
                Py_DECREF(opcode);
                PyErr_SetString(PyExc_KeyError, "opcode missing in opcode_operand");
                return nullptr;
            }
            Py_ssize_t sz = inst_size_from_operand_types(operand_ts);
            Py_DECREF(opcode);
            if (sz < 0) return nullptr;
            total_size += sz;
        }
    } else {
        // Caller didn't pass ``opcode_operand``. Use a generous estimate
        // and resize at the end. (The Python wrapper always passes it,
        // so this branch only runs when callers invoke
        // ``_fastutil.assemble_ops`` directly without it.)
        total_size = n * 16;
    }

    PyObject* out = PyByteArray_FromStringAndSize(nullptr, total_size);
    if (!out) return nullptr;
    uint8_t* buf = (uint8_t*)PyByteArray_AS_STRING(out);
    Py_ssize_t off = 0;

    // Pass 2: write opcode + operand bytes straight into the buffer.
    for (Py_ssize_t i = 0; i < n; ++i) {
        PyObject* inst = PySequence_GetItem(insts, i);
        if (!inst) {
            Py_DECREF(out);
            return nullptr;
        }

        PyObject* opcode = PySequence_GetItem(inst, 0);
        PyObject* operands = PySequence_GetItem(inst, 1);
        Py_DECREF(inst);
        if (!opcode || !operands) {
            Py_XDECREF(opcode);
            Py_XDECREF(operands);
            Py_DECREF(out);
            return nullptr;
        }

        PyObject* opval = PyDict_GetItem(opcode_mapper_inv, opcode);  // borrowed
        if (!opval) {
            Py_DECREF(opcode);
            Py_DECREF(operands);
            Py_DECREF(out);
            PyErr_SetString(PyExc_KeyError, "opcode missing in opcode_mapper_inv");
            return nullptr;
        }

        long op = PyLong_AsLong(opval);
        if (PyErr_Occurred()) {
            Py_DECREF(opcode);
            Py_DECREF(operands);
            Py_DECREF(out);
            return nullptr;
        }

        Py_ssize_t m = PySequence_Size(operands);
        if (m < 0) {
            Py_DECREF(opcode);
            Py_DECREF(operands);
            Py_DECREF(out);
            return nullptr;
        }

        if (opcode_operand) {
            PyObject* expected = PyDict_GetItem(opcode_operand, opcode);  // borrowed
            if (!expected) {
                Py_DECREF(opcode);
                Py_DECREF(operands);
                Py_DECREF(out);
                PyErr_SetString(PyExc_KeyError, "opcode missing in opcode_operand");
                return nullptr;
            }
            Py_ssize_t expected_len = PySequence_Size(expected);
            if (expected_len < 0) {
                Py_DECREF(opcode);
                Py_DECREF(operands);
                Py_DECREF(out);
                return nullptr;
            }
            if (expected_len != m) {
                Py_DECREF(opcode);
                Py_DECREF(operands);
                Py_DECREF(out);
                PyErr_SetString(PyExc_ValueError, "malicious instruction operand length mismatch");
                return nullptr;
            }
        }

        // Defensive: if pass 1 underestimated, grow the buffer rather
        // than overflow. Should never happen when ``opcode_operand``
        // is consistent between the two passes.
        if (off + 1 > total_size) {
            total_size = off + 1 + 16;
            if (PyByteArray_Resize(out, total_size) != 0) {
                Py_DECREF(opcode);
                Py_DECREF(operands);
                Py_DECREF(out);
                return nullptr;
            }
            buf = (uint8_t*)PyByteArray_AS_STRING(out);
        }
        buf[off++] = (uint8_t)(op & 0xFFu);

        for (Py_ssize_t k = 0; k < m; ++k) {
            PyObject* operand = PySequence_GetItem(operands, k);
            if (!operand) {
                Py_DECREF(opcode);
                Py_DECREF(operands);
                Py_DECREF(out);
                return nullptr;
            }

            PyObject* type_obj = PySequence_GetItem(operand, 0);
            PyObject* val_obj = PySequence_GetItem(operand, 2);
            Py_DECREF(operand);
            if (!type_obj || !val_obj) {
                Py_XDECREF(type_obj);
                Py_XDECREF(val_obj);
                Py_DECREF(opcode);
                Py_DECREF(operands);
                Py_DECREF(out);
                return nullptr;
            }

            const char* oper_t = PyUnicode_AsUTF8(type_obj);
            if (!oper_t) {
                Py_DECREF(type_obj);
                Py_DECREF(val_obj);
                Py_DECREF(opcode);
                Py_DECREF(operands);
                Py_DECREF(out);
                return nullptr;
            }

            // Reserve up to 8 bytes (max operand width: ``Double``).
            if (off + 8 > total_size) {
                total_size = off + 8 + 16;
                if (PyByteArray_Resize(out, total_size) != 0) {
                    Py_DECREF(type_obj);
                    Py_DECREF(val_obj);
                    Py_DECREF(opcode);
                    Py_DECREF(operands);
                    Py_DECREF(out);
                    return nullptr;
                }
                buf = (uint8_t*)PyByteArray_AS_STRING(out);
            }

            int wrote = write_operand_bytes(buf + off, oper_t, val_obj);
            Py_DECREF(type_obj);
            Py_DECREF(val_obj);
            if (wrote < 0) {
                Py_DECREF(opcode);
                Py_DECREF(operands);
                Py_DECREF(out);
                return nullptr;
            }
            off += wrote;
        }

        Py_DECREF(opcode);
        Py_DECREF(operands);
    }

    if (off != total_size) {
        // Truncate to actual written size if pre-pass over-allocated
        // (e.g. ``opcode_operand`` was missing and we used the
        // optimistic estimate).
        if (PyByteArray_Resize(out, off) != 0) {
            Py_DECREF(out);
            return nullptr;
        }
    }

    return out;
}

static PyObject* fu_to_uint8(PyObject*, PyObject* args) {
    PyObject* buf;
    if (!PyArg_ParseTuple(args, "O", &buf)) return nullptr;
    uint8_t b0;
    if (get_u8(buf, 0, &b0) != 0) {
        if (!PyErr_Occurred()) PyErr_SetString(PyExc_IndexError, "buffer too small");
        return nullptr;
    }
    return PyLong_FromUnsignedLong((unsigned long)b0);
}

static PyObject* fu_to_uint16(PyObject*, PyObject* args) {
    PyObject* buf;
    if (!PyArg_ParseTuple(args, "O", &buf)) return nullptr;
    uint8_t b0, b1;
    if (get_u8(buf, 0, &b0) != 0 || get_u8(buf, 1, &b1) != 0) {
        if (!PyErr_Occurred()) PyErr_SetString(PyExc_IndexError, "buffer too small");
        return nullptr;
    }
    uint16_t v = (uint16_t)b0 | ((uint16_t)b1 << 8);
    return PyLong_FromUnsignedLong((unsigned long)v);
}

static PyObject* fu_to_uint32(PyObject*, PyObject* args) {
    PyObject* buf;
    if (!PyArg_ParseTuple(args, "O", &buf)) return nullptr;
    uint8_t b0, b1, b2, b3;
    if (get_u8(buf, 0, &b0) != 0 || get_u8(buf, 1, &b1) != 0 || get_u8(buf, 2, &b2) != 0 || get_u8(buf, 3, &b3) != 0) {
        if (!PyErr_Occurred()) PyErr_SetString(PyExc_IndexError, "buffer too small");
        return nullptr;
    }
    uint32_t v = (uint32_t)b0 | ((uint32_t)b1 << 8) | ((uint32_t)b2 << 16) | ((uint32_t)b3 << 24);
    return PyLong_FromUnsignedLong((unsigned long)v);
}

static PyObject* fu_to_int8(PyObject*, PyObject* args) {
    PyObject* buf;
    if (!PyArg_ParseTuple(args, "O", &buf)) return nullptr;
    uint8_t b0;
    if (get_u8(buf, 0, &b0) != 0) {
        if (!PyErr_Occurred()) PyErr_SetString(PyExc_IndexError, "buffer too small");
        return nullptr;
    }
    int8_t v = (int8_t)b0;
    return PyLong_FromLong((long)v);
}

static PyObject* fu_to_int32(PyObject*, PyObject* args) {
    PyObject* buf;
    if (!PyArg_ParseTuple(args, "O", &buf)) return nullptr;
    uint8_t b0, b1, b2, b3;
    if (get_u8(buf, 0, &b0) != 0 || get_u8(buf, 1, &b1) != 0 || get_u8(buf, 2, &b2) != 0 || get_u8(buf, 3, &b3) != 0) {
        if (!PyErr_Occurred()) PyErr_SetString(PyExc_IndexError, "buffer too small");
        return nullptr;
    }
    int32_t v = (int32_t)((uint32_t)b0 | ((uint32_t)b1 << 8) | ((uint32_t)b2 << 16) | ((uint32_t)b3 << 24));
    return PyLong_FromLong((long)v);
}

static PyObject* fu_to_double(PyObject*, PyObject* args) {
    PyObject* buf;
    if (!PyArg_ParseTuple(args, "O", &buf)) return nullptr;
    uint8_t b[8];
    for (int i = 0; i < 8; ++i) {
        if (get_u8(buf, i, &b[i]) != 0) {
            if (!PyErr_Occurred()) PyErr_SetString(PyExc_IndexError, "buffer too small");
            return nullptr;
        }
    }
    double d;
    memcpy(&d, b, 8);
    return PyFloat_FromDouble(d);
}

static PyObject* list_from_bytes(const uint8_t* p, Py_ssize_t n) {
    PyObject* out = PyList_New(n);
    if (!out) return nullptr;
    for (Py_ssize_t i = 0; i < n; ++i) {
        PyObject* v = PyLong_FromUnsignedLong((unsigned long)p[i]);
        if (!v) { Py_DECREF(out); return nullptr; }
        PyList_SET_ITEM(out, i, v);
    }
    return out;
}

static PyObject* fu_from_uint8(PyObject*, PyObject* args) {
    unsigned long v;
    if (!PyArg_ParseTuple(args, "k", &v)) return nullptr;
    uint8_t b = (uint8_t)(v & 0xFF);
    return list_from_bytes(&b, 1);
}

static PyObject* fu_from_uint16(PyObject*, PyObject* args) {
    unsigned long v;
    if (!PyArg_ParseTuple(args, "k", &v)) return nullptr;
    uint8_t b[2] = {(uint8_t)(v & 0xFF), (uint8_t)((v >> 8) & 0xFF)};
    return list_from_bytes(b, 2);
}

static PyObject* fu_from_uint32(PyObject*, PyObject* args) {
    unsigned long v;
    if (!PyArg_ParseTuple(args, "k", &v)) return nullptr;
    uint8_t b[4] = {
        (uint8_t)(v & 0xFF),
        (uint8_t)((v >> 8) & 0xFF),
        (uint8_t)((v >> 16) & 0xFF),
        (uint8_t)((v >> 24) & 0xFF)
    };
    return list_from_bytes(b, 4);
}

static PyObject* fu_from_int8(PyObject*, PyObject* args) {
    long v;
    if (!PyArg_ParseTuple(args, "l", &v)) return nullptr;
    int8_t i = (int8_t)v;
    uint8_t b = (uint8_t)i;
    return list_from_bytes(&b, 1);
}

static PyObject* fu_from_int32(PyObject*, PyObject* args) {
    long v;
    if (!PyArg_ParseTuple(args, "l", &v)) return nullptr;
    uint32_t u = (uint32_t)(int32_t)v;
    uint8_t b[4] = {
        (uint8_t)(u & 0xFF),
        (uint8_t)((u >> 8) & 0xFF),
        (uint8_t)((u >> 16) & 0xFF),
        (uint8_t)((u >> 24) & 0xFF)
    };
    return list_from_bytes(b, 4);
}

static PyObject* fu_from_double(PyObject*, PyObject* args) {
    double d;
    if (!PyArg_ParseTuple(args, "d", &d)) return nullptr;
    uint8_t b[8];
    memcpy(b, &d, 8);
    return list_from_bytes(b, 8);
}

static PyObject* fu_memcpy(PyObject*, PyObject* args) {
    PyObject* dest;
    PyObject* src;
    Py_ssize_t start;
    Py_ssize_t length;
    if (!PyArg_ParseTuple(args, "OOnn", &dest, &src, &start, &length)) return nullptr;

    if (start < 0 || length < 0) {
        PyErr_SetString(PyExc_IndexError, "memcpy: start/length must be non-negative");
        return nullptr;
    }

    // Bytearray destination: contiguous mutable buffer, real memcpy path.
    if (PyByteArray_Check(dest)) {
        Py_ssize_t dest_len = PyByteArray_GET_SIZE(dest);
        // Overflow-safe equivalent of ``start + length > dest_len``: matches
        // the pattern in ``read_bytes`` (line 43). Computing ``start + length``
        // directly is undefined behaviour on signed overflow and in practice
        // wraps to a large negative value, bypassing the bounds check.
        if (start > dest_len || length > dest_len - start) {
            PyErr_SetString(PyExc_IndexError, "memcpy: dest too small");
            return nullptr;
        }
        char* dest_buf = PyByteArray_AS_STRING(dest);

        // If src is also a contiguous byte buffer, do a real memcpy.
        if (PyByteArray_Check(src) || PyBytes_Check(src)) {
            const char* src_buf;
            Py_ssize_t src_len;
            if (PyByteArray_Check(src)) {
                src_buf = PyByteArray_AS_STRING(src);
                src_len = PyByteArray_GET_SIZE(src);
            } else {
                src_buf = PyBytes_AS_STRING(src);
                src_len = PyBytes_GET_SIZE(src);
            }
            // ``length`` and ``src_len`` are both already non-negative here,
            // so this comparison is overflow-safe.
            if (length > src_len) {
                PyErr_SetString(PyExc_IndexError, "memcpy: src too small");
                return nullptr;
            }
            // ``memmove``, not ``memcpy``: a Python caller can legitimately
            // pass overlapping (or identical) buffers --
            // ``util.memcpy(x, x, 1, 4)`` is well-defined Python and must
            // produce the same result as a slice assignment.  ``memcpy``
            // is undefined behaviour on overlap; ``memmove`` handles both
            // overlapping and disjoint regions and is the same speed on
            // every modern libc.
            memmove(dest_buf + start, src_buf, (size_t)length);
            Py_RETURN_NONE;
        }

        // Otherwise treat src as a sequence of ints in [0, 255].
        for (Py_ssize_t i = 0; i < length; ++i) {
            PyObject* item = PySequence_GetItem(src, i);
            if (!item) return nullptr;
            long v = PyLong_AsLong(item);
            Py_DECREF(item);
            if (PyErr_Occurred()) return nullptr;
            if (v < 0 || v > 255) {
                PyErr_SetString(PyExc_ValueError, "src item out of byte range");
                return nullptr;
            }
            dest_buf[start + i] = (char)v;
        }
        Py_RETURN_NONE;
    }

    // Legacy list[int] destination (kept so external callers keep working).
    if (PyList_Check(dest)) {
        Py_ssize_t dest_len = PyList_GET_SIZE(dest);
        if (start > dest_len || length > dest_len - start) {
            PyErr_SetString(PyExc_IndexError, "memcpy: dest too small");
            return nullptr;
        }
        for (Py_ssize_t i = 0; i < length; ++i) {
            PyObject* item = PySequence_GetItem(src, i);
            if (!item) return nullptr;
            long v = PyLong_AsLong(item);
            if (PyErr_Occurred()) { Py_DECREF(item); return nullptr; }
            if (v < 0 || v > 255) {
                Py_DECREF(item);
                PyErr_SetString(PyExc_ValueError, "src item out of byte range");
                return nullptr;
            }
            PyObject* pyv = PyLong_FromLong(v);
            Py_DECREF(item);
            if (!pyv) return nullptr;
            if (PyList_SetItem(dest, start + i, pyv) != 0) {
                Py_DECREF(pyv);
                return nullptr;
            }
        }
        Py_RETURN_NONE;
    }

    PyErr_SetString(PyExc_TypeError, "dest must be a list or bytearray");
    return nullptr;
}

static PyMethodDef FastUtilMethods[] = {
    {"to_uint8", fu_to_uint8, METH_VARARGS, "Convert buffer to uint8."},
    {"to_uint16", fu_to_uint16, METH_VARARGS, "Convert buffer to uint16 (LE)."},
    {"to_uint32", fu_to_uint32, METH_VARARGS, "Convert buffer to uint32 (LE)."},
    {"to_int8", fu_to_int8, METH_VARARGS, "Convert buffer to int8."},
    {"to_int32", fu_to_int32, METH_VARARGS, "Convert buffer to int32 (LE)."},
    {"to_double", fu_to_double, METH_VARARGS, "Convert buffer to double (LE bytes)."},
    {"from_uint8", fu_from_uint8, METH_VARARGS, "Pack uint8 to byte list."},
    {"from_uint16", fu_from_uint16, METH_VARARGS, "Pack uint16 to byte list."},
    {"from_uint32", fu_from_uint32, METH_VARARGS, "Pack uint32 to byte list."},
    {"from_int8", fu_from_int8, METH_VARARGS, "Pack int8 to byte list."},
    {"from_int32", fu_from_int32, METH_VARARGS, "Pack int32 to byte list."},
    {"from_double", fu_from_double, METH_VARARGS, "Pack double to byte list."},
    {"memcpy", fu_memcpy, METH_VARARGS, "Copy byte items from src to dest list."},
    {"disassemble_ops", fu_disassemble_ops, METH_VARARGS, "Disassemble opcodes in C++."},
    {"assemble_ops", fu_assemble_ops, METH_VARARGS, "Assemble opcodes in C++."},
    {nullptr, nullptr, 0, nullptr}
};

static struct PyModuleDef fastutilmodule = {
    PyModuleDef_HEAD_INIT,
    "_fastutil",
    "Fast utility helpers for hbctool.",
    -1,
    FastUtilMethods
};

PyMODINIT_FUNC PyInit__fastutil(void) {
    return PyModule_Create(&fastutilmodule);
}
