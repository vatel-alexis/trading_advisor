# Résultats du backtest

Généré par `python -m scripts.backtest run`, du 2019-01-02 au 2026-10-02, capital 20 000 $.
Méthode, hypothèses et limites : voir [backtest.md](backtest.md).

## Scénarios

| Scénario | Final | Rendement/an | Drawdown max | Sharpe | Trades | Gagnants | Profit factor | Durée |
|---|---|---|---|---|---|---|---|---|
| Paramètres actuels | 3 883 $ | -19.1 % | 86.9 % | -0.82 | 1605 | 63 % | 0.74 | 7 j |
| Entrée à 40-55 DTE | 13 913 $ | -4.6 % | 72.1 % | -0.10 | 1070 | 76 % | 0.91 | 12 j |
| Sans stop | 4 971 $ | -16.4 % | 84.7 % | -0.67 | 1821 | 64 % | 0.79 | 8 j |
| Stop à 3x le crédit | 3 804 $ | -19.3 % | 87.6 % | -0.87 | 1477 | 64 % | 0.73 | 7 j |
| Delta 0.10-0.20 | 12 336 $ | -6.0 % | 63.7 % | -0.31 | 1997 | 69 % | 0.88 | 7 j |
| Sans filtre IV Rank | 3 893 $ | -19.0 % | 84.9 % | -0.89 | 1333 | 64 % | 0.65 | 7 j |
| IV Rank > 50 | 3 574 $ | -19.9 % | 87.0 % | -1.02 | 999 | 62 % | 0.61 | 7 j |
| Sortie à 14 DTE | 21 699 $ | 1.1 % | 67.5 % | 0.17 | 1717 | 78 % | 1.01 | 9 j |
| ETF seuls | 21 516 $ | 0.9 % | 32.2 % | 0.14 | 552 | 67 % | 1.04 | 6 j |
| Grandes valeurs seules, 40-55 DTE | 7 523 $ | -11.8 % | 78.4 % | -0.55 | 707 | 70 % | 0.73 | 13 j |
| Wheel seule, 40-55 DTE | 27 047 $ | 4.0 % | 7.7 % | 1.26 | 519 | 78 % | 1.76 | 13 j |
| ETF + wheel, 40-55 DTE | 27 737 $ | 4.3 % | 30.8 % | 0.40 | 784 | 76 % | 1.16 | 12 j |
| ETF + wheel, 40-55 DTE, delta 0.10-0.20 | 28 846 $ | 4.8 % | 25.6 % | 0.58 | 792 | 78 % | 1.28 | 11 j |
| Actuel, options moins chères | 3 774 $ | -19.4 % | 86.5 % | -0.92 | 1012 | 63 % | 0.67 | 7 j |
| Actuel, options plus chères | 3 971 $ | -18.8 % | 87.7 % | -0.84 | 1765 | 61 % | 0.76 | 7 j |
| Prudent, options moins chères | 24 347 $ | 2.6 % | 24.1 % | 0.34 | 783 | 76 % | 1.14 | 10 j |
| Prudent, options plus chères | 34 454 $ | 7.3 % | 24.8 % | 0.89 | 751 | 80 % | 1.47 | 13 j |
| Prudent, entrée à mi-chemin du naturel | 24 801 $ | 2.8 % | 24.8 % | 0.38 | 769 | 77 % | 1.16 | 12 j |

Référence : SPY acheté et conservé (hors dividendes) finit à 61 527 $ avec un drawdown max de 34.1 %.

## Paramètres actuels

| Mesure | Valeur |
|---|---|
| Période | 2019-01-02 → 2026-10-02 |
| Capital initial → final | 20 000 $ → 3 883 $ |
| Rendement annualisé | -19.1 % |
| Drawdown max | 86.9 % |
| Sharpe (journalier, sans taux) | -0.82 |
| Trades clôturés | 1605 |
| Trades gagnants | 63.3 % |
| Gain moyen / perte moyenne | 45 $ / -105 $ |
| Profit factor | 0.74 |
| Durée moyenne | 7.0 jours |
| Capital engagé moyen | 30.5 % |
| Jours avec au moins un deal | 42.8 % |

