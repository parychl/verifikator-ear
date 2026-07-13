"""Hlavní okno verifikátoru EAR."""
from __future__ import annotations

import html
import os
import subprocess
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QBrush, QColor, QDesktopServices, QIcon, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ear_verifikator import __version__
from ear_verifikator.core import cesty, export, verapdf_setup
from ear_verifikator.core.model import Vysledek, VysledekSouboru
from ear_verifikator.core.signature import sestav_validacni_zdroje
from ear_verifikator.core.trusted_list import nacti_trusted_list
from ear_verifikator.core.verifier import Verifikator
from ear_verifikator.gui import aktualizace, style

CACHE_DIR = Path.home() / ".ear_verifikator" / "tl_cache"
IKONA = Path(__file__).resolve().parent.parent / "icon.ico"


def _barva(vysledek: Vysledek) -> QColor:
    return style.barva_vysledku(vysledek.name)

SYMBOLY = {
    Vysledek.PLATNY: "✔",
    Vysledek.NEPLATNY: "✘",
    Vysledek.TECHNICKY_NEVYHOVUJICI: "⚠",
    Vysledek.ZASTARALY_STANDARD: "ℹ",
    Vysledek.CHYBA_ZPRACOVANI: "?",
}


class TabulkaSouboru(QTableWidget):
    """Tabulka s nápovědou vykreslenou uprostřed, dokud je prázdná."""

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.rowCount() == 0:
            p = QPainter(self.viewport())
            p.setPen(self.palette().placeholderText().color())
            font = p.font()
            font.setPointSize(12)
            p.setFont(font)
            p.drawText(
                self.viewport().rect(),
                Qt.AlignCenter,
                "Přetáhněte sem PDF soubory nebo složku,\n"
                "nebo je vyberte tlačítky nahoře.",
            )
            p.end()


class KontrolniWorker(QObject):
    """Načte trusted list (jednou) a kontroluje soubory v pozadí."""

    tl_nacten = Signal(str)
    soubor_hotov = Signal(int, object)
    prubeh = Signal(int, int)
    hotovo = Signal()

    def __init__(self, soubory: list[Path], stahnout_verapdf: bool):
        super().__init__()
        self.soubory = soubory
        self.stahnout_verapdf = stahnout_verapdf
        self._verifikator: Verifikator | None = None
        self._zastavit = False

    def zastav(self):
        """Požadavek na zastavení — dokončí se rozpracovaný soubor."""
        self._zastavit = True

    _sdileny_verifikator: Verifikator | None = None
    _tl_popis: str = ""

    @classmethod
    def _priprav_verifikator(
        cls, progress, stahnout_verapdf: bool
    ) -> tuple[Verifikator, str]:
        if cls._sdileny_verifikator is not None:
            return cls._sdileny_verifikator, cls._tl_popis
        progress("Načítám trusted list…")
        vysledek_tl = nacti_trusted_list(CACHE_DIR)
        if vysledek_tl.registry is not None:
            zdroje = sestav_validacni_zdroje(vysledek_tl.registry)
            n_ca = len(list(vysledek_tl.registry.known_certificate_authorities))
            n_tsa = len(list(vysledek_tl.registry.known_timestamp_authorities))
            popis = f"Trusted list: {n_ca} kvalif. CA, {n_tsa} TSA"
            if vysledek_tl.z_prosle_cache:
                popis += " (⚠ starší kopie z cache)"
        else:
            zdroje = None
            popis = "⚠ Trusted list nedostupný — kvalifikovanost nelze ověřit"
        # veraPDF se stahuje jen se souhlasem uživatele (dialog v GUI vlákně
        # před spuštěním kontroly); bez souhlasu se použije jen to, co už je
        if stahnout_verapdf:
            verapdf = verapdf_setup.stahni_verapdf(progress)
        else:
            verapdf = verapdf_setup.verapdf_prikaz()
        popis += " | PDF/A validátor: " + (
            "připraven" if verapdf else "nedostupný (jen deklarace PDF/A)"
        )
        cls._sdileny_verifikator = Verifikator(zdroje, verapdf=verapdf)
        cls._tl_popis = popis
        return cls._sdileny_verifikator, popis

    def spust(self):
        try:
            verifikator, popis = self._priprav_verifikator(
                self.tl_nacten.emit, self.stahnout_verapdf
            )
            if verifikator.zdroje is not None:
                verifikator.zdroje.zacni_davku()  # čerstvá síťová cache
            self.tl_nacten.emit(popis)
            celkem = len(self.soubory)
            for i, soubor in enumerate(self.soubory):
                if self._zastavit:
                    break
                vysledek = verifikator.zkontroluj(soubor)
                self.soubor_hotov.emit(i, vysledek)
                self.prubeh.emit(i + 1, celkem)
        finally:
            self.hotovo.emit()


class AktualizacniWorker(QObject):
    """Tichá kontrola novější verze na GitHubu (na pozadí při startu)."""

    nalezena = Signal(str)
    hotovo = Signal()

    def spust(self):
        try:
            verze = aktualizace.zjisti_novejsi_verzi(__version__)
            if verze:
                self.nalezena.emit(verze)
        finally:
            self.hotovo.emit()


