"""Orchestrace kontrol nad jedním PDF — EAR profil dle metodiky MMR.

Vyhodnocení:
  - Neplatný — žádný podpis neprošel všemi kontrolami EAR profilu
  - Technicky nevyhovující — podpisy v pořádku, ale dokument je uzamčen
  - Platný — alespoň jeden podpis splňuje vše (EAR + QTS, integrita, PAdES)

Formát PDF/A se dle metodiky do výsledku nezapočítává a reportuje se zvlášť.
"""
from __future__ import annotations

import logging
from pathlib import Path

from pyhanko.pdf_utils.misc import PdfReadError
from pyhanko.pdf_utils.reader import PdfFileReader

from . import cesty, lock, pdfa
from .model import (
    Chyba,
    InfoPodpisu,
    KodChyby,
    Vysledek,
    VysledekSouboru,
    nazev_zeme,
)
from .signature import (
    LEGACY_SUBFILTERS,
    PADES_SUBFILTERS,
    ValidacniZdroje,
    sestav_validacni_zdroje,
    zvaliduj_podpisy,
)

log = logging.getLogger(__name__)

__all__ = ["Verifikator", "sestav_validacni_zdroje"]


def _chyby_podpisu(
    p: InfoPodpisu, doc_ts_ok: bool
) -> tuple[list[Chyba], list[Chyba]]:
    """(chyby, varování) jednoho podpisu v terminologii metodiky (kap. 4.1).

    Varování nezneplatňují podpis — dnes jediné: zastaralý formát Adobe
    PKCS#7 (podpis je technicky platný, ale verifikátor v ISSŘ ho může
    vyhodnotit jako „nebyl vytvořen správným způsobem“).
    """
    chyby: list[Chyba] = []
    varovani: list[Chyba] = []
    if not p.integrita or not p.pokryva_dokument:
        chyby.append(
            Chyba(
                KodChyby.EAR_PORUSENE,
                "Podpis je kryptograficky porušený nebo nepokrývá celý dokument "
                "(typicky po spojení již podepsaných souborů).",
            )
        )
    if p.sub_filter in LEGACY_SUBFILTERS:
        varovani.append(
            Chyba(
                KodChyby.ZASTARALY_STANDARD_PODPISU,
                f"Podpis je platný, ale je vytvořen starším formátem Adobe "
                f"(SubFilter {p.sub_filter}) místo formátu PAdES "
                "(ETSI.CAdES.detached), který vyžaduje metodika MMR. "
                "Není to chyba podpisu, ale verifikátor v ISSŘ / Portálu "
                "stavebníka může takový dokument označit chybou „Podpis EAR "
                "nebyl vytvořen správným způsobem“.",
            )
        )
    elif p.sub_filter not in PADES_SUBFILTERS:
        chyby.append(
            Chyba(
                KodChyby.PODPIS_NESPRAVNY_ZPUSOB,
                f"Podpis není ve formátu PAdES (SubFilter {p.sub_filter or 'chybí'}); "
                "vyžadován ETSI.CAdES.detached.",
            )
        )
    if not p.duveryhodny_retezec:
        detail = (
            "Certifikát podpisu se nepodařilo ověřit proti kvalifikované CA "
            "z trusted listu"
            + (f" (důvod: {p.duvod_neduvery})" if p.duvod_neduvery else "")
            + ". Může jít i o dočasný výpadek ověřovací služby (OCSP/CRL) — "
            "zkuste kontrolu souboru zopakovat."
        )
        chyby.append(Chyba(KodChyby.CERT_NENI_KVALIFIKOVANY, detail))
    elif not p.kvalifikovany_cert:
        chyby.append(
            Chyba(
                KodChyby.CERT_NENI_KVALIFIKOVANY,
                "Certifikát podpisu není kvalifikovaný dle eIDAS "
                "(nevede na kvalifikovanou CA z trusted listu).",
            )
        )
    if not p.ear.je_ear:
        chyby.append(
            Chyba(
                KodChyby.PODPIS_NENI_EAR,
                "Certifikát neobsahuje náležitosti autorizačního razítka "
                "(označení Komory a číslo autorizace) dle § 13 odst. 3 písm. b) "
                "zák. č. 360/1992 Sb.",
            )
        )
    if not p.razitko.pritomno and not doc_ts_ok:
        chyby.append(Chyba(KodChyby.CHYBI_CASOVE_RAZITKO))
    elif p.razitko.pritomno and not p.razitko.kvalifikovane:
        napoveda = ""
        if p.razitko.zeme not in ("", "CZ", "SK"):
            napoveda = (
                f" Autorita razítka je ze země {nazev_zeme(p.razitko.zeme)} "
                f"({p.razitko.zeme}) — zapněte tlačítko „EU časová razítka“ "
                "a soubor zkontrolujte znovu."
            )
        chyby.append(
            Chyba(
                KodChyby.CASOVE_RAZITKO_NEPLATNE,
                "Časové razítko není platné kvalifikované razítko od TSA "
                "uvedené na trusted listu." + napoveda,
            )
        )
    elif p.razitko.kvalifikovane and p.razitko.zeme not in ("", "CZ"):
        zeme = nazev_zeme(p.razitko.zeme)
        varovani.append(
            Chyba(
                KodChyby.RAZITKO_JINE_ZEME,
                f"Razítko vydala autorita ze země {zeme} ({p.razitko.zeme}) — "
                f"{p.razitko.tsa}. Je kvalifikovaná podle evropského "
                "trusted listu (eIDAS), ale je možné, že ji oficiální "
                "verifikátor ISSŘ neuzná. Není to chyba podpisu.",
                doplnek=zeme,
            )
        )
    return chyby, varovani


