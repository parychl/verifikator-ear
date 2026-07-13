"""Vzhled aplikace — tmavý a světlý režim (Fusion + QSS)."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

log = logging.getLogger(__name__)

NASTAVENI = Path.home() / ".ear_verifikator" / "nastaveni.json"

TMAVY = {
    "pozadi": "#1e1f22",
    "panel": "#26282c",
    "vstup": "#2d2f34",
    "vstup_hover": "#34373d",
    "vstup_pressed": "#24262a",
    "okraj": "#3a3d43",
    "okraj_hover": "#4a4e56",
    "alt_radek": "#2a2c30",
    "text": "#d6d8dc",
    "tlumeny": "#8b8f98",
    "akcent": "#4a88c7",
    "akcent_hover": "#5b99d8",
    "selekce": "#3a5a7a",
    "selekce_text": "white",
    "scroll": "#45484f",
    "scroll_hover": "#565a63",
}

SVETLY = {
    "pozadi": "#f2f3f5",
    "panel": "#ffffff",
    "vstup": "#e9ebee",
    "vstup_hover": "#dfe2e6",
    "vstup_pressed": "#d4d8dd",
    "okraj": "#d0d4da",
    "okraj_hover": "#b8bdc5",
    "alt_radek": "#f6f7f9",
    "text": "#24262a",
    "tlumeny": "#70757d",
    "akcent": "#3574b5",
    "akcent_hover": "#2a67a6",
    "selekce": "#3574b5",
    "selekce_text": "white",
    "scroll": "#c2c6cd",
    "scroll_hover": "#a9aeb7",
}

REZIMY = {"tmavy": TMAVY, "svetly": SVETLY}

# barvy výsledků kontroly čitelné na daném pozadí
BARVY_VYSLEDKU = {
    "tmavy": {
        "PLATNY": QColor(102, 187, 106),
        "NEPLATNY": QColor(239, 83, 80),
        "TECHNICKY_NEVYHOVUJICI": QColor(255, 167, 38),
        "ZASTARALY_STANDARD": QColor(255, 200, 60),
        "CHYBA_ZPRACOVANI": QColor(158, 158, 158),
    },
    "svetly": {
        "PLATNY": QColor(37, 199, 42),
        "NEPLATNY": QColor(198, 40, 40),
        "TECHNICKY_NEVYHOVUJICI": QColor(230, 126, 34),
        "ZASTARALY_STANDARD": QColor(217, 119, 6),
        "CHYBA_ZPRACOVANI": QColor(117, 117, 117),
    },
}

aktualni_rezim = "tmavy"


def _qss(t: dict[str, str]) -> str:
    return f"""