| Sortie | Trades | Gagnants | P&L total | P&L moyen |
|---|---|---|---|---|
| profit_target | 706 | 100.0 % | 38 888 $ | 55 $ |
| stop_loss | 196 | 0.0 % | -40 301 $ | -206 $ |
| time_exit | 703 | 44.1 % | -14 704 $ | -21 $ |

| Groupe | Trades | Gagnants | P&L total | P&L moyen |
|---|---|---|---|---|
| etf | 266 | 69.9 % | 3 538 $ | 13 $ |
| large_cap | 1116 | 59.4 % | -21 631 $ | -19 $ |
| wheel | 223 | 74.9 % | 1 976 $ | 9 $ |

| Année d'entrée | Trades | Gagnants | P&L total | P&L moyen |
|---|---|---|---|---|
| 2019 | 242 | 73.1 % | 8 240 $ | 34 $ |
| 2020 | 281 | 63.3 % | -16 332 $ | -58 $ |
| 2021 | 132 | 68.2 % | 426 $ | 3 $ |
| 2022 | 345 | 51.6 % | -6 803 $ | -20 $ |
| 2023 | 153 | 70.6 % | 323 $ | 2 $ |
| 2024 | 279 | 63.1 % | -970 $ | -3 $ |
| 2025 | 145 | 63.4 % | -379 $ | -3 $ |
| 2026 | 28 | 60.7 % | -623 $ | -22 $ |

| DTE à l'entrée | Trades | Gagnants | P&L total | P&L moyen |
|---|---|---|---|---|
| 25+ | 1059 | 58.6 % | -19 990 $ | -19 $ |
| 30+ | 318 | 68.9 % | 1 474 $ | 5 $ |
| 35+ | 66 | 71.2 % | 73 $ | 1 $ |
| 40+ | 43 | 76.7 % | 393 $ | 9 $ |
| 45+ | 58 | 79.3 % | 934 $ | 16 $ |
| 50+ | 61 | 82.0 % | 1 000 $ | 16 $ |

## Scénario prudent

| Mesure | Valeur |
|---|---|
| Période | 2019-01-02 → 2026-10-02 |
| Capital initial → final | 20 000 $ → 28 846 $ |
| Rendement annualisé | 4.8 % |
| Drawdown max | 25.6 % |
| Sharpe (journalier, sans taux) | 0.58 |
| Trades clôturés | 792 |
| Trades gagnants | 77.5 % |
| Gain moyen / perte moyenne | 66 $ / -178 $ |
| Profit factor | 1.28 |
| Durée moyenne | 11.2 jours |
| Capital engagé moyen | 24.6 % |
| Jours avec au moins un deal | 26.2 % |

| Sortie | Trades | Gagnants | P&L total | P&L moyen |
|---|---|---|---|---|
| profit_target | 584 | 100.0 % | 39 533 $ | 68 $ |
| stop_loss | 160 | 0.0 % | -30 793 $ | -192 $ |
| time_exit | 48 | 62.5 % | 18 $ | 0 $ |

| Groupe | Trades | Gagnants | P&L total | P&L moyen |
|---|---|---|---|---|
| etf | 343 | 74.3 % | 2 202 $ | 6 $ |
| wheel | 449 | 80.0 % | 6 557 $ | 15 $ |

| Année d'entrée | Trades | Gagnants | P&L total | P&L moyen |
|---|---|---|---|---|
| 2019 | 91 | 84.6 % | 3 944 $ | 43 $ |
| 2020 | 118 | 69.5 % | -306 $ | -3 $ |
| 2021 | 64 | 92.2 % | 2 513 $ | 39 $ |
| 2022 | 158 | 69.0 % | -2 432 $ | -15 $ |
| 2023 | 61 | 82.0 % | 1 291 $ | 21 $ |
| 2024 | 127 | 79.5 % | 746 $ | 6 $ |
| 2025 | 98 | 78.6 % | 1 047 $ | 11 $ |
| 2026 | 75 | 78.7 % | 1 956 $ | 26 $ |

| DTE à l'entrée | Trades | Gagnants | P&L total | P&L moyen |
|---|---|---|---|---|
| 40+ | 317 | 74.4 % | 797 $ | 3 $ |
| 45+ | 261 | 77.8 % | 3 963 $ | 15 $ |
| 50+ | 214 | 81.8 % | 3 998 $ | 19 $ |
