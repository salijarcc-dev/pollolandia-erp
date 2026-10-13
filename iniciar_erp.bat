@echo off
REM ==========================================================================
REM  ERP Pollolandia - inicio con doble clic (Windows)
REM  1) crea el entorno virtual la primera vez, 2) instala dependencias,
REM  3) abre el ERP en el navegador (http://localhost:8501).
REM ==========================================================================
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Creando el entorno virtual por primera vez...
    py -3 -m venv .venv || python -m venv .venv
    call ".venv\Scripts\activate.bat"
    python -m pip install --upgrade pip
    pip install -r requirements.txt
) else (
    call ".venv\Scripts\activate.bat"
)
python -m streamlit run app.py
pause
