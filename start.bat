@echo off
setlocal

:: 1. Virtuelle Umgebung erstellen, falls nicht vorhanden
if not exist "venv" (
    echo [INFO] Virtuelle Umgebung wird erstellt...
    python -m venv venv
    if errorlevel 1 (
        echo [FEHLER] Python konnte keine venv erstellen. Ist Python im PATH?
        pause
        exit /b %errorlevel%
    )
)

:: 2. Virtuelle Umgebung aktivieren
call venv\Scripts\activate.bat

:: 3. Requirements installieren / aktualisieren
if exist "requirements.txt" (
    echo [INFO] Installiere / Aktualisiere Paket-Abhaengigkeiten...
    python -m pip install --upgrade pip
    pip install -r requirements.txt
) else (
    echo [WARNUNG] Keine requirements.txt gefunden. Ueberspringe Installation.
)

:: 4. Python-Anwendung starten
echo.
echo [INFO] Starte app.py...
echo ----------------------------------------
python app.py

:: 5. Skript nach Beenden der App offen halten (falls Fehler auftreten)
echo ----------------------------------------
echo [INFO] Anwendung wurde beendet.
pause