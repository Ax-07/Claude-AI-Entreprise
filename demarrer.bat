@echo off
chcp 65001 >nul
cd /d "%~dp0"
title AI Enterprise Studio
echo Demarrage du moteur AI Enterprise Studio et du tableau de bord (Ctrl+C pour arreter)...
python -m ai_studio run --tableau
:boucle
echo Le moteur s'est arrete. Redemarrage dans 30 secondes (fermer la fenetre pour annuler)...
timeout /t 30
python -m ai_studio run --tableau --sans-navigateur
goto boucle
