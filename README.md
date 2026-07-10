# Verifikátor EAR

Desktopový nástroj pro hromadnou kontrolu elektronických autorizačních razítek (EAR)
v PDF dokumentaci **před podáním** do Portálu stavebníka. Vyhodnocuje soubory podle
Metodiky k Verifikátoru podpisů (MMR, 20. 11. 2025) — stejná terminologie výsledků
i chyb, jakou uvidí úředník v ISSŘ.

## Upozornění
**Nejedná se o oficiální aplikaci. Je to pouze nástroj pro projektanty, aby si ušetřili práci s kontrolou před podáním na úřad přes Portál stavebníka.
Za případné chyby a rozdíly s oficiálním reportem ISSŘ na sebe neberu žádnou zodpovědnost.**


## Stažení hotové aplikace

Hotovou aplikaci pro Windows (bez nutnosti instalovat Python či Javu) stáhnete
jako `Verifikator_EAR.zip` z [nejnovějšího vydání](../../releases/latest).
Postup instalace je v přiloženém `Navod_instalace.txt`.

## Co se kontroluje

| Kontrola | Právní základ |
|----------|---------------|
| Přítomnost elektronického podpisu/pečeti | — |
| Kvalifikovaný podpis (certifikát od kvalifikované CA dle EU Trusted Listu) | nařízení (EU) č. 910/2014 (eIDAS) |
| Elektronické autorizační razítko — jméno, číslo autorizace, obor, Komora v certifikátu | § 13 odst. 3 písm. b) zák. č. 360/1992 Sb. |
| Kvalifikované časové razítko (TSA na Trusted Listu) | tamtéž |
| Integrita podpisu, pokrytí celého dokumentu, formát PAdES | eIDAS / ETSI |
| Uzamčení dokumentu (DocMDP „žádné změny“, FieldMDP, šifrování) | metodika kap. 4.2 |
| Formát PDF/A-3: deklarace v XMP + skutečná shoda (veraPDF) | vyhláška č. 190/2024 Sb. |

Výsledek na soubor: **Platný** / **Neplatný** / **Technicky nevyhovující** (uzamčen).
Formát PDF/A se dle metodiky do výsledku nezapočítává a zobrazuje se zvlášť.

## Pro uživatele (hotová aplikace)

1. Rozbal `Verifikator_EAR.zip` do libovolné složky (např. `C:\Programy\Verifikator EAR`).
2. Spusť `Verifikator EAR.exe`.
3. Při prvním spuštění aplikace nabídne vytvoření zástupce na ploše
   a v nabídce Start — pak jde spouštět jako běžný program (a připnout
   na hlavní panel).

Není potřeba instalovat Python, Javu ani nic dalšího. Před první kontrolou
se aplikace zeptá, zda smí stáhnout PDF/A validátor veraPDF — bez něj se
formát PDF/A neověřuje (kontroluje se jen deklarace v metadatech); ostatní
kontroly fungují beze změny. Data aplikace (trusted list, PDF/A validátor,
nastavení) se ukládají do `%USERPROFILE%\.ear_verifikator`.
Odinstalace = smazat složku aplikace, zástupce a `.ear_verifikator`.

### Sestavení balíčku (pro vývojáře)

Spusť `build_app.bat` (PyInstaller; `--collect-all signxml` je nutné —
signxml načítá XSD schémata dynamicky a bez něj selže parsování trusted
listu). Výsledek je ve `dist\Verifikator EAR\` — tuto složku zabal do ZIPu.
Diagnostika sestavené aplikace bez GUI:
`"Verifikator EAR.exe" --overit soubor.pdf --vystup report.txt`

## Instalace (vývojové prostředí)

Z kořenové složky projektu (obsahuje balíček `ear_verifikator` a `launcher.py`):

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

Plnou validaci PDF/A zajišťuje [veraPDF](https://verapdf.org/) — **není potřeba
nic instalovat**: při prvním použití se aplikace zeptá a po odsouhlasení si ho
stáhne (~10 MB z Maven Central) do `~/.ear_verifikator/verapdf`; pokud v systému
není Java, přibalí si i přenosné JRE Temurin (~45 MB, jednorázově). Stahované
soubory jsou připnuté na konkrétní verzi a ověřují se proti otisku SHA-256.
Průběh stahování je vidět ve stavovém řádku. Bez validátoru (odmítnuto či bez
připojení) se kontroluje pouze deklarovaná úroveň PDF/A v metadatech.

## Spuštění

Z kořenové složky projektu:

```
.venv\Scripts\python -m ear_verifikator.gui.app
```

PDF soubory nebo celé složky lze přetáhnout do okna myší, případně vybrat tlačítky.
Kliknutím na řádek se zobrazí detail: jednotlivé podpisy, údaje EAR (jméno, číslo
autorizace, obor, Komora), časová razítka a doporučení k nápravě převzatá z metodiky.

## Export výsledků

Tlačítko **Exportovat…** nabízí rozsah *všechny výsledky* / *jen platné* /
*jen neplatné a s výhradami* a formáty **XLSX** (barevně odlišené výsledky,
filtr), **CSV** (středníky, UTF-8 s BOM — otevře se správně v českém Excelu)
a **TXT** (čitelný report vhodný k odeslání zpracovateli e-mailem).
Export obsahuje všechny kontrolované údaje včetně plného popisu chyb
a doporučení k nápravě. Hodnoty pocházející z certifikátů se při exportu
neutralizují, aby je Excel nevyhodnotil jako vzorce.

## Důvěra a offline režim

Při prvním spuštění se stáhne evropský seznam důvěryhodných seznamů (LOTL),
ověří se jeho podpis a z něj český Trusted List (kvalifikované CA a TSA).
Vše se ukládá do cache (`~/.ear_verifikator/tl_cache`, platnost 7 dní).
Bez připojení se použije i prošlá cache — s upozorněním ve stavovém řádku.

## Testy

```
.venv\Scripts\python -m pytest ear_verifikator\tests
```

## Omezení

- Rozdílné výsledky vůči verifikátoru ISSŘ jsou možné (metodika kap. 5 — rozhodující
  je vždy výsledek v ISSŘ); nástroj používá stejný princip (eIDAS + trusted listy),
  ale jinou implementaci (pyHanko místo EU DSS).
- **Revokace (odvolání) certifikátů se ověřuje v režimu „soft-fail“**: když se
  nepodaří stáhnout CRL/OCSP (výpadek sítě, nedostupný server), podpis se
  nezneplatní. Podpis s odvolaným certifikátem tak může být označen jako
  Platný, pokud revokační informace nejsou v okamžiku kontroly dostupné.
  Rozhodující ověření provádí vždy ISSŘ.
- Při validaci se stahují revokační informace (CRL/OCSP) a chybějící certifikáty
  z adres uvedených v certifikátech kontrolovaného dokumentu — kontrola tedy
  může kontaktovat servery třetích stran určené autorem dokumentu.
- Neřeší věcnou správnost (zda podepsala správná osoba se správným oborem autorizace) —
  vyčtené údaje z certifikátu zobrazuje k ručnímu ověření.
- LTV/archivní validace je implementována jen částečně: pokud certifikát
  podpisu už expiroval, ale podpis nese platné kvalifikované časové razítko
  z doby platnosti certifikátu, ověří se certifikát k času razítka (stejně
  jako verifikátor ISSŘ). Úplná archivní validace řetězců razítek (LTA)
  implementována není.
