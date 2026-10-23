@echo off
rem bob.cmd - Arranca a Bob con su CLI (bob-cli\): todo lo que Bob escribe, con la
rem barra de estado y los atajos. Desde scripts\:
rem   bob                  Bob de verdad (robot_bob\main.py)
rem   bob --falso [modo]   Bob de mentira, sin placas: demo, rafaga [s] o caida
rem Modo clasico, sin CLI:  cd robot_bob  y  python main.py
setlocal
cd /d "%~dp0bob-cli" || exit /b 1
if not exist node_modules\.bin\tsx.cmd (
  echo Instalando las dependencias del CLI, solo la primera vez...
  call npm install --no-fund --no-audit || exit /b 1
)
call node_modules\.bin\tsx.cmd src\index.tsx %*
exit /b %errorlevel%