def _fmt_cas(dt) -> str:
    if not dt:
        return "—"
    if dt.tzinfo is not None:
        dt = dt.astimezone()  # jednotně v místním čase (razítka bývají v UTC)
    return dt.strftime("%d.%m.%Y %H:%M:%S")


def _detail_html(v: VysledekSouboru, sbalene: set[str] | None = None) -> str:
    """Detail souboru se sbalitelnými sekcemi.

    Nadpisy sekcí jsou odkazy toggle:<klíč>; kliknutí obslouží okno (přidá /
    odebere klíč v ``sbalene`` a překreslí). Sbalení skryje jen údaje po
    následující nadpis.
    """
    e = html.escape
    sbalene = sbalene or set()
    barvy = style.BARVY_VYSLEDKU[style.aktualni_rezim]
    ZELENA = barvy["PLATNY"].name()
    CERVENA = barvy["NEPLATNY"].name()
    SEDA = barvy["CHYBA_ZPRACOVANI"].name()
    INFO = barvy["ZASTARALY_STANDARD"].name()
    # Qt rich text neumí color:inherit — barvu textu nadpisů (odkazů)
    # je nutné nastavit explicitně podle aktivního režimu
    TEXT = style.REZIMY[style.aktualni_rezim]["text"]

    def znak(ok: bool | None) -> str:
        if ok is None:
            return f" <span style='color:{SEDA}'>?</span>"
        barva, symbol = (ZELENA, "✔") if ok else (CERVENA, "✘")
        return f" <span style='color:{barva}'><b>{symbol}</b></span>"

    def znak_info() -> str:
        return f" <span style='color:{INFO}'><b>ℹ</b></span>"

    def nadpis(klic: str, text: str, znacka: str, uroven: str = "h4") -> str:
        sipka = "▶" if klic in sbalene else "▼"
        return (
            f"<{uroven}><a href='toggle:{klic}' "
            f"style='text-decoration:none;color:{TEXT};'>{sipka} {text}</a>"
            f"{znacka}</{uroven}>"
        )

    def radek(nazev, hodnota, ok=None):
        return (
            f"<tr><td>{nazev}:&nbsp;</td>"
            f"<td><b>{hodnota}</b>{'' if ok is None else znak(ok)}</td></tr>"
        )

    radky = [
        f"<h3>{e(v.soubor.name)}</h3>",
        f"<p><b>Výsledek:</b> <span style='color:{_barva(v.vysledek).name()}'>"
        f"<b>{e(v.vysledek.value)}</b></span></p>",
    ]
    if v.poznamka:
        radky.append(f"<p>{e(v.poznamka)}</p>")

    if v.chyby:
        upozorneni = v.vysledek == Vysledek.ZASTARALY_STANDARD
        radky.append(
            nadpis(
                "chyby",
                "Upozornění" if upozorneni else "Zjištěné chyby",
                znak_info() if upozorneni else znak(False),
            )
        )
        if "chyby" not in sbalene:
            radky.append("<ul>")
            for ch in v.chyby:
                radky.append(f"<li><b>{e(ch.kod.value)}</b>")
                if ch.detail:
                    radky.append(f"<br>{e(ch.detail)}")
                if ch.doporuceni:
                    radky.append(f"<br><i>Doporučení: {e(ch.doporuceni)}</i>")
                radky.append("</li>")
            radky.append("</ul>")

    radky.append(
        nadpis("pdfa", "Formát PDF/A (vyhláška č. 190/2024 Sb.)", znak(v.pdfa.vyhovuje))
    )
    if "pdfa" not in sbalene:
        pdfa_stav = {True: "splňuje", False: "nesplňuje", None: "neověřeno"}[v.pdfa.vyhovuje]
        radky.append(
            f"<p>Deklarováno: <b>{e(v.pdfa.deklarovana) or 'nedeklaruje PDF/A'}</b><br>"
            f"Skutečná shoda: {pdfa_stav}{znak(v.pdfa.vyhovuje)}"
            + (f"<br>{e(v.pdfa.detail)}" if v.pdfa.detail else "")
            + "</p>"
        )

    for i, p in enumerate(v.podpisy):
        typ = "Dokumentové časové razítko" if p.je_docasove_razitko else "Podpis"
        if p.je_docasove_razitko:
            podpis_ok = p.integrita and p.pokryva_dokument and p.razitko.kvalifikovane
            znacka = znak(podpis_ok)
        elif p.chyby:
            znacka = znak(False)
        elif p.varovani:
            znacka = znak_info()
        else:
            znacka = znak(True)
        radky.append(nadpis(f"p{i}", f"{typ}: pole „{e(p.pole)}“", znacka))

        if f"p{i}" not in sbalene:
            radky.append("<table>")
            if not p.je_docasove_razitko:
                radky.append(radek("Podepsal", e(p.podepsal)))
                radky.append(radek("Vydavatel certifikátu", e(p.vydavatel)))
                radky.append(radek("Čas podpisu (deklarovaný)", _fmt_cas(p.cas_podpisu)))
                radky.append(radek("Integrita podpisu", "neporušen" if p.integrita else "porušen", p.integrita))
                radky.append(radek("Pokrývá celý dokument", "ano" if p.pokryva_dokument else "ne", p.pokryva_dokument))
                radky.append(radek("Formát PAdES", e(p.sub_filter or "—"), p.sub_filter == "/ETSI.CAdES.detached"))
                duvera = "ověřen"
                if p.overen_k_razitku:
                    duvera = "ověřen k času razítka (certifikát již expiroval)"
                elif not p.duveryhodny_retezec:
                    duvera = "neověřen" + (f" ({e(p.duvod_neduvery)})" if p.duvod_neduvery else "")
                radky.append(radek("Ověření certifikátu (trusted list)", duvera, p.duveryhodny_retezec))
                radky.append(radek("Kvalifikovaný certifikát (eIDAS)", "ano" if p.kvalifikovany_cert else "ne", p.kvalifikovany_cert))
            else:
                radky.append(radek("Integrita", "neporušeno" if p.integrita else "porušeno", p.integrita))
                radky.append(radek("Pokrývá celý dokument", "ano" if p.pokryva_dokument else "ne", p.pokryva_dokument))
            # shrnutí za tento konkrétní podpis — u více razítek je hned
            # vidět, které z nich za co může
            radky.append("</table>")
            if p.chyby:
                radky.append(
                    "<p><b>Chyby tohoto podpisu:</b> "
                    + "; ".join(e(ch.kod.value) for ch in p.chyby)
                    + "</p>"
                )
            if p.varovani:
                radky.append(
                    "<p><b>Upozornění k tomuto podpisu:</b> "
                    + "; ".join(e(ch.kod.value) for ch in p.varovani)
                    + "</p>"
                )
            if not p.je_docasove_razitko and not p.chyby:
                if p.varovani:
                    radky.append(
                        f"<p><b>{znak_info()} Podpis je jinak platný, ale kvůli "
                        "upozornění výše ho verifikátor v ISSŘ nemusí "
                        "uznat.</b></p>"
                    )
                else:
                    radky.append(
                        f"<p><b>{znak(True)} Tento podpis splňuje všechny "
                        "kontroly EAR profilu.</b></p>"
                    )

        if not p.je_docasove_razitko:
            radky.append(
                nadpis(f"p{i}ear", "Autorizační razítko (EAR)", znak(p.ear.je_ear))
            )
            if f"p{i}ear" not in sbalene:
                radky.append("<table>")
                radky.append(radek("Je EAR", "ano" if p.ear.je_ear else "ne", p.ear.je_ear))
                if p.ear.komora:
                    radky.append(radek("Komora", e(p.ear.komora)))
                if p.ear.cislo_autorizace:
                    radky.append(radek("Číslo autorizace", e(p.ear.cislo_autorizace)))
                if p.ear.obor:
                    radky.append(radek("Obor", e(p.ear.obor)))
                radky.append("</table>")

            razitko_ok = p.razitko.pritomno and p.razitko.kvalifikovane
            radky.append(nadpis(f"p{i}ts", "Časové razítko", znak(razitko_ok)))
            if f"p{i}ts" not in sbalene:
                radky.append("<table>")
                if p.razitko.pritomno:
                    radky.append(radek("Čas", _fmt_cas(p.razitko.cas)))
                    radky.append(radek("Autorita (TSA)", e(p.razitko.tsa)))
                    radky.append(radek("Kvalifikované", "ano" if p.razitko.kvalifikovane else "ne", p.razitko.kvalifikovane))
                else:
                    radky.append(radek("Časové razítko", "chybí", False))
                radky.append("</table>")
        elif f"p{i}" not in sbalene and p.razitko.pritomno:
            radky.append("<table>")
            radky.append(radek("Čas", _fmt_cas(p.razitko.cas)))
            radky.append(radek("Autorita (TSA)", e(p.razitko.tsa)))
            radky.append(radek("Kvalifikované", "ano" if p.razitko.kvalifikovane else "ne", p.razitko.kvalifikovane))
            radky.append("</table>")

    return "".join(radky)


