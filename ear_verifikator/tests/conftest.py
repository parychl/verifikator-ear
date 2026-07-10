import datetime
import sys
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def _vytvor_cert(subject_attrs: list[x509.NameAttribute]):
    """Self-signed certifikát + klíč (PEM)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name(subject_attrs)
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=365))
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=True,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return cert_pem, key_pem


ATTRS_OBYCEJNY = [
    x509.NameAttribute(NameOID.COMMON_NAME, "Jan Novák"),
    x509.NameAttribute(NameOID.COUNTRY_NAME, "CZ"),
]

ATTRS_EAR = [
    x509.NameAttribute(NameOID.COMMON_NAME, "Ing. Jan Novák"),
    x509.NameAttribute(NameOID.COUNTRY_NAME, "CZ"),
    x509.NameAttribute(
        NameOID.ORGANIZATION_NAME,
        "Česká komora autorizovaných inženýrů a techniků činných ve výstavbě",
    ),
    x509.NameAttribute(NameOID.TITLE, "autorizovaný inženýr v oboru pozemní stavby"),
    x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, "číslo autorizace: 0012345"),
]


def _signer(tmp_path: Path, attrs, jmeno: str):
    from pyhanko.sign import signers

    cert_pem, key_pem = _vytvor_cert(attrs)
    cert_f = tmp_path / f"{jmeno}_cert.pem"
    key_f = tmp_path / f"{jmeno}_key.pem"
    cert_f.write_bytes(cert_pem)
    key_f.write_bytes(key_pem)
    return signers.SimpleSigner.load(str(key_f), str(cert_f), ca_chain_files=())


@pytest.fixture(scope="session")
def blank_pdf(tmp_path_factory) -> Path:
    """Minimální jednostránkové PDF bez podpisu."""
    import io

    from pyhanko.pdf_utils import generic, writer

    w = writer.PdfFileWriter()
    page = generic.DictionaryObject(
        {
            generic.pdf_name("/Type"): generic.pdf_name("/Page"),
            generic.pdf_name("/MediaBox"): generic.ArrayObject(
                [generic.NumberObject(x) for x in (0, 0, 595, 842)]
            ),
        }
    )
    w.insert_page(page)
    out = io.BytesIO()
    w.write(out)
    cesta = tmp_path_factory.mktemp("pdfs") / "blank.pdf"
    cesta.write_bytes(out.getvalue())
    return cesta


def _podepis(blank_pdf: Path, cil: Path, signer, certify=False, docmdp=None):
    from pyhanko.sign import signers
    from pyhanko.sign.fields import SigSeedSubFilter

    meta = signers.PdfSignatureMetadata(
        field_name="Signature1",
        subfilter=SigSeedSubFilter.PADES,
        certify=certify,
        docmdp_permissions=docmdp,
    )
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter

    with blank_pdf.open("rb") as inf:
        w = IncrementalPdfFileWriter(inf)
        with cil.open("wb") as outf:
            signers.sign_pdf(w, meta, signer=signer, output=outf)
    return cil


@pytest.fixture(scope="session")
def podepsany_pdf(blank_pdf, tmp_path_factory) -> Path:
    """PDF podepsané obyčejným self-signed certifikátem (bez EAR náležitostí)."""
    tmp = tmp_path_factory.mktemp("sig")
    signer = _signer(tmp, ATTRS_OBYCEJNY, "plain")
    return _podepis(blank_pdf, tmp / "signed_plain.pdf", signer)


@pytest.fixture(scope="session")
def podepsany_ear_pdf(blank_pdf, tmp_path_factory) -> Path:
    """PDF podepsané certifikátem s náležitostmi EAR v subjektu."""
    tmp = tmp_path_factory.mktemp("sig_ear")
    signer = _signer(tmp, ATTRS_EAR, "ear")
    return _podepis(blank_pdf, tmp / "signed_ear.pdf", signer)


@pytest.fixture(scope="session")
def uzamceny_pdf(blank_pdf, tmp_path_factory) -> Path:
    """PDF s certifikačním podpisem DocMDP „žádné změny“ (uzamčeno)."""
    from pyhanko.sign.fields import MDPPerm

    tmp = tmp_path_factory.mktemp("sig_lock")
    signer = _signer(tmp, ATTRS_EAR, "lock")
    return _podepis(
        blank_pdf, tmp / "locked.pdf", signer, certify=True, docmdp=MDPPerm.NO_CHANGES
    )


@pytest.fixture(scope="session")
def prazdna_spec():
    """Validační zdroje s prázdným trusted listem (nic není kvalifikované)."""
    from ear_verifikator.core.signature import sestav_validacni_zdroje
    from pyhanko.sign.validation.qualified.tsp import TSPRegistry

    return sestav_validacni_zdroje(TSPRegistry())


def nacti_asn1_cert(attrs):
    from asn1crypto import x509 as asn1_x509
    from cryptography import x509 as c_x509

    cert_pem, _ = _vytvor_cert(attrs)
    cert = c_x509.load_pem_x509_certificate(cert_pem)
    return asn1_x509.Certificate.load(cert.public_bytes(serialization.Encoding.DER))
