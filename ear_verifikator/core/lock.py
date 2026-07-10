"""Detekce uzamčení dokumentu (metodika kap. 4.2 — „Dokument je uzamčen“).

Dokument je uzamčen, pokud:
  - certifikační podpis nese DocMDP oprávnění „žádné změny“ (P = 1), nebo
  - podpisové pole zamyká všechna pole (FieldMDP ALL), nebo
  - PDF je šifrované s oprávněními, která znemožňují připojení dalšího podpisu.

Takový dokument nelze po nabytí právní moci opatřit ověřovací doložkou,
aniž by se zneplatnily (zlomily) předchozí podpisy.
"""
from __future__ import annotations

import logging

from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.sign.fields import FieldMDPAction, MDPPerm
from pyhanko.sign.validation.pdf_embedded import EmbeddedPdfSignature

log = logging.getLogger(__name__)

# bity /P dle ISO 32000-1, tab. 22 (číslováno od 1)
_BIT_ANNOTS_FORM_FILLING = 1 << (6 - 1)   # bit 6: komentáře + vyplňování polí
_BIT_FORM_FILLING = 1 << (9 - 1)          # bit 9: vyplňování polí (i podpisových)


def podpis_zamyka(sig: EmbeddedPdfSignature) -> bool:
    """True, pokud tento podpis uzamyká dokument proti dalším úpravám/podpisům."""
    try:
        if sig.docmdp_level == MDPPerm.NO_CHANGES:
            return True
    except Exception:
        log.debug("Nelze vyhodnotit DocMDP pro pole %s", sig.field_name, exc_info=True)
    try:
        fieldmdp = sig.fieldmdp
        if fieldmdp is not None and fieldmdp.action == FieldMDPAction.ALL:
            return True
    except Exception:
        log.debug("Nelze vyhodnotit FieldMDP pro pole %s", sig.field_name, exc_info=True)
    return False


def sifrovani_zamyka(reader: PdfFileReader) -> bool:
    """True, pokud šifrování PDF nedovoluje vyplnění podpisového pole."""
    if not reader.encrypted:
        return False
    try:
        p = int(reader.encrypt_dict.get("/P", -1))
    except Exception:
        return False
    # /P je 32bit se znaménkem; povolené akce mají bit = 1
    return not (p & _BIT_ANNOTS_FORM_FILLING or p & _BIT_FORM_FILLING)


def dokument_uzamcen(reader: PdfFileReader) -> bool:
    if sifrovani_zamyka(reader):
        return True
    return any(podpis_zamyka(s) for s in reader.embedded_regular_signatures)
