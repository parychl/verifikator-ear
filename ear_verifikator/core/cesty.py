"""Podpora dlouhých cest na Windows (limit MAX_PATH = 260 znaků).

Projektová dokumentace mívá hluboké složky a cesty přes 260 znaků; bez
ošetření pak otevření souboru i procházení složek selže s „No such file
or directory“, přestože soubor existuje. Prefix \\?\ vypíná zpracování
MAX_PATH ve Win32 API a funguje i na systémech bez zapnutého
LongPathsEnabled (výchozí stav Windows).

Prefixovaná podoba slouží jen pro přístup k souborům; pro zobrazení
a export se používá původní (bez_prefixu).
"""
from __future__ import annotations

import os
from pathlib import Path

_PREFIX = "\\\\?\\"
_PREFIX_UNC = "\\\\?\\UNC\\"


def fs_cesta(cesta: Path | str) -> Path:
    """Cesta pro souborové operace — na Windows absolutní s prefixem \\?\."""
    if os.name != "nt":
        return Path(cesta)
    s = os.path.abspath(str(cesta))
    if s.startswith(_PREFIX):
        return Path(s)
    if s.startswith("\\\\"):  # UNC: \\server\share → \\?\UNC\server\share
        return Path(_PREFIX_UNC + s[2:])
    return Path(_PREFIX + s)


def bez_prefixu(cesta: Path | str) -> Path:
    """Zobrazitelná podoba cesty (bez \\?\ prefixu)."""
    s = str(cesta)
    if s.startswith(_PREFIX_UNC):
        return Path("\\\\" + s[len(_PREFIX_UNC):])
    if s.startswith(_PREFIX):
        return Path(s[len(_PREFIX):])
    return Path(s)


def najdi_soubory(slozka: Path, maska: str) -> list[Path]:
    """Rekurzivní hledání přes dlouhé cesty; výsledky vrací bez prefixu."""
    return sorted(bez_prefixu(p) for p in fs_cesta(slozka).rglob(maska))
