@echo off
rem Double-click to open the CAD Checkout window (no console).
where pyw >nul 2>nul && (start "" pyw "%~dp0cad_checkout.py" & exit /b)
start "" pythonw "%~dp0cad_checkout.py"