class Verifikator:
    def __init__(
        self, zdroje: ValidacniZdroje | None, verapdf: list[str] | None = None
    ):
        """zdroje=None → trusted list není k dispozici, kontrola není možná.

        verapdf: příkaz veraPDF; None = najít automaticky, prázdný = nepoužívat.
        """
        self.zdroje = zdroje
        self.verapdf = verapdf if verapdf is not None else pdfa.najdi_verapdf()

    def zkontroluj(self, soubor: Path) -> VysledekSouboru:
        v = VysledekSouboru(soubor=soubor)
        try:
            # \\?\ prefix: dokumentace mívá cesty přes 260 znaků (MAX_PATH)
            with cesty.fs_cesta(soubor).open("rb") as f:
                reader = PdfFileReader(f, strict=False)
                self._zkontroluj_otevreny(soubor, reader, v)
        except (PdfReadError, OSError, ValueError) as e:
            v.vysledek = Vysledek.CHYBA_ZPRACOVANI
            v.poznamka = f"Soubor se nepodařilo zpracovat: {e}"
        except Exception as e:
            log.exception("Neočekávaná chyba při kontrole %s", soubor)
            v.vysledek = Vysledek.CHYBA_ZPRACOVANI
            v.poznamka = f"Neočekávaná chyba: {e}"
        return v

    def _zkontroluj_otevreny(
        self, soubor: Path, reader: PdfFileReader, v: VysledekSouboru
    ) -> None:
        v.pdfa = pdfa.zkontroluj_pdfa(soubor, reader, self.verapdf)
        v.uzamcen = lock.dokument_uzamcen(reader)

        if self.zdroje is None:
            v.vysledek = Vysledek.CHYBA_ZPRACOVANI
            v.poznamka = (
                "Trusted list není k dispozici — kvalifikovanost podpisů nelze ověřit."
            )
            return

        podpisy, doc_ts = zvaliduj_podpisy(reader, self.zdroje)
        v.podpisy = podpisy + doc_ts

        if not podpisy:
            v.vysledek = Vysledek.NEPLATNY
            v.chyby.append(Chyba(KodChyby.PODPIS_NENALEZEN))
            if v.uzamcen:
                v.chyby.append(Chyba(KodChyby.DOKUMENT_UZAMCEN))
            return

        # kvalifikované dokumentové razítko kryje i podpisy bez vlastního QTS
        doc_ts_ok = any(
            t.integrita and t.pokryva_dokument and t.razitko.kvalifikovane
            for t in doc_ts
        )

        for p in podpisy:
            p.chyby, p.varovani = _chyby_podpisu(p, doc_ts_ok)
        ciste = [p for p in podpisy if not p.chyby]

        if not ciste:  # ani jeden podpis bez chyb
            v.vysledek = Vysledek.NEPLATNY
            videne: set[KodChyby] = set()
            for p in podpisy:
                for ch in p.chyby + p.varovani:
                    if ch.kod not in videne:
                        videne.add(ch.kod)
                        v.chyby.append(ch)
            if v.uzamcen:
                v.chyby.append(Chyba(KodChyby.DOKUMENT_UZAMCEN))
        elif v.uzamcen:
            v.vysledek = Vysledek.TECHNICKY_NEVYHOVUJICI
            v.chyby.append(Chyba(KodChyby.DOKUMENT_UZAMCEN))
        else:
            # o šedém stavu rozhoduje jen zastaralý formát podpisu; ostatní
            # varování (např. zahraniční TSA) nechávají verdikt Platný a jen
            # se zobrazí jako upozornění
            zastaraly = all(
                any(ch.kod == KodChyby.ZASTARALY_STANDARD_PODPISU for ch in p.varovani)
                for p in ciste
            )
            v.vysledek = (
                Vysledek.ZASTARALY_STANDARD if zastaraly else Vysledek.PLATNY
            )
            videne = set()
            for p in ciste:
                for ch in p.varovani:
                    if ch.kod not in videne:
                        videne.add(ch.kod)
                        v.chyby.append(ch)
