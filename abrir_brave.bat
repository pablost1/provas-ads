@echo off
rem Abre o Brave normal (sem automacao) com porta de depuracao e perfil proprio.
start "" "C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe" --remote-debugging-port=9222 --user-data-dir="%~dp0data\brave-profile" https://www.pciconcursos.com.br/provas/analista-de-sistemas
