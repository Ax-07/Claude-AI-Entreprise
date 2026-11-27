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
- Piste d'évolution : passer à l'API Responses d'OpenAI pour garder le raisonnement avec les outils (PDG).

## Non vérifié
- Aucun cycle réel complet n'a encore réussi. Prochain test chez le propriétaire : `python -m ai_studio run --once`.
- Les noms de modèles OpenAI viennent de la page de prix officielle du 27/11/2026 ; si le compte n'y a pas accès, changer `models.openai` dans `config.json`.

## Prochaines étapes
1. Premier lancement réel (OpenAI + Brave) et corrections.
2. API locale (127.0.0.1:8765) et branchement du tableau de bord.
3. Phase « Prêt à vendre » : domaine, email pro, CGV, n8n.
4. Choisir l'activité et viser le premier paiement Stripe.
