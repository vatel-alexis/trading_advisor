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
| `backend/app/services` | Exécution du screener : données, moteurs et base |
| `backend/migrations` | Migrations Alembic |
| `backend/scripts/data_spike.py` | Spike données du Sprint 0 |
| `frontend` | Interface Next.js + Tailwind (4 sections) |

## Démarrer avec Docker

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

## Garde-fou paper

`BROKER_ENV` n'accepte que `paper`, et les URL du broker sont fixées dans le code sur les
environnements démo (`app/config.py`). Passer en réel demande une modification de code.
