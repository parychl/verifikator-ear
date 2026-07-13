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
