"""„Instalace" zabalené aplikace — zástupci na ploše a v nabídce Start.

Aplikace se šíří jako složka (extrahuj kamkoli a spusť exe). Při prvním
spuštění nabídne vytvoření zástupců, aby se chovala jako běžný program:
ikona na ploše, položka v nabídce Start (dohledatelná vyhledáváním,
připnutelná na hlavní panel).
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

log = logging.getLogger(__name__)

NAZEV = "Verifikátor EAR"
APP_ID = "CKAIT.VerifikatorEAR"  # AppUserModelID pro seskupování na hlavním panelu
NASTAVENI = Path.home() / ".ear_verifikator" / "nastaveni.json"


def zabaleno() -> bool:
    """True, když běžíme jako PyInstaller exe."""
    return bool(getattr(sys, "frozen", False))


def cesta_exe() -> Path:
    return Path(sys.executable)


def nastav_app_id() -> None:
    """Vlastní identita procesu na hlavním panelu (ikona/seskupení) místo Pythonu."""
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        except Exception:
            log.debug("SetCurrentProcessExplicitAppUserModelID selhal", exc_info=True)


def _ps_retezec(hodnota) -> str:
    """PowerShellový single-quoted literál — apostrofy uvnitř se zdvojují.

    Cesty (profil uživatele, složka s aplikací) mohou obsahovat apostrof;
    bez escapování by ukončil řetězec a zbytek by se vykonal jako příkaz.
    """
    return "'" + str(hodnota).replace("'", "''") + "'"


def _slozka_plocha() -> Path | None:
    try:
        vystup = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "[Environment]::GetFolderPath('Desktop')",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        cesta = vystup.stdout.strip()
        return Path(cesta) if cesta else None
    except Exception:
        return None


def _slozka_start_menu() -> Path | None:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def _vytvor_lnk(cil_lnk: Path, exe: Path) -> bool:
    skript = (
        "$ws = New-Object -ComObject WScript.Shell; "
        f"$l = $ws.CreateShortcut({_ps_retezec(cil_lnk)}); "
        f"$l.TargetPath = {_ps_retezec(exe)}; "
        f"$l.WorkingDirectory = {_ps_retezec(exe.parent)}; "
        f"$l.IconLocation = {_ps_retezec(f'{exe},0')}; "
        "$l.Description = 'Kontrola autorizačních razítek (EAR) v PDF'; "
        "$l.Save()"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", skript],
            capture_output=True,
            text=True,
            timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return proc.returncode == 0 and cil_lnk.exists()
    except Exception:
        log.warning("Vytvoření zástupce %s selhalo", cil_lnk, exc_info=True)
        return False


def vytvor_zastupce() -> list[str]:
    """Vytvoří zástupce na ploše a v nabídce Start. Vrátí popisy vytvořených."""
    exe = cesta_exe()
    vytvoreno: list[str] = []
    plocha = _slozka_plocha()
    if plocha and _vytvor_lnk(plocha / f"{NAZEV}.lnk", exe):
        vytvoreno.append("na ploše")
    start = _slozka_start_menu()
    if start and _vytvor_lnk(start / f"{NAZEV}.lnk", exe):
        vytvoreno.append("v nabídce Start")
    return vytvoreno


def _nastaveni_nacti() -> dict:
    try:
        return json.loads(NASTAVENI.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _nastaveni_uloz(data: dict) -> None:
    try:
        NASTAVENI.parent.mkdir(parents=True, exist_ok=True)
        NASTAVENI.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except OSError:
        log.warning("Nepodařilo se uložit nastavení", exc_info=True)


def nabidni_zastupce_pri_prvnim_spusteni(parent) -> None:
    """Při prvním spuštění zabalené aplikace nabídne vytvoření zástupců."""
    if not zabaleno():
        return
    data = _nastaveni_nacti()
    if data.get("zastupci_nabidnuti"):
        return
    data["zastupci_nabidnuti"] = True
    _nastaveni_uloz(data)

    from PySide6.QtWidgets import QMessageBox

    volba = QMessageBox.question(
        parent,
        "Vytvořit zástupce?",
        "Přidat zástupce aplikace na plochu a do nabídky Start,\n"
        "aby šla příště spustit jako běžný program?",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.Yes,
    )
    if volba == QMessageBox.Yes:
        vytvoreno = vytvor_zastupce()
        if vytvoreno:
            QMessageBox.information(
                parent, "Hotovo", "Zástupce vytvořen " + " a ".join(vytvoreno) + "."
            )
        else:
            QMessageBox.warning(
                parent, "Nepodařilo se", "Zástupce se nepodařilo vytvořit."
            )
