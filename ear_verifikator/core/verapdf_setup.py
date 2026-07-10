"""Automatické obstarání veraPDF — uživatel nemusí nic instalovat.

Validátor PDF/A veraPDF je Java aplikace. Tento modul si (po odsouhlasení
uživatelem v GUI) stáhne do datové složky aplikace:
  - greenfield-apps.jar z Maven Central (~10 MB) — samostatně spustitelný
    veraPDF CLI (main class GreenfieldCliWrapper),
  - přenosné JRE Temurin (~45 MB) — jen pokud v systému žádná Java není.

Obě položky jsou připnuté na konkrétní verzi a stažený soubor se ověřuje
proti očekávanému otisku SHA-256 — spustí se jen nezměněný, ověřený kód.

Nic se neinstaluje do systému; vše žije v ~/.ear_verifikator/verapdf
a dá se smazat prostým odstraněním složky.
"""
from __future__ import annotations

import hashlib
import logging
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Callable

import requests

log = logging.getLogger(__name__)

SLOZKA = Path.home() / ".ear_verifikator" / "verapdf"
JAR = SLOZKA / "greenfield-apps.jar"
JRE_SLOZKA = SLOZKA / "jre"
MAIN_CLASS = "org.verapdf.apps.GreenfieldCliWrapper"

VERAPDF_VERZE = "1.28.2"
JAR_URL = (
    "https://repo1.maven.org/maven2/org/verapdf/apps/greenfield-apps/"
    f"{VERAPDF_VERZE}/greenfield-apps-{VERAPDF_VERZE}.jar"
)
JAR_SHA256 = "687d4d8bcfec48c9f7e931cf78ed061f1eeccfba55d4cdc000ba9b4d8aa46ce6"

# Temurin 17 JRE pro Windows x64 — pevná verze + otisk (Adoptium/GitHub releases)
JRE_VERZE = "jdk-17.0.19+10"
JRE_URL = (
    "https://github.com/adoptium/temurin17-binaries/releases/download/"
    "jdk-17.0.19%2B10/OpenJDK17U-jre_x64_windows_hotspot_17.0.19_10.zip"
)
JRE_SHA256 = "79a598e1fbb4e16582d92c4ee22280a3c4d72fd52606e1e46b1223c0fe53b0da"

ProgressCb = Callable[[str], None]


def _najdi_java() -> str | None:
    """Java: nejdřív přibalené JRE, pak systémová."""
    if JRE_SLOZKA.is_dir():
        for kandidat in JRE_SLOZKA.glob("*/bin/java.exe"):
            return str(kandidat)
        for kandidat in JRE_SLOZKA.glob("bin/java.exe"):
            return str(kandidat)
    return shutil.which("java")


def _java_funguje(java: str) -> bool:
    try:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.run(
            [java, "-version"],
            capture_output=True,
            timeout=30,
            creationflags=creationflags,
        )
        return proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def verapdf_prikaz() -> list[str] | None:
    """Vrátí příkaz pro spuštění veraPDF, nebo None, když není k dispozici.

    Pořadí: samostatná instalace veraPDF v PATH → stažený jar + Java.
    """
    for nazev in ("verapdf", "verapdf.bat"):
        cesta = shutil.which(nazev)
        if not cesta:
            continue
        # starší Pythony na Windows hledají i v aktuálním adresáři — tam by
        # mohl ležet podvržený skript vedle kontrolovaných dat
        if Path(cesta).resolve().parent == Path.cwd().resolve():
            continue
        return [cesta]
    if JAR.exists():
        java = _najdi_java()
        if java:
            return [java, "-cp", str(JAR), MAIN_CLASS]
    return None


def _stahni(
    url: str, cil: Path, popis: str, progress: ProgressCb, sha256: str
) -> None:
    docasny = cil.with_suffix(cil.suffix + ".part")
    otisk = hashlib.sha256()
    with requests.get(url, stream=True, timeout=120) as resp:
        resp.raise_for_status()
        celkem = int(resp.headers.get("Content-Length", 0))
        stazeno = 0
        posledni_mb = -1
        with docasny.open("wb") as f:
            for blok in resp.iter_content(chunk_size=1 << 18):
                f.write(blok)
                otisk.update(blok)
                stazeno += len(blok)
                mb = stazeno >> 20
                if celkem and mb != posledni_mb:
                    posledni_mb = mb
                    progress(f"Stahuji {popis}… {mb}/{celkem >> 20} MB")
    if otisk.hexdigest() != sha256:
        docasny.unlink(missing_ok=True)
        raise RuntimeError(
            f"{popis}: stažený soubor neodpovídá očekávanému otisku SHA-256 "
            "— stahování bylo zahozeno."
        )
    docasny.replace(cil)


def stahni_verapdf(progress: ProgressCb | None = None) -> list[str] | None:
    """Zajistí veraPDF (stáhne, co chybí). Vrátí příkaz, nebo None při neúspěchu."""
    progress = progress or (lambda _: None)
    prikaz = verapdf_prikaz()
    if prikaz is not None:
        return prikaz

    SLOZKA.mkdir(parents=True, exist_ok=True)
    try:
        if not JAR.exists():
            _stahni(JAR_URL, JAR, "PDF/A validátor veraPDF", progress, JAR_SHA256)

        java = _najdi_java()
        if java is None or not _java_funguje(java):
            progress("Stahuji přenosné prostředí Java…")
            jre_zip = SLOZKA / "jre.zip"
            _stahni(JRE_URL, jre_zip, "prostředí Java", progress, JRE_SHA256)
            if JRE_SLOZKA.exists():
                shutil.rmtree(JRE_SLOZKA, ignore_errors=True)
            with zipfile.ZipFile(jre_zip) as z:
                z.extractall(JRE_SLOZKA)
            jre_zip.unlink(missing_ok=True)
    except Exception as e:
        log.warning("Stažení veraPDF selhalo: %s", e)
        progress(f"Stažení PDF/A validátoru selhalo: {e}")
        return None

    prikaz = verapdf_prikaz()
    if prikaz is None:
        progress("PDF/A validátor se nepodařilo zprovoznit.")
    return prikaz
