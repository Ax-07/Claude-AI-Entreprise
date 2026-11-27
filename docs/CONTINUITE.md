# Résumé de continuité

_Mis à jour le 27/11/2026_

## Contexte
- Micro-entreprise française enregistrée ; comptes Stripe et GitHub ; pas encore de nom de domaine.
- Budget de test : 20 $ d'API par mois. Le moteur tourne 24 h/24 sur le PC du propriétaire.
- Première activité : pas encore choisie (piste recommandée : service numérique « produit », ex. réponses aux avis Google).

## Fait
1. **Maquette du tableau de bord** (néon, isométrique, gamifié, vue ville / vue bureaux) — https://claude.ai/artifact/CvRpS2tPDf75zQ9GG3chXB
2. **Moteur v0.1** (ce dépôt) : 7 agents (pdg sur Sonnet 5.5, les autres sur Haiku 5.5), outils, plafonds budgétaires, file de validation, orchestrateur 24/7, CLI, 10 tests (faux modèle) qui passent.
3. **Documentation de conception** — https://claude.ai/artifact/5HJkzTKdme1Dr48Cui2UPh

## Décisions récentes
- Brave Search API devient la recherche web principale (5 $ / 1 000 requêtes, 5 $ de crédits mensuels offerts) ; la recherche web Anthropic reste en secours. **Pas encore codé.**
- n8n (auto-hébergé, gratuit) servira de couche d'intégrations à partir de la phase 4 ; le moteur Python garde budget, validations et journal.

## Non vérifié
- Aucun lancement réel avec l'API Claude n'a encore été fait (premier test à faire chez le propriétaire : `python -m ai_studio run --once`).

## Prochaines étapes
1. Ajouter l'outil `brave_search` avec repli sur la recherche web Anthropic.
2. Premier lancement réel et corrections.
3. API locale (127.0.0.1:8765) et branchement du tableau de bord.
4. Phase « Prêt à vendre » : domaine, email pro, CGV, n8n.
5. Choisir l'activité et viser le premier paiement Stripe.