# volby filtru tabulky: (popisek, None=vše | "problemy" | Vysledek)
_FILTRY = [
    ("Vše", None),
    ("Jen s problémy", "problemy"),
    ("Platné", Vysledek.PLATNY),
    ("Neplatné", Vysledek.NEPLATNY),
    ("Zastaralý standard podpisu", Vysledek.ZASTARALY_STANDARD),
    ("Technicky nevyhovující", Vysledek.TECHNICKY_NEVYHOVUJICI),
    ("Chyba zpracování", Vysledek.CHYBA_ZPRACOVANI),
]


class HlavniOkno(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(
            f"Verifikátor EAR {__version__} — kontrola autorizačních razítek v PDF"
        )
        if IKONA.exists():
            self.setWindowIcon(QIcon(str(IKONA)))
        self.resize(1150, 650)
        self.setAcceptDrops(True)

        # _vysledky, _soubory a _radky jsou paralelní seznamy indexované
        # "indexem výsledku"; řádek tabulky se hledá přes item v _radky,
        # takže řazení ani filtrování tabulky mapování nerozbije
        self._vysledky: list[VysledekSouboru | None] = []
        self._soubory: list[Path] = []
        self._radky: list[QTableWidgetItem] = []
        self._cilove_indexy: list[int] = []
        self._thread: QThread | None = None
        self._worker: KontrolniWorker | None = None
        self._verapdf_odmitnut = False  # uživatel v tomto sezení odmítl stažení
        self._nova_verze: str | None = None

        styl = self.style()
        tlacitka = QHBoxLayout()
        tlacitka.setSpacing(8)
        self.btn_soubory = QPushButton("Vybrat PDF soubory…")
        self.btn_soubory.setObjectName("primarni")
        self.btn_soubory.setIcon(styl.standardIcon(styl.StandardPixmap.SP_FileIcon))
        self.btn_slozka = QPushButton("Vybrat složku…")
        self.btn_slozka.setIcon(styl.standardIcon(styl.StandardPixmap.SP_DirOpenIcon))
        self.btn_vycistit = QPushButton("Vyčistit")
        self.btn_export = QPushButton("Exportovat…")
        self.btn_export.setIcon(styl.standardIcon(styl.StandardPixmap.SP_DialogSaveButton))
        self.btn_export.setEnabled(False)
        menu_export = QMenu(self.btn_export)
        menu_export.addAction(
            "Všechny výsledky", lambda: self._exportuj("vse")
        )
        menu_export.addAction(
            "Jen platné", lambda: self._exportuj("platne")
        )
        menu_export.addAction(
            "Jen neplatné a s výhradami", lambda: self._exportuj("neplatne")
        )
        menu_export.addAction("Jen vybrané", self._exportuj_vybrane)
        self.btn_export.setMenu(menu_export)
        self.cmb_filtr = QComboBox()
        self.cmb_filtr.setToolTip("Filtrovat zobrazené výsledky")
        for popisek, data in _FILTRY:
            self.cmb_filtr.addItem(popisek, data)
        self.cmb_filtr.currentIndexChanged.connect(self._aplikuj_filtr)
        self.btn_info = QPushButton("?")
        self.btn_info.setObjectName("rezim")
        self.btn_info.setToolTip("O aplikaci")
        self.btn_info.setFixedWidth(44)
        self.btn_rezim = QPushButton()
        self.btn_rezim.setObjectName("rezim")
        self.btn_rezim.setToolTip("Přepnout tmavý/světlý režim")
        self.btn_rezim.setFixedWidth(44)
        self._aktualizuj_ikonu_rezimu()
        tlacitka.addWidget(self.btn_soubory)
        tlacitka.addWidget(self.btn_slozka)
        tlacitka.addWidget(self.btn_vycistit)
        tlacitka.addWidget(self.btn_export)
        tlacitka.addWidget(QLabel("Zobrazit:"))
        tlacitka.addWidget(self.cmb_filtr)
        tlacitka.addStretch()
        tlacitka.addWidget(self.btn_info)
        tlacitka.addWidget(self.btn_rezim)

        self.tabulka = TabulkaSouboru(0, 4)
        self.tabulka.setHorizontalHeaderLabels(["Soubor", "Výsledek", "PDF/A", "Chyby"])
        hlavicka = self.tabulka.horizontalHeader()
        for sloupec, sirka in ((0, 280), (1, 190), (2, 110)):
            hlavicka.setSectionResizeMode(sloupec, QHeaderView.Interactive)
            self.tabulka.setColumnWidth(sloupec, sirka)
        hlavicka.setSectionResizeMode(3, QHeaderView.Interactive)
        hlavicka.setStretchLastSection(True)
        hlavicka.setMinimumSectionSize(60)
        self.tabulka.verticalHeader().setVisible(False)
        self.tabulka.verticalHeader().setDefaultSectionSize(30)
        self.tabulka.setAlternatingRowColors(True)
        self.tabulka.setShowGrid(False)
        self.tabulka.setSelectionBehavior(QTableWidget.SelectRows)
        self.tabulka.setSelectionMode(QTableWidget.ExtendedSelection)
        self.tabulka.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tabulka.itemSelectionChanged.connect(self._zobraz_detail)
        self.tabulka.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tabulka.customContextMenuRequested.connect(self._menu_tabulky)

        self.detail = QTextBrowser()
        self.detail.setOpenExternalLinks(False)
        self.detail.setOpenLinks(False)  # toggle: odkazy obsluhujeme sami
        self.detail.anchorClicked.connect(self._prepni_sekci)
        self.detail.setPlaceholderText("Vyberte soubor v tabulce pro zobrazení detailu…")
        self._sbalene: set[str] = set()   # sbalené sekce detailu
        self._detail_index: int | None = None

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.tabulka)
        splitter.addWidget(self.detail)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.lbl_prubeh = QLabel("")  # počítadlo X/Y zkontrolovaných souborů
        self.lbl_prubeh.setVisible(False)
        self.btn_zastavit = QPushButton("Zastavit")
        self.btn_zastavit.setVisible(False)
        prubeh_radek = QHBoxLayout()
        prubeh_radek.setSpacing(8)
        prubeh_radek.addWidget(self.progress, 1)
        prubeh_radek.addWidget(self.lbl_prubeh)
        prubeh_radek.addWidget(self.btn_zastavit)

        layout = QVBoxLayout()
        layout.setContentsMargins(12, 12, 12, 8)
        layout.setSpacing(10)
        layout.addLayout(tlacitka)
        layout.addWidget(splitter, 1)
        layout.addLayout(prubeh_radek)
        stred = QWidget()
        stred.setLayout(layout)
        self.setCentralWidget(stred)
        self.statusBar().showMessage("Připraven")

        self.btn_soubory.clicked.connect(self._vyber_soubory)
        self.btn_slozka.clicked.connect(self._vyber_slozku)
        self.btn_vycistit.clicked.connect(self._vycisti)
        self.btn_zastavit.clicked.connect(self._zastav_kontrolu)
        self.btn_rezim.clicked.connect(self._prepni_rezim)
        self.btn_info.clicked.connect(self._zobraz_o_aplikaci)

        self._spust_kontrolu_aktualizaci()

    # --- mapování výsledků na řádky tabulky ------------------------------
    def _radek_indexu(self, idx: int) -> int:
        """Aktuální řádek tabulky pro daný index výsledku (i po seřazení)."""
        return self.tabulka.row(self._radky[idx])

    def _index_radku(self, radek: int) -> int | None:
        item = self.tabulka.item(radek, 0)
        return None if item is None else item.data(Qt.UserRole)

    def _vybrane_indexy(self) -> list[int]:
        radky = sorted({i.row() for i in self.tabulka.selectedItems()})
        return [
            idx for radek in radky if (idx := self._index_radku(radek)) is not None
        ]

    # --- vzhled ---------------------------------------------------------
    def _aktualizuj_ikonu_rezimu(self):
        # ikona ukazuje režim, na který se kliknutím přepne
        self.btn_rezim.setText("☀️" if style.aktualni_rezim == "tmavy" else "🌙")

    def _prepni_rezim(self):
        novy = "svetly" if style.aktualni_rezim == "tmavy" else "tmavy"
        style.aplikuj_vzhled(QApplication.instance(), novy)
        style.uloz_rezim(novy)
        self._aktualizuj_ikonu_rezimu()
        # překreslit barvy výsledků v tabulce i detail podle nového režimu
        razeni = self.tabulka.isSortingEnabled()
        self.tabulka.setSortingEnabled(False)
        for idx, v in enumerate(self._vysledky):
            if v is not None:
                self._zapis_vysledek(idx, v)
        self.tabulka.setSortingEnabled(razeni)
        self._vykresli_detail()

    # --- výběr souborů -------------------------------------------------
    def _vyber_soubory(self):
        soubory, _ = QFileDialog.getOpenFileNames(
            self, "Vyberte PDF soubory", "", "PDF soubory (*.pdf)"
        )
        if soubory:
            self._zkontroluj([Path(s) for s in soubory])

    def _vyber_slozku(self):
        slozka = QFileDialog.getExistingDirectory(self, "Vyberte složku s PDF")
        if slozka:
            pdf = cesty.najdi_soubory(Path(slozka), "*.pdf")
            if not pdf:
                QMessageBox.information(self, "Prázdná složka", "Ve složce nejsou žádné PDF soubory.")
                return
            self._zkontroluj(pdf)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        soubory: list[Path] = []
        for url in event.mimeData().urls():
            cesta = Path(url.toLocalFile())
            if cesty.fs_cesta(cesta).is_dir():
                soubory.extend(cesty.najdi_soubory(cesta, "*.pdf"))
            elif cesta.suffix.lower() == ".pdf":
                soubory.append(cesta)
        if soubory:
            self._zkontroluj(soubory)

    def _vycisti(self):
        if self._thread is not None:
            # worker zapisuje výsledky podle indexů řádků — smazání tabulky
            # během kontroly by je rozhodilo
            QMessageBox.warning(
                self, "Probíhá kontrola", "Počkejte na dokončení probíhající kontroly."
            )
            return
        self.tabulka.setRowCount(0)
        self._vysledky.clear()
        self._soubory.clear()
        self._radky.clear()
        self.detail.clear()
        self._detail_index = None
        self._sbalene.clear()
        self.btn_export.setEnabled(False)

    # --- export -------------------------------------------------------
    def _exportuj(self, rozsah: str):
        vysledky = export.filtruj(
            [v for v in self._vysledky if v is not None], rozsah
        )
        self._exportuj_seznam(vysledky, rozsah)

    def _exportuj_vybrane(self):
        vysledky = [
            self._vysledky[idx]
            for idx in self._vybrane_indexy()
            if self._vysledky[idx] is not None
        ]
        self._exportuj_seznam(vysledky, "vybrane")

    def _exportuj_seznam(self, vysledky: list[VysledekSouboru], nazev_rozsahu: str):
        if not vysledky:
            QMessageBox.information(
                self, "Nic k exportu", "Zvolenému rozsahu neodpovídají žádné výsledky."
            )
            return
        vychozi = f"kontrola_EAR_{datetime.now():%Y-%m-%d}_{nazev_rozsahu}.xlsx"
        cesta, filtr = QFileDialog.getSaveFileName(
            self,
            "Uložit export",
            vychozi,
            "Excel (*.xlsx);;CSV (*.csv);;Textový report (*.txt)",
        )
        if not cesta:
            return
        soubor = Path(cesta)
        if soubor.suffix.lower() not in export.EXPORTERY:
            pripona = ".xlsx" if "xlsx" in filtr else ".csv" if "csv" in filtr else ".txt"
            soubor = soubor.with_suffix(pripona)
        try:
            export.exportuj(cesty.fs_cesta(soubor), vysledky)
        except Exception as e:
            QMessageBox.critical(self, "Export selhal", str(e))
            return
        self.statusBar().showMessage(
            f"Exportováno {len(vysledky)} záznamů do {soubor}", 10000
        )

    # --- kontextové menu -------------------------------------------------
    def _menu_tabulky(self, pozice):
        indexy = self._vybrane_indexy()
        if not indexy:
            return
        menu = QMenu(self)
        a_pdf = menu.addAction("Otevřít PDF")
        a_pdf.setEnabled(len(indexy) <= 5)  # neotvírat desítky oken najednou
        a_slozka = menu.addAction("Otevřít složku souboru")
        a_slozka.setEnabled(len(indexy) <= 5)
        menu.addSeparator()
        popisek = "Zkontrolovat znovu" + (f" ({len(indexy)})" if len(indexy) > 1 else "")
        a_znovu = menu.addAction(popisek)
        a_znovu.setEnabled(self._thread is None)
        a_export = menu.addAction("Exportovat vybrané…")
        a_export.setEnabled(any(self._vysledky[i] is not None for i in indexy))

        akce = menu.exec(self.tabulka.viewport().mapToGlobal(pozice))
        if akce is a_pdf:
            for idx in indexy:
                QDesktopServices.openUrl(
                    QUrl.fromLocalFile(str(self._soubory[idx]))
                )
        elif akce is a_slozka:
            for idx in indexy:
                # explorer /select,"cesta" zvýrazní soubor ve složce
                subprocess.Popen(["explorer", f"/select,{self._soubory[idx]}"])
        elif akce is a_znovu:
            self._zkontroluj(
                [self._soubory[idx] for idx in indexy], cilove_indexy=indexy
            )
        elif akce is a_export:
            self._exportuj_vybrane()

    # --- filtr a souhrn --------------------------------------------------
    def _aplikuj_filtr(self):
        volba = self.cmb_filtr.currentData()
        for radek in range(self.tabulka.rowCount()):
            idx = self._index_radku(radek)
            v = self._vysledky[idx] if idx is not None else None
            if volba is None:
                skryt = False
            elif v is None:
                skryt = True  # nezkontrolované řádky jen v pohledu „Vše“
            elif volba == "problemy":
                skryt = v.vysledek == Vysledek.PLATNY
            else:
                skryt = v.vysledek != volba
            self.tabulka.setRowHidden(radek, skryt)

    def _souhrn_text(self) -> str:
        pocty = {stav: 0 for stav in Vysledek}
        for v in self._vysledky:
            if v is not None:
                pocty[v.vysledek] += 1
        casti = [
            f"{SYMBOLY[stav]} {pocty[stav]}"
            for stav in Vysledek
            if pocty[stav]
        ]
        return "  ".join(casti)

    # --- kontrola ------------------------------------------------------
    def _zeptej_se_na_verapdf(self) -> bool:
        """Souhlas se stažením veraPDF; ptá se jen když validátor chybí."""
        if KontrolniWorker._sdileny_verifikator is not None:
            return False  # verifikátor už je sestavený, rozhodnutí padlo
        if verapdf_setup.verapdf_prikaz() is not None:
            return True  # už je stažený/nainstalovaný — jen se použije
        if self._verapdf_odmitnut:
            return False
        volba = QMessageBox.question(
            self,
            "Stáhnout PDF/A validátor?",
            "Ke kontrole formátu PDF (PDF/A dle vyhlášky č. 190/2024 Sb.)\n"
            "je potřeba validátor veraPDF. Stáhne se jednorázově z oficiálního\n"
            "zdroje (~10 MB; pokud v počítači není Java, přibalí se i přenosné\n"
            "prostředí Java, ~45 MB). Stažené soubory se ověřují proti\n"
            "očekávanému otisku SHA-256.\n\n"
            "Pokud se validátor nestáhne, program NEBUDE ověřovat formát PDF\n"
            "(PDF/A atd.) — zkontroluje se pouze deklarace v metadatech\n"
            "dokumentu. Ostatní kontroly (podpisy, razítka) fungují dál.\n\n"
            "Stáhnout validátor nyní?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if volba != QMessageBox.Yes:
            self._verapdf_odmitnut = True
            return False
        return True

    def _zkontroluj(self, soubory: list[Path], cilove_indexy: list[int] | None = None):
        """Spustí kontrolu; cilove_indexy = přepsat existující řádky (re-check)."""
        if self._thread is not None:
            QMessageBox.warning(self, "Probíhá kontrola", "Počkejte na dokončení probíhající kontroly.")
            return
        stahnout_verapdf = self._zeptej_se_na_verapdf()
        self.tabulka.setSortingEnabled(False)  # vkládání do seřazené tabulky přesouvá řádky

        if cilove_indexy is None:
            self._cilove_indexy = []
            for soubor in soubory:
                idx = len(self._vysledky)
                radek = self.tabulka.rowCount()
                self.tabulka.insertRow(radek)
                nazev = QTableWidgetItem(soubor.name)
                nazev.setToolTip(str(soubor))
                nazev.setData(Qt.UserRole, idx)
                self.tabulka.setItem(radek, 0, nazev)
                self.tabulka.setItem(radek, 1, QTableWidgetItem("kontroluje se…"))
                self.tabulka.setItem(radek, 2, QTableWidgetItem(""))
                self.tabulka.setItem(radek, 3, QTableWidgetItem(""))
                self._vysledky.append(None)
                self._soubory.append(soubor)
                self._radky.append(nazev)
                self._cilove_indexy.append(idx)
        else:
            self._cilove_indexy = list(cilove_indexy)
            for idx in cilove_indexy:
                radek = self._radek_indexu(idx)
                self._vysledky[idx] = None
                self._radky[idx].setBackground(QBrush())
                self.tabulka.setItem(radek, 1, QTableWidgetItem("kontroluje se…"))
                self.tabulka.setItem(radek, 2, QTableWidgetItem(""))
                self.tabulka.setItem(radek, 3, QTableWidgetItem(""))
        self._pocet_davky = len(soubory)

        self.progress.setVisible(True)
        self.progress.setRange(0, len(soubory))
        self.progress.setValue(0)
        self.lbl_prubeh.setText(f"0/{len(soubory)}")
        self.lbl_prubeh.setVisible(True)
        self.btn_zastavit.setEnabled(True)
        self.btn_zastavit.setVisible(True)
        self.statusBar().showMessage("Načítám trusted list…")

        self._thread = QThread()
        self._worker = KontrolniWorker(soubory, stahnout_verapdf)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.spust)
        # napojení na metody okna (QObject v GUI vlákně) → Qt doručí
        # signály z workeru queued connection; lambda by běžela ve vlákně
        # workeru a sahala na widgety mimo GUI vlákno
        self._worker.tl_nacten.connect(self._na_tl_nacten)
        self._worker.soubor_hotov.connect(self._na_soubor_hotov)
        self._worker.prubeh.connect(self._na_prubeh)
        self._worker.hotovo.connect(self._kontrola_dokoncena)
        self._thread.start()

    def _na_tl_nacten(self, popis: str):
        self._tl_zprava = popis
        self.statusBar().showMessage(popis)

    def _na_soubor_hotov(self, i: int, v: VysledekSouboru):
        self._zapis_vysledek(self._cilove_indexy[i], v)

    def _na_prubeh(self, hotovo: int, celkem: int):
        self.progress.setValue(hotovo)
        self.lbl_prubeh.setText(f"{hotovo}/{celkem}")

    def _zastav_kontrolu(self):
        if self._worker is not None and self._thread is not None:
            self._worker.zastav()
            self.btn_zastavit.setEnabled(False)
            self.statusBar().showMessage("Zastavuji po dokončení aktuálního souboru…")

    def _zapis_vysledek(self, idx: int, v: VysledekSouboru):
        self._vysledky[idx] = v
        radek = self._radek_indexu(idx)
        barva = _barva(v.vysledek)

        item = QTableWidgetItem(f"{SYMBOLY[v.vysledek]} {v.vysledek.value}")
        item.setForeground(QBrush(barva))
        font = item.font()
        font.setBold(True)
        item.setFont(font)
        self.tabulka.setItem(radek, 1, item)

        if v.pdfa.vyhovuje is True:
            pdfa_text = v.pdfa.deklarovana + " ✔"
        elif v.pdfa.vyhovuje is False:
            pdfa_text = (v.pdfa.deklarovana or "chybí") + " ✘"
        else:
            pdfa_text = v.pdfa.deklarovana + " ?"
        self.tabulka.setItem(radek, 2, QTableWidgetItem(pdfa_text))
        if v.chyby:
            chyby_text = "; ".join(ch.kod.value for ch in v.chyby[:2])
            if len(v.chyby) > 2:
                chyby_text += f" (+{len(v.chyby) - 2})"
        else:
            chyby_text = "—"
        self.tabulka.setItem(radek, 3, QTableWidgetItem(chyby_text))

        podbarveni = QColor(barva)
        podbarveni.setAlpha(36)
        for sloupec in range(self.tabulka.columnCount()):
            bunka = self.tabulka.item(radek, sloupec)
            if bunka is not None:
                bunka.setBackground(QBrush(podbarveni))

    def _kontrola_dokoncena(self):
        self.progress.setVisible(False)
        self.btn_zastavit.setVisible(False)
        pocet = self.lbl_prubeh.text()
        self.lbl_prubeh.setVisible(False)
        self.btn_export.setEnabled(any(v is not None for v in self._vysledky))
        zastaveno = self._worker is not None and self._worker._zastavit
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait()
            self._thread = None
        # po zastavení označit nezpracované řádky
        for idx in self._cilove_indexy:
            if self._vysledky[idx] is None:
                self.tabulka.setItem(
                    self._radek_indexu(idx),
                    1,
                    QTableWidgetItem("nezkontrolováno (zastaveno)"),
                )
        self.tabulka.setSortingEnabled(True)
        self._aplikuj_filtr()
        stav = "Zastaveno" if zastaveno else "Hotovo"
        if pocet:
            stav += f" ({pocet})"
        souhrn = self._souhrn_text()
        casti = [stav]
        if souhrn:
            casti.append(souhrn)
        zprava = getattr(self, "_tl_zprava", "")
        if zprava:
            casti.append(zprava)
        self.statusBar().showMessage(" | ".join(casti))
        # u jediného souboru rovnou zobrazit detail v pravém panelu
        if self._pocet_davky == 1 and self._cilove_indexy:
            self.tabulka.selectRow(self._radek_indexu(self._cilove_indexy[0]))
        self._vykresli_detail()  # obnovit detail po re-checku

    def _zobraz_detail(self):
        indexy = self._vybrane_indexy()
        if len(indexy) != 1:
            return
        idx = indexy[0]
        if self._vysledky[idx] is not None:
            if self._detail_index != idx:
                self._sbalene.clear()  # nový soubor → vše rozbalené
                self._detail_index = idx
            self._vykresli_detail()

    def _vykresli_detail(self):
        if (
            self._detail_index is None
            or self._detail_index >= len(self._vysledky)
            or self._vysledky[self._detail_index] is None
        ):
            return
        posuv = self.detail.verticalScrollBar().value()
        self.detail.setHtml(
            _detail_html(self._vysledky[self._detail_index], self._sbalene)
        )
        self.detail.verticalScrollBar().setValue(posuv)

    def _prepni_sekci(self, url):
        adresa = url.toString()
        if not adresa.startswith("toggle:"):
            return
        klic = adresa[len("toggle:"):]
        if klic in self._sbalene:
            self._sbalene.discard(klic)
        else:
            self._sbalene.add(klic)
        self._vykresli_detail()

    # --- aktualizace a o aplikaci ----------------------------------------
    def _spust_kontrolu_aktualizaci(self):
        if os.environ.get("EAR_VERIFIKATOR_BEZ_AKTUALIZACI"):
            return  # vypnuto (testy, offline prostředí)
        self._akt_thread = QThread(self)
        self._akt_worker = AktualizacniWorker()
        self._akt_worker.moveToThread(self._akt_thread)
        self._akt_thread.started.connect(self._akt_worker.spust)
        self._akt_worker.nalezena.connect(self._na_novou_verzi)
        self._akt_worker.hotovo.connect(self._akt_thread.quit)
        self._akt_thread.start()

    def _na_novou_verzi(self, verze: str):
        self._nova_verze = verze
        upozorneni = QLabel(
            f"K dispozici je nová verze {verze} — "
            f"<a href='{aktualizace.RELEASES_URL}'>stáhnout</a>"
        )
        upozorneni.setOpenExternalLinks(True)
        self.statusBar().addPermanentWidget(upozorneni)

    def _zobraz_o_aplikaci(self):
        nova = ""
        if self._nova_verze:
            nova = (
                f"<p><b>K dispozici je novější verze {self._nova_verze}</b> — "
                f"<a href='{aktualizace.RELEASES_URL}'>stáhnout na GitHubu</a>.</p>"
            )
        QMessageBox.about(
            self,
            "O aplikaci",
            f"<h3>Verifikátor EAR {__version__}</h3>"
            "<p>Kontrola elektronických autorizačních razítek (EAR) v PDF "
            "dokumentaci před podáním do Portálu stavebníka, podle Metodiky "
            "k Verifikátoru podpisů (MMR).</p>"
            f"{nova}"
            "<p>Zdrojový kód a hlášení chyb: "
            f"<a href='https://github.com/{aktualizace.REPOZITAR}'>"
            f"github.com/{aktualizace.REPOZITAR}</a></p>"
            "<p>© 2026 Patrik Rychlý, licence MIT.<br>"
            "Nejedná se o oficiální aplikaci — rozhodující je vždy výsledek "
            "verifikátoru v ISSŘ / Portálu stavebníka.</p>",
        )
