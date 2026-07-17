"""Kontrola formátu PDF/A (vyhláška č. 190/2024 Sb. vyžaduje PDF/A-3).

Dvě roviny dle metodiky (kap. 4.3):
  - deklarovaná úroveň — co dokument tvrdí v XMP metadatech (pdfaid:part/conformance)
  - splňuje — zda tomu skutečně odpovídá; plná validace přes veraPDF (je-li k dispozici)
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path

from pyhanko.pdf_utils.reader import PdfFileReader

from . import cesty, verapdf_setup
from .model import InfoPDFA

log = logging.getLogger(__name__)

# XML atributy smí používat uvozovky i apostrofy (některé podepisovací
# nástroje zapisují pdfaid:part='3')
_PART = re.compile(
    rb'pdfaid:part\s*=\s*["\'](\d+)["\']|<pdfaid:part>\s*(\d+)\s*</pdfaid:part>'
)
_CONF = re.compile(
    rb'pdfaid:conformance\s*=\s*["\']([A-Ua-u])["\']'
    rb'|<pdfaid:conformance>\s*([A-Ua-u])\s*</pdfaid:conformance>'
)

POZADOVANA_CAST = 3  # PDF/A-3


def deklarovana_uroven(reader: PdfFileReader) -> str:
    """Vrátí deklarovanou úroveň z XMP, např. 'PDF/A-3B', nebo '' pokud nedeklaruje."""
    try:
        metadata = reader.root.get("/Metadata")
        if metadata is None:
            return ""
        # .get() nedereferencuje nepřímé objekty (na rozdíl od [])
        if hasattr(metadata, "get_object"):
            metadata = metadata.get_object()
        xmp = metadata.data
    except Exception:
        log.debug("Nelze přečíst XMP metadata", exc_info=True)
        return ""
    m_part = _PART.search(xmp)
    if not m_part:
        return ""
    part = (m_part.group(1) or m_part.group(2)).decode()
    m_conf = _CONF.search(xmp)
    conf = (m_conf.group(1) or m_conf.group(2)).decode().upper() if m_conf else ""
    return f"PDF/A-{part}{conf}"


def najdi_verapdf() -> list[str] | None:
    """Příkaz pro veraPDF (systémová instalace nebo stažený jar + Java)."""
    return verapdf_setup.verapdf_prikaz()


def _vysledek_z_json(vystup: str) -> tuple[bool | None, str]:
    data = json.loads(vystup)
    jobs = data.get("report", data).get("jobs", [])
    if not jobs:
        return None, "veraPDF nevrátil žádný výsledek"
    vr = jobs[0].get("validationResult")
    if isinstance(vr, list):
        vr = vr[0] if vr else None
    if not vr:
        return None, "veraPDF nevrátil validationResult"
    compliant = bool(vr.get("compliant", vr.get("isCompliant", False)))
    profil = vr.get("profileName", vr.get("flavour", ""))
    n_chyb = vr.get("details", {}).get("failedRules", 0)
    detail = f"profil: {profil}" + (f", porušených pravidel: {n_chyb}" if not compliant else "")
    return compliant, detail


def plna_validace(soubor: Path, prikaz: list[str]) -> tuple[bool | None, str]:
    """Spustí veraPDF (flavour dle deklarace v souboru). Vrací (vyhovuje, detail)."""
    try:
        proc = subprocess.run(
            # \\?\ cesta: absolutní (název s "-" se nevyloží jako přepínač)
            # a funguje i přes 260 znaků; veraPDF/Java ji přijímá
            [*prikaz, "--format", "json", str(cesty.fs_cesta(soubor))],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            # bez CREATE_NO_WINDOW by pod pythonw.exe probliklo okno konzole
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, f"veraPDF se nepodařilo spustit: {e}"
    # veraPDF vrací nenulový kód i při pouhé nevyhovující validaci — rozhoduje obsah
    if not proc.stdout.strip():
        return None, f"veraPDF selhal: {proc.stderr.strip()[:200]}"
    try:
        return _vysledek_z_json(proc.stdout)
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        return None, f"Nesrozumitelný výstup veraPDF: {e}"


def zkontroluj_pdfa(
    soubor: Path, reader: PdfFileReader, verapdf: list[str] | None
) -> InfoPDFA:
    info = InfoPDFA(deklarovana=deklarovana_uroven(reader))
    if not info.deklarovana:
        info.vyhovuje = False
        info.detail = (
            "Dokument nedeklaruje formát PDF/A — nesplňuje vyhlášku č. 190/2024 Sb. "
            "(vyžadován PDF/A-3)."
        )
        return info
    if not info.deklarovana.startswith(f"PDF/A-{POZADOVANA_CAST}"):
        info.vyhovuje = False
        info.detail = (
            f"Deklarováno {info.deklarovana}, vyhláška č. 190/2024 Sb. vyžaduje PDF/A-3."
        )
        return info
    if not verapdf:
        info.vyhovuje = None
        info.detail = (
            "Deklarace odpovídá; skutečnou shodu nelze ověřit (veraPDF není k dispozici)."
        )
        return info
    info.vyhovuje, info.detail = plna_validace(soubor, verapdf)
    if info.vyhovuje is False and "vyhlášk" not in info.detail:
        info.detail = f"Deklaruje {info.deklarovana}, ale validaci nesplňuje ({info.detail})."
    return info
