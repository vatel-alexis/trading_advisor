# Trading Advisor

Plateforme de paper trading d'options orientée vente de primes : put credit spreads sur les
grandes valeurs liquides, et wheel (CSP puis Covered Calls) sur les titres sous 20 $.
Compte simulé de 20 000, environnement broker démo uniquement.

## Structure

| Dossier | Contenu |
| --- | --- |
| `backend/app` | API FastAPI, worker planifié, modèle de données SQLAlchemy |
| `backend/app/domain` | Moteurs de stratégie et de risque en Python pur (Sprint 2) |
| `backend/app/marketdata` | Données de marché Yahoo (prix, earnings, chaînes d'options) |
| `backend/app/broker` | Client Alpaca paper : ordres options, cotations, assignations (Sprint 3) |
| `backend/app/services` | Screener, puis cycle de vie des ordres et positions |
| `backend/migrations` | Migrations Alembic |
| `backend/scripts/data_spike.py` | Spike données du Sprint 0 |
| `frontend` | Interface Next.js + Tailwind (4 sections) |

## Démarrer avec Docker

Guide complet (horaires, premier cycle paper, dépannage) : [docs/mise-en-service.md](docs/mise-en-service.md).

```bash
cp .env.example .env
docker compose up --build
```

- Interface : http://localhost:3000
- API : http://localhost:8000/health

## Développer sans Docker

```bash
# Base de données seule
docker compose up db

# Backend
cd backend
pip install -e '.[dev]'
alembic upgrade head
uvicorn app.main:app --reload
python -m app.worker          # dans un autre terminal

# Frontend
cd frontend
npm install
API_URL=http://localhost:8000 npm run dev
```

## Vérifications

```bash
cd backend && ruff check . && ruff format --check . && pytest -q
cd frontend && npm run lint && npm run build
```

Les tests backend ont besoin d'une base Postgres (`DATABASE_URL`, par défaut celle du compose).

## Spike données (Sprint 0)

```bash
cd backend
python -m scripts.data_spike              # univers par défaut (ETF, grandes valeurs, wheel)
python -m scripts.data_spike F SOFI AAPL  # titres au choix
```

Le script affiche, pour chaque titre, combien de puts passent chaque filtre (DTE 25-55,
delta 0.15-0.30, OI ≥ 100, volume ≥ 10, spread ≤ 15 %, earnings). Il écrit les contrats retenus dans
`spike_results.csv`. Il n'utilise que la bibliothèque standard Python (aucun `pip install`) et a
besoin d'un accès réseau à `fc.yahoo.com`, `query1.finance.yahoo.com` et `query2.finance.yahoo.com`.
Résultats du premier passage : [docs/spike-sprint0.md](docs/spike-sprint0.md).

## Moteur de stratégie et de risque (Sprint 2)

Le worker lance le screener du lundi au vendredi à 10 h 30 (heure de New York). Il enregistre un
`screener_run` avec l'entonnoir de chaque filtre, jusqu'à 5 opportunités, et l'IV30 du jour de
chaque titre dans `iv_history`. Les propositions de la veille sans réponse passent en `expired`.

| Règle | Valeur par défaut |
| --- | --- |
| Univers | ETF (SPY, QQQ, IWM) et grandes valeurs en put credit spread ; 16 titres wheel en CSP, strike ≤ 20 |
| Filtres | DTE 25-55, delta 0,15-0,30, OI ≥ 100, volume ≥ 10, spread ≤ 15 %, IV Rank ≥ 30, AROC ≥ 15 % |
| Earnings | Rejet si la publication tombe avant l'échéance ; une action sans date connue est rejetée |
| Spread | Jambe longue 5 $ sous la jambe vendue (sinon 10 $ puis 2,5 $), crédit ≥ 0,25 $ |
| Score | 40 % PoP, 40 % AROC (rangs au sein de la stratégie), 20 % IV Rank ; meilleur trade par titre |
| Risque | 10 % du capital max par trade, 50 % max engagé, 2 trades max par secteur (les ETF comptent comme un secteur), pas de 2e entrée sur un titre déjà en position |
| Sorties | Rachat à 50 % du crédit (ordre GTC), stop quand l'option vaut 2x le crédit, sortie à 21 DTE ; pas de stop sur les Covered Calls |
| Wheel | Après assignation, un Covered Call au strike ≥ prix de revient est proposé pour les actions détenues |

Les paramètres sont versionnés dans `strategy_configs` (version 1 = valeurs par défaut de
`app/domain/params.py`) et chaque opportunité garde la version utilisée.

**IV Rank.** Yahoo ne fournit pas d'historique d'IV. Tant que `iv_history` compte moins de 120 jours
pour un titre, l'IV Rank est un proxy : la position de l'IV30 du jour dans la fourchette sur un an de
la volatilité réalisée à 30 jours. L'IV étant d'habitude au-dessus de la volatilité réalisée, ce
proxy surestime un peu l'IV Rank. La méthode utilisée est notée dans `metrics.iv_rank_method`.

Pour voir les deals du jour sans base de données :

```bash
cd backend
python -m scripts.screen            # univers complet, compte vide de 20 000
python -m scripts.screen SPY NVDA F
```

## Ordres Alpaca paper (Sprint 3)

Accepter une opportunité passe l'ordre d'ouverture ; la rejeter enregistre le motif.

```bash
curl -X POST localhost:8000/opportunities/12/accept -H 'Content-Type: application/json' \
  -d '{"idempotency_key": "<uuid généré au clic>"}'
curl -X POST localhost:8000/opportunities/13/reject -H 'Content-Type: application/json' \
  -d '{"idempotency_key": "<uuid>", "reason": "no_conviction", "note": "Fed demain"}'
```

| Étape | Comportement |
| --- | --- |
| Acceptation | Contrôle de nouveau la limite de 50 % engagé et la position déjà ouverte sur le titre, puis ordre limite *day* au crédit mid du screener (`limit_price` pour le changer). Spread en ordre multi-jambes, CSP et Covered Call en ordre simple. Un double clic avec la même clé n'envoie qu'un ordre. |
| Exécution | Le worker synchronise les ordres toutes les 5 minutes (8 h-17 h, heure de New York). Au remplissage, la position passe `open` avec le crédit réellement reçu et un ordre GTC de rachat à 50 % du crédit part aussitôt. Un ordre d'ouverture expiré ou refusé passe la position en `canceled`. |
| Stop et 21 DTE | Pendant la séance, chaque position est valorisée au mid (table `position_marks`). Stop (coût de rachat ≥ 2x le crédit) ou 21 DTE : l'ordre GTC est annulé et confirmé, puis rachat au prix naturel (vendeur à l'ask, acheteur au bid), re-tarifé au bout de 15 minutes s'il n'est pas exécuté. |
| Expiration | Une expiration sans valeur clôt la position en gardant tout le crédit. |
| Wheel | Un CSP assigné devient un lot de 100 actions par contrat au prix de revient strike − crédit (la prime n'est pas comptée deux fois). Le Covered Call accepté est rattaché au lot. Quand les actions sont appelées, le call garde sa prime et le lot réalise (strike − prix de revient) × actions. |

Alpaca publie les assignations et expirations du compte paper le lendemain matin seulement ;
le worker les lit chaque matin avant l'ouverture. Une assignation anticipée sur un spread
n'est pas traitée automatiquement : elle est signalée dans `position_events`.

Pour vérifier la connexion sans base (compte, cotations, forme des ordres) :

```bash
cd backend
python -m scripts.broker_check                 # lecture seule
python -m scripts.broker_check --probe-orders  # + 4 ordres impossibles à exécuter, annulés aussitôt
```

## Interface (étape 4)

| Page | Contenu |
| --- | --- |
| Tableau de bord | Capital (départ + P&L réalisé), disponible, immobilisé, marge restante sous la limite de 50 %, P&L réalisé et latent, deals à traiter |
| Opportunités | Une carte par deal du jour : strikes, échéance, delta, PoP, rendement/risque, AROC, IV Rank, capital requis, poids dans le compte, gain visé à 50 %, stop et date de sortie. *Accepter* demande une confirmation (crédit limite modifiable), *Rejeter* demande un motif et une note facultative |
| Positions | Positions ouvertes et ordres en attente : crédit d'entrée, dernier mark, P&L latent, % du crédit capturé, état de l'ordre de sortie, bouton *Racheter*. Actions détenues par la wheel. Rafraîchie toutes les 30 secondes |
| Historique | Positions fermées, expirées, assignées ou jamais exécutées, et deals rejetés ou non traités ; filtres par statut, titre, stratégie et période |

Le navigateur ne parle qu'au serveur Next.js : les clics passent par des *server actions* qui
appellent l'API. Chaque clic génère une clé d'idempotence, gardée en cas de nouvel essai.

**Rachat manuel** (`POST /positions/{id}/close`). Même chemin qu'un stop : l'ordre GTC de prise
de profit est annulé et confirmé, puis la position est rachetée au prix naturel par un ordre
limite *day*. Refusé marché fermé ou sans cotation. Si l'ordre n'est pas exécuté dans la journée,
l'ordre de prise de profit est replacé.

Routes de lecture : `GET /dashboard`, `GET /opportunities?status=proposed`, `GET /positions`,
`GET /history?kind=closed&kind=rejected&underlying=SPY&strategy=put_credit_spread&since=2026-10-01`.

## Garde-fou paper

`BROKER_ENV` n'accepte que `paper`, et les URL du broker sont fixées dans le code sur les
environnements démo (`app/config.py`). Passer en réel demande une modification de code.
Le client Alpaca refuse en plus toute autre URL que celle du compte paper.
