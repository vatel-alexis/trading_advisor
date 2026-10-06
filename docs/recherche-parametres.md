# Recherche de paramètres (2026-10-06)

Backtests lancés dans le conteneur de Claude (hors du site en ligne et de sa base), moteur 2,
`history.json`, 2019-01-02 → 2026-10-02, capital 20 000 $, exécution **réaliste**.
Outil : `backend/scripts/param_search.py`. Données brutes : dossier partagé du projet, `backtest/recherche/`.

## Méthode contre la sur-optimisation

- **Choix sur la calibration seulement** (2019-01 → 2023-08, 60 % des jours) ; le hors échantillon
  (2023-08 → 2026-10) ne sert qu'à juger.
- **Plateau plutôt que pic** : chaque combinaison est notée avec la moyenne de ses voisines de grille
  (un cran de plus ou de moins sur un paramètre). Un réglage isolé qui brille seul est écarté.
- **Walk-forward ancré** : pour chaque année de 2021 à 2026, on choisit avec la même règle sur les
  années précédentes et on note sur l'année suivante, jamais vue.
- **Grille modeste** : 162 backtests en 2 étapes (entrées puis sorties), pas de recherche fine.
- **Garde-fous fixes** : risque 1 % par trade, perte ouverte 10 %, cluster 5 %, stop mensuel 4 %,
  fenêtre de détention 21 jours. Stop 2x et sortie 21 DTE testés, mais gardés (voir plus bas).

## Étape 1 : entrées (108 backtests)

Stratégies (ETF + Short Put Income / ETF seuls / Short Put Income seul) x delta (4 bandes) x DTE
(42-56, 45-65, 60-80) x IV Rank min (0, 30, 50).

| Stratégies | Combinaisons positives | Médiane /an | Meilleure /an |
|---|---|---|---|
| Spreads ETF seuls | **0 / 36** | -1,15 % | -0,63 % |
| ETF + Short Put Income (actuel) | 24 / 36 | +0,23 % | +0,97 % |
| Short Put Income seul | **36 / 36** | +1,19 % | +1,74 % |

- Les spreads ETF perdent dans toutes les variantes (en moyenne -1 480 $ sur la période quand ils
  sont combinés) : c'est eux qui tirent le profil actuel vers zéro.
- Classement calibration vs hors échantillon : corrélation de rang 0,76. Le signal est réel, et il
  vient surtout de ce choix de stratégie.
- Dans le Short Put Income, la zone stable est **delta 0,15-0,30, IV Rank ≥ 30, DTE 45-65 ou 60-80**.
  Les écarts entre voisins restent petits (±0,3 point/an).

## Étape 2 : sorties (54 backtests sur 3 zones stables)

Objectif de gain 35 / 50 / 75 % x stop 1,5x / 2x / 3x x sortie 14 / 21 DTE.

- **Objectif à 75 %** arrive en tête dans les 3 zones, en calibration comme en hors échantillon.
- Stop 1,5x / 2x / 3x et sortie 14 / 21 DTE : différences de l'ordre du bruit (corrélation
  calibration / hors échantillon proche de 0 dans 2 zones sur 3). On garde donc **stop 2x et
  sortie 21 DTE**, vos décisions du 3 octobre.

## Candidat : « Short Put Income, delta 0,15-0,25, objectif 75 % »

Trois changements par rapport au Prudent actuel : spreads ETF désactivés, delta 0,15-0,25 au lieu de
0,10-0,20, objectif de gain 75 % au lieu de 50 %. Le reste est inchangé (DTE 45-65, IV Rank 30,
stop 2x, sortie 21 DTE, tous les garde-fous).

| Mesure (réaliste) | Actuel | Candidat |
|---|---|---|
| Rendement /an | +0,5 % | **+2,1 %** |
| Calibration /an | +0,8 % | +2,3 % |
| **Hors échantillon /an** | **-0,0 %** (PF 0,98) | **+1,8 %** (PF 3,4) |
| Drawdown max | 3,7 % | 1,8 % |
| Profit factor | 1,17 | 3,01 |
| Trades / gagnants | 436 / 69 % | 150 / 78 % |
| Fenêtres 12 mois positives | 71 % (fragile) | **100 % (robuste)** |
| Pire année | -1,9 % (2022 et 2026) | +1,3 % (2025) |

Années du candidat : 2019 +2,7 %, 2020 +2,7 %, 2021 +2,8 %, 2022 +1,4 %, 2023 +2,0 %, 2024 +1,5 %,
2025 +1,3 %, 2026 +1,8 %.

D'où vient le gain (réaliste, /an) : retirer les ETF +0,9 point, objectif 75 % +0,5, delta +0,2.