* {{
    font-family: "Segoe UI";
    font-size: 10pt;
}}
QMainWindow, QWidget {{
    background: {t['pozadi']};
    color: {t['text']};
}}
QPushButton {{
    background: {t['vstup']};
    border: 1px solid {t['okraj']};
    border-radius: 6px;
    padding: 7px 16px;
}}
QPushButton:hover {{
    background: {t['vstup_hover']};
    border-color: {t['okraj_hover']};
}}
QPushButton:pressed {{
    background: {t['vstup_pressed']};
}}
QPushButton:disabled {{
    color: {t['tlumeny']};
}}
QPushButton#primarni {{
    background: {t['akcent']};
    border-color: {t['akcent']};
    color: white;
    font-weight: 600;
}}
QPushButton#primarni:hover {{
    background: {t['akcent_hover']};
}}
QPushButton#rezim {{
    padding: 7px 10px;
    font-size: 12pt;
}}
QComboBox {{
    background: {t['vstup']};
    border: 1px solid {t['okraj']};
    border-radius: 6px;
    padding: 6px 10px;
    min-width: 120px;
}}
QComboBox:hover {{
    border-color: {t['okraj_hover']};
}}
QComboBox QAbstractItemView {{
    background: {t['vstup']};
    border: 1px solid {t['okraj']};
    selection-background-color: {t['akcent']};
    selection-color: white;
}}
QTableWidget {{
    background: {t['panel']};
    alternate-background-color: {t['alt_radek']};
    border: 1px solid {t['okraj']};
    border-radius: 8px;
    gridline-color: transparent;
    selection-background-color: {t['selekce']};
    selection-color: {t['selekce_text']};
}}
QTableWidget::item {{
    padding: 4px 8px;
    border: none;
}}
QHeaderView::section {{
    background: {t['panel']};
    color: {t['tlumeny']};
    border: none;
    border-bottom: 1px solid {t['okraj']};
    border-right: 1px solid {t['okraj']};
    padding: 6px 8px;
    font-weight: 600;
}}
QHeaderView::section:last {{
    border-right: none;
}}
QTableCornerButton::section {{
    background: {t['panel']};
    border: none;
}}
QTextBrowser {{
    background: {t['panel']};
    border: 1px solid {t['okraj']};
    border-radius: 8px;
    padding: 8px;
}}
QSplitter::handle {{
    background: transparent;
    width: 6px;
}}
QProgressBar {{
    background: {t['vstup']};
    border: 1px solid {t['okraj']};
    border-radius: 6px;
    height: 10px;
    text-align: center;
    color: {t['tlumeny']};
}}
QProgressBar::chunk {{
    background: {t['akcent']};
    border-radius: 5px;
}}
QStatusBar {{
    background: {t['pozadi']};
    color: {t['tlumeny']};
    border-top: 1px solid {t['okraj']};
}}
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {t['scroll']};
    border-radius: 4px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background: {t['scroll_hover']};
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: {t['scroll']};
    border-radius: 4px;
    min-width: 30px;
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    height: 0; width: 0;
}}
QScrollBar::add-page, QScrollBar::sub-page {{
    background: transparent;
}}
QMenu {{
    background: {t['vstup']};
    border: 1px solid {t['okraj']};
    border-radius: 6px;
    padding: 4px;
}}
QMenu::item {{
    padding: 6px 20px;
    border-radius: 4px;
}}
QMenu::item:selected {{
    background: {t['akcent']};
    color: white;
}}
QPushButton::menu-indicator {{
    subcontrol-position: right center;
    subcontrol-origin: padding;
    right: 8px;
}}
QToolTip {{
    background: {t['vstup']};
    color: {t['text']};
    border: 1px solid {t['okraj']};
    padding: 4px;
}}
QMessageBox {{
    background: {t['panel']};
}}
"""


def aplikuj_vzhled(app: QApplication, rezim: str = "tmavy") -> None:
    global aktualni_rezim
    if rezim not in REZIMY:
        rezim = "tmavy"
    aktualni_rezim = rezim
    t = REZIMY[rezim]
    app.setStyle("Fusion")
    paleta = QPalette()
    paleta.setColor(QPalette.Window, QColor(t["pozadi"]))
    paleta.setColor(QPalette.WindowText, QColor(t["text"]))
    paleta.setColor(QPalette.Base, QColor(t["panel"]))
    paleta.setColor(QPalette.AlternateBase, QColor(t["alt_radek"]))
    paleta.setColor(QPalette.Text, QColor(t["text"]))
    paleta.setColor(QPalette.Button, QColor(t["vstup"]))
    paleta.setColor(QPalette.ButtonText, QColor(t["text"]))
    paleta.setColor(QPalette.Highlight, QColor(t["selekce"]))
    paleta.setColor(QPalette.HighlightedText, QColor(t["selekce_text"]))
    paleta.setColor(QPalette.ToolTipBase, QColor(t["vstup"]))
    paleta.setColor(QPalette.ToolTipText, QColor(t["text"]))
    paleta.setColor(QPalette.PlaceholderText, QColor(t["tlumeny"]))
    app.setPalette(paleta)
    app.setStyleSheet(_qss(t))


def barva_vysledku(nazev: str) -> QColor:
    return BARVY_VYSLEDKU[aktualni_rezim][nazev]


def nacti_rezim() -> str:
    try:
        return json.loads(NASTAVENI.read_text(encoding="utf-8")).get("rezim", "tmavy")
    except (OSError, json.JSONDecodeError):
        return "tmavy"


def uloz_rezim(rezim: str) -> None:
    try:
        NASTAVENI.parent.mkdir(parents=True, exist_ok=True)
        data = {}
        if NASTAVENI.exists():
            try:
                data = json.loads(NASTAVENI.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                data = {}
        data["rezim"] = rezim
        NASTAVENI.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except OSError:
        log.warning("Nepodařilo se uložit nastavení vzhledu", exc_info=True)


def aplikuj_tmavy_vzhled(app: QApplication) -> None:
    """Zpětně kompatibilní zkratka."""
    aplikuj_vzhled(app, "tmavy")
