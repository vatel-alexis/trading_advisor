// Plain-language glossary of the settings page, collapsed by default.
const TERMS: { term: string; text: string }[] = [
  {
    term: "Put vendue",
    text: "On vend une option de vente : on encaisse tout de suite une prime et on s'engage à acheter 100 actions au strike si le cours passe dessous à l'échéance.",
  },
  { term: "Prime, crédit", text: "Ce que rapporte la vente de l'option, encaissé à l'entrée. Le gain maximal d'un trade." },
  { term: "Strike", text: "Prix d'exercice de l'option. Plus il est loin sous le cours, plus le trade est sûr et moins il rapporte." },
  {
    term: "Échéance, DTE",
    text: "Date de fin de l'option. DTE = jours restants avant l'échéance. Entrée entre 45 et 65 DTE, sortie à 21 DTE par défaut.",
  },
  {
    term: "Delta",
    text: "Sensibilité de l'option au cours, entre 0 et 1. Un delta de 0,15 correspond grossièrement à 15 % de chances de finir dans la monnaie : plus il est bas, plus le strike est loin.",
  },
  {
    term: "Put credit spread",
    text: "Une put vendue plus une put achetée plus bas. La put achetée plafonne la perte : perte max = écart entre les strikes - crédit. Utilisé sur les ETF (SPY, QQQ, IWM).",
  },
  {
    term: "Short Put Income",
    text: "Put vendue seule sur une action, rachetée avant l'échéance (à 21 DTE) : on cherche la prime, pas les actions.",
  },
  {
    term: "True Wheel, assignation, covered call",
    text: "Assignation : la put finit sous le strike et on achète les actions. En True Wheel on l'accepte, on garde les actions et on vend des calls dessus (covered calls). Seulement sur les titres que tu acceptes de détenir.",
  },
  {
    term: "Volatilité implicite (IV), IV Rank",
    text: "L'IV est la volatilité anticipée par le prix des options. L'IV Rank la situe entre son plus bas (0) et son plus haut (100) de l'année : plus il est haut, plus les primes sont chères, donc intéressantes à vendre.",
  },
  {
    term: "IV / HV",
    text: "Volatilité implicite divisée par la volatilité réellement constatée (HV). Au-dessus de 1, les options sont plus chères que le mouvement réel ne le justifie.",
  },
  {
    term: "Open interest, volume",
    text: "Nombre de contrats ouverts et échangés dans la journée sur l'option. Trop faibles, l'option est difficile à acheter ou revendre à bon prix.",
  },
  {
    term: "Bid, ask, mid, naturel",
    text: "Bid : meilleur prix d'achat proposé ; ask : meilleur prix de vente. Mid : le milieu. Naturel : le prix moins favorable qui s'exécute tout de suite. L'écart bid/ask mesure la liquidité.",
  },
  {
    term: "Rendement sur risque",
    text: "Crédit divisé par la perte max (spread) ou par le cash immobilisé (put), sur toute la durée du trade, sans annualiser.",
  },
  { term: "AROC", text: "Le même rendement ramené à l'année. Informatif : il favorise les échéances courtes." },
  {
    term: "Collatéral",
    text: "Cash bloqué par le courtier pendant le trade : perte max d'un spread, strike x 100 pour une put vendue.",
  },
  {
    term: "Perte max, perte en stress",
    text: "Perte max : le pire contractuel (action à 0 pour une put). Perte en stress : la perte si le sous-jacent baisse du choc de stress (15 % ETF, 30 % actions). Le risque d'un trade est sa perte max pour un spread, sa perte en stress pour une put.",
  },
  {
    term: "Risque max d'un trade",
    text: "Part du capital qu'un seul trade peut perdre. 1 % de 20 000 $ = 200 $ ; le nombre de contrats est arrondi à l'inférieur pour rester dessous.",
  },
  {
    term: "Perte max ouverte",
    text: "Somme des pertes max de toutes les positions ouvertes ou en attente. Le niveau de risque de la vue simple règle ce plafond.",
  },
  { term: "Cluster", text: "Groupe de positions qui baissent ensemble : les ETF indiciels, un secteur, ou une même échéance." },
  {
    term: "Drawdown",
    text: "Baisse du compte depuis son plus haut. Au-delà du drawdown max, plus aucune entrée et la pire position est rachetée.",
  },
  { term: "Objectif de gain", text: "Rachat automatique quand une part de la prime est gagnée (50 % par défaut)." },
  {
    term: "Stop à x fois le crédit",
    text: "Rachat quand l'option vaut x fois la prime encaissée : à 2, une prime de 1 $ est rachetée à 2 $, soit environ 1 $ de perte.",
  },
  { term: "Résultats trimestriels", text: "Publication des comptes d'une entreprise : le cours peut sauter. Pas d'entrée si elle tombe avant l'échéance." },
  {
    term: "Score, NO TRADE",
    text: "Chaque deal reçoit des notes de 0 à 1 (qualité, exécution, adéquation au portefeuille). Sous le score min sur l'une d'elles, il est marqué NO TRADE.",
  },
  {
    term: "Ordre limite, paliers",
    text: "Ordre à un prix fixé. Il part au mid puis se rapproche du naturel palier par palier s'il n'est pas exécuté.",
  },
  { term: "Backtest, %/an", text: "Simulation de la stratégie sur 2019-2026. Le %/an est le rendement annuel moyen simulé." },
  {
    term: "Profil actif",
    text: "Le profil utilisé par le screener. Chaque enregistrement crée une version ; les positions ouvertes gardent les règles de leur version.",
  },
];

export function Glossary() {
  return (
    <details className="group rounded-2xl border border-line bg-surface text-sm">
      <summary className="cursor-pointer list-none p-3 font-semibold [&::-webkit-details-marker]:hidden">
        <span className="mr-1 inline-block transition-transform group-open:rotate-90">›</span>
        Glossaire
        <span className="ml-2 text-xs font-normal opacity-60">DTE, delta, IV Rank, drawdown…</span>
      </summary>
      <dl className="grid gap-x-6 gap-y-3 px-3 pb-3 md:grid-cols-2">
        {TERMS.map((t) => (
          <div key={t.term}>
            <dt className="font-medium">{t.term}</dt>
            <dd className="text-xs opacity-70">{t.text}</dd>
          </div>
        ))}
      </dl>
    </details>
  );
}
