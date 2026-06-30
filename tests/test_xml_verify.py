"""Robustness of verify_xml against malformed / adversarial XAdES structures.

A verifier is routinely handed untrusted input, so a structurally broken signature must produce
a clean INVALID indication, never an uncaught internal exception (e.g. AttributeError)."""

from cedula_uy_pdf_sign.xml_verify import verify_xml

DS = "xmlns:ds='http://www.w3.org/2000/09/xmldsig#'"


def _invalid(xml: bytes):
    result = verify_xml(xml, trust_roots=None)
    assert result.indication == "INVALID", [(c.name, c.detail) for c in result.checks]
    return result


def test_no_signature_is_invalid():
    _invalid(b"<?xml version='1.0'?><root/>")


def test_signature_without_signedinfo_is_invalid_not_crash():
    # Previously raised AttributeError: 'NoneType' object has no attribute 'findall'.
    _invalid(f"<root><ds:Signature {DS}></ds:Signature></root>".encode())


def test_signature_without_signaturevalue_is_invalid():
    _invalid(
        f"<root><ds:Signature {DS}><ds:SignedInfo></ds:SignedInfo>"
        f"</ds:Signature></root>".encode()
    )


def test_signature_with_empty_signaturevalue_is_invalid():
    _invalid(
        f"<root><ds:Signature {DS}><ds:SignedInfo></ds:SignedInfo>"
        f"<ds:SignatureValue>   </ds:SignatureValue></ds:Signature></root>".encode()
    )


def test_signature_without_certificate_is_invalid():
    _invalid(
        f"<root><ds:Signature {DS}><ds:SignedInfo></ds:SignedInfo>"
        f"<ds:SignatureValue>QUJD</ds:SignatureValue></ds:Signature></root>".encode()
    )