**Walk-forward** (étape 1, la règle choisit chaque année sur le passé) : 2021 +2,8 %, 2022 +0,1 %,
2023 +1,2 %, 2024 -0,4 %, 2025 +2,1 %, 2026 +0,7 %, soit environ **+1,1 %/an** en conditions
« jamais vues », contre +0,1 %/an pour le profil actuel sur les mêmes années. Le choix retombe toujours sur Short Put Income,
delta 0,15-0,30, IV Rank 0-30.

## Contrôles de robustesse du candidat

| Variante | Rendement /an | Hors échantillon /an | Drawdown |
|---|---|---|---|
| Optimiste (entrée au mid) | +2,3 % | +2,0 % | 1,7 % |
| Réaliste | +2,1 % | +1,8 % | 1,8 % |
| Pessimiste (naturel, écarts x1,5)* | +1,8 % | +1,5 % | 2,0 % |
| Options moins chères (IV = volatilité réalisée x1,0) | +1,5 % | +1,2 % | 1,3 % |
| Moins chères + écarts x1,5* | +1,4 % | +1,0 % | 1,4 % |
| Sans NIO ni CLF (37 % du gain) | +1,7 % | +1,4 % | 1,8 % |
| Sans les 4 meilleurs titres | +1,4 % | +1,7 % | 0,6 % |
| Stress : glissement +1 demi-écart | +13 % au total (actuel -11 %) | | 2,1 % |
| Stress : taux de gain -10 points | +11 % au total (actuel -7 %) | | 2,0 % |

\* Avec des écarts x1,5, le filtre d'écart bid/ask (15 %) bloque toutes les puts : le candidat ne
trade plus du tout (0 trade, 0 perte). Ces deux lignes sont calculées avec le filtre relâché à 25 %,
pour mesurer le coût seul.

Information à part (garde-fou non modifié) : passer le risque par trade de 1 % à 2 % ne rapporte
rien (+2,0 %/an) ; ce sont les plafonds par secteur, cluster et nombre de positions qui limitent.

## Limites à garder en tête

- **Les gains restent petits** : +2 %/an ≈ 400 $/an sur 20 000 $, capital engagé ~6 %. SPY acheté et
  conservé fait +15,6 %/an (avec un drawdown de 34 %).
- **Prix reconstitués** : les primes des 16 titres Short Put Income viennent de la volatilité réalisée
  x1,2, pas de cotations historiques. Avec x1,0 le candidat reste positif (+1,5 %/an).
- **Biais du survivant** : la liste des 16 titres a été choisie en 2026 parmi des titres encore cotés
  et liquides, ce qui flatte le passé de cette stratégie. Aucun backtest ne corrige ce biais.
- **Concentration** : sans les ETF, tout repose sur 16 actions moyennes ; les limites par secteur et
  par cluster restent actives.
- 162 combinaisons testées sur une seule histoire : le +2,1 % est optimiste, le walk-forward
  (+1,1 %/an) est l'estimation la plus honnête.

## Pistes de rentabilité (ajout du 2026-10-06, réaliste, candidat comme base)

Ce qui limite le candidat, c'est la **perte max ouverte de 10 %** : pour une put vendue, elle compte
l'action tombant à 0 (strike x 100), donc une ou deux puts ouvertes à la fois. Capital engagé ~6 %.

