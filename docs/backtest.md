# Backtest de la stratégie (2019-2026)

Simulation jour par jour de la stratégie sur l'historique, pour valider les seuils avant de
trader. Les chiffres détaillés sont dans [backtest-resultats.md](backtest-resultats.md).

## En bref

- **Avec les paramètres actuels, la stratégie perd de l'argent** : 20 000 $ deviennent 3 883 $
  entre janvier 2019 et octobre 2026 (-19 %/an, drawdown max 87 %). Le résultat ne dépend pas
  des hypothèses du modèle : options plus chères ou moins chères, il reste à -19 %/an.
- **Première cause : les entrées trop proches de la sortie à 21 DTE.** Le score favorise
  l'AROC, donc les échéances les plus courtes : deux tiers des trades entrent à 25-29 DTE et
  sont fermés une semaine plus tard. Ils encaissent peu de theta et paient deux fois la
  fourchette. Ces trades perdent 20 000 $ au total ; ceux entrés à 30 DTE ou plus gagnent.
- **Deuxième cause : les credit spreads sur grandes valeurs.** Ils perdent dans tous les
  scénarios (-12 %/an seuls, même à 40-55 DTE). Les actions individuelles décrochent plus
  fort que les ETF et ensemble : le 24 février 2020, GOOGL, AMZN, AAPL et MSFT sont stoppés
  le même jour (-4 600 $), rouverts le soir même et stoppés de nouveau le 27 (-3 800 $). La
  limite de 2 trades par secteur ne compte que les deals du jour, pas les positions déjà
  ouvertes, et rien n'empêche de rouvrir un titre juste après son stop.
- **Le stop et le filtre IV Rank changent peu** : sans stop -16 %/an, stop à 3x -19 %/an,
  IV Rank 0 ou 50 au lieu de 30 : -19 à -20 %/an.
- **Réglage prudent** (ETF + wheel, entrée à 40-55 DTE, delta 0.10-0.20) : +4,8 %/an,
  drawdown max 26 %, de +2,6 à +7,3 %/an selon les hypothèses. C'est positif, mais proche du
  taux sans risque et loin de SPY acheté et conservé (+15,6 %/an hors dividendes, drawdown
  34 %). Le backtest ne montre pas d'avantage net de la vente de primes avec ces règles.
- **La wheel ne tourne jamais** : la sortie à 21 DTE ferme les puts avant l'échéance, donc
  aucune assignation et aucun covered call. Les puts sur titres à moins de 20 $ restent
  pourtant le meilleur morceau (+4 %/an, drawdown 8 %).

## Méthode

Code : `backend/app/backtest/` (données, chaînes reconstruites, simulation, rapport) et
`backend/scripts/backtest.py`. Le backtest réutilise le moteur réel : chaque jour, à la
clôture, le **screener de l'application** (`app.domain.screener.screen`) choisit les deals
sur une chaîne d'options reconstruite, avec les mêmes paramètres, le même score, le même
dimensionnement (10 % par trade, 50 % engagés au plus, 5 deals par jour, 2 par secteur) et
le refus d'un deuxième trade sur un sous-jacent déjà ouvert. Les sorties appellent
`app.domain.exits.evaluate_exit` (objectif 50 %, stop à 2x le crédit, sortie à 21 DTE).
Toutes les propositions sont acceptées. Le capital suit le P&L réalisé, comme dans
l'application.

**Primes reconstruites.** Yahoo ne fournit pas d'historique de chaînes d'options, donc
chaque put est valorisé par Black-Scholes à partir du cours de clôture et d'une volatilité
estimée :

- volatilité ATM : VIX x 0,85 pour SPY, VXN x 0,85 pour QQQ ; pour les autres titres, la
  volatilité réalisée sur 30 jours (hors jours de résultats) x 1,2 ;
- skew : IV(K) = IV ATM + 0,25 x ln(S/K) / racine(T) pour les ETF, 0,15 pour les actions ;
- fourchette bid/ask : 0,8 % du prix pour les ETF, 5 % pour les grandes valeurs, 6 % pour
  la wheel (au moins 0,5 cent de chaque côté) ;
- exécution comme l'application : entrée au prix milieu, objectif à son prix limite (ordre
  GTC), stop et sortie à 21 DTE au prix naturel, plafonnés à la largeur du spread.

Ces valeurs viennent des chaînes Yahoo du 2 octobre 2026 : la volatilité implicite mesurée
sur SPY, QQQ et IWM pour le skew et le rapport au VIX, et les fourchettes médianes des puts
de delta 0.10-0.35 pour les coûts. L'IV Rank se calcule sur l'année précédente de la
volatilité reconstruite.

**Échéances et strikes** : vendredis dans la fenêtre de DTE (troisièmes vendredis seulement
pour la wheel), pas de strike de 1 $ pour les ETF, de 0,5 à 5 $ selon le prix des actions.

**Stops intrajournaliers** : le moniteur de l'application vérifie toutes les 5 minutes, donc
le stop est testé sur le plus bas du jour (rempli au niveau du stop, ou à l'ouverture après
un gap) et l'objectif sur le plus haut, avec la volatilité de la clôture.

**Splits** : les cours sont ajustés pour la volatilité, mais les strikes et les montants
utilisent le prix réellement coté ce jour-là (NVDA, AAPL, AMZN, GOOGL ont splitté).

**Résultats d'entreprises** : calendrier Yahoo jusqu'à mi-2025, puis dates estimées tous
les 91 jours.

## Limites

- Les primes sont un modèle, pas des prix observés : la volatilité implicite réelle peut
  s'écarter du proxy, surtout dans les crises et sur les petits titres. Les scénarios
  « options plus chères / moins chères » donnent l'ordre de grandeur de cet effet.
- Les filtres de liquidité (open interest, volume) ne sont pas simulés : toutes les options
  reconstruites sont supposées liquides, seule la fourchette est filtrée.
- Les entrées au prix milieu sont supposées exécutées. Le scénario « entrée à mi-chemin du
  naturel » montre le coût d'un remplissage moins favorable.
- Pas de dividendes, pas d'assignation anticipée, pas de covered calls.
- L'univers est celui d'aujourd'hui (biais du survivant), et le secteur de chaque titre est
  son secteur actuel.
- 2019-2026 contient deux krachs (2020, 2022) qui pèsent lourd ; la période est courte pour
  conclure sur un réglage précis.

## Ce que je recommande

1. Ne pas utiliser les paramètres actuels tels quels.
2. Passer la fenêtre d'entrée à 40-55 DTE : c'est la correction la plus solide, avec un
   mécanisme clair (laisser au trade le temps de vieillir avant la sortie à 21 DTE).
3. Retirer les credit spreads sur grandes valeurs, ou les limiter à un trade ouvert à la
   fois.
4. Garder le paper trading pour mesurer les vraies exécutions et la vraie volatilité
   implicite, et recalibrer le modèle avec les chaînes que l'application enregistre.

À corriger dans le moteur, indépendamment des paramètres : compter les positions déjà
ouvertes dans la limite par secteur, et ne pas rouvrir un titre le jour de son stop.

## Relancer

```bash
cd backend
python -m scripts.backtest fetch                 # historique Yahoo -> .cache/backtest.json
python -m scripts.backtest run --out ../docs     # 18 scénarios, environ 8 minutes sur 4 cœurs
python -m scripts.backtest run --scenarios actuel dte_40_55
```

Les scénarios sont définis dans `SCENARIOS` (`backend/scripts/backtest.py`) ; les hypothèses
du modèle dans `ModelConfig` (`backend/app/backtest/synth.py`).
