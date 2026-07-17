"""Validace elektronických podpisů v PDF přes pyHanko.

Důvěra je rozdělená na dva zdroje:
  - certifikáty podpisů (EAR) se ověřují proti českému trusted listu,
  - certifikáty časových razítek proti trusted listům celé EU — kvalifikovaná
    TSA může být z kteréhokoli členského státu (eIDAS je celoevropské).

Kvalifikovanost certifikátů se posuzuje QualificationAssessorem nad validační
cestou — stejná logika, jakou pyHanko používá ve svém AdES flow
(_qualification_analysis). Záměrně nepoužíváme ades_lta_validation: ta
vyžaduje podpisové časové razítko a dokument opatřený pouze dokumentovým
razítkem (běžná praxe u české dokumentace) by neprošel posouzením
kvalifikovanosti vůbec.
"""
from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass, field

from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.sign.validation import (
    SignatureCoverageLevel,
    validate_pdf_signature,
    validate_pdf_timestamp,
)
from pyhanko.sign.validation.pdf_embedded import EmbeddedPdfSignature
from pyhanko.sign.validation.qualified.assess import QualificationAssessor
from pyhanko.sign.validation.qualified.tsp import TSPRegistry, TSPTrustManager
from pyhanko_certvalidator.authority import TrustedServiceType
from pyhanko_certvalidator.context import ValidationContext
from pyhanko_certvalidator.fetchers import Fetchers
from pyhanko_certvalidator.fetchers.requests_fetchers import (
    RequestsFetcherBackend,
)

from .ear import rozpoznat_ear
from .model import InfoPodpisu, InfoRazitka

log = logging.getLogger(__name__)

PADES_SUBFILTERS = {"/ETSI.CAdES.detached"}
# starší podpisové formáty Adobe — technicky platné, ale mimo PAdES profil
LEGACY_SUBFILTERS = {"/adbe.pkcs7.detached", "/adbe.pkcs7.sha1"}
DOCTS_SUBFILTER = "/ETSI.RFC3161"


class TrustManagerRazitek(TSPTrustManager):
    """TSPTrustManager rozšířený o kotvy razítkových služeb (QTST).

    Vestavěný manager nabízí jako kotvy důvěry jen certifikační autority
    (CA/QC). Řada evropských TSA ale na trusted listu figuruje pouze jako
    QTST služba (např. italské Ministero della Difesa) a jejich razítka by
    se bez tohoto rozšíření vůbec nedala ukotvit. České TSA fungují
    i bez něj jen proto, že jejich jednotky vydávají CA vedené zároveň
    jako CA/QC.
    """

    def find_potential_issuers(self, cert):
        for authority in itertools.chain(
            self.tsp_registry.known_certificate_authorities,
            self.tsp_registry.known_timestamp_authorities,
        ):
            as_anchor = self.as_trust_anchor(authority)
            if as_anchor and authority.is_potential_issuer_of(cert):
                yield as_anchor


@dataclass
class ValidacniZdroje:
    """Sdílené zdroje důvěry pro validaci.

    ``registry``/``trust_manager``/``assessor`` — český trusted list (podpisy),
    ``*_razitek`` — trusted listy celé EU (časová razítka).
    """

    registry: TSPRegistry
    trust_manager: TSPTrustManager
    assessor: QualificationAssessor
    registry_razitek: TSPRegistry
    trust_manager_razitek: TrustManagerRazitek
    assessor_razitek: QualificationAssessor
    _fetchery: Fetchers | None = field(default=None, init=False, repr=False)

    def zacni_davku(self) -> None:
        """Nová dávka souborů → čerstvá síťová cache (OCSP/CRL/certifikáty)."""
        self._fetchery = None

    def _sdilene_fetchery(self) -> Fetchers:
        # Síťová cache sdílená přes dávku: stejné razítko na stovkách souborů
        # se ověřuje online jen jednou (rychlost + odolnost proti výpadkům
        # OCSP). Stav validace se ale nesdílí — každý podpis dostává čerstvý
        # ValidationContext.
        if self._fetchery is None:
            self._fetchery = RequestsFetcherBackend().get_fetchers()
        return self._fetchery

    def novy_kontext(self) -> ValidationContext:
        # revocation_mode="soft-fail": nedostupnost CRL/OCSP nezneplatní
        # podpis, aby kontrola fungovala i bez internetu. Důsledek: odvolání
        # (revokaci) certifikátu nelze při výpadku sítě ověřit a podpis
        # s odvolaným certifikátem může projít — viz Omezení v README.
        return ValidationContext(
            trust_manager=self.trust_manager,
            allow_fetching=True,
            revocation_mode="soft-fail",
            fetchers=self._sdilene_fetchery(),
        )

    def kontext_razitek(self) -> ValidationContext:
        """Kontext pro certifikáty časových razítek (důvěra celé EU)."""
        return ValidationContext(
            trust_manager=self.trust_manager_razitek,
            allow_fetching=True,
            revocation_mode="soft-fail",
            fetchers=self._sdilene_fetchery(),
        )

    def kontext_k_okamziku(self, okamzik) -> ValidationContext:
        """Kontext pro ověření k času časového razítka (expirovaný certifikát).

        retroactive_revinfo: revokační data vydaná až po tomto okamžiku se
        přijímají — bez toho by čerstvá OCSP odpověď byla „příliš nová“.
        """
        return ValidationContext(
            trust_manager=self.trust_manager,
            allow_fetching=True,
            revocation_mode="soft-fail",
            fetchers=self._sdilene_fetchery(),
            moment=okamzik,
            retroactive_revinfo=True,
        )


