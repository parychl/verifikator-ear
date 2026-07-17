"""Testy perzistence hotového registru trusted listů (pickle na disku)."""
import pickle
from datetime import datetime, timedelta, timezone

import pytest

from ear_verifikator.core import trusted_list
from ear_verifikator.core.trusted_list import (
    _dumps_registr,
    _nacti_ulozeny_registr,
    _soubor_registru,
    _uloz_registr,
    nacti_trusted_list,
)
from ear_verifikator.tests.conftest import ATTRS_OBYCEJNY, nacti_asn1_cert

UZEMI = frozenset({"CZ", "SK"})
EXPIRACE = timedelta(days=7)


def _maly_registr():
    from pyhanko.sign.validation.qualified.tsp import (
        CA_QC_URI,
        BaseServiceInformation,
        CAServiceInformation,
        TSPRegistry,
    )

    base = BaseServiceInformation(
        service_type=CA_QC_URI,
        service_name="Testovací CA",
        valid_from=datetime(2020, 1, 1, tzinfo=timezone.utc),
        valid_until=None,
        provider_certs=(nacti_asn1_cert(ATTRS_OBYCEJNY),),
        additional_info_certificate_type=frozenset(),
        other_additional_info=frozenset(),
    )
    reg = TSPRegistry()
    reg.register_ca(
        CAServiceInformation(
            base_info=base,
            qualifications=frozenset(),
            expired_certs_revocation_info=None,
        )
    )
    return reg


def _prepis_ulozene(soubor, **zmeny):
    """Přepíše vybraná pole v uloženém pickle (např. datum uložení)."""
    data = pickle.loads(soubor.read_bytes())
    data.update(zmeny)
    soubor.write_bytes(_dumps_registr(data))


@pytest.fixture
def ulozeny_registr(tmp_path):
    reg = _maly_registr()
    _uloz_registr(tmp_path, UZEMI, reg, reg)
    return tmp_path


def test_uloz_a_nacti(ulozeny_registr):
    nactene = _nacti_ulozeny_registr(ulozeny_registr, UZEMI, EXPIRACE)
    assert nactene is not None
    registry_cz, registry_eu = nactene
    assert len(list(registry_cz.known_certificate_authorities)) == 1
    assert len(list(registry_eu.known_certificate_authorities)) == 1


def test_lookup_cerstvym_klicem(ulozeny_registr):
    """Vyhledání podle nově zkonstruované autority (jako certifikát z PDF)."""
    from asn1crypto import x509
    from pyhanko_certvalidator.authority import AuthorityWithCert

    registry_cz, _ = _nacti_ulozeny_registr(ulozeny_registr, UZEMI, EXPIRACE)
    (autorita,) = registry_cz.known_certificate_authorities
    novy_klic = AuthorityWithCert(x509.Certificate.load(autorita.certificate.dump()))
    assert list(registry_cz.applicable_service_definitions(novy_klic, moment=None))


def test_ruzna_uzemi_maji_ruzne_soubory(tmp_path):
    assert _soubor_registru(tmp_path, UZEMI) != _soubor_registru(tmp_path, None)
    assert _soubor_registru(tmp_path, None).name == "registr_EU.pickle"


def test_chybejici_soubor(tmp_path):
    assert _nacti_ulozeny_registr(tmp_path, UZEMI, EXPIRACE) is None


def test_prosly_registr(ulozeny_registr):
    soubor = _soubor_registru(ulozeny_registr, UZEMI)
    _prepis_ulozene(
        soubor, ulozeno=datetime.now(timezone.utc) - timedelta(days=8)
    )
    assert _nacti_ulozeny_registr(ulozeny_registr, UZEMI, EXPIRACE) is None
    # offline fallback prošlou kopii vrátí
    assert (
        _nacti_ulozeny_registr(ulozeny_registr, UZEMI, EXPIRACE, i_prosly=True)
        is not None
    )


def test_jina_verze_formatu(ulozeny_registr):
    _prepis_ulozene(_soubor_registru(ulozeny_registr, UZEMI), verze=-1)
    assert _nacti_ulozeny_registr(ulozeny_registr, UZEMI, EXPIRACE) is None


def test_jine_verze_knihoven(ulozeny_registr):
    """Po upgradu pyHanko se registr zahodí a sestaví znovu."""
    _prepis_ulozene(
        _soubor_registru(ulozeny_registr, UZEMI), knihovny="pyhanko==0.0.0"
    )
    assert _nacti_ulozeny_registr(ulozeny_registr, UZEMI, EXPIRACE) is None


def test_poskozeny_soubor(tmp_path):
    _soubor_registru(tmp_path, UZEMI).write_bytes(b"toto neni pickle")
    assert _nacti_ulozeny_registr(tmp_path, UZEMI, EXPIRACE) is None


def test_cerstvy_registr_bez_site(tmp_path, monkeypatch):
    """Čerstvý uložený registr se použije bez jakéhokoli přístupu k síti."""
    reg = _maly_registr()
    _uloz_registr(tmp_path, UZEMI, reg, reg)

    async def _zadna_sit(*_a, **_kw):
        raise AssertionError("nemělo se sahat na síť")

    monkeypatch.setattr(trusted_list, "_nacti", _zadna_sit)
    vysledek = nacti_trusted_list(tmp_path, uzemi_razitek=UZEMI)
    assert vysledek.registry_cz is not None
    assert not vysledek.z_prosle_cache
    assert not vysledek.chyby


def test_prosly_registr_pri_vypadku_site(tmp_path, monkeypatch):
    """Síť nedostupná + prošlý registr → použije se s upozorněním."""
    reg = _maly_registr()
    _uloz_registr(tmp_path, UZEMI, reg, reg)
    _prepis_ulozene(
        _soubor_registru(tmp_path, UZEMI),
        ulozeno=datetime.now(timezone.utc) - timedelta(days=8),
    )

    async def _sit_nedostupna(*_a, **_kw):
        raise OSError("síť nedostupná")

    monkeypatch.setattr(trusted_list, "_nacti", _sit_nedostupna)
    vysledek = nacti_trusted_list(tmp_path, uzemi_razitek=UZEMI)
    assert vysledek.registry_cz is not None
    assert vysledek.z_prosle_cache
    assert any("starší uložená kopie" in ch for ch in vysledek.chyby)
