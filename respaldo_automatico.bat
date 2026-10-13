@echo off
REM ==========================================================================
REM  ERP Pollolandia - respaldo automatico 3-2-1 (MAN-ARC-01 5.2)
REM  Programelo en el Programador de tareas de Windows (diario, p. ej. 19:00):
REM    schtasks /create /tn "Pollolandia respaldo" /tr "%~dp0respaldo_automatico.bat" /sc daily /st 19:00
REM  Hace la copia diaria, la completa semanal y las de cierre mensual y anual.
REM ==========================================================================
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Ejecute primero iniciar_erp.bat para crear el entorno.
    exit /b 1
)
call ".venv\Scripts\activate.bat"
if not exist "datos\logs" mkdir "datos\logs"
python -m scripts.respaldo_automatico >> "datos\logs\respaldo_automatico.log" 2>&1
exit /b %ERRORLEVEL%