| Variante (garde-fou modifié, à titre d'étude) | /an | Hors échantillon | Drawdown | Pire année |
|---|---|---|---|---|
| Candidat (perte max ouverte 10 %) | +2,1 % | +1,8 % | 1,8 % | +1,3 % |
| Perte max ouverte 20 % | +3,4 % | +3,0 % | 3,0 % | +1,6 % |
| Perte max ouverte 30 % | +3,9 % | +3,9 % | 4,4 % | -0,5 % |
| Perte max ouverte 30 %, options moins chères | +2,6 % | +2,5 % | 4,5 % | -1,3 % |
| Risque 2 % par trade | +2,0 % | +1,7 % | 1,6 % | |
| 10 deals par jour, 3 par secteur | +2,1 % | +1,8 % | 1,8 % | |

Non simulé : les intérêts sur le cash non engagé (~90 % du compte). Au taux des bons du Trésor à
3 mois sur 2019-2026 (environ 2,8 % en moyenne, estimation), cela ajouterait de l'ordre de +2,5 %/an.

## Étude : perte max ouverte et liste élargie (2026-10-06)

Base : le candidat (Short Put Income seul, delta 0,15-0,25, objectif 75 %), exécution réaliste et
variante « options moins chères » (IV = volatilité réalisée x1,0). Les autres garde-fous ne changent
pas (risque 1 % par trade, cluster 5 %, stop mensuel 4 %, drawdown 10 %).

Liste élargie = les 16 titres actuels + 18 titres pris par une règle fixée avant de regarder les
résultats : actions à petit prix avec options actives dès 2019, dans d'autres secteurs, y compris des
titres qui se sont effondrés depuis (GPRO, LUMN, AMC, CCL, BBD) pour limiter le biais du survivant :
PBR, KGC, BTG, GGB, NOK, ERIC, LUMN, SIRI, BCS, BBD, ABEV, CCL, M, GPRO, AMC, HPE, TEVA, GE.
MPW et ET n'ont pas d'historique exploitable sur Yahoo. Historique : dossier partagé du projet, `backtest/history-elargi.json`.

Note : avec 1 % de risque par trade et un choc de 30 %, une put n'a au moins 1 contrat que si l'action
vaut moins de ~11 $. Les titres plus chers de la liste ne tradent donc presque jamais.

### Liste actuelle (16 titres)

| Perte max ouverte | /an | Calibration | Hors éch. | Options moins chères | Drawdown | Pire année | Pire mois | Choc 30 % sur tout |
|---|---|---|---|---|---|---|---|---|
| 10 % (actuel) | +2,1 % | +2,3 % | +1,8 % | +1,5 % | 1,8 % | +1,3 % | -1,2 % | 2,1 % |
| 20 % | +3,4 % | +3,7 % | +3,0 % | +2,0 % | 3,0 % | +1,6 % | -1,7 % | 4,0 % |
| 30 % | +3,9 % | +3,8 % | +3,9 % | +2,6 % | 4,4 % | -0,5 % | -3,0 % | 5,6 % |
| **40 %** | **+4,6 %** | **+4,5 %** | **+4,8 %** | **+3,1 %** | **4,7 %** | **+0,2 %** | **-3,0 %** | **7,5 %** |
| 50 % | +4,8 % | +4,6 % | +5,2 % | +3,4 % | 5,2 % | +0,1 % | -2,9 % | 9,5 % |

« Choc 30 % sur tout » : perte maximale observée si toutes les positions ouvertes perdaient 30 % le même
jour. À 40 % elle reste sous la limite de drawdown de 10 % ; à 50 % elle la touche. Au-delà de 40 %, le
gain plafonne (plateau). Années à 40 % : 2019 +5,4, 2020 +8,9, 2021 +3,5, 2022 +0,2, 2023 +6,4,
2024 +3,1, 2025 +6,9, 2026 +1,6 %.

### Liste élargie (34 titres)

| Perte max ouverte | /an | Calibration | Hors éch. | Options moins chères (calib.) | Drawdown | Pire année | Pire mois |
|---|---|---|---|---|---|---|---|
| 10 % | +1,4 % | +1,1 % | +1,9 % | +0,5 % (-0,1 %) | 4,1 % | +0,5 % | -3,2 % |
| 20 % | +3,6 % | +3,5 % | +3,7 % | +1,8 % (+1,2 %) | 6,8 % | +2,1 % | -5,7 % |
| 30 % | +5,3 % | +5,1 % | +5,6 % | +2,9 % (+2,1 %) | 5,6 % | -0,1 % | -4,0 % |
| 40 % | +6,5 % | +5,5 % | +8,1 % | +3,4 % (+2,5 %) | 7,0 % | -0,5 % | -4,6 % |
| 50 % | +6,9 % | +5,5 % | +9,1 % | +3,8 % (+2,9 %) | 7,5 % | -2,2 % | -5,2 % |

- Plus de rendement, surtout parce que plus de positions s'ouvrent, mais un drawdown plus élevé et des
  mois pires que le stop mensuel de 4 %.
- Le gain se concentre sur 2023-2026. Sur la période de calibration avec des options moins chères, la
  liste élargie fait **moins bien** que la liste actuelle (+2,5 % contre +3,3 %/an à 40 %).
- Rendement / drawdown comparable : environ 0,95 pour les deux listes à 40 %.

### Trier les titres sur leurs résultats : non

Test : retirer les 7 titres perdants sur la calibration (PBR, ITUB, LYFT, AAL, SOFI, KGC, AMC), puis
regarder le hors échantillon. Résultat : +6,1 % au lieu de +5,6 % à 30 %, mais +7,3 % au lieu de +8,1 %
à 40 %, et +3,1 % au lieu de +4,8 % pour la liste actuelle triée. Trier sur le passé n'améliore pas la
suite de façon fiable : c'est de la sur-optimisation.

### Recommandation

Liste actuelle avec une perte max ouverte de 40 % : +4,6 %/an (+3,1 % avec des options moins chères),
hors échantillon +4,8 %, drawdown 4,7 %, aucune année négative. La liste élargie rapporte plus en
backtest mais n'est pas plus efficace par unité de risque, et son avantage dépend de la période.
