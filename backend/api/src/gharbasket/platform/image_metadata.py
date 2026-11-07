"""Remove location and other metadata from an uploaded photo, without re-encoding it (M0 §3.1).

Phones write GPS coordinates, the device and the time into EXIF/XMP; a door, donation or KYC photo
would carry a home's location into storage. `strip` rewrites only the container:

- JPEG: drops APP1 (EXIF, XMP), APP3 to APP13, APP15, COM, APP2 except the ICC profile, and anything
  after the image's end (motion-photo videos, Samsung trailers, MPF secondary images). It keeps
  APP0 (JFIF), the ICC profile and APP14 (Adobe colour transform), and writes back a minimal EXIF
  with only the Orientation tag, so a portrait photo still shows upright.
- PNG: drops eXIf, tEXt, zTXt, iTXt and tIME chunks, and anything after IEND.
- WebP: drops EXIF and XMP chunks (clearing their VP8X flags), and anything after the RIFF.

Bytes that are not the declared format raise ValueError: the upload is refused, not stored as is.
"""

import struct

JPEG, PNG, WEBP = "image/jpeg", "image/png", "image/webp"
TYPES = (JPEG, PNG, WEBP)

_ICC = b"ICC_PROFILE\x00"
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_PNG_DROPPED = {b"eXIf", b"tEXt", b"zTXt", b"iTXt", b"tIME"}
_WEBP_DROPPED = {b"EXIF", b"XMP "}
_VP8X_EXIF, _VP8X_XMP = 0x08, 0x04
_ORIENTATION = 0x0112


def strip(data: bytes, content_type: str) -> bytes:
    """`data` without its metadata; the same object when there was nothing to remove."""
    if content_type == JPEG:
        return _jpeg(data)
    if content_type == PNG:
        return _png(data)
    if content_type == WEBP:
        return _webp(data)
    raise ValueError(f"no metadata stripper for {content_type}")


def _jpeg(data: bytes) -> bytes:
    if not data.startswith(b"\xff\xd8"):
        raise ValueError("not a JPEG")
    kept: list[bytes] = []
    orientation, pos, scanned = 1, 2, False
    while pos < len(data):
        if data[pos] != 0xFF:
            raise ValueError("JPEG marker expected")
        while pos + 1 < len(data) and data[pos + 1] == 0xFF:  # fill bytes
            pos += 1
        if pos + 1 >= len(data):
            raise ValueError("JPEG ends inside a marker")
        marker = data[pos + 1]
        if marker == 0xD9:  # EOI: the rest is someone else's (a trailer, a video)
            kept.append(b"\xff\xd9")
            pos += 2
            break
        if marker == 0x01 or 0xD0 <= marker <= 0xD7:  # no length
            kept.append(data[pos : pos + 2])
            pos += 2
            continue
        if pos + 4 > len(data):
            raise ValueError("JPEG ends inside a segment")
        end = pos + 2 + struct.unpack(">H", data[pos + 2 : pos + 4])[0]
        if end > len(data):
            raise ValueError("JPEG segment runs past the end")
        payload = data[pos + 4 : end]
        if marker == 0xE1 and payload.startswith(b"Exif\x00\x00"):
            orientation = _exif_orientation(payload[6:])
        if _jpeg_keeps(marker, payload):
            kept.append(data[pos:end])
        pos = end
        if marker == 0xDA:  # SOS: entropy-coded data up to the next real marker
            scanned = True
            scan = pos
            while True:
                pos = data.find(b"\xff", pos)
                if pos == -1 or pos + 1 >= len(data):  # no EOI: keep the scan, end the file
                    pos = len(data)
                    break
                following = data[pos + 1]
                if following == 0x00 or 0xD0 <= following <= 0xD7 or following == 0xFF:
                    pos += 1 if following == 0xFF else 2
                    continue
                break
            kept.append(data[scan:pos])
    if not scanned:
        raise ValueError("JPEG has no image data")
    if orientation != 1:
        at = 1 if kept and kept[0][1] == 0xE0 else 0  # after JFIF, if there is one
        kept.insert(at, _exif_app1(orientation))
    cleaned = b"\xff\xd8" + b"".join(kept)
    return data if cleaned == data else cleaned


def _jpeg_keeps(marker: int, payload: bytes) -> bool:
    if marker == 0xE2:
        return payload.startswith(_ICC)
    if marker in (0xE0, 0xEE):
        return True
    return not (0xE1 <= marker <= 0xEF or marker == 0xFE)


def _exif_orientation(tiff: bytes) -> int:
    """The Orientation tag (1 to 8) of IFD0, or 1 when it is missing or the EXIF is malformed."""
    try:
        order = {b"II": "<", b"MM": ">"}[tiff[:2]]
        ifd = struct.unpack(order + "I", tiff[4:8])[0]
        (count,) = struct.unpack(order + "H", tiff[ifd : ifd + 2])
        for entry in range(ifd + 2, ifd + 2 + 12 * count, 12):
            tag, kind = struct.unpack(order + "HH", tiff[entry : entry + 4])
            if tag == _ORIENTATION and kind == 3:  # SHORT
                (value,) = struct.unpack(order + "H", tiff[entry + 8 : entry + 10])
                return value if 1 <= value <= 8 else 1
    except (KeyError, struct.error):
        pass
    return 1


def _exif_app1(orientation: int) -> bytes:
    """APP1 with a big-endian TIFF holding one IFD0 entry: Orientation."""
    tiff = b"MM\x00\x2a" + struct.pack(">I", 8)
    tiff += struct.pack(">HHHIHH", 1, _ORIENTATION, 3, 1, orientation, 0) + struct.pack(">I", 0)
    payload = b"Exif\x00\x00" + tiff
    return b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload


def _png(data: bytes) -> bytes:
    if not data.startswith(_PNG_SIGNATURE):
        raise ValueError("not a PNG")
    kept, pos, ended = [_PNG_SIGNATURE], len(_PNG_SIGNATURE), False
    while pos + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[pos : pos + 8])
        end = pos + 12 + length
        if end > len(data):
            raise ValueError("PNG chunk runs past the end")
        if pos == len(_PNG_SIGNATURE) and kind != b"IHDR":
            raise ValueError("PNG does not start with IHDR")
        if kind not in _PNG_DROPPED:
            kept.append(data[pos:end])
        pos = end
        if kind == b"IEND":
            ended = True
            break
    if not ended:
        raise ValueError("PNG has no IEND")
    cleaned = b"".join(kept)
    return data if cleaned == data else cleaned


def _webp(data: bytes) -> bytes:
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise ValueError("not a WebP")
    end = 8 + struct.unpack("<I", data[4:8])[0]
    if end > len(data):
        raise ValueError("WebP RIFF runs past the end")
    kept, pos = [], 12
    while pos + 8 <= end:
        kind, size = struct.unpack("<4sI", data[pos : pos + 8])
        stop = pos + 8 + size + (size & 1)  # chunks are padded to an even size
        if pos + 8 + size > end:
            raise ValueError("WebP chunk runs past the end")
        chunk = data[pos : min(stop, end)]
        if kind == b"VP8X" and size >= 1:
            flags = chunk[8] & ~(_VP8X_EXIF | _VP8X_XMP)
            chunk = chunk[:8] + bytes([flags]) + chunk[9:]
        if kind not in _WEBP_DROPPED:
            kept.append(chunk)
        pos = stop
    if not kept:
        raise ValueError("WebP has no image data")
    body = b"WEBP" + b"".join(kept)
    cleaned = b"RIFF" + struct.pack("<I", len(body)) + body
    return data if cleaned == data else cleaned
