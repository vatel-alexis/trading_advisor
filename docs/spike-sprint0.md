# Spike données Sprint 0 — résultats du 2026-10-03

Source : Yahoo Finance (cotations de clôture du vendredi 2 octobre 2026), 20 titres, puts uniquement.
Deux échéances tombent dans la fenêtre 30-50 DTE : 6 et 20 novembre 2026.

## Entonnoir avec les seuils actuels

DTE 30-50, delta 0,15-0,30, OI ≥ 500, volume ≥ 100, spread ≤ 10 %, échéance avant les earnings.

| ticker | spot | IV/HV | puts | dte | delta | OI | volume | spread | earnings |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| SPY | 769,64 | 1,16 | 524 | 297 | 48 | 16 | 14 | 14 | 14 |
| QQQ | 749,58 | 1,21 | 550 | 327 | 64 | 13 | 11 | 11 | 11 |
| IWM | 281,52 | 1,42 | 243 | 157 | 24 | 12 | 10 | 10 | 10 |
| AAPL | 333,69 | 1,18 | 114 | 80 | 7 | 4 | 4 | 4 | 0 |
| MSFT | 517,53 | 1,47 | 177 | 130 | 11 | 7 | 4 | 3 | 0 |
| AMZN | 251,52 | 1,58 | 108 | 75 | 6 | 5 | 5 | 4 | 0 |
| GOOGL | 343,50 | 1,42 | 148 | 108 | 8 | 7 | 4 | 4 | 0 |
| META | 728,08 | 0,90 | 296 | 200 | 19 | 10 | 4 | 4 | 0 |
| NVDA | 233,95 | 0,72 | 131 | 88 | 5 | 5 | 5 | 4 | 1 |
| AMD | 633,91 | 0,94 | 263 | 169 | 15 | 5 | 5 | 5 | 0 |
| JPM | 332,38 | 1,60 | 90 | 68 | 6 | 3 | 1 | 1 | 0 |
| BAC | 53,75 | 1,21 | 58 | 37 | 3 | 2 | 2 | 1 | 0 |
| XOM | 164,01 | 1,22 | 57 | 36 | 6 | 4 | 1 | 1 | 0 |
| KO | 85,65 | 1,63 | 53 | 34 | 5 | 2 | 2 | 1 | 0 |
| PFE | 27,80 | 1,34 | 48 | 31 | 4 | 1 | 1 | 0 | 0 |
| F | 12,10 | 1,24 | 56 | 33 | 1 | 1 | 1 | 0 | 0 |
| T | 24,30 | 1,66 | 43 | 23 | 2 | 2 | 0 | 0 | 0 |
| INTC | 119,33 | 1,07 | 159 | 88 | 8 | 4 | 4 | 4 | 0 |
| SOFI | 15,77 | 1,17 | 84 | 57 | 3 | 3 | 3 | 3 | 0 |
| AAL | 12,94 | 1,67 | 51 | 31 | 4 | 2 | 1 | 1 | 0 |

36 contrats retenus, tous sur SPY, QQQ, IWM sauf un put NVDA.

## Taux de passage par filtre (par groupe)

Chaque pourcentage est le taux de passage par rapport à l'étape précédente.

| groupe | dte | delta | OI ≥ 500 | vol ≥ 100 | spread ≤ 10 % | earnings |
|---|---:|---:|---:|---:|---:|---:|
| ETF (3) | 59 % | 17 % | 30 % | 85 % | 100 % | 100 % |
| Grandes valeurs (11) | 67 % | 9 % | 58 % | 70 % | 90 % | 3 % |
| Petits titres < 60 $ (6) | 62 % | 8 % | 65 % | 73 % | 62 % | 0 % |

Le filtre delta ne réduit pas la qualité : il ne garde que 2 à 4 strikes par échéance, c'est attendu.

## Sensibilité des seuils

Contrats retenus avant le filtre earnings, et titres ayant au moins un contrat :

| variante | ETF | grandes valeurs | petits titres |
|---|---:|---:|---:|
| seuils actuels | 35 (3/3) | 35 (11/11) | 5 (3/6) |
| delta 0,10-0,30 | 53 (3/3) | 54 (11/11) | 7 (4/6) |
| OI ≥ 100, vol ≥ 10 | 89 (3/3) | 56 (11/11) | 5 (3/6) |
| OI ≥ 100, vol ≥ 10, spread ≤ 15 % | 89 (3/3) | 66 (11/11) | 9 (5/6) |
| idem + DTE 25-55 | 141 (3/3) | 111 (11/11) | 13 (5/6) |

## Constats

1. **Le filtre earnings élimine toutes les actions individuelles en octobre.** Toutes publient entre
   le 13 octobre et le 17 novembre, avant les échéances de novembre. C'est saisonnier : sur l'année, une
   échéance à 30-50 jours précède les earnings environ une fois sur deux. En pleine saison, seuls les
   ETF (SPY, QQQ, IWM) restent tradables.
2. **OI ≥ 500 et volume ≥ 100 sont trop stricts** pour les strikes OTM à 30-50 DTE : ils divisent par
   deux ou trois les candidats des ETF, pourtant les plus liquides du marché. OI ≥ 100 et volume ≥ 10
   suffisent, le spread bid/ask reste le vrai garde-fou de liquidité.
3. **Spread ≤ 10 % pénalise les primes basses** (titres sous 20 $, primes de 0,20 à 0,50 $ où un tick de
   0,05 $ pèse 10 à 25 %). Un seuil à 15 % fait passer 5 petits titres sur 6.
4. **Univers wheel (strike < 20 $) très mince** : F, SOFI, AAL seulement dans cet échantillon. Avec
   20 k et 10 % max par trade (2 000 $ de collatéral, donc strike ≤ 20), il faut un univers plus large
   de titres sous 20 $ pour avoir des candidats chaque semaine.
5. **IV/HV entre 0,7 et 1,7** : NVDA (0,72), META (0,90) et AMD (0,94) ont une IV sous la volatilité
   réalisée, peu favorables à la vente de prime.

## Recommandation de recalibrage

- OI ≥ 100, volume ≥ 10, spread ≤ 15 %, DTE 25-55.
- Garder le filtre earnings (règle de gestion du risque), mais toujours inclure les ETF pour avoir des
  candidats pendant les saisons de résultats.
- Élargir l'univers wheel à une vingtaine de titres sous 20 $.

## Limites

- Yahoo ne fournit ni IV Rank, ni grecs, ni historique de chaînes : delta et POP sont recalculés en
  Black-Scholes à partir de l'IV de Yahoo.
- Données d'un seul jour (vendredi), sans comparaison Alpaca/Tradier faute de clés démo.
