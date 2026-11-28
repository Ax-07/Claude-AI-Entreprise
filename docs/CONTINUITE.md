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

## Place de rassemblement, Direction élargie, pôle Tech & Données (27/11/2026)
- Décision du propriétaire : Juridique et Trésorerie rejoignent la Direction (coin du haut : Command HQ, Trésorerie, Juridique, **Ressources humaines**). `DIVISIONS` : Direction = pdg, finance, juridique ; Tech & Données = data, securite ; la division Administration disparaît.
- Ressources humaines : bâtiment en préparation (Maison des talents / Tour des experts / Campus RH, hologramme d'équipe). Rôle prévu : réserve d'agents spécialisés mobilisables à la demande (mécanisme à concevoir).
- Coin du bas renommé **Tech & Données** (bordure blanche) : Data Core (coin avant) et Cybersécurité + 2 emplacements libres.
- Case centrale : **place de rassemblement** (dallage, bassin lumineux). Chaque agent y a un personnage à sa couleur, visible quand il est au repos ; quand il travaille ou a du travail en attente, ses robots apparaissent devant son bâtiment (3 s'il travaille, 1 s'il attend). Clic sur un personnage : fiche de son pôle.

- Tech & Données complété (décision du propriétaire) : **Infrastructure** (#2DD4BF : hébergement et mise en ligne des sites, domaines, HTTPS, surveillance ; piste de forfaits hébergement + maintenance récurrents) et **Automatisations** (#FACC15 : n8n, API, connecteurs, workflows à vendre aux clients), bâtiments en préparation (Centre de données / Tour d'hébergement / Nuage ; Usine à workflows / Tour d'intégration / Hub de connecteurs). Leurs agents seront activés avec les premières ventes de sites. Tous les emplacements de la carte sont occupés.

## Catalogue de départ et commandes d'essai (27/11/2026)
- `catalogue/offres.json` (versionné) : **Diagnostic SEO local** (89 €, 72 h, atelier, rapport HTML 8–12 pages, sources citées) et **Site vitrine** (390 €, 120 h, developpement, 1 à 5 pages HTML/CSS en .zip + LISEZMOI, domaine et hébergement non inclus, une série de corrections). Prix fixés sous le marché freelance (site vitrine : ~800 à 3 000 € chez un freelance généraliste ; audit de fiche Google : ~60 à 120 € sur les plateformes) car production par IA et premières ventes.
- CLI `importer-offres [fichier]` (création ou mise à jour, lien de paiement conservé) ; `commande-test "offre" -e email -i "infos client"` : vente fictive `essai_…` (mode test, sans paiement) qui suit exactement le parcours d'une vente Stripe (`commerce.open_order`, partagé avec la synchronisation Stripe) ; les infos du client deviennent une consigne du propriétaire. 119 tests.
- À faire chez le propriétaire : importer le catalogue, lancer une commande d'essai pour chaque offre, juger les livrables.

## Parler au PDG (27/11/2026)
- Demande du propriétaire : donner une mission au PDG depuis l'interface. Bouton « Parler au PDG » dans l'en-tête (et dans la fiche du Command HQ) : panneau de discussion à droite, réponse immédiate du PDG (modèle fort, coût affiché sous chaque réponse).
- `ai_studio/chat.py` : table `chat`, 16 derniers messages en contexte, 6 étapes max, consignes de discussion (court, tutoiement, consulter les outils plutôt qu'inventer, une seule question si ambigu, ne pas faire le travail lui-même). Outils : nouveau `create_objective`, `delegate_task`, `company_stats`, `budget_status`, `list_tasks`, `get_task_result`, `list_orders`, `list_offers`, `list_sales`, `recall` — aucune action externe directe. Budget et erreurs du modèle renvoyés comme réponse lisible. API GET/POST /api/chat. 121 tests.

## Bibliothèque de recherches (28/11/2026)
- Constat : les résultats bruts des recherches web n'étaient gardés que pendant la tâche ; une même recherche refaite par un autre agent recoûtait et consommait le quota (30/jour, atteint).
- Table `web_cache` : chaque recherche Brave (clé pays|requête normalisée) et chaque page lue (URL sans ancre) est enregistrée. Reprise gratuite et hors quota si moins de 7 jours (recherche) ou 3 jours (page) ; purge automatique au-delà de 30 jours (une fois par jour, dans le cycle).
- Outil commun `search_library` (aussi dans la discussion avec le PDG) ; règle ajoutée : consulter la bibliothèque avant toute recherche web. L'outil serveur web_search d'Anthropic (utilisé seulement sans clé Brave) n'est pas mis en cache.
- Tableau de bord : Connaissances → onglet « Recherches web » (filtre, contenu complet au clic). API GET /api/library (?q=, ?id=). 124 tests.

- Bug signalé par le propriétaire (28/11) : le rafraîchissement automatique redessinait la colonne Validations et effaçait la note en cours de saisie. Correction : les cartes sont des résumés cliquables ; la décision se prend dans une fenêtre (contenu complet, avis juridique, note sur plusieurs lignes, Valider / Refuser / Aperçu) que le rafraîchissement ne touche pas ; la colonne n'est redessinée que si la liste a changé ; une réponse vide à une décision est refusée.

- Discussions multiples avec le PDG (28/11, demande du propriétaire) : table `chat_threads`, colonne `chat.thread_id` (les anciens messages sont regroupés dans « Première discussion » à la migration). Boutons ＋ (nouvelle discussion, créée au premier message, titre = premier message) et ☰ (historique : titre, date, nombre de messages, coût). Le PDG ne reçoit que le contexte de la discussion ouverte. API : GET /api/chat?thread=, GET/POST /api/chat/threads, POST /api/chat {text, thread}. 126 tests.

- Incident du 28/11 : commande d'essai n°3 créée avec l'adresse d'exemple de ma documentation (ton@email.fr) ; le propriétaire a validé la livraison avec une note « change le destinataire », mais valider exécute l'action telle quelle (la note n'est que transmise) et l'action semble avoir été exécutée deux fois (double clic pendant l'envoi SMTP, vérification de statut non atomique).
  Corrections : prise en charge atomique des validations (statut `processing`, une seule exécution) ; boutons désactivés pendant l'envoi ; emails modifiables avant envoi (destinataire, objet, texte : `decide(..., edits)`, adresse vérifiée, trace dans le journal et le payload) ; bouton « Demander une modification » (renvoie le travail à l'agent avec la note) ; `commande-test` refuse les adresses d'exemple ; CLI `client N email` pour corriger l'email d'une commande. 128 tests.

- Emails non distribués (28/11, idée du propriétaire) : l'envoi SMTP réussit même vers une adresse inexistante ; le refus arrive plus tard par un avis de non-distribution (mailer-daemon), jusqu'ici classé « expéditeur automatique ». `sync_inbox` les détecte (`bounce_recipients` : expéditeur mailer-daemon/postmaster + objet type, Final-Recipient, X-Failed-Recipients, texte libre Gmail) et `handle_bounce` : validation marquée `bounced` avec le motif (code 550…), notification Windows (canal erreurs), journal ; si c'était une livraison, la commande repasse « à livrer » avec une consigne « ne pas renvoyer à cette adresse » et une tâche prioritaire au Service Client (autre adresse fiable ou request_owner_decision). Rattrapage unique des avis déjà reçus (10 derniers jours) au premier cycle après la mise à jour. 130 tests.

## Tableau des missions (28/11/2026)
- Demande du propriétaire : voir les missions et leur avancement, savoir quand les agents les considèrent terminées et pouvoir vérifier ; titre de mission dans le fil d'activité.
- `ai_studio/missions.py` : une mission = un objectif (tâches `objective_id`) ou une commande (tâches `order_ref`). Avancement par statut, anomalies détectées (tâches en échec, bloquées, « en cours » depuis plus de 30 min, déclarée terminée avec des tâches ouvertes ou sans tâche réussie, aucune activité depuis 24 h, échéance de commande dépassée). Vérification du propriétaire : « Confirmer : terminée » (objectives/sales.owner_check='ok') ou « Pas terminée » (note obligatoire → mission réactivée, tâche prioritaire au PDG avec la note et l'état des tâches ; pour une commande : consigne + « à livrer »). Si le PDG redéclare la mission terminée, la vérification est remise à zéro.
- Les sous-tâches héritent désormais de la mission de leur parent (`add_task`). Les décisions sur validations sont journalisées avec leur tâche.
- Tableau de bord : menu Missions → onglets À vérifier / En cours / Anomalies / Toutes, détail en arbre des tâches ; « Toutes les tâches » garde l'ancienne liste. Fil d'activité : titre « Mission #N · … » ou « Commande n°N · … » (cliquable). API /api/missions, /api/missions/{objectif|commande}/ID (GET, POST verdict). 133 tests.

- Retour du propriétaire sur le premier tableau réel (28/11) : mission #6 avec 12/12 tâches réussies mais restée « En cours » ; commandes n°2 et n°3 « À livrer » avec 13/13 tâches réussies ; missions #2 à #4 en double de #1 avec d'anciens échecs. Améliorations : « À vérifier » inclut aussi les missions dont toutes les tâches sont finies sans rien en attente ; anomalie « plus personne n'y travaille » ; validations en attente rattachées à leur mission (« elle attend ta décision #N ») ; une mission en pause n'affiche plus ses tâches suspendues comme anomalie ; verdict « Archiver » (objectif `archived`, commande `historique` + owner_check `archived`, tâches en attente annulées) et onglet « Archivées ». 134 tests.

## Outils de production : audit et contrôle qualité (28/11/2026)
- Audit demandé par le propriétaire. Déjà en place : write_file (200 Ko/fichier, append), read_file, list_files, fetch_url, recherche web, package_files (.zip sans secrets ni .js), deliver_order (email + pièce jointe), envoi SMTP avec type MIME. Manquait : aucun moyen de vérifier un livrable, lecture tronquée à 12 000 caractères, aucun aperçu pour le propriétaire.
- `ai_studio/quality.py` + outil `check_deliverable` (Atelier, Développement) : site (dossier ou .zip) → index.html, title, meta description, viewport, lang, un seul h1, alt des images, liens internes cassés, mentions légales, lien de contact, fichiers bloqués par Gmail, texte provisoire ; document HTML/MD → longueur, titres de sections, sources citées, CSS externe (erreur : non autonome), texte provisoire. Erreurs = lorem/TODO/FIXME/XXX/{{…}}, liens cassés, pas de title, CSS local externe.
- `deliver_order` refuse la livraison s'il reste des erreurs et joint le résumé du contrôle (`qa`) à la validation. Consignes Atelier/Développement : check_deliverable avant livraison ; rapport HTML autonome. `read_file` accepte `offset`.
- Aperçu du livrable dans la fenêtre de validation : GET /api/approvals/ID/livrable[/chemin] (page HTML, document, ou fichiers d'une archive de site avec navigation entre pages), isolé (CSP sandbox, aucun script). 139 tests.
- Limites restantes : pas de génération d'images ni de PDF (rapports HTML imprimables) ; pas de rendu visuel automatique (le propriétaire juge via l'aperçu).

## Chefs de projet (28/11/2026)
- Décision du propriétaire : un chef de projet par mission (le PDG reste l'arbitre ; l'auditeur RH viendra ensuite et évaluera aussi les chefs de projet).
- Rôle unique `chef_projet` (Direction, logé au Command HQ, modèle par défaut, 14 étapes, 0,25 $/tâche), instancié par mission : chaque tâche porte sa mission (objective_id / order_ref) et commence par `mission_status` (dossier complet). Outils : mission_status, close_mission, delegate_task, list_orders, list_offers, check_deliverable, set_order_status. PDG : `assign_project_manager` (aussi dans la discussion), consigne de confier les missions de plusieurs étapes/agents.
- Réveil événementiel : à la fin (ou à l'échec définitif) d'une tâche d'une mission pilotée, `missions.wake_pm` crée UNE tâche de suivi (les fins suivantes s'y ajoutent tant qu'elle attend). Revue périodique : « Point de mission » du chef de projet au lieu de la revue PDG. Clôture (`close_mission`) refusée s'il reste des tâches ouvertes ou une validation en attente ; une commande se clôt par sa livraison ; bilan stocké (`objectives.pm_summary`) et affiché au propriétaire, la mission passe « À vérifier ».
- Commandes : chaque nouvelle commande a son chef de projet (tâche de livraison et relances vers lui) ; les commandes ouvertes existantes en reçoivent un à la migration.
- Plafond par mission pilotée : `missions.max_usd_per_mission` (3 $ par défaut) ; au-delà, les tâches de la mission sont suspendues (« blocked ») avec le motif.
- Carte : `EXTRA_AGENTS` (hq → chef_projet), personnage sur la place. Tableau des missions : badge « chef de projet », bilan de clôture. 142 tests.

- Remise à zéro opérationnelle (28/11, demande du propriétaire avant de tester les nouveautés) : `python -m ai_studio repartir-propre --confirmer [--memoire-agents]` (`ai_studio/reset.py`). Sauvegarde complète d'abord ; supprime missions, tâches, journal, validations, discussions, commandes d'essai ; déplace l'espace de travail dans data/archives/ ; conserve configuration, offres, activités, ventes réelles, coûts, emails reçus, bibliothèque de recherches, mémoire du propriétaire (et des agents sans l'option). Les numéros de tâches ne sont pas réutilisés (coûts historiques). 143 tests.

## Chaîne de commandement (28/11/2026)
- Constat du propriétaire : la Cybersécurité lui signalait des dépenses. Principe validé : chaque pôle son domaine ; ce qui en sort remonte au PDG (ou au chef de projet), qui décide quel pôle intervient et ce qui remonte au propriétaire.
- Dépenses → Trésorerie : `finance_checks.spending_report` (jour vs moyenne 7 jours, par agent, agent inconnu), outil `spending_check`, contrôle quotidien automatique (`finance.daily_review`) ; une dérive inexpliquée est signalée au PDG, jamais au propriétaire directement. Le PDG peut alors demander à la Cybersécurité de vérifier une intrusion.
- Cybersécurité : `security.anomalies` ne garde que les signaux de sécurité (envois inhabituels, emails suspects) ; `security_alert` niveau « critique » = alerte directe du propriétaire (seule exception), niveau « attention » = tâche pour le PDG. Les tâches en échec relèvent du pilotage (tableau des missions, chefs de projet).
- Règle permanente ajoutée pour tous les agents (chaîne de commandement) et consigne d'arbitrage dans la mission du PDG. 144 tests.

- Retour du premier essai après remise à zéro (28/11) : une prospection demandée dans la discussion a bien fonctionné (emails envoyés) mais n'a créé aucune mission (le PDG avait utilisé delegate_task : tâche orpheline n°15). Correction : nouvel outil `launch_mission` (PDG et discussion) qui crée la mission numérotée puis, soit la tâche de l'agent qui la réalise (mission simple), soit un chef de projet (plusieurs étapes) ; dans la discussion, delegate_task et create_objective sont retirés : toute demande du propriétaire devient une mission. La remise à zéro remet aussi à 1 la numérotation des missions, validations, journal et discussions (pas des tâches, à cause des coûts historiques). 144 tests.

- Analyse du journal de la mission #1 (28/11, « contacte le propriétaire d'ax-07.fr pour proposer un audit SEO ») : la mission a bien été créée (launch_mission → Ventes), mais Ventes, retrouvant en mémoire la prospection déjà faite avant la remise à zéro (souvenirs d'agents conservés, sans --memoire-agents) et la réponse de Xavier, a traité un « suivi » sans renvoyer d'email ; puis la revue automatique du PDG a relancé la mission (tâche Support) sans que rien ne le justifie ; nombreux appels répétés (emails automatiques relus par 3 agents).
  Corrections : missions simples (`objectives.simple`) sans revue périodique du PDG — la mission va directement au propriétaire (« À vérifier ») ; si le propriétaire la renvoie, elle redevient suivie par le PDG ; consigne : demande déjà traitée → ne pas refaire en double, dire précisément quoi/quand/résultat ; `list_inbox` masque par défaut les messages automatiques et avis de non-distribution ; « Toute l'activité » masque les appels d'outils (case à cocher pour les afficher). 145 tests.

- Coûts par mission et par tâche (28/11) : `missions._costs` (somme des coûts des tâches rattachées), colonne « Coût » dans le tableau des missions, coût par tâche et total dans le détail ; Analyses → tableau « Par mission » (coût USD et EUR, chiffre d'affaires et marge pour une commande, clic → mission) + coût « hors mission » du mois (revues quotidiennes, discussions). Conversion `prices.usd_to_eur` (0,92 par défaut, à ajuster). 146 tests.

- Emails en temps réel (28/11, demande du propriétaire) : `ai_studio/mail_watch.py`, fil séparé lancé par le moteur (`commerce.realtime_inbox`, actif par défaut) : connexion IMAP en IDLE (le serveur signale l'arrivée), relevé immédiat avec le même traitement (tâche, alerte, rebond), reconnexion toutes les 25 min, nouvel essai après 1 min en cas de coupure (journalisé une fois). Le relevé périodique (5 min) reste en filet de sécurité ; un verrou empêche les deux de se chevaucher. Notification Windows « Email reçu de … » (objet, commande liée, agent chargé ; « ⚠ Suspect » si besoin) pour chaque email traité — pas pour les newsletters, messages automatiques ou ignorés (`notifications.emails`). Non testé contre le vrai serveur Gmail (IDLE simulé dans les tests). 151 tests.

- Analyse de la première mission pilotée (28/11, « proposer un audit SEO à ax-07.fr ») : 15 tâches réussies mais aucun email soumis et mission non close. Causes : (1) les agents se transmettaient leurs résultats par tâches au chef de projet (doublons de suivi, profondeur de délégation atteinte) ; (2) travail en double (Labo ×2, Développement ×2 pour un examen SEO, Juridique ×2) ; (3) avis juridique bloquant sur une précaution non légale ; (4) la rédaction de l'email (Ventes) s'est terminée sans rien produire et a été comptée comme réussie ; (5) les réveils du chef de projet ont été absorbés par des tâches qu'il traitait sans relancer l'étape manquante.
  Corrections : dans une mission pilotée, seuls le chef de projet et le PDG délèguent (les autres terminent leur tâche avec résultat et recommandation ; le réveil est automatique) ; une tâche sans résultat n'est plus « réussie » (nouvel essai puis échec, chef de projet prévenu) ; `relaunch_if_stalled` relance le chef de projet si la mission n'est ni close ni livrée sans rien en cours, et passe la main au PDG après 2 relances sans progrès ; consigne du chef de projet (chemin le plus court, pas de doublon, le bon agent par besoin) ; Juridique : avis actionnables (ok / corrections précises / bloquant seulement si interdiction légale sourcée), un avis par question ; Atelier/Développement/brief de commande : le chef de projet organise la livraison. 154 tests.

- Mission pilotée à l'arrêt non relancée (28/11 : le PDG, interrogé dans la discussion, n'avait aucun outil pour relancer un chef de projet déjà nommé, et la relance automatique n'existait qu'à la fin d'une tâche du chef de projet). Corrections : `missions.relaunch_mission(kind, id, request)` ; le moteur vérifie à chaque cycle les missions pilotées à l'arrêt et relance immédiatement le chef de projet (sans attendre le point périodique de 3 h) ; après 2 relances sans progrès → arbitrage du PDG ; si l'arbitrage ne débloque rien → plus de relance automatique, journal « ta décision est nécessaire » (une fois) ; `assign_project_manager` sur une mission déjà pilotée = relance avec la consigne (brief) du PDG ou du propriétaire, toujours acceptée. 155 tests.
- Mission tournant en rond (28/11, même mission ax-07 : relances #144, #147, #150, #151 jamais escaladées, Juridique exigeant l'identité légale de l'expéditeur et la politique de confidentialité du prospect). Causes : le compteur de relances ne regardait que les 3 dernières tâches, or chaque relance déléguait une étape (Ventes, Juridique) puis un suivi ; l'identité de l'expéditeur (`company.legal`) n'était connue d'aucun agent. Corrections : relances et arbitrages comptés depuis la dernière intervention du propriétaire (2 relances → arbitrage du PDG ; arbitrage sans effet ou 2 nouvelles relances → demande de décision « Mission bloquée » dans les validations, une seule fois ; la réponse relance le chef de projet et remet les compteurs à zéro ; une consigne du PDG remplace cette demande) ; `send_email` ajoute d'office aux emails de prospection (hors réponse, hors commande) la signature légale et la phrase d'opposition « STOP », et signale dans la validation si `company.legal` est incomplet ; l'identité de l'expéditeur figure dans le prompt de chaque agent ; Juridique : signature, opposition et politique de confidentialité du destinataire ne sont jamais bloquantes. 158 tests.
- Mode test (28/11) : `company.test_mode` {enabled, recipients} ; CLI `mode-test on EMAIL… | off`. Actif : identité fictive (`config.sender_legal`, `TEST_LEGAL`) dans la signature des emails, `send_email` refuse tout destinataire hors liste, motif préfixé « [MODE TEST — identité fictive] », prompt de chaque agent averti (Juridique : « ok (mode test) » sans condition). Les pages publiques gardent la vraie identité. 160 tests.

## Non vérifié
- Les toasts et le démarrage automatique n'ont pas pu être testés sur un vrai Windows (pas de Windows dans l'environnement de Claude) : à vérifier avec `notifier-test` et `demarrage-auto`.
- Les noms de modèles OpenAI viennent de la page de prix officielle du 27/11/2026 ; si le compte n'y a pas accès, changer `models.openai` dans `config.json`.

## Prochaines étapes
1. Configuration chez le propriétaire : Gmail (mot de passe d'application), dépôt public du site + jeton GitHub, clé Stripe (lecture Checkout Sessions), mentions légales dans config.json.
3. Notes du propriétaire sur les livrables, pour l'amélioration des agents.
4. Phase « Prêt à vendre » : domaine, email pro, CGV, n8n.
3. Choisir l'activité (piste : diagnostic SEO local) et viser le premier paiement Stripe.
