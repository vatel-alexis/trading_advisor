# Mise en service et premier cycle paper

La stack tourne sur une machine avec Docker (Docker Desktop sur un portable suffit). Le
workflow `E2E` de la CI la monte à l'identique à chaque PR : compose, screener sur Yahoo,
cycle paper simulé, contrôle de l'API et des pages.

## Une fois

```bash
git clone https://github.com/vatel-alexis/trading_advisor && cd trading_advisor
cp .env.example .env          # puis renseigner BROKER_API_KEY et BROKER_API_SECRET (paper)
docker compose up --build -d
docker compose exec worker python -m app.worker check
```

`check` doit afficher `[ok]` sur trois lignes : base migrée, compte Alpaca paper `ACTIVE` avec
le niveau d'options 3, Yahoo joignable. Il donne aussi l'heure du prochain screener et du
prochain passage du moniteur.

## Les horaires (heure de Paris, jusqu'au 25 octobre ; une heure de moins ensuite jusqu'au 1er novembre)

| Heure | Ce qui se passe |
| --- | --- |
| 14 h 00 - 23 h 55 | Moniteur toutes les 5 min : synchro des ordres, assignations, stops, sortie 21 DTE |
| 15 h 30 | Ouverture du marché américain |
| 16 h 30 | Screener : les deals du jour apparaissent dans **Opportunités** |
| 22 h 00 | Clôture ; les ordres d'ouverture *day* non exécutés expirent |

La stack doit tourner pendant toute cette plage : le moniteur est ce qui déclenche les stops
et la sortie à 21 DTE. L'ordre de prise de profit à 50 % est un ordre GTC posé chez Alpaca,
il reste actif même machine éteinte ; les stops, eux, non.

## Lundi

1. Avant 16 h 30, `docker compose up -d` puis `check`.
2. Vers 16 h 35, ouvrir http://localhost:3000/opportunites. Si la stack a démarré après
   16 h 30, lancer le screener à la main :
   `docker compose exec worker python -m app.worker screener`.
3. Accepter un deal envoie un ordre limite *day* au crédit mid du screener (modifiable dans la
   confirmation). Rejeter demande un motif.
4. Dans **Positions**, la ligne « Ordre d'ouverture à … » devient une position ouverte au plus 5 minutes après
   l'exécution chez Alpaca ; l'ordre de prise de profit apparaît alors. Pour forcer une
   synchro : `docker compose exec worker python -m app.worker monitor`.
5. Un ordre non exécuté à la clôture passe la position en « Non exécutée » (Historique).

## Arrêter, redémarrer

- `docker compose stop` arrête tout en gardant la base ; `docker compose up -d` repart.
- Les services redémarrent seuls après un redémarrage de Docker (`restart: unless-stopped`).
- Ne pas lancer `docker compose down -v` : `-v` efface la base (positions, historique, IV).

## Dépannage

| Symptôme | Commande |
| --- | --- |
| Page « API injoignable » | `docker compose ps` puis `docker compose logs api` |
| Pas de deals | `docker compose logs worker` : l'entonnoir du screener y est écrit |
| Doute sur le compte | `docker compose exec api python -m scripts.broker_check` (lecture seule) |
