"""Načtení EU Trusted Listů do pyHanko TSPRegistry.

Používá vestavěnou podporu pyHanko (eutl_fetch/eutl_parse): stáhne LOTL,
ověří jeho podpis proti certifikátům z Úředního věstníku EU a z něj
načte národní trusted listy. Sestavují se dva registry: český (důvěra
pro certifikáty podpisů — EAR) a celoevropský (důvěra pro časová
razítka — kvalifikovaná TSA může být z kteréhokoli členského státu).

Cache na disku má dvě vrstvy: surová XML (spravuje pyHanko TLCache) a nad
ní hotový naparsovaný a podpisově ověřený registr (pickle). Čerstvý registr
se načte přímo z disku bez sítě i bez parsování XML; po 7 dnech expiruje
a vše se stáhne a ověří znovu. Při výpadku sítě se použije i prošlá kopie
(s upozorněním).
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import os
import pickle
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import aiohttp
from asn1crypto import x509
from pyhanko.sign.validation.qualified import eutl_parse
from pyhanko.sign.validation.qualified.eutl_fetch import (
    FileSystemTLCache,
    fetch_lotl,
    lotl_to_registry,
)
from pyhanko.sign.validation.qualified.tsp import TSPRegistry

log = logging.getLogger(__name__)

VYCHOZI_UZEMI = frozenset({"CZ"})               # důvěra pro podpisy (EAR)
# razítka: výchozí jen ČR a Slovensko (rychlý start); None = celá EU
VYCHOZI_UZEMI_RAZITEK = frozenset({"CZ", "SK"})
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
    """Dva zdroje důvěry: CZ pro certifikáty podpisů (EAR je česká
    záležitost), celá EU pro časová razítka (kvalifikovaná TSA může být
    z kteréhokoli členského státu — eIDAS je celoevropské)."""

    registry_cz: TSPRegistry | None = None
    registry_eu: TSPRegistry | None = None
    z_prosle_cache: bool = False
    chyby: list[str] = field(default_factory=list)


# --- perzistence hotového registru (pickle) --------------------------------
# Formát uloženého souboru; zvýšit při změně struktury níže.
_REGISTR_VERZE = 1


def _verze_knihoven() -> str:
    """Verze knihoven, jejichž třídy jsou v pickle — po upgradu se registr
    zahodí a sestaví znovu (unpickle napříč verzemi není spolehlivý)."""
    casti = []
    for balik in ("pyhanko", "pyhanko-certvalidator", "asn1crypto"):
        try:
            casti.append(f"{balik}=={version(balik)}")
        except PackageNotFoundError:
            casti.append(balik)
    return " ".join(casti)


def _soubor_registru(cache_dir: Path, uzemi_razitek: frozenset[str] | None) -> Path:
    klic = "EU" if uzemi_razitek is None else "-".join(sorted(uzemi_razitek))
    return cache_dir / f"registr_{klic}.pickle"


def _cert_z_der(der: bytes) -> x509.Certificate:
    return x509.Certificate.load(der)


def _dumps_registr(objekt) -> bytes:
    """pickle.dumps s certifikáty uloženými jako DER bajty.

    Přímý pickle asn1crypto objektů obchází jejich lazy inicializaci
    (_setup tříd) — v čerstvém procesu pak unpickle padá. Rekonstrukce
    přes Certificate.load() jde normální parsovací cestou.
    """
    buf = io.BytesIO()
    p = pickle.Pickler(buf)
    p.dispatch_table = {x509.Certificate: lambda c: (_cert_z_der, (c.dump(),))}
    p.dump(objekt)
    return buf.getvalue()


def _uloz_registr(
    cache_dir: Path,
    uzemi_razitek: frozenset[str] | None,
    registry_cz: TSPRegistry,
    registry_eu: TSPRegistry,
) -> None:
    """Uloží hotový registr na disk; selhání se jen zaloguje (příště se
    registr znovu sestaví z XML)."""
    soubor = _soubor_registru(cache_dir, uzemi_razitek)
    try:
        data = _dumps_registr(
            {
                "verze": _REGISTR_VERZE,
                "knihovny": _verze_knihoven(),
                "ulozeno": datetime.now(timezone.utc),
                "registry_cz": registry_cz,
                "registry_eu": registry_eu,
            }
        )
        cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = soubor.with_suffix(".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, soubor)  # atomicky — po pádu nezůstane torzo
    except Exception as e:
        log.warning("Uložení hotového registru na disk selhalo: %s", e)


def _nacti_ulozeny_registr(
    cache_dir: Path,
    uzemi_razitek: frozenset[str] | None,
    expirace: timedelta,
    i_prosly: bool = False,
) -> tuple[TSPRegistry, TSPRegistry] | None:
    """(registr CZ, registr razítek) z disku; None = chybí, prošlý nebo
    nečitelný. Soubor je v profilu uživatele — stejná úroveň důvěry jako
    XML cache vedle něj, unpickle je zde proto v pořádku."""
    soubor = _soubor_registru(cache_dir, uzemi_razitek)
    try:
        data = pickle.loads(soubor.read_bytes())
        if (
            data["verze"] != _REGISTR_VERZE
            or data["knihovny"] != _verze_knihoven()
        ):
            return None
        if not i_prosly and datetime.now(timezone.utc) > data["ulozeno"] + expirace:
            return None
        registry_cz, registry_eu = data["registry_cz"], data["registry_eu"]
        if not any(True for _ in registry_cz.known_certificate_authorities):
            return None
        return registry_cz, registry_eu
    except FileNotFoundError:
        return None
    except Exception as e:
        log.warning("Uložený registr %s nelze načíst: %s", soubor.name, e)
        return None


async def _prefetchni_narodni_tl(
    lotl_xml: str, client, cache, uzemi: frozenset[str] | None
) -> None:
    """Stáhne národní TL do cache s hlavičkou Accept: */* (None = všechny země).

    Např. tsl.gov.cz vrací 406 na Accept hlavičky, které posílá pyHanko;
    lotl_to_registry pak TL najde v cache a stahovat už nemusí.
    """
    povolena = None if uzemi is None else {u.casefold() for u in uzemi}
    for ref in eutl_parse.parse_lotl_unsafe(lotl_xml).references:
        if povolena is not None and ref.territory.casefold() not in povolena:
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


async def _nacti(
    cache, uzemi_razitek: frozenset[str] | None
) -> tuple[TSPRegistry, TSPRegistry]:
    """(registr CZ pro podpisy, registr pro razítka) z jednoho stažení LOTL."""
    async with aiohttp.ClientSession() as client:
        lotl_xml = await fetch_lotl(client, cache)
        prefetch_uzemi = (
            None
            if uzemi_razitek is None
            else frozenset(VYCHOZI_UZEMI | uzemi_razitek)
        )
        await _prefetchni_narodni_tl(lotl_xml, client, cache, prefetch_uzemi)
        registry_cz, errors_cz = await lotl_to_registry(
            lotl_xml, client, cache=cache, only_territories=set(VYCHOZI_UZEMI)
        )
        registry_eu, errors_eu = await lotl_to_registry(
            lotl_xml,
            client,
            cache=cache,
            only_territories=None if uzemi_razitek is None else set(uzemi_razitek),
        )
    for e in errors_cz:
        log.warning("Trusted list (CZ): %s", e)
    for e in errors_eu:
        log.warning("Trusted list (razítka): %s", e)
    if not any(True for _ in registry_cz.known_certificate_authorities):
        raise RuntimeError("Trusted list neobsahuje žádné certifikační autority")
    return registry_cz, registry_eu


def nacti_trusted_list(
    cache_dir: Path,
    expirace: timedelta = VYCHOZI_EXPIRACE,
    uzemi_razitek: frozenset[str] | None = VYCHOZI_UZEMI_RAZITEK,
) -> VysledekTL:
    """Vrátí registry (CZ + razítka); při nedostupnosti sítě zkusí prošlou cache.

    uzemi_razitek: země, jejichž TSA se uznávají pro časová razítka;
    None = všechny země EU (delší stahování a parsování).

    Čerstvý hotový registr na disku (mladší než ``expirace``) se použije
    rovnou — bez sítě a bez opětovného parsování a ověřování ~30 XML.
    Po expiraci se vše stáhne a sestaví znovu, aby se projevily změny
    v kvalifikovaných službách (např. odebraná autorita).
    """
    vysledek = VysledekTL()
    ulozene = _nacti_ulozeny_registr(cache_dir, uzemi_razitek, expirace)
    if ulozene is not None:
        vysledek.registry_cz, vysledek.registry_eu = ulozene
        return vysledek

    try:
        cache = _Utf8TLCache(cache_dir, expire_after=expirace)
        vysledek.registry_cz, vysledek.registry_eu = asyncio.run(
            _nacti(cache, uzemi_razitek)
        )
        _uloz_registr(
            cache_dir, uzemi_razitek, vysledek.registry_cz, vysledek.registry_eu
        )
        return vysledek
    except Exception as e:
        log.warning("Stažení trusted listu selhalo: %s", e)
        vysledek.chyby.append(f"Stažení trusted listu selhalo: {e}")

    # offline: nejdřív prošlý hotový registr (rychlé), až pak prošlá XML
    # cache (pomalé nové parsování); prošlý registr se na disk znovu
    # neukládá, aby datum uložení dál odpovídalo stáří dat
    ulozene = _nacti_ulozeny_registr(cache_dir, uzemi_razitek, expirace, i_prosly=True)
    if ulozene is not None:
        vysledek.registry_cz, vysledek.registry_eu = ulozene
        vysledek.z_prosle_cache = True
        vysledek.chyby.append(
            "Použita starší uložená kopie trusted listu — výsledky nemusí "
            "odrážet aktuální stav kvalifikovaných služeb."
        )
        return vysledek

    try:
        stale_cache = _StaleTolerantTLCache(cache_dir, expire_after=expirace)
        vysledek.registry_cz, vysledek.registry_eu = asyncio.run(
            _nacti(stale_cache, uzemi_razitek)
        )
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
