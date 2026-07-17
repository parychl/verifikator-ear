"""Kontrola dostupnosti novější verze na GitHub Releases.

Ptá se GitHub API na poslední vydání; jakákoli chyba (offline, rate limit,
změna API) znamená „žádná novinka“ — kontrola nesmí nikdy obtěžovat.
"""
from __future__ import annotations

import logging

import requests

log = logging.getLogger(__name__)

REPOZITAR = "parychl/verifikator-ear"
RELEASES_URL = f"https://github.com/{REPOZITAR}/releases/latest"
# stálá adresa přílohy nejnovějšího vydání (přesměruje na aktuální verzi)
STAZENI_URL = (
    f"https://github.com/{REPOZITAR}/releases/latest/download/Verifikator_EAR.zip"
)
_API_URL = f"https://api.github.com/repos/{REPOZITAR}/releases/latest"


def _jako_cisla(verze: str) -> tuple[int, ...] | None:
    """'v1.2.3' → (1, 2, 3); None, pokud řetězec není číselná verze."""
    verze = verze.strip().lstrip("vV")
    try:
        return tuple(int(c) for c in verze.split("."))
    except ValueError:
        return None


def je_novejsi(kandidat: str, aktualni: str) -> bool:
    k, a = _jako_cisla(kandidat), _jako_cisla(aktualni)
    if k is None or a is None:
        return False
    return k > a


def slozka_stahovani() -> "Path":
    from pathlib import Path

    kandidat = Path.home() / "Downloads"
    return kandidat if kandidat.is_dir() else Path.home()


def stahni_novou_verzi(verze: str, progress) -> "Path":
    """Stáhne ZIP nejnovějšího vydání do složky Stažené soubory.

    progress(staženo_bajtů, celkem_bajtů) se volá průběžně; celkem může
    být 0, když server velikost neuvádí.
    """
    cil = slozka_stahovani() / f"Verifikator_EAR_{verze}.zip"
    docasny = cil.with_suffix(".part")
    with requests.get(STAZENI_URL, stream=True, timeout=120) as resp:
        resp.raise_for_status()
        celkem = int(resp.headers.get("Content-Length", 0))
        stazeno = 0
        with docasny.open("wb") as f:
            for blok in resp.iter_content(chunk_size=1 << 18):
                f.write(blok)
                stazeno += len(blok)
                progress(stazeno, celkem)
    docasny.replace(cil)
    return cil


def zjisti_novejsi_verzi(aktualni: str) -> str | None:
    """Vrátí označení novější verze (např. '1.2.0'), nebo None."""
    try:
        resp = requests.get(
            _API_URL,
            headers={"Accept": "application/vnd.github+json"},
            timeout=5,
        )
        resp.raise_for_status()
        tag = str(resp.json().get("tag_name", ""))
    except Exception as e:
        log.debug("Kontrola aktualizací selhala: %s", e)
        return None
    if je_novejsi(tag, aktualni):
        return tag.lstrip("vV")
    return None
