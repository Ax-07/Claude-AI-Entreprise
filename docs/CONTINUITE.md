# Résumé de continuité

_Mis à jour le 27/11/2026_

## Contexte
- Micro-entreprise française enregistrée ; comptes Stripe et GitHub ; pas encore de nom de domaine.
- Budget de test : 20 $ d'API par mois. Le moteur tourne 24 h/24 sur le PC du propriétaire.
- Première activité : pas encore choisie (piste recommandée : service numérique « produit », ex. réponses aux avis Google).

## Fait
1. **Maquette du tableau de bord** (néon, isométrique, gamifié, vue ville / vue bureaux) — https://claude.ai/artifact/CvRpS2tPDf75zQ9GG3chXB
2. **Moteur v0.1** (ce dépôt) : 7 agents (PDG sur modèle fort, les autres sur modèle économique), outils, plafonds budgétaires, file de validation, orchestrateur 24/7, CLI.
3. **Documentation de conception** — https://claude.ai/artifact/5HJkzTKdme1Dr48Cui2UPh
4. **Fournisseurs et recherche** : Anthropic ou OpenAI au choix (`provider: auto` selon la clé dans `.env`) ; outil `brave_search` prioritaire, repli sur la recherche serveur Anthropic. 20 tests (faux modèle) qui passent.

## Décisions récentes
- Brave Search API devient la recherche web principale (5 $ / 1 000 requêtes, 5 $ de crédits mensuels offerts) ; la recherche web Anthropic reste en secours. **Codé.**
- Phase de test avec OpenAI (crédits du propriétaire) : gpt-6-luna (agents) et gpt-6.1-terra (PDG).
- n8n (auto-hébergé, gratuit) servira de couche d'intégrations à partir de la phase 4 ; le moteur Python garde budget, validations et journal.

