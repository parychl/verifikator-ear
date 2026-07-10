"""Datový model výsledků kontroly — terminologie dle Metodiky k Verifikátoru podpisů (MMR, 20. 11. 2025)."""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


class Vysledek(enum.Enum):
    PLATNY = "Platný"
    NEPLATNY = "Neplatný"
    TECHNICKY_NEVYHOVUJICI = "Technicky nevyhovující"
    # podpis platný a kvalifikovaný, ale ve starším formátu (Adobe PKCS#7
    # místo PAdES) — metodika MMR ho může vyhodnotit jako nesprávně vytvořený
    ZASTARALY_STANDARD = "Zastaralý standard podpisu"
    CHYBA_ZPRACOVANI = "Chyba zpracování"


class KodChyby(enum.Enum):
    # kapitola 4.1 metodiky
    CHYBI_CASOVE_RAZITKO = "Chybí časové razítko"
    CASOVE_RAZITKO_NEPLATNE = "Časové razítko je neplatné"
    PODPIS_NENI_EAR = "Podpis nebyl vytvořen elektronickým autorizačním razítkem (EAR)"
    PODPIS_NENALEZEN = "Podpis nebo pečeť nebyly nalezeny"
    PODPIS_NESPRAVNY_ZPUSOB = "Podpis EAR nebyl vytvořen správným způsobem"
    EAR_PORUSENE = "Elektronické autorizační razítko (EAR) je porušené"
    CERT_NENI_KVALIFIKOVANY = "Podpis není založen na kvalifikovaném certifikátu"
    # kapitola 4.2
    DOKUMENT_UZAMCEN = "Dokument je uzamčen"
    # upozornění nad rámec metodiky (nejde o chybu podpisu)
    ZASTARALY_STANDARD_PODPISU = "Podpis je vytvořen zastaralým standardem (Adobe PKCS#7)"


DOPORUCENI: dict[KodChyby, str] = {
    KodChyby.CHYBI_CASOVE_RAZITKO: (
        "Objednat kvalifikované časové razítko u certifikační autority a nastavit "
        "jeho automatické připojení při podpisu."
    ),
    KodChyby.CASOVE_RAZITKO_NEPLATNE: (
        "Objednat si kvalifikované časové razítko u certifikační autority."
    ),
    KodChyby.PODPIS_NENI_EAR: (
        "Objednat si u certifikační autority EAR a v nástroji pro podepisování používat EAR."
    ),
    KodChyby.PODPIS_NENALEZEN: (
        "Objednat si u certifikační autority EAR a dokumenty jím podepisovat."
    ),
    KodChyby.PODPIS_NESPRAVNY_ZPUSOB: (
        "Zkontrolovat nastavení nástroje nebo postupu, který byl použit pro vytvoření podpisu."
    ),
    KodChyby.EAR_PORUSENE: (
        "Nejprve spojit nepodepsané dokumenty, až poté je podepsat."
    ),
    KodChyby.CERT_NENI_KVALIFIKOVANY: (
        "Podepisovat kvalifikovaným certifikátem (EAR) vydaným kvalifikovanou certifikační autoritou."
    ),
    KodChyby.DOKUMENT_UZAMCEN: (
        "Při podpisu nezaškrtávat volbu „uzamknout dokument“."
    ),
    KodChyby.ZASTARALY_STANDARD_PODPISU: (
        "V nástroji pro podepisování nastavit formát podpisu CAdES "
        "(v Adobe Acrobatu: Předvolby → Podpisy → Vytvoření a vzhled → "
        "Výchozí formát podpisu → „Rovnocenné normě CAdES“) a dokument "
        "podepsat znovu."
    ),
}


@dataclass
class Chyba:
    kod: KodChyby
    detail: str = ""

    @property
    def doporuceni(self) -> str:
        return DOPORUCENI.get(self.kod, "")


@dataclass
class InfoEAR:
    """Údaje vyčtené ze subjektu certifikátu — náležitosti dle § 13 odst. 3 písm. b) zák. 360/1992 Sb."""
    je_ear: bool = False
    jmeno: str = ""
    cislo_autorizace: str = ""
    obor: str = ""
    komora: str = ""


@dataclass
class InfoRazitka:
    pritomno: bool = False
    cas: datetime | None = None
    tsa: str = ""
    podpis_platny: bool = False
    kvalifikovane: bool = False


@dataclass
class InfoPodpisu:
    pole: str = ""
    podepsal: str = ""
    vydavatel: str = ""
    cas_podpisu: datetime | None = None
    sub_filter: str = ""
    integrita: bool = False
    pokryva_dokument: bool = False
    duveryhodny_retezec: bool = False       # řetězec končí u kvalifikované CA z trusted listu
    duvod_neduvery: str = ""                # proč ověření řetězce selhalo (AdES sub-indikace)
    overen_k_razitku: bool = False          # certifikát expiroval; ověřeno k času QTS (LTV)
    kvalifikovany_cert: bool = False        # QCStatements: qcCompliance (+ qcSSCD)
    je_docasove_razitko: bool = False       # pole typu /DocTimeStamp
    ear: InfoEAR = field(default_factory=InfoEAR)
    razitko: InfoRazitka = field(default_factory=InfoRazitka)
    chyby: list[Chyba] = field(default_factory=list)
    varovani: list[Chyba] = field(default_factory=list)  # upozornění, ne chyby


@dataclass
class InfoPDFA:
    deklarovana: str = ""       # např. "PDF/A-3b", "" = nedeklaruje
    vyhovuje: bool | None = None  # None = plná validace neproběhla (chybí veraPDF)
    detail: str = ""


@dataclass
class VysledekSouboru:
    soubor: Path
    vysledek: Vysledek = Vysledek.NEPLATNY
    chyby: list[Chyba] = field(default_factory=list)
    podpisy: list[InfoPodpisu] = field(default_factory=list)
    pdfa: InfoPDFA = field(default_factory=InfoPDFA)
    uzamcen: bool = False
    poznamka: str = ""
