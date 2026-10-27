@echo off
rem Lance de Jarvis sin PowerShell.
rem Ejecuta python main/main.py desde la carpeta del proyecto, sin
rem importar desde donde se haga doble clic en este archivo.
cd /d "%~dp0"
python main/main.py
