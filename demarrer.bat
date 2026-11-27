@echo off
chcp 65001 >nul
cd /d "%~dp0"
title AI Enterprise Studio
rem Avec --auto (démarrage avec Windows), le navigateur ne s'ouvre pas : une notification préviendra si besoin.
set OPTIONS=
if "%1"=="--auto" set OPTIONS=--sans-navigateur
echo Demarrage du moteur AI Enterprise Studio et du tableau de bord (Ctrl+C pour arreter)...
python -m ai_studio run --tableau %OPTIONS%
:boucle
echo Le moteur s'est arrete. Redemarrage dans 30 secondes (fermer la fenetre pour annuler)...
timeout /t 30
python -m ai_studio run --tableau --sans-navigateur
goto boucle
