"""Location and other metadata leave uploaded photos; the pixels stay as they were (M0 §3.1)."""

import io

import pytest
from PIL import ExifTags, Image, PngImagePlugin

from gharbasket.platform import image_metadata
from gharbasket.platform.image_metadata import strip

GPS: dict[int, object] = {
    ExifTags.GPS.GPSLatitudeRef: "N",
    ExifTags.GPS.GPSLatitude: (19.0, 2.0, 31.0),
    ExifTags.GPS.GPSLongitudeRef: "E",
    ExifTags.GPS.GPSLongitude: (73.0, 4.0, 12.0),
}
XMP = b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:Description where="kharghar"/></x:xmpmeta>'
TRAILER = b"MotionPhoto_Data video-with-location"


def picture() -> Image.Image:
    image = Image.new("RGB", (32, 16))
    image.putdata([(x * 8 % 256, y * 16 % 256, 99) for y in range(16) for x in range(32)])
    return image


def exif(orientation: int | None) -> Image.Exif:
    found = Image.Exif()
    found[ExifTags.Base.Make] = "PhoneCo"
    if orientation is not None:
        found[ExifTags.Base.Orientation] = orientation
    found.get_ifd(ExifTags.IFD.GPSInfo).update(GPS)
    return found


def jpeg(orientation: int | None = 6, **extra: object) -> bytes:
    out = io.BytesIO()
    picture().save(out, "JPEG", exif=exif(orientation), xmp=XMP, comment=b"by Priya", **extra)
    return out.getvalue() + TRAILER


def pixels(data: bytes) -> bytes:
    return Image.open(io.BytesIO(data)).tobytes()


def test_a_jpeg_loses_gps_device_xmp_comment_and_trailer_and_keeps_pixels_and_rotation() -> None:
    original = jpeg(icc_profile=b"\x00" * 128)
    assert Image.open(io.BytesIO(original)).getexif().get_ifd(ExifTags.IFD.GPSInfo) == GPS

    cleaned = strip(original, "image/jpeg")

    opened = Image.open(io.BytesIO(cleaned))
    tags = opened.getexif()
    assert dict(tags) == {ExifTags.Base.Orientation: 6}  # big-endian, written back on its own
    assert tags.get_ifd(ExifTags.IFD.GPSInfo) == {}
    assert b"kharghar" not in cleaned and b"Priya" not in cleaned and b"PhoneCo" not in cleaned
    assert cleaned.endswith(b"\xff\xd9") and TRAILER not in cleaned
    assert opened.info.get("icc_profile") == b"\x00" * 128
    assert pixels(cleaned) == pixels(original)
    assert strip(cleaned, "image/jpeg") is cleaned  # nothing left to remove


@pytest.mark.parametrize("orientation", [None, 1])
def test_an_upright_jpeg_gets_no_exif_back(orientation: int | None) -> None:
    cleaned = strip(jpeg(orientation), "image/jpeg")

    assert b"Exif" not in cleaned
    assert dict(Image.open(io.BytesIO(cleaned)).getexif()) == {}


def test_a_progressive_jpeg_keeps_every_scan() -> None:
    original = jpeg(progressive=True)

    cleaned = strip(original, "image/jpeg")

    assert cleaned.count(b"\xff\xda") == original.count(b"\xff\xda") > 1
    assert pixels(cleaned) == pixels(original)


def test_a_clean_jpeg_comes_back_as_the_same_bytes() -> None:
    out = io.BytesIO()
    picture().save(out, "JPEG")

    original = out.getvalue()

    assert strip(original, "image/jpeg") is original  # so `clean` writes nothing back


def test_a_png_loses_exif_text_and_time() -> None:
    info = PngImagePlugin.PngInfo()
    info.add_text("Location", "Sunrise Towers, flat 1204")
    info.add_itxt("Comment", "kharghar", zip=True)
    out = io.BytesIO()
    picture().save(out, "PNG", exif=exif(6), pnginfo=info)
    original = out.getvalue() + TRAILER

    cleaned = strip(original, "image/png")

    opened = Image.open(io.BytesIO(cleaned))
    assert dict(opened.getexif()) == {}
    assert b"Sunrise" not in cleaned and b"tEXt" not in cleaned and b"iTXt" not in cleaned
    assert cleaned.endswith(b"IEND\xaeB`\x82")
    assert pixels(cleaned) == pixels(original)


def test_a_webp_loses_exif_and_xmp_and_their_flags() -> None:
    out = io.BytesIO()
    picture().save(out, "WEBP", lossless=True, exif=exif(6), xmp=XMP)
    original = out.getvalue() + TRAILER

    cleaned = strip(original, "image/webp")

    assert b"EXIF" not in cleaned and b"XMP " not in cleaned and TRAILER not in cleaned
    assert int.from_bytes(cleaned[4:8], "little") == len(cleaned) - 8
    flags = cleaned[cleaned.index(b"VP8X") + 8]
    assert flags & 0x0C == 0
    opened = Image.open(io.BytesIO(cleaned))
    assert dict(opened.getexif()) == {}
    assert pixels(cleaned) == pixels(original)


@pytest.mark.parametrize(
    ("data", "content_type"),
    [
        (b"%PDF-1.7 not a photo", "image/jpeg"),
        (b"\xff\xd8\xff\xe1\xff\xff", "image/jpeg"),  # a segment longer than the file
        (b"\xff\xd8\xff\xe0\x00\x04\x00\x00\xff\xd9", "image/jpeg"),  # no image data
        (b"<svg/>", "image/png"),
        (b"\x89PNG\r\n\x1a\n\x00\x00\x00\x00IHDR\x00\x00\x00\x00", "image/png"),  # no IEND
        (b"RIFF\x04\x00\x00\x00WEBP", "image/webp"),
        (b"RIFF\xff\x00\x00\x00WEBPVP8 ", "image/webp"),
        (b"anything", "application/pdf"),
    ],
)
def test_bytes_that_are_not_the_declared_image_are_refused(data: bytes, content_type: str) -> None:
    with pytest.raises(ValueError):
        strip(data, content_type)


def test_every_presignable_image_type_has_a_stripper() -> None:
    from gharbasket.platform.files import PURPOSES

    declared = {kind for purpose in PURPOSES.values() for kind in purpose.types}
    assert declared - {"application/pdf"} <= set(image_metadata.TYPES)
