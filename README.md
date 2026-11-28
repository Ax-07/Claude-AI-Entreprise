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
| `support` | Service Client | économique | brouillons d'emails* |
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

`python -m ai_studio serveur` ouvre le tableau de bord Command HQ sur http://127.0.0.1:8765, fidèle à la maquette validée : carte isométrique néon (vue ville avec bâtiments au choix A/B/C, vue bureaux), fiche du pôle, indicateurs, **validations en un clic**, performance, fil d'activité, succès, escouade d'agents, file de tâches, pipeline et prochains événements. Le menu ouvre les missions, objectifs, analyses de coûts, livrables, intégrations et paramètres.

La carte est générée par `outils/carte/gen_carte.py` (à relancer seulement si on change le dessin). Tout est lu en direct dans la base, rafraîchi toutes les 5 secondes, et accessible uniquement depuis ton PC.

Pour le fonctionnement 24 h/24 : double-clique sur `demarrer.bat`. Il lance le moteur **et** le tableau de bord (`run --tableau`) et redémarre tout seul en cas d'arrêt.

- `python -m ai_studio demarrage-auto` : le lance automatiquement à chaque ouverture de session Windows (`--retirer` pour annuler).
- Notifications Windows : tu es prévenu dès qu'une validation t'attend, que le budget est atteint ou que le moteur est bloqué. Clique sur la notification pour ouvrir le tableau de bord. Test : `python -m ai_studio notifier-test`.
- Sauvegarde automatique chaque jour dans `data/sauvegardes` (14 jours gardés). `sauvegardes` pour la liste, `restaurer NOM` pour revenir en arrière, moteur arrêté.
Pense à désactiver la mise en veille du PC.

### Activités et sécurité

L'entreprise suit plusieurs activités (services numériques, studio vidéo, e-commerce) : `python -m ai_studio activites`
montre les coûts d'API, les revenus et les tâches de chacune, pour ajuster le budget sur des chiffres réels.
Un objectif se rattache à une activité : `objectif "texte" -a video`.

L'agent Cybersécurité vérifie chaque jour la configuration (clés trop puissantes, fichiers sensibles exposés, secrets
recopiés, sauvegardes) et les anomalies, et signale les emails suspects. Les problèmes critiques déclenchent une
notification immédiate, sans coût d'API.

### Offres et commandes

