import shutil
import sys
from pathlib import Path

import pytest

from ear_verifikator.core import cesty

jen_windows = pytest.mark.skipif(sys.platform != "win32", reason="Windows cesty")


@jen_windows
def test_fs_cesta_pridava_prefix():
    assert str(cesty.fs_cesta(Path(r"C:\slozka\soubor.pdf"))) == r"\\?\C:\slozka\soubor.pdf"


@jen_windows
def test_fs_cesta_idempotentni():
    p = cesty.fs_cesta(Path(r"C:\slozka\soubor.pdf"))
    assert cesty.fs_cesta(p) == p


@jen_windows
def test_fs_cesta_unc():
    assert str(cesty.fs_cesta(Path(r"\\server\share\soubor.pdf"))) == (
        r"\\?\UNC\server\share\soubor.pdf"
    )


@jen_windows
def test_bez_prefixu_roundtrip():
    for puvodni in (r"C:\slozka\soubor.pdf", r"\\server\share\soubor.pdf"):
        assert str(cesty.bez_prefixu(cesty.fs_cesta(Path(puvodni)))) == puvodni


def _dlouha_slozka(koren: Path, minimum: int = 265) -> Path:
    slozka = koren
    while len(str(slozka)) < minimum:
        slozka = slozka / "velmi dlouhy nazev slozky projektove dokumentace"
    return slozka


@jen_windows
def test_najdi_soubory_v_dlouhe_ceste(tmp_path, blank_pdf):
    slozka = _dlouha_slozka(tmp_path)
    cil = slozka / "dokument.pdf"
    cesty.fs_cesta(slozka).mkdir(parents=True)
    shutil.copyfile(cesty.fs_cesta(blank_pdf), cesty.fs_cesta(cil))

    nalezene = cesty.najdi_soubory(tmp_path, "*.pdf")
    assert nalezene == [cil]
    assert not str(nalezene[0]).startswith("\\\\?\\")


@jen_windows
def test_verifikator_zvladne_dlouhou_cestu(tmp_path, podepsany_pdf, prazdna_spec):
    from ear_verifikator.core.model import Vysledek
    from ear_verifikator.core.verifier import Verifikator

    slozka = _dlouha_slozka(tmp_path)
    cil = slozka / "podepsany dokument s velmi dlouhou cestou.pdf"
    cesty.fs_cesta(slozka).mkdir(parents=True)
    shutil.copyfile(cesty.fs_cesta(podepsany_pdf), cesty.fs_cesta(cil))
    assert len(str(cil)) > 260

    v = Verifikator(prazdna_spec, verapdf=[]).zkontroluj(cil)
    assert v.vysledek != Vysledek.CHYBA_ZPRACOVANI
    assert v.podpisy  # soubor se otevřel a podpis se přečetl
