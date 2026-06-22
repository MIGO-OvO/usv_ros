from __future__ import print_function

import struct
import zlib


_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _png_chunks(data):
    if not data or not data.startswith(_PNG_MAGIC):
        return None
    offset = len(_PNG_MAGIC)
    chunks = []
    while offset + 8 <= len(data):
        try:
            size = struct.unpack(">I", data[offset:offset + 4])[0]
        except struct.error:
            return None
        tag = data[offset + 4:offset + 8]
        start = offset + 8
        end = start + size
        if end + 4 > len(data):
            return None
        chunks.append((tag, data[start:end]))
        offset = end + 4
        if tag == b"IEND":
            break
    return chunks


def _paeth(left, up, up_left):
    estimate = left + up - up_left
    dist_left = abs(estimate - left)
    dist_up = abs(estimate - up)
    dist_up_left = abs(estimate - up_left)
    if dist_left <= dist_up and dist_left <= dist_up_left:
        return left
    if dist_up <= dist_up_left:
        return up
    return up_left


def _defilter_png(raw, width, height, channels):
    row_len = int(width) * int(channels)
    bpp = int(channels)
    rows = []
    previous = bytearray(row_len)
    offset = 0
    for _row_index in range(int(height)):
        if offset + 1 + row_len > len(raw):
            return None
        filter_type = raw[offset]
        offset += 1
        row = bytearray(raw[offset:offset + row_len])
        offset += row_len
        for i in range(row_len):
            left = row[i - bpp] if i >= bpp else 0
            up = previous[i]
            up_left = previous[i - bpp] if i >= bpp else 0
            if filter_type == 1:
                row[i] = (row[i] + left) & 0xFF
            elif filter_type == 2:
                row[i] = (row[i] + up) & 0xFF
            elif filter_type == 3:
                row[i] = (row[i] + ((left + up) // 2)) & 0xFF
            elif filter_type == 4:
                row[i] = (row[i] + _paeth(left, up, up_left)) & 0xFF
            elif filter_type != 0:
                return None
        rows.append(bytes(row))
        previous = row
    return rows


def is_blank_tile(data):
    chunks = _png_chunks(data)
    if not chunks:
        return False
    ihdr = None
    idat = []
    for tag, payload in chunks:
        if tag == b"IHDR":
            ihdr = payload
        elif tag == b"IDAT":
            idat.append(payload)
    if ihdr is None or not idat or len(ihdr) < 13:
        return False
    try:
        width, height, bit_depth, color_type = struct.unpack(">IIBB", ihdr[:10])
    except struct.error:
        return False
    channels_by_type = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
    channels = channels_by_type.get(color_type)
    if bit_depth != 8 or channels is None or width <= 0 or height <= 0:
        return False
    try:
        raw = zlib.decompress(b"".join(idat))
    except zlib.error:
        return False
    rows = _defilter_png(raw, width, height, channels)
    if rows is None:
        return False
    first = None
    for row in rows:
        for idx in range(0, len(row), channels):
            pixel = row[idx:idx + channels]
            if first is None:
                first = pixel
            elif pixel != first:
                return False
    return first is not None
