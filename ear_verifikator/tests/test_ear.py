from conftest import ATTRS_EAR, ATTRS_OBYCEJNY, nacti_asn1_cert
from ear_verifikator.core.ear import rozpoznat_ear


def test_ear_cert_rozpoznan():
    info = rozpoznat_ear(nacti_asn1_cert(ATTRS_EAR))
    assert info.je_ear
    assert info.jmeno == "Ing. Jan Novák"
    assert info.komora == "ČKAIT"
    assert info.cislo_autorizace == "0012345"
    assert "pozemní stavby" in info.obor


def test_obycejny_cert_neni_ear():
    info = rozpoznat_ear(nacti_asn1_cert(ATTRS_OBYCEJNY))
    assert not info.je_ear
    assert info.jmeno == "Jan Novák"
    assert info.komora == ""


def test_cka_architekt():
    from cryptography import x509
    from cryptography.x509.oid import NameOID

    attrs = [
        x509.NameAttribute(NameOID.COMMON_NAME, "Ing. arch. Petra Svobodová"),
        x509.NameAttribute(NameOID.COUNTRY_NAME, "CZ"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Česká komora architektů"),
        x509.NameAttribute(NameOID.TITLE, "autorizovaný architekt"),
        x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, "autorizace č. 04321"),
    ]
    info = rozpoznat_ear(nacti_asn1_cert(attrs))
    assert info.je_ear
    assert info.komora == "ČKA"
    assert info.cislo_autorizace == "04321"
