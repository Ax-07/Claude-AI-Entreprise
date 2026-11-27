# Consignes pour Claude — AI Enterprise Studio

Projet : entreprise multi-agents IA autonome qui doit générer des revenus réels (micro-entreprise française).
Le propriétaire parle français : réponds en français et tutoie-le.

## Règles
- Lis `docs/CONTINUITE.md` avant de reprendre un développement ; mets-le à jour à la fin de chaque phase.
- Préserve les données et fonctionnalités existantes ; pas de réécriture inutile.
- Python 3.10+ et bibliothèque standard uniquement, sauf décision contraire notée dans la documentation.
- Tout nouvel outil ou agent arrive avec ses tests (`python -m unittest discover -s tests -v`), qui utilisent un faux modèle et ne coûtent rien.
- Ne jamais demander ni écrire de clé API ; les secrets restent dans `.env` (non versionné).
- Toute action externe (email, paiement, publication) passe par la file de validation (`approvals`).
- Contrôle les coûts : modèle économique par défaut, plafonds dans `config.json`.
- Ne prétends jamais avoir testé quelque chose qui ne l'a pas été.

## Repères
- Ajouter un agent : `ai_studio/agents.py` (ROLES).
- Ajouter un outil : décorateur `@tool` dans `ai_studio/tools/core.py` ou `business.py`, puis l'ajouter aux rôles concernés.
- Documentation de conception : https://claude.ai/artifact/5HJkzTKdme1Dr48Cui2UPh
- Maquette du tableau de bord : https://claude.ai/artifact/CvRpS2tPDf75zQ9GG3chXB
