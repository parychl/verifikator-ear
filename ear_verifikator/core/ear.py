"""Rozpoznání elektronického autorizačního razítka (EAR) z certifikátu.

Dle § 13 odst. 3 písm. b) zák. č. 360/1992 Sb. musí kvalifikovaný certifikát
obsahovat: jméno autorizované osoby, číslo v seznamu autorizovaných osob
vedeném Komorou, obor (příp. specializaci) a označení Komory.

Certifikační autority (I.CA, PostSignum) tyto údaje vkládají do subjektu
certifikátu (CN, title, O, OU, pseudonym…), formát se mezi CA mírně liší —
parsování je proto záměrně tolerantní: prohledávají se všechny textové
hodnoty subjektu.
"""
from __future__ import annotations

import re

from asn1crypto import x509 as asn1_x509

from .model import InfoEAR

# označení komor dle autorizačního zákona
_KOMORY = [
    (re.compile(r"česk[áé]\s+komo[rř][aey]\s+autorizovaných\s+inženýrů", re.I), "ČKAIT"),
    (re.compile(r"\bČKAIT\b", re.I), "ČKAIT"),
    (re.compile(r"česk[áé]\s+komo[rř][aey]\s+architekt", re.I), "ČKA"),
    (re.compile(r"\bČKA\b"), "ČKA"),
]

# číslo autorizace: ČKAIT 7 číslic (např. 0011223), ČKA zpravidla 5 číslic
_CISLO_AUTORIZACE = re.compile(
    r"(?:číslo\s+autorizace|autorizace\s*(?:č\.|číslo)?|evidenční\s+číslo)\s*[:\-]?\s*(\d{4,8})",
    re.I,
)
_CISLO_SAMOSTATNE = re.compile(r"\b(\d{7})\b")

# obor: text za „v oboru“ / „pro obor“, nebo celé označení autorizace
_OBOR = re.compile(r"(?:v\s+oboru|pro\s+obor[uy]?)\s+(.+)", re.I)
_AUTORIZOVANY = re.compile(
    r"(autorizovan[ýá]\s+(?:inženýr|technik|stavitel|architekt)[^,;]*)", re.I
)


def _hodnoty_subjektu(cert: asn1_x509.Certificate) -> list[str]:
    hodnoty: list[str] = []
    for rdn in cert.subject.native.values():
        if isinstance(rdn, list):
            hodnoty.extend(str(v) for v in rdn)
        else:
            hodnoty.append(str(rdn))
    return hodnoty


def rozpoznat_ear(cert: asn1_x509.Certificate) -> InfoEAR:
    """Vytáhne ze subjektu certifikátu náležitosti EAR a vyhodnotí, zda jde o EAR."""
    info = InfoEAR()
    hodnoty = _hodnoty_subjektu(cert)
    text = " | ".join(hodnoty)

    subj = cert.subject.native
    info.jmeno = str(subj.get("common_name", ""))

    for vzor, zkratka in _KOMORY:
        if vzor.search(text):
            info.komora = zkratka
            break

    m = _CISLO_AUTORIZACE.search(text)
    if m:
        info.cislo_autorizace = m.group(1)
    else:
        m = _CISLO_SAMOSTATNE.search(text)
        if m and info.komora:
            info.cislo_autorizace = m.group(1)

    m = _OBOR.search(text)
    if m:
        info.obor = m.group(1).strip(" |,;")
    else:
        m = _AUTORIZOVANY.search(text)
        if m:
            info.obor = m.group(1).strip(" |,;")

    # EAR = subjekt nese označení Komory a číslo autorizace
    info.je_ear = bool(info.komora and info.cislo_autorizace)
    return info


def ma_qc_statements(cert: asn1_x509.Certificate) -> bool:
    """Informativní kontrola qcCompliance v QCStatements (pro režim bez trusted listu)."""
    try:
        for ext in cert["tbs_certificate"]["extensions"]:
            if ext["extn_id"].native == "qc_statements":
                for stmt in ext["extn_value"].parsed:
                    if stmt["statement_id"].native in (
                        "0.4.0.1862.1.1",  # id-etsi-qcs-QcCompliance
                        "qc_compliance",
                    ):
                        return True
    except (ValueError, KeyError):
        pass
    return False
