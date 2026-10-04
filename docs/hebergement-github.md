# Faire tourner le worker sans serveur (GitHub Actions + Neon)

Coût : 0 €. Aucune carte bancaire demandée.

- **Base de données** : PostgreSQL gratuit chez [Neon](https://neon.com).
- **Worker** : le workflow `Worker` de GitHub Actions (`.github/workflows/worker.yml`) se lance
  toutes les 5 minutes en semaine. Il exécute `python -m app.worker tick`, qui fait ce qui est
  dû à ce moment-là :
  - le moniteur de 8 h 00 à 17 h 55, heure de New York (synchro des ordres, assignations,
    stops, sortie à 21 DTE) ;
  - le screener une fois par jour, à partir de 10 h 30 à New York (16 h 30 à Paris). Si le
    passage de 10 h 30 a été retardé, il est rattrapé au suivant.
- Le dépôt est public, donc les minutes GitHub Actions sont gratuites et illimitées.
- La prise de profit à 50 % reste un ordre GTC chez Alpaca, actif en permanence.

## À faire une fois

1. **Créer la base Neon.** Inscription avec le compte GitHub, plan *Free*. Créer un projet
   dans la région *AWS US East (N. Virginia)*, près d'Alpaca et des serveurs GitHub. Dans
   *Connect*, désactiver *Connection pooling* et copier l'adresse, qui commence par
   `postgresql://`.
2. **Déclarer les secrets dans GitHub.** Dans le dépôt, aller dans *Settings → Secrets and
   variables → Actions → New repository secret* et créer :

   | Nom | Valeur |
   | --- | --- |
   | `DATABASE_URL` | l'adresse Neon copiée à l'étape 1 |
   | `BROKER_API_KEY` | la clé Alpaca paper |
   | `BROKER_API_SECRET` | le secret Alpaca paper |

   Les secrets ne sont visibles par personne, pas même dans les journaux. Ne les écrire nulle
   part ailleurs.
3. **Vérifier.** Aller dans *Actions → Worker → Run workflow*, choisir `check`, puis lancer.
   Le journal du job doit afficher trois lignes `[ok]` : base migrée, compte Alpaca `ACTIVE`
   avec le niveau d'options 3, Yahoo joignable. Les tables sont créées automatiquement au
   premier passage.

Le même bouton *Run workflow* permet de lancer `screener` ou `monitor` à la main.

## Suivre

- *Actions → Worker* liste chaque passage. Une croix rouge signale un job en échec, avec le
  détail dans le journal. GitHub envoie aussi un e-mail en cas d'échec.
- Sans secret `DATABASE_URL`, chaque passage s'arrête à la première étape, sans erreur.

## Limites à connaître

- **Retards.** GitHub peut retarder une tâche planifiée de plusieurs minutes, voire en sauter
  une quand ses serveurs sont chargés. Les stops sont donc vérifiés toutes les 5 à 15 minutes
  environ, et non à la minute près.
- **60 jours.** GitHub désactive les tâches planifiées d'un dépôt public sans aucune activité
  (commit, PR...) pendant 60 jours. Il prévient par e-mail, et un clic sur *Enable workflow*
  les relance.
- **Neon gratuit.** 0,5 Go de stockage et 100 heures de calcul par mois. Avec la base
  réveillée environ 10 h par jour de séance, on en consomme à peu près 60.
- Pour arrêter le worker : *Actions → Worker → ⋯ → Disable workflow*.
