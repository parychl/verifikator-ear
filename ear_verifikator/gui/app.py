"""Spuštění GUI: python -m ear_verifikator.gui.app"""
import logging
import sys

from PySide6.QtWidgets import QApplication

# validace neexistujících řetězců k CA loguje očekávané chyby — nezahlcovat konzoli
logging.basicConfig(level=logging.ERROR)
logging.getLogger("pyhanko").setLevel(logging.CRITICAL)
logging.getLogger("pyhanko_certvalidator").setLevel(logging.CRITICAL)

from ear_verifikator.gui import instalace
from ear_verifikator.gui.main_window import HlavniOkno
from ear_verifikator.gui.style import aplikuj_vzhled, nacti_rezim

_POUZITI = "Použití: --overit <soubor.pdf> [--vystup <report.txt>]"


def _hodnota_prepinace(argv: list[str], prepinac: str):
    """Hodnota za přepínačem, None pokud přepínač chybí, SystemExit(2) bez hodnoty."""
    if prepinac not in argv:
        return None
    idx = argv.index(prepinac) + 1
    if idx >= len(argv) or argv[idx].startswith("--"):
        print(f"Chybí hodnota přepínače {prepinac}. {_POUZITI}", file=sys.stderr)
        raise SystemExit(2)
    return argv[idx]


def _samotest() -> int:
    """Diagnostika bez GUI: --overit <pdf> [--vystup <txt>] — ověří soubor a zapíše výsledek."""
    from pathlib import Path

    from ear_verifikator.core import cesty, export
    from ear_verifikator.core.model import Vysledek
    from ear_verifikator.core.signature import sestav_validacni_zdroje
    from ear_verifikator.core.trusted_list import nacti_trusted_list
    from ear_verifikator.core.verifier import Verifikator

    pdf = Path(_hodnota_prepinace(sys.argv, "--overit"))
    vystup_arg = _hodnota_prepinace(sys.argv, "--vystup")
    vystup = cesty.fs_cesta(vystup_arg) if vystup_arg else None
    tl = nacti_trusted_list(Path.home() / ".ear_verifikator" / "tl_cache")
    zdroje = sestav_validacni_zdroje(tl.registry) if tl.registry else None
    vysledek = Verifikator(zdroje).zkontroluj(pdf)
    if vystup:
        export.export_txt(vystup, [vysledek])
        if tl.chyby:
            with vystup.open("a", encoding="utf-8") as f:
                f.write("\nTrusted list — chyby:\n")
                f.writelines(f"  {ch}\n" for ch in tl.chyby)
    return 0 if vysledek.vysledek is not Vysledek.CHYBA_ZPRACOVANI else 1


def main():
    if "--overit" in sys.argv:
        sys.exit(_samotest())
    instalace.nastav_app_id()
    app = QApplication(sys.argv)
    aplikuj_vzhled(app, nacti_rezim())
    okno = HlavniOkno()
    okno.show()
    instalace.nabidni_zastupce_pri_prvnim_spusteni(okno)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
