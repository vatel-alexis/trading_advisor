# Héberger la plateforme gratuitement (Neon + GitHub Actions + Vercel)

Coût : 0 €. Aucune carte bancaire demandée. Aucune machine à laisser allumée.

| Morceau | Où | Rôle |
| --- | --- | --- |
| Base PostgreSQL | [Neon](https://neon.com), plan *Free* | Toutes les données (deals, positions, historique) |
| Worker | GitHub Actions, workflow `Worker` | Screener, moniteur et backtests |
| API | [Vercel](https://vercel.com), plan *Hobby*, dossier `backend` | Ce que le site appelle (lecture, acceptation des deals) |
| Site | Vercel, plan *Hobby*, dossier `frontend` | L'interface, protégée par un mot de passe |

Le workflow `Worker` (`.github/workflows/worker.yml`) exécute `python -m app.worker tick`, qui
fait ce qui est dû à ce moment-là :

- le moniteur de 8 h 00 à 17 h 55, heure de New York (synchro des ordres, assignations, stops,
  sortie à 21 DTE) ;
- le screener une fois par jour, à partir de 10 h 30 à New York (16 h 30 à Paris). Si le passage
  de 10 h 30 a été retardé, il est rattrapé au suivant ;
- puis les backtests demandés depuis la page Backtests du site.

GitHub saute la plupart des tâches planifiées (une douzaine de passages en deux jours au lieu
de plusieurs centaines). Le workflow ne compte donc pas sur elles : chaque exécution lance
`python -m app.worker loop`, qui passe toutes les 5 minutes de 8 h 00 à 17 h 55 à New York en
semaine et toutes les heures le reste du temps, pendant 5 h 30, puis démarre elle-même
l'exécution suivante. Hors séance, un backtest lancé depuis le site démarre donc dans l'heure.
La tâche planifiée horaire ne sert qu'à relancer la chaîne si elle s'arrête. Pour la démarrer
tout de suite : *Actions → Worker → Run workflow* avec `loop`.

Une fusion sur `main` n'atteint le worker qu'à l'exécution suivante (5 h 30 au plus) ; pour
l'appliquer tout de suite, annuler l'exécution en cours puis relancer `loop`.

Le dépôt est public, donc les minutes GitHub Actions sont gratuites et illimitées. La prise de
profit à 50 % reste un ordre GTC chez Alpaca, actif en permanence.

## 1. La base Neon

Inscription sur neon.com avec le compte GitHub. Créer un projet dans la région
*AWS US East (N. Virginia)*, près d'Alpaca, de GitHub et de Vercel. Dans *Connect*, désactiver
*Connection pooling* et copier l'adresse, qui commence par `postgresql://`.

## 2. Les secrets GitHub (worker)

Dans le dépôt, aller dans *Settings → Secrets and variables → Actions → New repository secret*
et créer :

| Nom | Valeur |
| --- | --- |
| `DATABASE_URL` | l'adresse Neon |
| `BROKER_API_KEY` | la clé Alpaca paper |
| `BROKER_API_SECRET` | le secret Alpaca paper |

Puis lancer *Actions → Worker → Run workflow* avec `check`. Le journal doit afficher trois
lignes `[ok]` : base migrée, compte Alpaca `ACTIVE` avec le niveau d'options 3, Yahoo
joignable. Les tables sont créées à ce premier passage. Le même bouton permet de lancer
`screener` ou `monitor` à la main.

## 3. L'API sur Vercel

Préparer d'abord un **jeton d'API** : une longue suite de caractères au hasard (au moins 32,
lettres et chiffres, par exemple tirée d'un gestionnaire de mots de passe). Il permet au site,
et à lui seul, d'appeler l'API.

Inscription sur vercel.com avec le compte GitHub (plan *Hobby*). Puis *Add New → Project*,
importer `trading_advisor` et régler :

- *Root Directory* : `backend` (Vercel reconnaît FastAPI tout seul) ;
- *Environment Variables* :

  | Nom | Valeur |
  | --- | --- |
  | `DATABASE_URL` | l'adresse Neon |
  | `BROKER_API_KEY` | la clé Alpaca paper |
  | `BROKER_API_SECRET` | le secret Alpaca paper |
  | `API_TOKEN` | le jeton d'API |

Déployer, puis ouvrir `https://<adresse de l'API>/health` : la page doit afficher
`"database":"ok"`. Toute autre adresse de l'API répond « Jeton d'API manquant ou invalide »,
c'est normal.

## 4. Le site sur Vercel

*Add New → Project*, importer à nouveau `trading_advisor` :

- *Root Directory* : `frontend` ;
- *Environment Variables* :

  | Nom | Valeur |
  | --- | --- |
  | `API_URL` | l'adresse de l'API, par exemple `https://trading-advisor-api.vercel.app` (sans `/` final) |
  | `API_TOKEN` | le même jeton d'API |
  | `SITE_PASSWORD` | le mot de passe du site |

Déployer et ouvrir l'adresse du site. Le navigateur demande un identifiant (n'importe lequel)
et le mot de passe. Le site peut passer des ordres paper : ne pas partager ce mot de passe.

Vercel redéploie l'API et le site à chaque fusion sur `main`. Quand une fusion ajoute une
migration, le workflow `Migrations` (`.github/workflows/migrate.yml`) l'applique aussitôt à la
base Neon, sans attendre le passage suivant du worker. Sans elle, le nouveau code de l'API lit
des colonnes absentes et le site affiche « API injoignable ». Pour la rejouer à la main :
*Actions → Migrations → Run workflow*.

## Suivre

- *Actions → Worker* liste chaque passage. Une croix rouge signale un job en échec, avec le
  détail dans le journal. GitHub envoie aussi un e-mail en cas d'échec.
- Sans secret `DATABASE_URL`, chaque passage s'arrête à la première étape, sans erreur.
- Vercel montre les journaux de l'API et du site dans l'onglet *Logs* de chaque projet.

## Limites à connaître

- **Retards.** Entre deux exécutions de la chaîne, le moniteur s'interrompt une à deux
  minutes (installation). Les stops sont vérifiés toutes les 5 minutes, et non à la minute près.
- **60 jours.** GitHub désactive les tâches planifiées d'un dépôt public sans aucune activité
  (commit, PR...) pendant 60 jours. Il prévient par e-mail, et un clic sur *Enable workflow*
  les relance.
- **Neon gratuit.** 0,5 Go de stockage et 100 heures de calcul par mois. Avec la base réveillée
  environ 10 h par jour de séance, on en consomme à peu près 60.
- **Premier affichage lent.** La base Neon s'endort après 5 minutes sans requête, et l'API
  Vercel aussi : la première page après une pause peut mettre quelques secondes.
- **Backtests longs.** Un seul passage du worker tourne à la fois : pendant un backtest (une à
  deux minutes), le passage suivant du moniteur attend la fin.
- Pour arrêter le worker : *Actions → Worker → ⋯ → Disable workflow*, puis annuler
  l'exécution en cours.