def sestav_validacni_zdroje(
    registry: TSPRegistry, registry_razitek: TSPRegistry | None = None
) -> ValidacniZdroje:
    """registry = český trusted list; registry_razitek = celá EU (razítka).

    Bez druhého registru se razítka posuzují proti prvnímu (testy, nouzový
    režim).
    """
    if registry_razitek is None:
        registry_razitek = registry
    tm = TSPTrustManager(tsp_registry=registry)
    tm_razitek = TrustManagerRazitek(tsp_registry=registry_razitek)
    return ValidacniZdroje(
        registry=registry,
        trust_manager=tm,
        assessor=QualificationAssessor(registry),
        registry_razitek=registry_razitek,
        trust_manager_razitek=tm_razitek,
        assessor_razitek=QualificationAssessor(registry_razitek),
    )


def _je_kvalifikovany(
    assessor: QualificationAssessor, validation_path
) -> bool:
    """Posouzení kvalifikovanosti dle trusted listu (zrcadlí pyHanko _qualification_analysis)."""
    if validation_path is None:
        return False
    try:
        vysledek = assessor.check_entity_cert_qualified(validation_path)
        return bool(vysledek.status.qualified)
    except Exception as e:
        log.debug("Posouzení kvalifikovanosti selhalo: %s", e)
        return False


def _kvalifikovane_razitko(zdroje: ValidacniZdroje, ts_status) -> bool:
    """Kvalifikovanost časového razítka (ETSI TS 119 615).

    Razítko je kvalifikované, pokud jeho jednotka (TSU) kotví ve službě
    typu QTST platné v čase razítka. Certifikátové posouzení (QCStatements)
    slouží jako doplněk pro TSU zakotvené přes CA/QC (typicky české TSA,
    jejichž TSU certifikáty QCStatements nesou).
    """
    cesta = getattr(ts_status, "validation_path", None)
    if cesta is None:
        return False
    try:
        quals = cesta.trust_anchor.trust_qualifiers
        if (
            quals.trusted_service_type
            == TrustedServiceType.TIME_STAMPING_AUTHORITY
        ):
            cas = getattr(ts_status, "timestamp", None)
            od, do = quals.valid_from, quals.valid_until
            if cas is None:
                return True
            if od is not None and cas < od:
                return False
            if do is not None and cas > do:
                return False
            return True
    except Exception as e:
        log.debug("Posouzení kotvy razítka selhalo: %s", e)
    return _je_kvalifikovany(zdroje.assessor_razitek, cesta)


def _info_razitka(zdroje: ValidacniZdroje, ts_status) -> InfoRazitka:
    razitko = InfoRazitka(pritomno=ts_status is not None)
    if ts_status is None:
        return razitko
    razitko.cas = getattr(ts_status, "timestamp", None)
    cert = getattr(ts_status, "signing_cert", None)
    if cert is not None:
        razitko.tsa = cert.subject.native.get("common_name", "")
        razitko.zeme = str(cert.subject.native.get("country_name", "") or "")
    razitko.podpis_platny = bool(
        ts_status.intact and ts_status.valid and ts_status.trusted
    )
    razitko.kvalifikovane = razitko.podpis_platny and _kvalifikovane_razitko(
        zdroje, ts_status
    )
    return razitko


