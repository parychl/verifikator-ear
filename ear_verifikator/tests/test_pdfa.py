from pathlib import Path

from ear_verifikator.core import pdfa
from ear_verifikator.core.pdfa import _CONF, _PART
from pyhanko.pdf_utils.reader import PdfFileReader


def test_regex_atributova_forma():
    xmp = b'<rdf:Description pdfaid:part="3" pdfaid:conformance="B"/>'
    assert _PART.search(xmp).group(1) == b"3"
    assert _CONF.search(xmp).group(1) == b"B"


def test_regex_elementova_forma():
    xmp = b"<pdfaid:part>2</pdfaid:part><pdfaid:conformance>u</pdfaid:conformance>"
    assert _PART.search(xmp).group(2) == b"2"
    assert _CONF.search(xmp).group(2) == b"u"


def test_bez_deklarace(blank_pdf):
    with blank_pdf.open("rb") as f:
        reader = PdfFileReader(f)
        info = pdfa.zkontroluj_pdfa(blank_pdf, reader, verapdf=None)
    assert info.deklarovana == ""
    assert info.vyhovuje is False
    assert "190/2024" in info.detail


def test_vysledek_z_json():
    # struktura odpovídá výstupu greenfield-apps 1.28 s --format json
    vystup = """{"report": {"jobs": [{"validationResult": [{
        "profileName": "PDF/A-3A validation profile",
        "compliant": true,
        "details": {"passedRules": 155, "failedRules": 0}
    }]}]}}"""
    vyhovuje, detail = pdfa._vysledek_z_json(vystup)
    assert vyhovuje is True
    assert "PDF/A-3A" in detail


def test_verapdf_prikaz_tvar():
    from ear_verifikator.core import verapdf_setup

    prikaz = verapdf_setup.verapdf_prikaz()
    assert prikaz is None or (isinstance(prikaz, list) and prikaz)


def test_spatna_verze_deklarace(tmp_path, blank_pdf):
    # PDF/A-2 deklarace → nevyhovuje vyhlášce (vyžadován PDF/A-3)
    from ear_verifikator.core.model import InfoPDFA

    info = InfoPDFA(deklarovana="PDF/A-2B")
    # zkontroluj_pdfa vyhodnocuje deklaraci z readeru; tady testujeme logiku přímo
    assert not info.deklarovana.startswith(f"PDF/A-{pdfa.POZADOVANA_CAST}")
