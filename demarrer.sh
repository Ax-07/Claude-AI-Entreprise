#!/usr/bin/env bash
# Lance le moteur et le relance automatiquement s'il s'arrête.
cd "$(dirname "$0")"
while true; do
  python3 -m ai_studio run
  echo "Le moteur s'est arrêté. Redémarrage dans 30 s (Ctrl+C pour annuler)..."
  sleep 30
done