def _spolecne_udaje(sig: EmbeddedPdfSignature) -> InfoPodpisu:
    info = InfoPodpisu(pole=sig.field_name or "")
    info.je_docasove_razitko = str(sig.sig_object_type) == "/DocTimeStamp"
    info.sub_filter = str(sig.sig_object.get("/SubFilter", ""))
    cert = sig.signer_cert
    if cert is not None:
        info.podepsal = cert.subject.native.get("common_name", "")
        info.vydavatel = cert.issuer.native.get("common_name", "")
        info.ear = rozpoznat_ear(cert)
    info.cas_podpisu = sig.self_reported_timestamp
    return info


def _vypln_stav(
    info: InfoPodpisu, status, assessor: QualificationAssessor
) -> None:
    info.integrita = bool(status.intact and status.valid)
    info.pokryva_dokument = getattr(status, "coverage", None) in (
        SignatureCoverageLevel.ENTIRE_REVISION,
        SignatureCoverageLevel.ENTIRE_FILE,
    )
    info.duveryhodny_retezec = bool(status.trusted)
    if not info.duveryhodny_retezec:
        duvod = getattr(status, "trust_problem_indic", None)
        info.duvod_neduvery = getattr(duvod, "name", str(duvod)) if duvod else ""
    info.kvalifikovany_cert = _je_kvalifikovany(
        assessor, getattr(status, "validation_path", None)
    )


def zvaliduj_podpis(
    sig: EmbeddedPdfSignature, zdroje: ValidacniZdroje
) -> InfoPodpisu:
    info = _spolecne_udaje(sig)
    try:
        status = validate_pdf_signature(
            sig,
            signer_validation_context=zdroje.novy_kontext(),
            ts_validation_context=zdroje.kontext_razitek(),
        )
    except Exception as e:
        log.warning("Validace podpisu %s selhala: %s", info.pole, e, exc_info=True)
        info.integrita = False
        return info
    _vypln_stav(info, status, zdroje.assessor)
    info.razitko = _info_razitka(zdroje, getattr(status, "timestamp_validity", None))
    _zkus_overit_k_razitku(sig, info, zdroje)
    return info


def _zkus_overit_k_razitku(
    sig: EmbeddedPdfSignature, info: InfoPodpisu, zdroje: ValidacniZdroje
) -> None:
    """Certifikát už expiroval, ale kvalifikované razítko dokládá, že podpis
    vznikl v době jeho platnosti → ověřit k historickému okamžiku (jako EU DSS
    v ISSŘ).

    Zkouší se konec platnosti certifikátu (novější záznamy trusted listu už
    v tu dobu platí; podpis tehdy prokazatelně existoval — razítko je starší)
    a čas razítka samotného.
    """
    if (
        info.duveryhodny_retezec
        or info.duvod_neduvery != "OUT_OF_BOUNDS_NO_POE"
        or not info.razitko.kvalifikovane
        or info.razitko.cas is None
    ):
        return
    from datetime import timedelta

    konec_platnosti = sig.signer_cert.not_valid_after - timedelta(minutes=1)
    for okamzik in (konec_platnosti, info.razitko.cas):
        if okamzik < info.razitko.cas:
            continue  # podpis musí v daném okamžiku prokazatelně existovat
        try:
            status = validate_pdf_signature(
                sig,
                signer_validation_context=zdroje.kontext_k_okamziku(okamzik),
                ts_validation_context=zdroje.kontext_razitek(),
            )
        except Exception as e:
            log.debug("Ověření k okamžiku %s selhalo: %s", okamzik, e)
            continue
        if status.trusted:
            _vypln_stav(info, status, zdroje.assessor)
            info.overen_k_razitku = True
            info.duvod_neduvery = ""
            return


def zvaliduj_docasove_razitko(
    sig: EmbeddedPdfSignature, zdroje: ValidacniZdroje
) -> InfoPodpisu:
    info = _spolecne_udaje(sig)
    try:
        status = validate_pdf_timestamp(sig, zdroje.kontext_razitek())
    except Exception as e:
        log.warning(
            "Validace dokumentového razítka %s selhala: %s", info.pole, e, exc_info=True
        )
        info.integrita = False
        return info
    _vypln_stav(info, status, zdroje.assessor_razitek)
    info.razitko = _info_razitka(zdroje, status)
    return info


def zvaliduj_podpisy(
    reader: PdfFileReader, zdroje: ValidacniZdroje
) -> tuple[list[InfoPodpisu], list[InfoPodpisu]]:
    """Vrátí (běžné podpisy, dokumentová časová razítka)."""
    podpisy = [zvaliduj_podpis(s, zdroje) for s in reader.embedded_regular_signatures]
    doc_ts = [
        zvaliduj_docasove_razitko(s, zdroje)
        for s in reader.embedded_timestamp_signatures
    ]
    return podpisy, doc_ts
