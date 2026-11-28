# Méthode — Diagnostic SEO local

Cette méthode s'impose à chaque commande de l'offre « Diagnostic SEO local ». Le propriétaire peut la modifier ;
elle est relue automatiquement à chaque tâche de production.

## Ce qu'on promet, et ce qu'on ne promet jamais
- On promet : un état des lieux factuel et sourcé de la visibilité locale, et 10 actions priorisées avec des textes prêts à copier.
- On ne promet JAMAIS : une position dans Google, un nombre de visites ou d'appels, un délai de résultat.
- On n'invente rien : pas de statistiques de la fiche Google (vues, appels, clics : non publiques), pas de mot-clé
  « recherché X fois par mois » sans source, pas de vitesse mesurée (pas d'outil de mesure). Ce qui n'est pas
  vérifiable est écrit comme tel : « non vérifiable publiquement — à consulter dans votre espace Google ».

## Étapes
1. **Dossier** : relis la fiche d'offre, la commande (list_orders) et les consignes du propriétaire. Il te faut le
   nom de l'établissement, l'adresse, l'activité, la ville ou la zone. S'il manque l'adresse du site ou l'activité, dis-le
   dans ton résultat (le chef de projet demandera au client) au lieu de deviner.
2. **Analyse technique** : `seo_check` sur le site (5 pages). C'est la seule source des constats techniques.
   Ne relis pas le code des pages toi-même.
3. **Présence locale** (search_library d'abord, puis recherche web, 4 recherches au plus) :
   - « [nom] [ville] » : la fiche Google apparaît-elle ? les avis sont-ils visibles (nombre, note) dans les résultats ?
   - 2 annuaires publics au plus (PagesJaunes, annuaire de la profession) : nom, adresse, téléphone identiques au site ?
   - « [activité] [ville] » : relève 3 concurrents qui apparaissent, et pour chacun 2 ou 3 faits observables
     (site oui/non, nombre d'avis visible, page dédiée à la ville…). Pas de jugement sans fait.
   Note pour chaque constat l'URL et la date de consultation.
4. **Notes** :
   - Score technique : celui de `seo_check` (sur 100), sans le modifier.
   - Score de présence locale (sur 100), grille fixe :
     fiche Google trouvée 25 · avis visibles 15 (note ≥ 4 : +5) · NAP identique site/annuaires 20 ·
     page ou mention de la ville/zone sur le site 15 · données structurées d'entreprise 10 · téléphone cliquable 10.
     Ce qui n'a pas pu être vérifié vaut 0 et est signalé « non vérifié ».
   - Note globale = moyenne des deux, arrondie.
5. **Les 10 actions** : chaque action = titre court · pourquoi (le constat et sa source) · impact (fort/moyen/faible) ·
   effort (moins de 30 min / une demi-journée / un professionnel) · comment faire, en étapes simples · et quand c'est
   un texte : le texte PRÊT À COPIER (title de page, meta description, description de fiche Google de 750 caractères
   au plus, réponse type à un avis). Classe les actions par impact puis effort. Les 3 premières doivent pouvoir être
   faites par le client lui-même en moins d'une heure.
6. **Rédaction** (rapport HTML autonome, converti en PDF à la livraison) :
   1. Page de garde : « Diagnostic SEO local — [établissement] », date, note globale.
   2. Synthèse (une page) : les 3 notes, les 3 forces, les 3 priorités, en langage simple.
   3. Analyse du site : tableau des constats de seo_check (constat · pourquoi c'est important · source).
   4. Présence locale : fiche Google, avis, cohérence nom/adresse/téléphone, annuaires.
   5. Concurrents : tableau des 3 concurrents et de leurs faits observables.
   6. Les 10 actions (format de l'étape 5).
   7. Méthode et limites : outils, pages analysées, dates, ce qui n'a pas pu être vérifié.
   Ton : vouvoiement, phrases courtes, pas de jargon sans explication (« balise title : le titre qui s'affiche dans Google »).
   8 à 12 pages A4 : pas de remplissage.
7. **Contrôle** : `check_deliverable` sur le fichier, corrige tout ce qu'il signale, relis chaque chiffre contre sa
   source. Termine ta tâche en indiquant le fichier ; le chef de projet organise la livraison.

## Budget
Vise moins de 0,40 $ d'API : seo_check et la bibliothèque sont gratuits, les recherches web sont limitées à 4.
