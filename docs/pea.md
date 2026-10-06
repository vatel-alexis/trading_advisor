# Portefeuille PEA

Page `/pea` du site : répartition mensuelle d'ETF éligibles PEA (investisseur français, en euros),
par niveau de risque, avec alerte d'ajustement. Propositions uniquement : aucun ordre n'est passé.

## Règle

- Univers : Amundi PEA S&P 500 (PSP5), Nasdaq-100 (PUST), MSCI Europe (PCEU), Émergents ESG (PAEEM),
  Japon Topix (PTPXE) ; repli sur Amundi PEA Euro Court Terme (OBLI, monétaire €STR).
  Le PEA n'accepte ni or ni obligations : le monétaire est le seul actif défensif.
- Le dernier jour de bourse du mois : score = moyenne des performances 1, 3, 6 et 12 mois de chaque ETF actions.
  Un niveau garde ses N meilleurs ; un ETF n'est acheté que si son score est positif et son cours au-dessus de
  sa moyenne 10 mois, sinon sa part va au monétaire.
- Niveaux : Sécurité = 60 % rotation (3 meilleurs) + 40 % monétaire ; Moyen = rotation 3 meilleurs ;
  Dynamique = rotation 2 meilleurs.
- Exécution au cours de clôture suivant le signal ; rééquilibrage aussi quand une ligne s'écarte de plus de
  5 points de sa cible ; coût 0,3 % par euro acheté ou vendu.

## Résultats (janvier 2007 → 6 octobre 2026, par an)

| Niveau | 1 an | 3 ans | 5 ans | 10 ans | 15 ans | Depuis 2007 | Baisse max | Pire année | Ajust./an |
|---|---|---|---|---|---|---|---|---|---|
| Sécurité | +16,5 % | +12,4 % | +7,3 % | +6,6 % | +6,6 % | +6,1 % | 15 % | 2022 -11,0 % | 5,8 |
| Moyen | +26,6 % | +19,1 % | +10,8 % | +10,5 % | +10,6 % | +9,6 % | 23 % | 2022 -17,6 % | 5,8 |
| Dynamique | +26,4 % | +22,1 % | +12,2 % | +10,7 % | +10,9 % | +10,1 % | 23 % | 2022 -22,7 % | 5,4 |
| S&P 500 conservé | +21,9 % | +20,9 % | +14,5 % | +15,2 % | +16,7 % | +11,8 % | 51 % | 2008 -34,0 % | 0 |

## Données

Prix quotidiens ajustés Yahoo. Les ETF PEA sont récents (2016-2019) : avant leur date, leur indice est
reconstitué avec l'ETF américain équivalent converti en euros (SPY, QQQ, VGK, EEM, EWJ et EURUSD=X ;
corrélation mensuelle 0,97-0,99 et performances à ±0,5 point par an près sur la période commune).
Le monétaire OBLI ne suit l'€STR que depuis octobre 2024 : avant, un taux court euro annuel approché
(EONIA puis €STR, moins 0,25 % de frais) est utilisé.

## Fonctionnement

- `app/domain/pea.py` : signal, backtest et rapport (bibliothèque standard).
- Le worker recalcule le rapport chaque jour après 18 h (Paris), week-end compris (le signal de fin de mois est confirmé le 1er du mois) et le stocke dans `pea_reports`
  (45 jours gardés) ; `python -m app.worker pea` le force.
- API : `GET /pea` (rapport complet), `GET /pea/alert` (bandeau).
- Alerte : pendant 10 jours après un signal qui change une répartition, un bandeau apparaît sous l'état du
  système et le niveau concerné est marqué sur la page. En cours de mois, la page montre aussi le signal
  provisoire (« si le mois finissait aujourd'hui »).
