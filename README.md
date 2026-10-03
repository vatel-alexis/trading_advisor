# Trading Advisor

Plateforme de paper trading d'options orientée vente de primes : put credit spreads sur les
grandes valeurs liquides, et wheel (CSP puis Covered Calls) sur les titres sous 20 $.
Compte simulé de 20 000, environnement broker démo uniquement.

## Structure

| Dossier | Contenu |
| --- | --- |
| `backend/app` | API FastAPI, worker planifié, modèle de données SQLAlchemy |
| `backend/app/domain` | Moteurs de stratégie et de risque en Python pur (Sprint 2) |
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
python -m scripts.data_spike              # 20 titres par défaut
python -m scripts.data_spike F SOFI AAPL  # titres au choix
```

Le script affiche, pour chaque titre, combien de puts passent chaque filtre (DTE 30-50,
delta 0.15-0.30, open interest, volume, spread, earnings). Il écrit les contrats retenus dans
`spike_results.csv`. Il n'utilise que la bibliothèque standard Python (aucun `pip install`) et a
besoin d'un accès réseau à `fc.yahoo.com`, `query1.finance.yahoo.com` et `query2.finance.yahoo.com`.
Résultats du premier passage : [docs/spike-sprint0.md](docs/spike-sprint0.md).

## Garde-fou paper

`BROKER_ENV` n'accepte que `paper`, et les URL du broker sont fixées dans le code sur les
environnements démo (`app/config.py`). Passer en réel demande une modification de code.
