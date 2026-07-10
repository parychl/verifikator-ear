from ear_verifikator.core.model import (
    InfoEAR,
    InfoPodpisu,
    InfoRazitka,
    KodChyby,
    Vysledek,
)
from ear_verifikator.core.verifier import Verifikator, _chyby_podpisu


def _verifikator(spec):
    return Verifikator(spec, verapdf=[])  # prázdný příkaz = veraPDF nepoužívat


def _plny_podpis(sub_filter: str) -> InfoPodpisu:
    """Jinak zcela platný podpis EAR s daným SubFilterem."""
    return InfoPodpisu(
        pole="Sig1",
        sub_filter=sub_filter,
        integrita=True,
        pokryva_dokument=True,
        duveryhodny_retezec=True,
        kvalifikovany_cert=True,
        ear=InfoEAR(True, "Ing. Test", "0012345", "pozemní stavby", "ČKAIT"),
        razitko=InfoRazitka(pritomno=True, podpis_platny=True, kvalifikovane=True),
    )


def test_zastaraly_subfilter_je_varovani():
    chyby, varovani = _chyby_podpisu(_plny_podpis("/adbe.pkcs7.detached"), False)
    assert chyby == []  # není to chyba…
    assert [v.kod for v in varovani] == [KodChyby.ZASTARALY_STANDARD_PODPISU]


def test_pades_subfilter_bez_varovani():
    chyby, varovani = _chyby_podpisu(_plny_podpis("/ETSI.CAdES.detached"), False)
    assert chyby == [] and varovani == []


def test_neznamy_subfilter_je_chyba():
    chyby, _ = _chyby_podpisu(_plny_podpis(""), False)
    assert KodChyby.PODPIS_NESPRAVNY_ZPUSOB in {ch.kod for ch in chyby}


def test_nepodepsane_pdf(blank_pdf, prazdna_spec):
    v = _verifikator(prazdna_spec).zkontroluj(blank_pdf)
    assert v.vysledek == Vysledek.NEPLATNY
    assert KodChyby.PODPIS_NENALEZEN in {ch.kod for ch in v.chyby}


def test_podepsane_bez_ear(podepsany_pdf, prazdna_spec):
    v = _verifikator(prazdna_spec).zkontroluj(podepsany_pdf)
    assert v.vysledek == Vysledek.NEPLATNY
    kody = {ch.kod for ch in v.chyby}
    # obyčejný self-signed cert: není EAR, není kvalifikovaný, chybí razítko
    assert KodChyby.PODPIS_NENI_EAR in kody
    assert KodChyby.CERT_NENI_KVALIFIKOVANY in kody
    assert KodChyby.CHYBI_CASOVE_RAZITKO in kody
    assert KodChyby.PODPIS_NENALEZEN not in kody


def test_ear_subjekt_rozpoznan_v_pdf(podepsany_ear_pdf, prazdna_spec):
    v = _verifikator(prazdna_spec).zkontroluj(podepsany_ear_pdf)
    kody = {ch.kod for ch in v.chyby}
    # subjekt má náležitosti EAR → tato chyba odpadá; kvalifikovanost dál chybí
    assert KodChyby.PODPIS_NENI_EAR not in kody
    assert KodChyby.CERT_NENI_KVALIFIKOVANY in kody
    assert v.podpisy[0].ear.je_ear
    assert v.podpisy[0].ear.cislo_autorizace == "0012345"


def test_uzamceny_dokument(uzamceny_pdf, prazdna_spec):
    v = _verifikator(prazdna_spec).zkontroluj(uzamceny_pdf)
    assert v.uzamcen
    # podpis má i jiné vady → celkově Neplatný, ale uzamčení je mezi chybami
    assert KodChyby.DOKUMENT_UZAMCEN in {ch.kod for ch in v.chyby}


def test_integrita_poruseneho_souboru(podepsany_pdf, prazdna_spec, tmp_path):
    data = bytearray(podepsany_pdf.read_bytes())
    # přepsání bajtů uvnitř podepsané revize poruší digest podpisu
    idx = data.find(b"/MediaBox")
    assert idx != -1
    data[idx : idx + 9] = b"/mediaBOX"
    poskozeny = tmp_path / "poskozeny.pdf"
    poskozeny.write_bytes(bytes(data))
    v = _verifikator(prazdna_spec).zkontroluj(poskozeny)
    assert v.vysledek in (Vysledek.NEPLATNY, Vysledek.CHYBA_ZPRACOVANI)
    if v.vysledek == Vysledek.NEPLATNY and v.podpisy:
        assert KodChyby.EAR_PORUSENE in {ch.kod for ch in v.chyby}