Chaque offre a une fiche (prix, livrable, délai, informations à demander, agent qui produit) : `python -m ai_studio offres`.
Le catalogue de départ est dans `catalogue/offres.json` (Diagnostic SEO local, Site vitrine) : `python -m ai_studio importer-offres` le charge ou le met à jour (les liens de paiement déjà créés sont gardés).
Pour repartir sur une base propre (après une phase d'essais) : arrête le moteur puis `python -m ai_studio repartir-propre --confirmer` (ajoute `--memoire-agents` pour effacer aussi les souvenirs des agents). Une sauvegarde est faite avant.

Auditeur RH (bâtiment Ressources humaines) : chaque lundi, un dossier chiffré de la semaine est écrit dans `rapports/audit-agents-AAAA-Sxx.md` (par agent : tâches et réussite, fins sans résultat, coût par tâche, outils refusés, délégations refusées, mémoire et bibliothèque consultées, recherches web, doublons, demandes acceptées, refusées, renvoyées ou corrigées par toi ; pilotage des missions ; points d'attention). L'auditeur RH, indépendant du PDG (aucun agent ne peut lui confier de tâche), l'analyse et te fait au plus 3 recommandations. Pour un comportement qui se répète, il propose une consigne permanente pour l'agent concerné : elle arrive dans tes validations avec sa preuve, s'applique à toutes les tâches de cet agent si tu la valides, et se retire dans Paramètres.

Analyse SEO et méthodes de production : l'outil `seo_check` (Atelier, Labo, Ventes, Développement) mesure gratuitement, en Python, l'état technique d'un site (title, meta description, titres, mobile, HTTPS, robots.txt, sitemap, données structurées d'entreprise, téléphones, liens cassés, mentions légales) sur l'accueil et quelques pages, avec un score sur 100 ; l'analyse est enregistrée dans `analyses/` et réutilisée 24 h. Chaque offre peut avoir sa méthode de production dans `catalogue/methodes/<offre>.md` (par exemple `diagnostic-seo-local.md`) : elle est donnée automatiquement au producteur à chaque commande, et tu peux la modifier.

Bilan mensuel : le 1er du mois, le bilan chiffré du mois écoulé est écrit dans `rapports/bilan-AAAA-MM.md` (par offre : ventes, CA, coût d'API réel par commande, marge après Stripe, cotisations et API, délai moyen et retards, satisfaction, prospection et taux de réponse), tu reçois une notification, et Finance ajoute une analyse avec au plus 3 recommandations transmises au PDG. Le mois en cours est visible à tout moment dans Analyses, ou avec `python -m ai_studio bilan [AAAA-MM]`.

Après la livraison : 3 jours après (`aftercare.days`), Support prépare un message de suivi (modèle `suivi_livraison` : remerciement, demande d'avis, lien d'avis si `aftercare.review_url` est renseigné, offre suivante de la fiche d'offre — `next_offer`, ex. Diagnostic SEO → Site vitrine — si le client ne l'a pas déjà achetée). Un seul message, validé par toi. Les retours du client sont enregistrés sur la commande (`record_feedback`, visibles dans la fiche de la mission) ; un client insatisfait déclenche une notification et ne reçoit plus de proposition commerciale.

Validation des offres : une offre proposée par un agent (`save_offer`) arrive dans tes validations avec le calcul de marge (prix − frais Stripe − cotisations − coût d'API : moyenne réelle des commandes livrées, sinon le plafond par mission). Brouillon : ni lien de paiement, ni modèle d'email, ni vente avant ta décision. Modification d'une offre en vente : la version actuelle reste en vente jusqu'à ta décision. Taux réglables dans `config.json` (`offers` : `social_rate` 0,256 = BNC par défaut, 0,212 si services BIC ; `stripe_pct` 0,028 = carte professionnelle ; `min_margin_pct` 50). Les offres importées par toi (`importer-offres`) sont actives directement.

Fichier prospects (menu « Prospects ») : chaque contact de prospection avec son statut (contacté, relancé, a répondu, intéressé, client, refus, ne plus contacter, sans suite, adresse invalide), ses dates et la source publique de son adresse. Ventes doit enregistrer le prospect (`save_prospect`, source obligatoire) avant de lui écrire ; un second premier email, un contact après refus ou « STOP » sont refusés. Une seule relance, déclenchée automatiquement 7 jours après l'envoi sans réponse, puis « sans suite » 7 jours plus tard (`prospects.followup_days`, `prospects.close_after_days`). Les réponses sont reconnues dans la boîte mail (« STOP », refus, réponse → tâche à Ventes). `repartir-propre` conserve ce fichier (les oppositions doivent être gardées).

Rapports livrés en PDF : les agents écrivent le rapport en HTML (contrôle qualité, aperçu) et `deliver_order` le convertit en PDF A4 paginé avec Microsoft Edge ou Google Chrome déjà installé (sans fenêtre). Le client reçoit le PDF, le HTML reste dans le dossier de la commande. Navigateur introuvable : livraison bloquée avec un message ; chemin réglable par `delivery.pdf_browser` dans config.json, `delivery.pdf: false` pour livrer le HTML.

Modèles d'emails : `catalogue/modeles_email.json` (prospection Diagnostic SEO, prospection Site vitrine, relance unique, réponse à un prospect intéressé ; vouvoiement). Les agents remplissent seulement les champs personnalisés ; offre, prix, délai, informations à demander et lien de paiement viennent du catalogue ; un champ manquant, trop long ou « à compléter » est refusé. Tu peux modifier les textes du fichier sans redémarrer.

Pour supprimer une seule mission : bouton « Supprimer » dans sa fiche (tableau des missions, deux clics), ou `python -m ai_studio supprimer-mission ID --confirmer` (`--commande` pour une commande d'essai). Sauvegarde faite avant ; coûts, bibliothèque, mémoire des agents et fichiers conservés ; refusé si un agent y travaille ou pour une commande réellement payée.

Mode test (le temps du développement) : `python -m ai_studio mode-test on ton@adresse.fr` — identité légale fictive dans les emails, envoi possible uniquement vers les adresses données (les tiennes), et le Juridique valide sans condition pour elles. `mode-test` affiche l'état, `mode-test off` le coupe. Redémarre le moteur après un changement.
Pour essayer une offre sans paiement : `python -m ai_studio commande-test "Site vitrine" -e ADRESSE_RÉELLE -i "informations du client"`. La commande suit exactement le parcours d'une vente (mode test) et la livraison t'est envoyée après ta validation.
Chaque paiement devient une commande suivie jusqu'à la livraison : `python -m ai_studio commandes`. Le moteur relance
le PDG tant qu'une commande n'est pas livrée, et te prévient si elle est bloquée ou en retard. `livree N` marque une
commande livrée si tu l'as livrée toi-même.

### Mémoire des agents

Les agents gardent une mémoire partagée. Si une information fausse s'y est glissée :
`python -m ai_studio memoire "mots clés"` pour la retrouver, `oublier ID` pour l'effacer, et
`retenir SUJET "texte"` pour ajouter une consigne qui passe avant tout ce que les agents ont mémorisé.

### Vendre : paiements, emails, site

- **Ventes Stripe** : lues toutes les 10 minutes. Chaque paiement s'affiche dans le tableau de bord, déclenche une notification et une tâche de livraison pour les agents.
- **Boîte Gmail** : avec Gmail dans `SMTP_*` (mot de passe d'application), les nouveaux emails sont lus toutes les 5 minutes, sans être marqués comme lus. Les agents préparent les réponses, que tu valides. Newsletters et envois automatiques sont ignorés.
- **Site** : les agents proposent des pages d'offre ; tu les vois avec « Aperçu de la page » puis tu publies en un clic sur GitHub Pages (`GITHUB_PAGES_REPO`, `GITHUB_TOKEN`). Renseigne `company.legal` dans `config.json` (nom, SIRET, adresse, email) : ces mentions légales sont ajoutées en bas de chaque page.

Le tableau de bord se reconstruit avec `python outils/tableau/build.py` après une modification de `outils/tableau/index_source.html`.

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
