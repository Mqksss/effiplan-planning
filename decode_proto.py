"""Generic Protobuf wire-format decoder (no .proto schema needed).

Effiplan's /api/v3/activities/events endpoint returns raw protobuf bytes
without publishing a .proto schema, so this walks the wire format directly
and infers structure from field numbers/types.
"""


def read_varint(b, i):
    result = 0
    shift = 0
    while True:
        byte = b[i]
        i += 1
        result |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            break
        shift += 7
    return result, i


def _is_printable(bs):
    try:
        s = bs.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if all(32 <= ord(c) < 127 or c in "\n\r\t" for c in s):
        return s
    return None


def decode_message(b):
    i = 0
    n = len(b)
    fields = []
    while i < n:
        tag, i = read_varint(b, i)
        field_num = tag >> 3
        wire_type = tag & 0x7
        if wire_type == 0:
            val, i = read_varint(b, i)
            fields.append((field_num, "varint", val))
        elif wire_type == 1:
            val = b[i:i + 8]
            i += 8
            fields.append((field_num, "fixed64", val.hex()))
        elif wire_type == 2:
            length, i = read_varint(b, i)
            val = b[i:i + length]
            i += length
            s = _is_printable(val)
            if s is not None:
                fields.append((field_num, "string", s))
            else:
                try:
                    sub = decode_message(val)
                    fields.append((field_num, "message", sub))
                except Exception:
                    fields.append((field_num, "bytes", val.hex()))
        elif wire_type == 5:
            val = b[i:i + 4]
            i += 4
            fields.append((field_num, "fixed32", val.hex()))
        else:
            raise ValueError(f"unknown wire type {wire_type}")
    return fields


def get_all(fields, field_num):
    return [v for (n, t, v) in fields if n == field_num]


def get_first(fields, field_num, default=None):
    for (n, t, v) in fields:
        if n == field_num:
            return v
    return default
