"""GUI testy hlavního okna (offscreen) — filtr, řazení, výběr, re-check."""
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["EAR_VERIFIKATOR_BEZ_AKTUALIZACI"] = "1"  # žádná síť v testech

from ear_verifikator.core.model import InfoPDFA, Vysledek, VysledekSouboru

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="session")
def app():
    return QApplication.instance() or QApplication([])


def _vysledek(nazev: str, stav: Vysledek) -> VysledekSouboru:
    return VysledekSouboru(
        soubor=Path(f"C:/dokumentace/{nazev}"),
        vysledek=stav,
        pdfa=InfoPDFA("PDF/A-3B", True),
    )


@pytest.fixture()
def okno(app):
    from ear_verifikator.gui.main_window import HlavniOkno, QTableWidgetItem

    o = HlavniOkno()
    # naplnit tabulku výsledky všech stavů (bez spouštění workeru)
    stavy = [
        ("a.pdf", Vysledek.PLATNY),
        ("b.pdf", Vysledek.NEPLATNY),
        ("c.pdf", Vysledek.ZASTARALY_STANDARD),
        ("d.pdf", Vysledek.PLATNY),
    ]
    o.tabulka.setSortingEnabled(False)
    for nazev, stav in stavy:
        idx = len(o._vysledky)
        radek = o.tabulka.rowCount()
        o.tabulka.insertRow(radek)
        item = QTableWidgetItem(nazev)
        item.setData(Qt.UserRole, idx)
        o.tabulka.setItem(radek, 0, item)
        for sloupec in (1, 2, 3):
            o.tabulka.setItem(radek, sloupec, QTableWidgetItem(""))
        o._vysledky.append(None)
        o._soubory.append(Path(f"C:/dokumentace/{nazev}"))
        o._radky.append(item)
        o._zapis_vysledek(idx, _vysledek(nazev, stav))
    o.tabulka.setSortingEnabled(True)
    yield o
    o.close()


def test_filtr_jen_problemy(okno):
    okno.cmb_filtr.setCurrentIndex(1)  # „Jen s problémy“
    skryte = [
        okno.tabulka.isRowHidden(okno._radek_indexu(i)) for i in range(4)
    ]
    assert skryte == [True, False, False, True]  # platné soubory schované


def test_filtr_konkretni_stav(okno):
    idx_neplatne = [
        i for i, (text, data) in enumerate(
            (okno.cmb_filtr.itemText(j), okno.cmb_filtr.itemData(j))
            for j in range(okno.cmb_filtr.count())
        )
        if data == Vysledek.NEPLATNY
    ][0]
    okno.cmb_filtr.setCurrentIndex(idx_neplatne)
    viditelne = [
        i for i in range(4) if not okno.tabulka.isRowHidden(okno._radek_indexu(i))
    ]
    assert viditelne == [1]


def test_razeni_nerozbije_mapovani(okno):
    okno.tabulka.sortItems(1)  # seřadit dle sloupce Výsledek
    # index výsledku se po přeskupení řádků najde přes item, ne přes pořadí
    for idx in range(4):
        radek = okno._radek_indexu(idx)
        assert okno._index_radku(radek) == idx
        assert okno.tabulka.item(radek, 0).text() == okno._soubory[idx].name


def test_vyber_vice_radku(okno):
    okno.tabulka.clearSelection()
    okno.tabulka.selectRow(okno._radek_indexu(0))
    model = okno.tabulka.selectionModel()
    from PySide6.QtCore import QItemSelectionModel

    radek3 = okno._radek_indexu(3)
    model.select(
        okno.tabulka.model().index(radek3, 0),
        QItemSelectionModel.Select | QItemSelectionModel.Rows,
    )
    # pořadí odpovídá vizuálnímu pořadí řádků (tabulka může být seřazená)
    assert sorted(okno._vybrane_indexy()) == [0, 3]


def test_recheck_zapise_do_spravneho_radku(okno):
    okno.tabulka.sortItems(1)  # rozházet pořadí řádků
    okno._cilove_indexy = [2]
    novy = _vysledek("c.pdf", Vysledek.PLATNY)
    okno._na_soubor_hotov(0, novy)  # simulace výsledku re-checku
    assert okno._vysledky[2] is novy
    radek = okno._radek_indexu(2)
    assert "Platný" in okno.tabulka.item(radek, 1).text()
    assert okno.tabulka.item(radek, 0).text() == "c.pdf"


def test_souhrn_text(okno):
    souhrn = okno._souhrn_text()
    assert "✔ 2" in souhrn and "✘ 1" in souhrn and "ℹ 1" in souhrn
