# AI Enterprise Studio — moteur multi-agents (v0.1)

Une équipe de 7 agents IA qui travaillent pour ton entreprise, 24 h/24, sur ton PC.
Tu fixes des objectifs, l'agent PDG les découpe en missions et les confie aux autres agents.
Tout ce qui engage l'entreprise vers l'extérieur (envoyer un email, créer un lien de paiement) attend ta validation.

- Documentation de conception : [docs/CONCEPTION.md](docs/CONCEPTION.md) (version en ligne : https://claude.ai/artifact/5HJkzTKdme1Dr48Cui2UPh)
- Maquette du tableau de bord : https://claude.ai/artifact/CvRpS2tPDf75zQ9GG3chXB
- Suivi du projet : [docs/CONTINUITE.md](docs/CONTINUITE.md)

## L'équipe

| Agent | Pôle | Modèle (Anthropic / OpenAI) | Outils spécifiques |
|---|---|---|---|
| `pdg` | Command HQ | Sonnet 5.5 / gpt-6.1-terra (fort) | déléguer, budget, statistiques, objectifs |
| `recherche` | Labo Recherche | Haiku 5.5 / gpt-6-luna (économique) | recherche web, lecture de pages |
| `marketing` | Hub Marketing | économique | recherche web, lecture de pages |
| `ventes` | Centre Ventes | économique | recherche web, brouillons d'emails*, liens de paiement* |
| `support` | Zone Support | économique | brouillons d'emails* |
| `data` | Data Core | économique | statistiques, budget |
| `finance` | Trésorerie | économique | budget, Stripe (lecture), liens de paiement* |

\* soumis à ta validation.
Tous les agents ont en plus : mémoire partagée, fichiers de travail, liste des tâches, demande de décision au propriétaire.

## Garde-fous intégrés

- **Budget** : plafond par mois (20 $), par jour (0,65 $), par tâche (0,15 $) et nombre de recherches web par jour (15).
  Quand un plafond est atteint, le moteur se met en pause tout seul et reprend le lendemain.
- **Validation** : emails et liens de paiement passent par `python -m ai_studio validations`.
- **Limites** : 8 étapes maximum par tâche, 30 tâches ouvertes maximum, délégation limitée à 3 niveaux.
- **Bac à sable** : les agents n'écrivent que dans `data/workspace/`. La lecture web refuse les adresses locales.
- **Journal** : chaque action est tracée (`python -m ai_studio journal`).

## Installation (Windows)

1. Installe **Python 3.10 ou plus récent** depuis python.org (coche « Add Python to PATH »).
2. Clone le dépôt (ou ouvre-le dans VS Code), par exemple dans `C:\AI-Studio`.
3. Ouvre un terminal dans ce dossier et tape :
   ```
   python -m ai_studio init
   ```
4. Ouvre le fichier `.env` créé et colle **une** clé de fournisseur d'IA : `OPENAI_API_KEY=` ou `ANTHROPIC_API_KEY=`.
   Le moteur choisit automatiquement le fournisseur selon la clé présente (`"provider": "auto"` dans `config.json`).
   Ajoute aussi `BRAVE_API_KEY=` pour la recherche web (Brave Search API, offre Search).
   (Optionnel : clé Stripe restreinte et paramètres SMTP.)
5. Dans la console de ton fournisseur (OpenAI ou Anthropic), fixe aussi une **limite de dépense mensuelle** : c'est une double sécurité.
   `python -m ai_studio agents` affiche le fournisseur, les modèles et le moteur de recherche utilisés.

Aucune bibliothèque à installer : tout utilise Python standard.

## Utilisation

```
python -m ai_studio agents                          # voir l'équipe
python -m ai_studio objectif "Identifier 3 offres de services numériques rentables pour une micro-entreprise"
python -m ai_studio run --once                      # un seul cycle, pour tester
python -m ai_studio status                          # vue d'ensemble
python -m ai_studio taches                          # liste des tâches
python -m ai_studio voir 3                          # résultat complet de la tâche 3
python -m ai_studio validations                     # actions en attente de ta décision
python -m ai_studio valider 2 -n "OK"               # valider
python -m ai_studio refuser 2 -n "trop tôt"         # refuser
python -m ai_studio couts                           # dépenses détaillées
python -m ai_studio objectifs                       # liste des objectifs
python -m ai_studio archiver 2                      # met l'objectif 2 en pause
python -m ai_studio relancer                        # remet en attente les tâches en échec
python -m ai_studio tache "Rédiger une FAQ" -a support -d "FAQ pour l'offre X"
```

## Tableau de bord

`python -m ai_studio serveur` ouvre le tableau de bord Command HQ sur http://127.0.0.1:8765 : indicateurs, carte des pôles, agents, **validations en un clic**, fil d'activité, tâches (avec leur résultat complet), objectifs, coûts et livrables. Tout est lu en direct dans la base, rafraîchi toutes les 5 secondes, et accessible uniquement depuis ton PC.

Pour le fonctionnement 24 h/24 : double-clique sur `demarrer.bat`. Il lance le moteur **et** le tableau de bord (`run --tableau`) et redémarre tout seul en cas d'arrêt.
Pense à désactiver la mise en veille du PC.

Les livrables des agents sont dans `data/workspace/`, la base de données dans `data/studio.db`.

## Tests

```
python -m unittest discover -s tests -v
```
Les tests utilisent un faux modèle : ils ne consomment rien.

## Structure

```
ai_studio/
  config.py        configuration + chargement du .env
  db.py            base SQLite (objectifs, tâches, mémoire, coûts, validations, journal)
  llm.py           client API Claude, calcul des coûts, plafonds budgétaires
  agents.py        les 7 rôles (missions, outils, modèle)  ← ajouter un agent ici
  runner.py        boucle d'exécution d'une tâche par un agent
  orchestrator.py  cycle 24/7 : revues d'objectifs par le PDG, exécution, pauses budget
  approvals.py     exécution des actions validées (email SMTP, lien Stripe)
  tools/           outils des agents (core = communs, business = email/Stripe/statistiques)
```
