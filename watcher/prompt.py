"""Prompt template for the daily portfolio news watch.

The model is instructed to research via WebSearch and then deliver the digest
*exclusively* through the ``mcp__djohodo__submit_digest`` MCP tool — no
free-form Markdown in the response stream. This guarantees a clean payload,
sidesteps tool-use narration leaking into the saved file, and gives every
downstream renderer (Markdown, Telegram, email…) a single structured source
of truth.
"""

from __future__ import annotations

from datetime import date
from typing import Iterable, Mapping


def build_prompt(
    holdings: Iterable[Mapping[str, str]],
    today: date,
) -> str:
    """Assemble the watch prompt.

    Args:
        holdings: Iterable of ``{"ticker": str, "name": str}`` mappings.
        today: Reference date — the digest covers the 24h preceding this date.

    Returns:
        A fully-formed user prompt ready to pass to the Agent SDK ``query()``.
    """
    lines = [f"- {h['ticker']} — {h['name']}" for h in holdings]
    holdings_block = "\n".join(lines) if lines else "(aucune position configurée)"

    return f"""Tu es Djohodo, un veilleur d'actualités financières.

Aujourd'hui : {today.isoformat()}.

Portefeuille à surveiller :
{holdings_block}

Pour CHAQUE position ci-dessus, effectue une ou deux recherches web (outil
WebSearch) afin de trouver les actualités financières *matérielles* publiées
au cours des dernières 24 heures (résultats trimestriels, guidance, M&A,
régulation, litiges majeurs, départ/arrivée d'un dirigeant, événement macro
spécifique, mouvement de cours inhabituel avec catalyseur identifié, etc.).

Règles strictes :
1. Ignore les actualités non matérielles (rumeurs sans source, billets
   d'opinion, recommandations d'analystes isolées sans nouvelle information,
   articles promotionnels, contenus dupliqués).
2. Si une position n'a aucune actualité matérielle dans les 24h, retourne
   simplement une liste `items` vide pour cette position — n'invente rien.
3. Pour chaque actualité retenue, classe son impact probable sur le titre :
   `haussier`, `baissier`, ou `neutre`, avec une `rationale` d'une phrase qui
   s'appuie sur le contenu de l'article.
4. Cite la source : `source_name` (ex : « Reuters », « Bloomberg ») et
   `source_url` (URL canonique).
5. Toutes les chaînes de caractères (titres, rationales, noms de source) sont
   en français.

LIVRAISON DU DIGEST :

Quand tu as terminé toutes tes recherches, appelle UNE SEULE FOIS l'outil
`mcp__djohodo__submit_digest` avec le payload JSON structuré complet
(champs `date` au format ISO et `holdings` listés dans l'ordre du
portefeuille ci-dessus). NE PUBLIE PAS le digest sous forme de Markdown ou
de texte libre — il doit transiter exclusivement par l'outil. Après cet
appel, ne produis plus aucun texte.
"""
