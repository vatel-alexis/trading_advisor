# Garde-fous de risque

## Profil par défaut

Le profil **Prudent** est actif par défaut : ETF (SPY, QQQ, IWM) en put credit spreads et wheel,
entrée à 40-55 DTE, delta 0,10-0,20, grandes valeurs désactivées. L'ancien profil **Actuel**
(-19,1 %/an, drawdown 86,9 % au backtest 2019-2026) reste disponible, mais son activation est
bloquée tant qu'elle n'est pas confirmée explicitement dans la page Réglages.

Un profil est signalé quand son dernier backtest terminé **avec exactement les mêmes réglages**
est déficitaire, ou quand son drawdown dépasse la limite de drawdown du profil. Sans backtest
de ces réglages, le profil est « non testé » (avertissement non bloquant). Toute activation est
gardée avec la version du screener (`strategy_configs.activation`).

## Trois mesures du risque

| Mesure | Spread | Put vendue (wheel) | Actions assignées |
| --- | --- | --- | --- |
| Collatéral | perte max | strike × 100 | prix de revient × actions |
| Perte max contractuelle | largeur − crédit | strike − crédit (action à 0) | prix de revient × actions |
| Stress loss | perte si le sous-jacent baisse du choc | idem | valeur × choc |

Le choc de stress vaut 15 % pour les ETF et 30 % pour les actions. Le **risque d'un trade**
est sa perte max pour un spread, sa stress loss pour une put vendue.

## Limites par défaut

| Limite | Valeur | Mesure |
| --- | --- | --- |
| Risque d'un trade | 1 % du capital | contrats = arrondi inférieur (budget / risque d'un contrat) |
| Seuil exceptionnel (désactivé) | 2 % | réservé aux deals au score ≥ 0,85 |
| Perte max ouverte totale | 10 % | somme des pertes max contractuelles, positions ouvertes et en attente comprises |
| Cluster corrélé | 5 % | SPY/QQQ/IWM ensemble, puis chaque secteur |
| Positions par secteur | 2 | positions ouvertes et en attente comprises |
| Perte du jour | 2 % | valeur du compte depuis la veille |
| Stop mensuel | 4 % | valeur du compte depuis la fin du mois précédent |
| Drawdown | 10 % | depuis le plus haut du compte |

Si aucun contrat ne tient dans ces limites, le deal est « NO TRADE » avec la raison exacte
(`filter_counts.no_trade` du passage du screener). Avec 20 000 $ et 1 % de risque, un spread
SPY de 5 $ de large ne passe plus : le screener prend la première largeur (5, 2,5, 2, 1 $)
dont la perte max tient dans 200 $. Une put vendue sur une action à plus de 7-8 $ environ
dépasse souvent le budget avec un choc de 30 % : c'est voulu.

## Blocage des nouvelles entrées

L'acceptation d'un deal est refusée, avec chaque raison, si l'un de ces points échoue :

- le worker n'a rien exécuté avec succès depuis 60 min ;
- le moniteur des positions n'a pas réussi de passage depuis 30 min, ou son dernier passage a
  échoué ;
- aucun screener n'a terminé aujourd'hui (données obsolètes) ;
- le courtier ne répond pas, ou le marché est fermé ;
- une cotation manque pour une jambe, ou le crédit coté est plus de 25 % sous le crédit
  proposé ;
- la limite journalière, mensuelle ou de drawdown est atteinte (pas pour un covered call sur des
  actions déjà détenues, qui réduit le risque) ;
- le deal dépasserait la perte max ouverte, la limite de son cluster, de son secteur ou le
  collatéral disponible.

Le moniteur enregistre chaque passage (`job_heartbeats`) et la valeur du compte du jour
(`account_snapshots`). Le bandeau en haut de chaque page affiche le profil actif, PAPER, la
santé du worker et du moniteur, la dernière mise à jour des données et si le trading est
autorisé ; un clic montre le détail de chaque contrôle.