## Premier lancement réel (27/11/2026)
- Configuration lue correctement (OpenAI + Brave), mais chaque appel refusé : `Function tools with reasoning_effort are not supported for gpt-6.1-terra in /v1/chat/completions`.
- Corrigé : envoi de `reasoning_effort: "none"` ; erreurs de configuration affichées et sans perte de tâches ; pas de nouvel essai dans le même cycle ; commandes `relancer`, `objectifs`, `archiver`.
- 2e lancement : **premier cycle réel réussi** (0,063 $). PDG → Recherche (3 pistes : diagnostic SEO local, réécriture de fiches produits, automatisation no-code) → Finance (grille de rentabilité) → 4 tâches de suite créées. Agents honnêtes (faits / hypothèses séparés, sources réelles).
- Défauts corrigés ensuite : rapport de la Recherche non écrit (arguments write_file coupés par la limite de 2 000 tokens → limite portée à 6 000, `append` pour écrire en plusieurs parties, message d'aide) ; outils Stripe proposés sans clé (désormais masqués) ; `run --once` affichait d'anciens échecs.
- 3e lancement : rapport des 3 offres écrit et sourcé (La Fabrique du Net, NOIISE, Zapier). Offre jugée la plus adaptée : **diagnostic SEO local** (données publiques, très automatisable). Défaut : l'agent raisonnait en heures humaines.
- Ajustements : règle « coût d'API et temps de validation, pas heures humaines », 30 recherches par jour (couvertes par les crédits Brave), compactage de l'historique envoyé au modèle.
- Décision du propriétaire : on reste sur la **construction de l'app** ; les agents gardent un objectif d'entraînement sans action externe.
- Piste d'évolution : passer à l'API Responses d'OpenAI pour garder le raisonnement avec les outils (PDG).

## Phase 3 — tableau de bord branché (27/11/2026)
- `ai_studio/server.py` (API locale sécurisée) + `ai_studio/web/index.html` (page néon : indicateurs, carte des pôles dont les bâtiments grandissent avec les tâches terminées, agents, validations en un clic, activité, tâches, objectifs, coûts, livrables).
- `python -m ai_studio serveur` ou `run --tableau` ; `demarrer.bat` lance les deux. 42 tests ; rendu vérifié par capture d'écran sur des données de démonstration.
- Refonte fidèle à la maquette (demande du propriétaire) : même carte (outils/carte/gen_carte.py → web/carte.svg, compressée ≈ 6×), même mise en page et icônes ; panneaux sans données encore réelles affichés vides honnêtement (revenus, ventes) ; choix des bâtiments et de la vue mémorisés ; ajout du panneau Validations (absent de la maquette) en haut de la colonne de droite. 57 tests.
- Critère de fin de phase : le propriétaire voit ses vraies données et valide une action depuis la page.

## Fiabilité 24 h/24 (27/11/2026)
- `ai_studio/notify.py` : notifications Windows (toast via PowerShell, sans dépendance) pour toute nouvelle validation, la pause budget et une erreur bloquante. Une seule notification par événement, clic → tableau de bord. Le texte des agents passe par une variable d'environnement en XML échappé (pas d'injection de commande). Commande de test : `notifier-test`.
- `ai_studio/backup.py` : sauvegarde quotidienne automatique (API de sauvegarde SQLite + zip des livrables) dans `data/sauvegardes/AAAA-MM-JJ`, 14 gardées. Commandes `sauvegarder`, `sauvegardes`, `restaurer NOM` (l'ancienne base est conservée).
- `demarrage-auto [--retirer]` : lancement à l'ouverture de session Windows (fichier dans le dossier Démarrage, fenêtre réduite, sans navigateur).
- Réglages : `notifications` et `backup` dans config.json. 69 tests.

## Boucle commerciale (27/11/2026)
- `ai_studio/commerce.py` : synchro Stripe (sessions de paiement → table `sales`, tâche de livraison prioritaire pour le PDG, notification), lecture Gmail IMAP en lecture seule (table `inbox`, tâche de réponse ventes/support, filtres newsletters/automatiques, 10 par jour), publication GitHub Pages (API contents) après validation.
- Outils : `list_sales`, `list_inbox`, `read_email`, `propose_page` ; `send_email` accepte `in_reply_to` et `attachment`. Le contenu des emails reçus est délimité comme donnée externe (anti-injection) et toute réponse repasse par la validation.
- Tableau de bord : revenus du mois réels, pipeline (emails reçus, pages publiées, ventes) cliquable, aperçu isolé (CSP sandbox) des pages avant publication. Source de la page : `outils/tableau/index_source.html` + `build.py`.
- Choix du propriétaire : site sur GitHub Pages dans un dépôt public séparé ; boîte Gmail dédiée avec mot de passe d'application.
- 85 tests. Non testé avec les vrais Stripe, Gmail et GitHub (simulés dans les tests).

## Premier test réel de la boucle (27/11/2026)
- Vente de test Stripe détectée (commande #18) et livraison lancée par le PDG : la synchro Stripe fonctionne en réel.
- Problème : l'outil `draft_email` (« prépare un email ») a fait croire aux agents qu'ils ne pouvaient qu'écrire un brouillon. Ils ont fait valider le texte par `request_owner_decision`, puis demandé au propriétaire de l'envoyer à la main.
- Corrections : outil renommé `send_email` (alias de l'ancien nom), description et résultat explicites (« part automatiquement à la validation »), règle « ne jamais demander au propriétaire de faire une action à ta place », `request_owner_decision` interdit pour valider un email ; une validation dont le réglage manque (SMTP, Stripe, GitHub) est refusée avec un message clair et reste en attente au lieu d'échouer. 87 tests.

## Catalogue d'offres et suivi des commandes (27/11/2026)
- Constat du test réel : le support a écrit le cahier des charges du diagnostic puis s'est arrêté ; personne n'était responsable de la commande jusqu'à la livraison, et les agents ne savaient pas ce qu'était le produit « Test diagnostic » (lien créé à la main).
- Table `offers` (nom, prix, livrable, délai, infos à demander, agent producteur, lien de paiement) ; outils `save_offer`, `list_offers` ; `propose_payment_link(offer=…)` relie le lien Stripe à la fiche.
- Commandes = ventes avec un état (`a_livrer`, `attente_client`, `livree`, `historique`), un numéro (n°N), une échéance (délai de l'offre) ; `tasks.order_ref` rattache toutes les tâches (héritée par délégation). Migration automatique des bases existantes.
- Brief de livraison détaillé pour le PDG (fiche d'offre, producteur, fichier attendu, « un plan n'est pas un livrable », délai à annoncer). Outils `list_orders`, `set_order_status`, `deliver_order` (pièce jointe obligatoire et non triviale ; la validation marque la commande livrée).
- Suivi automatique (`follow_orders`, à chaque cycle, sans coût) : relance du PDG si plus aucune tâche ni validation en cours depuis 30 min (48 h en attente client), 3 relances puis notification « commande bloquée » ; notification si le délai promis est dépassé ; une réponse du client relance la commande.
- CLI : `offres`, `commandes [-t]`, `livree N`. 99 tests.

## Consignes du propriétaire sur une commande (27/11/2026)
- Constat : la consigne du propriétaire (donnée dans la note d'un refus) n'a vécu que dans une tâche ; relancé, le PDG cherchait une « confirmation du client » et remettait la commande en attente du client alors que celui-ci avait répondu.
- `sales.owner_notes` : toute note du propriétaire sur une validation liée à une commande y est attachée, rappelée en tête de chaque relance (« font foi ») et dans `list_orders` ; la commande repasse à livrer. CLI `consigne N "texte"` (relance immédiate du PDG).
- `set_order_status(attente_client)` refusé si aucun email n'a été envoyé au client ou s'il a déjà répondu au dernier.
- Règles : les consignes du propriétaire font foi ; avancer avec une hypothèse raisonnable signalée plutôt que bloquer. 101 tests.

## Atelier de production (27/11/2026)
- Constat du propriétaire : le rapport client était produit par le Labo Recherche, dont la mission est la veille, pas la production. Il manquait la fonction « métier ».
- 8e agent `atelier` (pôle Atelier, #FB923C) : produit les livrables clients selon la fiche d'offre, méthode de qualité (faits vérifiables, hypothèses signalées, relecture), puis délègue la livraison au support. Modèle fort, 16 étapes, plafond 0,40 $ par tâche (champs `Role.max_steps` et `Role.task_budget_usd`).
- Producteur par défaut des offres et des commandes sans fiche : l'atelier.
- Carte : 8e district au coin avant du plateau (Manufacture / Atelier à sheds / Tour-atelier, open-space avec engrenage holographique).
- Première commande (test) livrée par le propriétaire le 27/11/2026, rapport produit par le Labo Recherche. 102 tests.

## Pôle Juridique (27/11/2026)
- 9e agent `juridique` (pôle legal, #CBD5E1, coin du haut du plateau ; Palais de justice / Tour de la balance / Archives scellées) : CGV, mentions légales, RGPD, facturation, propriété intellectuelle, avec sources officielles citées ; modèle fort.
- Relecture automatique (`legal.queue_reviews`, à chaque cycle) des demandes `publish_page` et `stripe_payment_link` ; emails de prospection en option (`legal_review.prospection_emails`, désactivé par défaut pour le coût). Outil `legal_review` → avis affiché sur la carte de validation ; ne bloque jamais la décision.
- Règle commune : questions juridiques déléguées au juridique. 105 tests.
- Prochaine étape juridique : faire rédiger CGV, mentions et politique de confidentialité ; factures conformes générées automatiquement à chaque vente (à développer).

## Organisation en divisions (27/11/2026)
- Divisions (décision du propriétaire) : Direction (PDG + Data Core, au centre), Commerciale (Marketing, Ventes, Support), Production (Atelier, Labo Recherche en R&D), Administration (Trésorerie, Juridique).
- `agents.DIVISIONS` / `DIVISION_OF` : l'équipe est présentée par division dans le prompt de chaque agent ; `/api/agents` renvoie la division ; l'escouade du tableau de bord est rangée par division.
- Carte refaite en trois rangées (outils/carte/gen_carte.py, positions `XY`) : HQ au fond, Data Core au centre (moyeu des routes), Commerciale à gauche, Production à droite (coin droit libre pour un futur pôle), Administration devant ; plateformes de division (contour lumineux à la couleur de la division) et noms des divisions ; étiquettes compactes sur une ligne (état en infobulle, nombre de tâches en pastille).
- Data Core : 3 nouveaux bâtiments proposés par le propriétaire (D L'Archive circulaire, E Le Prisme neural, F L'Agora en spirale) ; variantes A à F acceptées par /api/map. 106 tests.

## Un bâtiment par pôle (27/11/2026)
- Précision du propriétaire : « réorganiser par pôle » = un bâtiment par pôle, plusieurs agents dedans. Remplace la carte à 9 bâtiments.
- 5 bâtiments : Command HQ (pdg) au fond, Data Core (data) au centre, Pôle commercial (marketing, ventes, support) à gauche, Pôle production (atelier, recherche) à droite, Administration (finance, juridique) devant.
- carte.json : `agents_of_pole` (liste d'agents par bâtiment) ; un pôle regroupé propose tous les bâtiments de ses anciens pôles (commercial A–I, production A–F, administration A–F, data A–F, hq A–C). /api/map n'accepte que ces 5 pôles.
- Page : état, pastille et robots d'un bâtiment agrègent ses agents ; la fiche du pôle liste ses agents et leurs dernières tâches. 106 tests.

## Multi-activités et Cybersécurité (27/11/2026)
- Décisions du propriétaire : deux nouvelles activités à venir, **Studio vidéo (YouTube, international)** et **E-commerce (dropshipping, international)** ; budget augmenté au fur et à mesure, sur la base des coûts réels par activité ; agent de cybersécurité.
- Modèle retenu : divisionnel avec services partagés (Direction ; services partagés Trésorerie, Juridique, Cybersécurité, Support, Labo ; une division par activité).
- Table `activities` (services active ; video et ecommerce « en préparation ») ; colonne `activity` sur objectifs, tâches (héritée du parent / de l'objectif / de la commande), coûts (prise sur la tâche) et offres. `activities.report` : coûts mois/total, revenus (selon l'offre vendue), tâches. CLI `activites`, `objectif "…" -a video` ; API /api/activities ; Analyses du tableau de bord par activité ; choix de l'activité à la création d'un objectif ; activité rappelée dans le briefing des tâches.
- Agent `securite` (pôle Administration, rebaptisé services partagés ; modèle économique) : `security.config_audit` (Git, clés Stripe/GitHub trop puissantes, mot de passe Gmail, sauvegardes, secrets recopiés dans les livrables ou la mémoire — jamais la valeur d'un secret), `anomalies` (dépenses, échecs, envois, emails suspects), `suspicious_text` (emails piégés : signalés dans la tâche de réponse) ; alerte automatique sans coût sur tout constat critique (une fois) ; revue quotidienne par l'agent ; outils `security_audit`, `security_alert` (3 par jour). 116 tests.
- Points d'attention relevés : YouTube ne monétise pas le contenu produit en masse (politique « inauthentic content », clarifiée en juillet 2026) → vidéos originales, peu nombreuses, publication validée ; dropshipping = vendeur pleinement responsable (garanties, rétractation 14 jours, GPSR, fournisseurs UE à privilégier), international = TVA/douane à traiter par le Juridique.

## Plateau final : quartiers par pôle, un bâtiment par agent (27/11/2026)
- Décision du propriétaire : un quartier par pôle et un bâtiment par agent (remplace la carte à 5 bâtiments).
- Quartiers (dalle à la couleur du pôle) : Direction au centre (Command HQ, Data Core), Pôle commercial à gauche (Marketing, Ventes, Support), Pôle production à droite (Labo Recherche, Atelier), Services partagés devant (Juridique, Trésorerie, Cybersécurité). Deux terrains réservés au fond, avec grue : Studio vidéo et E-commerce (« bientôt »).
- 10 bâtiments (un par agent, 3 au choix, 6 pour le Data Core) ; 3 nouveaux pour la Cybersécurité (Bouclier, Pare-feu, Centre de veille). carte.json : `quarters` (avec `planned`), un agent par bâtiment.
- Carte allégée : au repos, chaque bâtiment n'a qu'un voyant d'état (et le nombre de tâches) ; le nom s'affiche au survol ; les bâtiments eux-mêmes sont cliquables ; le bâtiment sélectionné a un voyant cerclé. 116 tests.

## Plateau « ville » (27/11/2026)
- Choix du propriétaire, d'après son image de référence : la carte devient une ville vue en isométrie (remplace les quartiers en dalles).
- Grille impaire de 5 × 5 îlots (demande du propriétaire : une case bien au centre) séparés par des rues (marquage, passages piétons, lampadaires, voitures) ; le Data Core occupe l'îlot central, avec un halo holographique  ; en fond, seulement le halo violet (pas de ligne d'horizon, choix du propriétaire).
- Disposition (demande du propriétaire) : Data Core sur la case centrale ; chaque pôle occupe un coin de 2 × 2 cases, soit 4 emplacements de bâtiment : Direction en haut (Command HQ + 3 libres), Pôle commercial à gauche (Marketing, Ventes, Support + 1 libre), Pôle production à droite (Labo Recherche, Atelier + chantiers Studio vidéo et E-commerce), Services partagés en bas (Juridique, Cybersécurité, Trésorerie + 1 libre). Les 8 cases entre les coins sont de petits parcs (allées, bassin, arbres). Emplacements libres occupés par des immeubles éclairés (bordure à la couleur du pôle), en attendant un futur agent. carte.json : `free` par pôle.
- Nom du pôle sur une enseigne à l'avant de chaque îlot ; au survol : nom, niveau de l'agent et tâches. Pas de noms de code (décision du propriétaire : rester professionnel malgré le design ludique).
- `/api/agents` renvoie `level` (1 + tâches réussies // 10, même barème que l'entreprise). Les 3 bâtiments au choix par pôle (6 pour le Data Core) et la vue bureaux sont conservés. Générateur : `outils/carte/gen_carte.py` (E, ZONES, LOTS, PLOTS, PARKS, FREE). 116 tests.

- Survol d'un bâtiment (sur la carte ou son étiquette) : aura lumineuse à la couleur du pôle (`applyHover`, classe CSS `hov`).
- Data Core en blanc (#F1F5F9) : bâtiment, halo, bordure de sa case, carte d’agent (choix du propriétaire).

- Réorganisation validée par le propriétaire (proposition de Gemini, ajustée) : E-commerce (chantier) dans le Pôle commercial, emplacement libre de ce coin ; « Zone Support » renommé **Service Client** (l'agent `support` fait du service client, pas du support interne) ; **Cybersécurité dans le coin Direction**, rebaptisé « Direction & Tech » (`DIVISIONS` : Direction & Tech = pdg, data, securite ; Administration = finance, juridique). Une case Tech collée au Data Core aurait supprimé un parc de la croix : écarté. Emplacements libres : 2 en Direction & Tech, 1 en Production, 2 en Services partagés.

## Pôle Développement (27/11/2026)
- Demande du propriétaire : vendre aussi des sites web et des applications. L'Atelier garde les autres livrables (rapports, audits, contenus).
- 11e agent `developpement` (Division production, cyan #22D3EE, emplacement libre du coin production ; bâtiments Tour du code / Campus tech / Pile logicielle, open-space avec hologramme « </> »). Modèle fort, 24 étapes, 0,60 $ par tâche. Outil `package_files`. Le PDG route sites/applications vers lui ; `save_offer` accepte `producer=developpement`. 118 tests.
- À faire avant de vendre des sites : créer une offre « Site vitrine » (prix, délai, contenus à demander au client) et tester sur une commande fictive ; la mise en ligne chez le client reste à sa charge (LISEZMOI) tant qu'on n'a pas d'hébergement.

- Bâtiments du Studio vidéo (Studio de tournage / Tour média / Plateau de diffusion) et de l'E-commerce (Entrepôt logistique / Boutique en ligne / Hub de livraison) construits à la place des chantiers, à la demande du propriétaire, même si ces divisions n'ont pas encore d'agent : `planned: true` dans carte.json, pas de robots, « En préparation » au survol et dans la fiche ; bâtiment au choix comme les autres pôles.

## Non vérifié
- Les toasts et le démarrage automatique n'ont pas pu être testés sur un vrai Windows (pas de Windows dans l'environnement de Claude) : à vérifier avec `notifier-test` et `demarrage-auto`.
- Les noms de modèles OpenAI viennent de la page de prix officielle du 27/11/2026 ; si le compte n'y a pas accès, changer `models.openai` dans `config.json`.

## Prochaines étapes
1. Configuration chez le propriétaire : Gmail (mot de passe d'application), dépôt public du site + jeton GitHub, clé Stripe (lecture Checkout Sessions), mentions légales dans config.json.
3. Notes du propriétaire sur les livrables, pour l'amélioration des agents.
4. Phase « Prêt à vendre » : domaine, email pro, CGV, n8n.
3. Choisir l'activité (piste : diagnostic SEO local) et viser le premier paiement Stripe.
