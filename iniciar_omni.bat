@echo off
cd /d "%~dp0"

:: Verifica se ja tem servidor rodando na porta 5000
netstat -ano | findstr ":5000" | findstr "LISTENING" >nul 2>&1
if %errorlevel% == 0 (
    echo OMNI ja esta rodando, abrindo browser...
    start "" http://127.0.0.1:5000
    exit
)

:: Inicia o servidor em background
echo Iniciando OMNI...
start "" /min cmd /c "python app.py > server.log 2>&1"

:: Aguarda o servidor subir
timeout /t 2 /nobreak >nul

:: Abre o browser
start "" http://127.0.0.1:5000
echo OMNI iniciado!
