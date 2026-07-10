"""Hlavní okno verifikátoru EAR."""
from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter
from PySide6.QtWidgets import (
    QApplication,
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

from ear_verifikator.core import cesty, export, verapdf_setup
from ear_verifikator.core.model import Vysledek, VysledekSouboru
from ear_verifikator.core.signature import sestav_validacni_zdroje
from ear_verifikator.core.trusted_list import nacti_trusted_list
from ear_verifikator.core.verifier import Verifikator
from ear_verifikator.gui import style

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


class HlavniOkno(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Verifikátor EAR — kontrola autorizačních razítek v PDF")
        if IKONA.exists():
            self.setWindowIcon(QIcon(str(IKONA)))
        self.resize(1150, 650)
        self.setAcceptDrops(True)

        self._vysledky: list[VysledekSouboru] = []
        self._thread: QThread | None = None
        self._worker: KontrolniWorker | None = None
        self._verapdf_odmitnut = False  # uživatel v tomto sezení odmítl stažení

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
        self.btn_export.setMenu(menu_export)
        self.btn_rezim = QPushButton()
        self.btn_rezim.setObjectName("rezim")
        self.btn_rezim.setToolTip("Přepnout tmavý/světlý režim")
        self.btn_rezim.setFixedWidth(44)
        self._aktualizuj_ikonu_rezimu()
        tlacitka.addWidget(self.btn_soubory)
        tlacitka.addWidget(self.btn_slozka)
        tlacitka.addWidget(self.btn_vycistit)
        tlacitka.addWidget(self.btn_export)
        tlacitka.addStretch()
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
        self.tabulka.setSelectionMode(QTableWidget.SingleSelection)
        self.tabulka.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tabulka.itemSelectionChanged.connect(self._zobraz_detail)

        self.detail = QTextBrowser()
        self.detail.setOpenExternalLinks(False)
        self.detail.setOpenLinks(False)  # toggle: odkazy obsluhujeme sami
        self.detail.anchorClicked.connect(self._prepni_sekci)
        self.detail.setPlaceholderText("Vyberte soubor v tabulce pro zobrazení detailu…")
        self._sbalene: set[str] = set()   # sbalené sekce detailu
        self._detail_radek: int | None = None

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
        for radek, v in enumerate(self._vysledky):
            if v is not None:
                self._zapis_vysledek(radek, v)
        self._zobraz_detail()

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
        self.detail.clear()
        self._detail_radek = None
        self._sbalene.clear()
        self.btn_export.setEnabled(False)

    # --- export -------------------------------------------------------
    def _exportuj(self, rozsah: str):
        vysledky = export.filtruj(
            [v for v in self._vysledky if v is not None], rozsah
        )
        if not vysledky:
            QMessageBox.information(
                self, "Nic k exportu", "Zvolenému rozsahu neodpovídají žádné výsledky."
            )
            return
        pripona_rozsahu = {"vse": "vse", "platne": "platne", "neplatne": "neplatne"}[rozsah]
        vychozi = f"kontrola_EAR_{datetime.now():%Y-%m-%d}_{pripona_rozsahu}.xlsx"
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

    def _zkontroluj(self, soubory: list[Path]):
        if self._thread is not None:
            QMessageBox.warning(self, "Probíhá kontrola", "Počkejte na dokončení probíhající kontroly.")
            return
        stahnout_verapdf = self._zeptej_se_na_verapdf()
        self._start_radek = self.tabulka.rowCount()
        self._pocet_davky = len(soubory)
        for soubor in soubory:
            radek = self.tabulka.rowCount()
            self.tabulka.insertRow(radek)
            nazev = QTableWidgetItem(soubor.name)
            nazev.setToolTip(str(soubor))
            self.tabulka.setItem(radek, 0, nazev)
            self.tabulka.setItem(radek, 1, QTableWidgetItem("kontroluje se…"))
            self.tabulka.setItem(radek, 2, QTableWidgetItem(""))
            self.tabulka.setItem(radek, 3, QTableWidgetItem(""))
        self._vysledky.extend([None] * len(soubory))

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
        self._zapis_vysledek(self._start_radek + i, v)

    def _na_prubeh(self, hotovo: int, celkem: int):
        self.progress.setValue(hotovo)
        self.lbl_prubeh.setText(f"{hotovo}/{celkem}")

    def _zastav_kontrolu(self):
        if self._worker is not None and self._thread is not None:
            self._worker.zastav()
            self.btn_zastavit.setEnabled(False)
            self.statusBar().showMessage("Zastavuji po dokončení aktuálního souboru…")

    def _zapis_vysledek(self, radek: int, v: VysledekSouboru):
        self._vysledky[radek] = v
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
        for radek in range(min(self.tabulka.rowCount(), len(self._vysledky))):
            if self._vysledky[radek] is None:
                self.tabulka.setItem(
                    radek, 1, QTableWidgetItem("nezkontrolováno (zastaveno)")
                )
        stav = "Zastaveno" if zastaveno else "Hotovo"
        if pocet:
            stav += f" ({pocet})"
        zprava = getattr(self, "_tl_zprava", "")
        self.statusBar().showMessage(f"{stav} | {zprava}" if zprava else stav)
        # u jediného souboru rovnou zobrazit detail v pravém panelu
        if getattr(self, "_pocet_davky", 0) == 1:
            self.tabulka.selectRow(self._start_radek)

    def _zobraz_detail(self):
        radky = {i.row() for i in self.tabulka.selectedItems()}
        if len(radky) != 1:
            return
        radek = radky.pop()
        if radek < len(self._vysledky) and self._vysledky[radek] is not None:
            if self._detail_radek != radek:
                self._sbalene.clear()  # nový soubor → vše rozbalené
                self._detail_radek = radek
            self._vykresli_detail()

    def _vykresli_detail(self):
        if (
            self._detail_radek is None
            or self._detail_radek >= len(self._vysledky)
            or self._vysledky[self._detail_radek] is None
        ):
            return
        posuv = self.detail.verticalScrollBar().value()
        self.detail.setHtml(
            _detail_html(self._vysledky[self._detail_radek], self._sbalene)
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
