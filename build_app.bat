@echo off
rem Sestaveni samostatne aplikace Verifikator EAR (vysledek: dist\Verifikator EAR\)
cd /d "%~dp0"
.venv\Scripts\pyinstaller --noconfirm --clean --windowed ^
  --name "Verifikator EAR" ^
  --icon ear_verifikator\icon.ico ^
  --paths "." ^
  --collect-data pyhanko ^
  --collect-data pyhanko_certvalidator ^
  --collect-data certifi ^
  --collect-all signxml ^
  --add-data "ear_verifikator\icon.ico;ear_verifikator" ^
  launcher.py
