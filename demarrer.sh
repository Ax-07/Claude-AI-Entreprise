#!/usr/bin/env bash
# Lance le moteur et le tableau de bord, et les relance automatiquement s'ils s'arrêtent.
cd "$(dirname "$0")"
python3 -m ai_studio run --tableau
while true; do
  echo "Le moteur s'est arrêté. Redémarrage dans 30 s (Ctrl+C pour annuler)..."
  sleep 30
  python3 -m ai_studio run --tableau --sans-navigateur
done
