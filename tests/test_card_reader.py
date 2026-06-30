"""Unit tests for the cédula AIS reader's pure parsing/formatting/redaction logic.

These exercise only the pure functions (no PC/SC / pyscard, no card), in particular that --redact
hides *every* biographical field: an identity dump is all personal data, so a partial redaction
(e.g. leaking birth date or birthplace) would defeat the purpose."""

import json

from cedula_uy_pdf_sign.card_reader import (
    card_to_json_obj,
    format_card_human,
    parse_bio,
    parse_doc_number,
    parse_mrz,
    parse_photo,
    _fmt_date,
)


def _tlv(tag: int, value: bytes) -> list:
    return [0x1F, tag, len(value)] + list(value)


# A fictitious card record (no real personal data).
_CARD = {
    "bio": {
        0x01: "PEREZ", 0x02: "GOMEZ", 0x03: "JUAN", 0x04: "URY",
        0x05: "01011970", 0x06: "MONTEVIDEO", 0x07: "12345678", 0x09: "01012099",
    },
    "doc_num": "00000TXXXX",
    "mrz": ["I<URY00000", "7001010<99", "PEREZ<GOMEZ"],
}

_SENSITIVE = ["PEREZ", "GOMEZ", "JUAN", "MONTEVIDEO", "12345678", "01011970",
              "01/01/1970", "00000TXXXX", "I<URY00000", "URY"]


# --- parsers ----------------------------------------------------------------

def test_parse_bio_reads_1f_tlv():
    data = _tlv(0x01, b"PEREZ") + _tlv(0x03, b"JUAN PABLO")
    assert parse_bio(data) == {0x01: "PEREZ", 0x03: "JUAN PABLO"}


def test_parse_bio_stops_at_non_1f():
    data = _tlv(0x01, b"PEREZ") + [0x00, 0x00]
    assert parse_bio(data) == {0x01: "PEREZ"}


def test_parse_doc_number():
    data = [0x5F, 0x01, 0x05] + list(b"12345")
    assert parse_doc_number(data) == "12345"
    assert parse_doc_number([0x00, 0x00]) is None


def test_parse_mrz_td1_three_lines():
    raw = ("A" * 30 + "B" * 30 + "C" * 30).encode("ascii")
    assert parse_mrz(list(raw)) == ["A" * 30, "B" * 30, "C" * 30]


def test_fmt_date():
    assert _fmt_date("01011970") == "01/01/1970"


# --- redaction (the regression guard) ---------------------------------------

def test_json_obj_full_then_redacted():
    full = card_to_json_obj(_CARD, redact=False)
    assert full["first_lastname"] == "PEREZ"
    assert full["birth_date"] == "01/01/1970"      # date formatted
    assert full["birthplace"] == "MONTEVIDEO"
    assert full["document_number"] == "00000TXXXX"
    assert full["mrz"] == _CARD["mrz"]

    red = card_to_json_obj(_CARD, redact=True)
    # Every biographical field, the document number and the MRZ are hidden.
    for key in ("first_lastname", "second_lastname", "given_names", "nationality",
                "birth_date", "birthplace", "id_number", "expiry_date", "document_number"):
        assert red[key] == "[REDACTED]", key
    assert red["mrz"] == "[REDACTED]"
    # No sensitive value survives anywhere in the serialised output.
    blob = json.dumps(red, ensure_ascii=False)
    for needle in _SENSITIVE:
        assert needle not in blob, needle


def test_human_output_redacted_leaks_nothing():
    full = format_card_human(_CARD, redact=False)
    assert "PEREZ" in full and "MONTEVIDEO" in full        # shows data without --redact
    assert "CÉDULA DE IDENTIDAD - URUGUAY" in full         # header, no em dash

    red = format_card_human(_CARD, redact=True)
    for needle in _SENSITIVE:
        assert needle not in red, needle
    assert "[REDACTED]" in red


def test_absent_fields_are_omitted():
    card = {"bio": {0x01: "PEREZ"}, "doc_num": None, "mrz": None}
    out = card_to_json_obj(card, redact=False)
    assert out == {"first_lastname": "PEREZ"}   # missing fields/doc/mrz omitted


# --- photo (file 7004) parsing ----------------------------------------------

def _ber_tlv(tag: bytes, value: bytes) -> list:
    """Wrap value in BER-TLV with `tag`, using long-form length above 127 bytes (as the card does)."""
    n = len(value)
    if n < 0x80:
        length = bytes([n])
    elif n <= 0xFFFF:
        length = bytes([0x82, (n >> 8) & 0xFF, n & 0xFF])
    else:
        length = bytes([0x83, (n >> 16) & 0xFF, (n >> 8) & 0xFF, n & 0xFF])
    return list(tag + length + value)


# A synthetic JPEG: SOI ... EOI, >127 bytes so the wrapper uses a long-form length like the card.
_JPEG = b"\xff\xd8\xff" + b"\x00" * 200 + b"\xff\xd9"


def test_parse_photo_tlv_long_length():
    data = _ber_tlv(b"\x3f\x01", _JPEG)        # 3F 01 82 LL LL <jpeg>, exactly like file 7004
    assert data[2] == 0x82                     # long-form length, as on the real card
    assert parse_photo(data) == _JPEG


def test_parse_photo_tlv_short_length():
    small = b"\xff\xd8\xff\x00\xff\xd9"        # < 128 bytes -> short-form length
    data = _ber_tlv(b"\x3f\x01", small)
    assert data[2] < 0x80
    assert parse_photo(data) == small


def test_parse_photo_fallback_locates_soi():
    # An unexpected wrapper: the JPEG is still recovered via its SOI marker.
    data = list(b"\x99\x88\x77" + _JPEG)
    assert bytes(parse_photo(data)).startswith(b"\xff\xd8\xff")


def test_parse_photo_none_without_jpeg():
    assert parse_photo(list(b"\x3f\x01\x05hello")) is None


class _FakeCard:
    """Minimal PC/SC card serving one file via SELECT AID / SELECT FILE / READ BINARY, enough to
    exercise the APDU read path (select_applet -> select_file -> read_file)."""

    def __init__(self, file_bytes: bytes):
        self.file = file_bytes

    def transmit(self, apdu):
        if apdu[:4] == [0x00, 0xA4, 0x04, 0x00]:          # SELECT AID
            return [], 0x90, 0x00
        if apdu[:4] == [0x00, 0xA4, 0x00, 0x00]:          # SELECT FILE -> FCI with tag 81 (size)
            size = len(self.file)
            return [0x6F, 0x04, 0x81, 0x02, (size >> 8) & 0xFF, size & 0xFF], 0x90, 0x00
        if apdu[:2] == [0x00, 0xB0]:                       # READ BINARY
            offset = ((apdu[2] & 0x7F) << 8) | apdu[3]
            return list(self.file[offset:offset + apdu[4]]), 0x90, 0x00
        raise AssertionError(f"unexpected APDU: {apdu}")


def test_read_photo_end_to_end_with_fake_card():
    from cedula_uy_pdf_sign.card_reader import read_photo

    card = _FakeCard(bytes(_ber_tlv(b"\x3f\x01", _JPEG)))   # file 7004 as on the card
    assert read_photo(card) == _JPEG                        # full path: applet -> file -> JPEG
