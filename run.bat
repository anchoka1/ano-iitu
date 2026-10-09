@echo off
rem ==================================================================
rem  ANO IITU: zapusk dvojnym klikom (Windows).
rem  Vsya logika v run.ps1; zdes my tolko zapuskaem ego v obhod
rem  zapreta na skripty PowerShell (tolko dlya etogo zapuska).
rem  Kommentarii latinicej: cmd.exe ploho chitaet kirillicu v .bat.
rem ==================================================================
chcp 65001 >nul
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" %*
rem Okno ne zakryvaem, chtoby bylo vidno, pochemu server ostanovilsya.
echo.
echo [ANO IITU] Server ostanovlen. Nazhmite lyubuyu klavishu, chtoby zakryt okno.
pause >nul
