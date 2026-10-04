# Réglages et backtests depuis l'interface

Deux pages s'ajoutent à l'interface : **Réglages** pour éditer des profils de paramètres, et
**Backtests** pour rejouer un profil sur 2019-2026 et comparer les résultats.

## Profils de réglages

Un profil est un jeu complet de paramètres de la stratégie, avec un nom. Chaque filtre et
chaque règle de sortie a un interrupteur : le désactiver garde son seuil pour plus tard.

| Groupe | Réglages |
| --- | --- |
| Univers | ETF, grandes valeurs, wheel (chaque groupe activable, listes de titres éditables), strike max de la wheel |
| Filtres des contrats | DTE min/max, delta min/max ; interrupteurs IV Rank, open interest, volume, écart bid/ask, résultats trimestriels, AROC |
| Indicateurs optionnels (désactivés par défaut) | Tendance : clôture au-dessus de sa moyenne mobile (200 jours par défaut). Prime de volatilité : IV30 / HV30 au moins égal à un seuil |
| Construction | Largeurs de spread essayées, crédit minimum |
| Risque | % max par trade, % engagé max, deals par jour, limite par secteur (interrupteur, et option « compter les positions ouvertes »), délai avant de revenir sur un titre après une clôture |
| Sorties | Objectif de gain, stop, sortie avant l'échéance : chacun activable |
| Score | Poids PoP, AROC et IV Rank (0 retire l'indicateur du classement) |

Au premier affichage, les paramètres actifs deviennent le profil **Actuel**, et un profil
**Prudent** (ETF + wheel, 40-55 DTE, delta 0.10-0.20) est créé. Les valeurs par défaut n'ont
pas changé : le profil Actuel reproduit exactement le backtest de la PR #8 (-19,1 %/an,
1 605 trades), le profil Prudent aussi (+4,8 %/an, 792 trades).

**Profil actif.** Le screener tourne sur le profil actif. « Activer pour le screener », ou
enregistrer le profil actif, écrit une nouvelle version dans `strategy_configs` : chaque
opportunité garde la version exacte avec laquelle elle a été proposée, et une position ouverte
garde les règles de sortie de sa version (un stop désactivé après coup ne s'applique pas aux
positions déjà ouvertes). Sans objectif de gain, aucun ordre GTC n'est placé après l'ouverture.

## Backtests

Un backtest se lance depuis la page Backtests (profil, période, capital, hypothèses avancées
de reconstruction des prix) ou directement depuis l'éditeur de réglages avec les valeurs
affichées, même non enregistrées. La fiche d'un backtest montre le rendement et le drawdown
face à SPY, la courbe du compte, les rendements par année, l'entonnoir du screener, les
résultats par sortie, groupe, année, DTE et titre, et la liste des trades. « Enregistrer ces
réglages comme profil » transforme un essai en profil. La page Comparer superpose jusqu'à
cinq backtests (courbes, mesures, années, réglages qui diffèrent).

**Exécution.** L'API met le backtest en file d'attente (`backtest_runs`) ; le worker la
vérifie toutes les 15 secondes et exécute les backtests un par un dans un processus séparé,
pour que le moniteur des positions reste à l'heure. Un backtest complet prend 30 à
90 secondes. Un backtest interrompu par un arrêt du worker est marqué en échec au
redémarrage.

**Historique de marché.** Il est gardé en base (`market_history_cache`, ~1 Mo compressé). Le
premier backtest le télécharge depuis Yahoo (quelques minutes, réseau nécessaire depuis le
worker) ; ensuite seuls les titres ajoutés à un univers sont téléchargés. La case
« Retélécharger l'historique » le met à jour jusqu'à aujourd'hui. Pour éviter le premier
téléchargement, on peut charger le fichier de la PR #8 :

```bash
cd backend
python -m scripts.backtest import --cache /chemin/vers/history.json
```

## Premiers résultats des nouveaux réglages

Mêmes données et hypothèses que la PR #8, du 02/01/2019 au 02/10/2026, capital 20 000 $.

| Profil | Rendement/an | Drawdown max | Trades | Profit factor |
| --- | --- | --- | --- | --- |
| Actuel | -19,1 % | 86,9 % | 1 605 | 0,74 |
| Prudent | +4,8 % | 25,6 % | 792 | 1,28 |
| Prudent, limite secteur sur positions ouvertes + 1 jour avant de revenir | +5,3 % | 16,6 % | 684 | 1,39 |
| Prudent + tendance (moyenne 200 jours) | +2,5 % | 13,8 % | 397 | 1,29 |
| Prudent sans filtre IV Rank | +6,3 % | 25,3 % | 1 231 | 1,23 |
| Prudent sans sortie avant l'échéance | +5,1 % | 25,2 % | 770 | 1,28 |

SPY acheté et conservé : +15,6 %/an hors dividendes.

## Limites

- La wheel n'est pas assignée dans le backtest : sans sortie avant l'échéance, un put dans la
  monnaie à l'échéance est clôturé à sa valeur intrinsèque (même P&L qu'une assignation suivie
  d'une revente immédiate des actions), sans covered calls ensuite.
- En backtest, la volatilité implicite des actions est reconstruite depuis la volatilité
  réalisée : le filtre IV / HV n'y est informatif que pour SPY et QQQ (VIX et VXN).
- Les filtres open interest et volume n'agissent pas en backtest (options supposées liquides).
- Par défaut la limite par secteur compte les deals du jour seulement, comme avant ; l'option
  « compter les positions ouvertes » change ce comportement en live comme en backtest.

## Ce que seule la CI vérifie

Dans l'environnement de développement de cette PR, PyPI et npm étaient bloqués : la
migration `0003`, les tests base de données et API (`tests/test_lab.py`, `test_schema.py`),
`alembic check`, le lint et le build Next.js ne tournent que dans la CI. Les tests du domaine
et du moteur de backtest (`test_settings_toggles.py`, `test_screener.py`, `test_backtest.py`,
`test_engines.py`) et ruff ont tourné en local, ainsi que les six backtests du tableau
ci-dessus sur l'historique réel. Le worker en processus séparé n'est vérifiable qu'en le
lançant (`docker compose up`).
