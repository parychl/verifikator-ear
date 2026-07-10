"""Načtení EU Trusted Listů do pyHanko TSPRegistry.

Používá vestavěnou podporu pyHanko (eutl_fetch/eutl_parse): stáhne LOTL,
ověří jeho podpis proti certifikátům z Úředního věstníku EU a z něj
načte národní trusted listy (výchozí: jen CZ). Výsledný TSPRegistry
obsahuje kvalifikované CA (CA/QC) i TSA (TSA/QTST) a slouží jako zdroj
důvěry pro validaci podpisů i posouzení kvalifikovanosti.

Cache na disku; při výpadku sítě se použije i prošlá cache (s upozorněním).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp
from pyhanko.sign.validation.qualified import eutl_parse
from pyhanko.sign.validation.qualified.eutl_fetch import (
    FileSystemTLCache,
    fetch_lotl,
    lotl_to_registry,
)
from pyhanko.sign.validation.qualified.tsp import TSPRegistry

log = logging.getLogger(__name__)

VYCHOZI_UZEMI = frozenset({"CZ"})
VYCHOZI_EXPIRACE = timedelta(days=7)


class _Utf8TLCache(FileSystemTLCache):
    """FileSystemTLCache s binárně věrným uložením.

    Rodičovská implementace používá textový open() bez encoding — na Windows
    (cp1250) padá při zápisu XML s neevropskými znaky a překlad konců řádků
    (\\n ↔ \\r\\n) mění bajty dokumentu, čímž rozbije XML podpis trusted listu.
    """

    ignoruj_expiraci = False

    def __getitem__(self, key: str) -> str:
        exp_ts, fname = self._cache[key]
        if not self.ignoruj_expiraci and datetime.now(timezone.utc) > exp_ts:
            raise KeyError(key)
        try:
            return (self._root / fname).read_bytes().decode("utf-8")
        except OSError:
            raise KeyError(key)

    def __setitem__(self, key: str, value: str) -> None:
        exp_ts = datetime.now(timezone.utc) + self._expire_after
        fname = hashlib.sha256(key.encode("utf8")).hexdigest()
        index = self._root / "index.json"
        index_data = {}
        if index.exists():
            try:
                index_data = json.loads(index.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                index_data = {}
        index_data[key] = {"exp_epoch_seconds": exp_ts.timestamp(), "fname": fname}
        (self._root / fname).write_bytes(value.encode("utf-8"))
        index.write_text(json.dumps(index_data), encoding="utf-8")
        self._cache[key] = (exp_ts, fname)


class _StaleTolerantTLCache(_Utf8TLCache):
    """Cache, která při čtení ignoruje expiraci — fallback pro offline režim."""

    ignoruj_expiraci = True


@dataclass
class VysledekTL:
    registry: TSPRegistry | None = None
    z_prosle_cache: bool = False
    chyby: list[str] = field(default_factory=list)


async def _prefetchni_narodni_tl(lotl_xml: str, client, cache) -> None:
    """Stáhne národní TL do cache s hlavičkou Accept: */*.

    Např. tsl.gov.cz vrací 406 na Accept hlavičky, které posílá pyHanko;
    lotl_to_registry pak TL najde v cache a stahovat už nemusí.
    """
    uzemi = {u.casefold() for u in VYCHOZI_UZEMI}
    for ref in eutl_parse.parse_lotl_unsafe(lotl_xml).references:
        if ref.territory.casefold() not in uzemi:
            continue
        try:
            cache[ref.location_uri]
            continue  # čerstvá cache
        except KeyError:
            pass
        try:
            resp = await client.get(
                ref.location_uri,
                headers={"Accept": "*/*"},
                raise_for_status=True,
                timeout=aiohttp.ClientTimeout(total=60),
            )
            # dekódovat vždy jako UTF-8: servery často neposílají charset
            # a špatné dekódování rozbije XML podpis trusted listu
            cache[ref.location_uri] = (await resp.read()).decode("utf-8-sig")
        except Exception as e:
            log.warning("Prefetch TL %s selhal: %s", ref.location_uri, e)


async def _nacti(cache) -> TSPRegistry:
    async with aiohttp.ClientSession() as client:
        lotl_xml = await fetch_lotl(client, cache)
        await _prefetchni_narodni_tl(lotl_xml, client, cache)
        registry, errors = await lotl_to_registry(
            lotl_xml, client, cache=cache, only_territories=set(VYCHOZI_UZEMI)
        )
    for e in errors:
        log.warning("Trusted list: %s", e)
    if not any(True for _ in registry.known_certificate_authorities):
        raise RuntimeError("Trusted list neobsahuje žádné certifikační autority")
    return registry


def nacti_trusted_list(cache_dir: Path, expirace: timedelta = VYCHOZI_EXPIRACE) -> VysledekTL:
    """Vrátí TSPRegistry pro CZ; při nedostupnosti sítě zkusí prošlou cache."""
    vysledek = VysledekTL()
    try:
        cache = _Utf8TLCache(cache_dir, expire_after=expirace)
        vysledek.registry = asyncio.run(_nacti(cache))
        return vysledek
    except Exception as e:
        log.warning("Stažení trusted listu selhalo: %s", e)
        vysledek.chyby.append(f"Stažení trusted listu selhalo: {e}")

    try:
        stale_cache = _StaleTolerantTLCache(cache_dir, expire_after=expirace)
        vysledek.registry = asyncio.run(_nacti(stale_cache))
        vysledek.z_prosle_cache = True
        vysledek.chyby.append(
            "Použita starší uložená kopie trusted listu — výsledky nemusí "
            "odrážet aktuální stav kvalifikovaných služeb."
        )
    except Exception as e:
        log.error("Trusted list není k dispozici ani v cache: %s", e)
        vysledek.chyby.append(
            "Trusted list není k dispozici (bez připojení a bez cache). "
            "Kvalifikovanost podpisů nelze ověřit."
        )
    return vysledek
