@echo off
chcp 65001 >nul
cd /d "%~dp0"
title AI Enterprise Studio
echo Demarrage du moteur AI Enterprise Studio (Ctrl+C pour arreter)...
:boucle
python -m ai_studio run
echo Le moteur s'est arrete. Redemarrage dans 30 secondes (fermer la fenetre pour annuler)...
timeout /t 30
goto boucle