## Stratégies et fenêtre d'entrée

| Mode | Titres | Sortie | Assignation |
| --- | --- | --- | --- |
| Put credit spread ETF | SPY, QQQ, IWM | objectif 50 %, stop, 21 DTE | non |
| Short Put Income | liste « Short Put Income » | objectif 50 %, stop, 21 DTE | évitée (rachat) |
| True Wheel | seulement les actions cochées « J'accepte de détenir » | objectif 50 % seulement | acceptée, puis covered calls |
| Grandes valeurs | profil « Expérimental : grandes valeurs » | comme les spreads | non |

- Un titre présent dans les deux listes va à la True Wheel : le choix de le détenir est explicite.
- Une position garde les règles de la version de réglages avec laquelle elle a été ouverte : un
  ancien cash secured put garde donc sa sortie à 21 DTE.
- Entrée à 45-65 DTE (réglable). Fenêtre de détention = DTE d'entrée - DTE de sortie ; un
  candidat sous `min_holding_days` (21 j) est écarté (étape « holding » de l'entonnoir).
- La distance du strike est donnée de trois façons : % du cours, écarts-types (IV et DTE) et
  |delta|. Delta et PoP sont des estimations du modèle, jamais des probabilités de gain.
- Le plafond de strike à 20 $ n'est plus un critère de qualité : il est désactivé par défaut
  (`use_wheel_max_strike`), la taille vient de la stress loss.
- La migration 0005 ajoute le type `short_put`, passe le profil Prudent à 45-65 DTE (nouvelle
  version active s'il l'était) et crée le profil expérimental, dont l'activation demande une
  confirmation (backtest déficitaire).

## Notes de qualité et NO TRADE

Chaque candidat reçoit des notes séparées (0 à 1), stockées avec le deal :

| Note | Contenu (poids par défaut) |
| --- | --- |
| Qualité absolue | marge de sécurité 20 (point mort en écarts-types), rendement sur risque non annualisé 15, fenêtre temporelle 10, régime de marché 10 (IV Rank, IV/HV, tendance), qualité des données 5 |
| Qualité d'exécution | exécution et liquidité 25 : écart bid/ask, écart mid/naturel, open interest |
| Adéquation au portefeuille | 15 : usage maximal des limites (perte ouverte, cluster, échéance, collatéral) après ajout du deal |

- Score final = la plus basse des trois notes ; la somme pondérée des sept composantes est
  affichée pour information. Rang relatif = place parmi les candidats du jour.
- NO TRADE dès qu'une règle bloque : une note sous `min_quality_score` (0,30), un contrat qui ne
  tient dans aucune limite, une position déjà ouverte, le secteur plein. Les raisons exactes
  sont listées sous les deals (« NO TRADE ») et dans « Pourquoi ce deal ? ».
- Rendement sur risque : crédit / perte max pour un spread, crédit / cash immobilisé pour une
  put, sans annualisation (l'AROC reste affiché, filtre désactivé par défaut).
- Cluster d'échéance : 5 % du capital de risque au plus sur une même date d'échéance.
- Grecques du portefeuille (delta, vega, theta) : calculées à l'entrée de chaque deal ; les
  positions plus anciennes sont comptées en « donnée absente ».
- Migration 0006 : écrit ces réglages dans Prudent (nouvelle version active s'il l'est) ;
  Actuel garde son filtre AROC sans minimum de qualité.

## Migrations sur la base hébergée

Les migrations Alembic atteignent Neon par le workflow GitHub Actions `Worker` : chaque passage
exécute `alembic upgrade head` avant `python -m app.worker tick`. Après la fusion d'une PR sur
`main`, la migration s'applique donc au premier passage suivant (5 min en séance, 30 min hors
séance), ou tout de suite avec *Actions → Worker → Run workflow* (`check`). L'API sur Vercel ne
migre pas : entre le déploiement de Vercel et ce passage, elle peut tourner sur l'ancien
schéma quelques minutes.
