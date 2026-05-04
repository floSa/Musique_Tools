# Service : Recommandation

Interface Streamlit pour découvrir des artistes que tu ne possèdes pas encore,
en exploitant les bases de similarité **Last.fm** et **Spotify** déjà constituées
par les services `Artistes_Similaires_*`.

---

## Objectif

> *"Recommande-moi des artistes que je ne possède pas et qui sont souvent cités
> comme similaires aux artistes que j'aime."*

L'enjeu est de **maximiser la nouveauté utile** : pas de recommandation d'un
artiste déjà en bibliothèque ou en playlist (que tu connais et possèdes), mais
les artistes déjà entendus ponctuellement dans l'historique restent valides
(signal "tu connais un peu, creuse").

---

## Architecture

```
sources/Recommandation/
├── app.py              # Interface Streamlit
├── engine.py           # Moteur (logique pure, pas de Streamlit)
├── data_provider.py    # Chargement des données avec cache @st.cache_data
├── feedback.py         # Persistance des 👍/👎 utilisateur
├── session_log.py      # Persistance des sessions de recos (data/Recommandation/sessions.csv)
├── sync_seeds.py       # Script de synchro biblio+playlists → artistes_liste.csv
├── tests/
│   └── test_engine.py  # Tests unitaires du moteur
├── requirements.txt
└── pyproject.toml
```

La séparation `engine` / `app` permet de tester le moteur indépendamment de
l'UI (et de le réutiliser dans un notebook ou une CLI le cas échéant).

---

## Sources de données utilisées

| Donnée | Fichier | Rôle |
|---|---|---|
| Bibliothèque physique | `data/Bibliotheque/bibliotheque.csv` | **Exclusion** : on ne recommande pas ce qu'on possède |
| Playlists Spotify | `data/Playlists_Spotify/*.csv` | **Exclusion** : on ne recommande pas ce qu'on a déjà ajouté |
| Historique d'écoute | `data/Historique_Spotify/*.json` | **Pondération** : top artistes → seeds, et boost des artistes déjà entendus |
| Similaires Last.fm | `data/Artistes_Similaires_LastFM/similar_artists.db` | Source de similarité (score 0–1) + tags pour le filtre genre |
| Similaires Spotify | `data/Artistes_Similaires_Spotify/output_related.csv` | Source de similarité (rang 1–40) |
| Feedback utilisateur | `data/Recommandation/feedback.csv` | **Exclusion** des 👎, mémorisation des 👍 |

Les playlists `Titres_AAAA.csv` et thématiques sont toutes incluses dans
l'exclusion. Pour les pistes "featuring" (`"Daft Punk, Pharrell"`), tous les
artistes sont split sur la virgule et ajoutés à l'exclusion.

---

## Choix de design

### 1. Pourquoi exclure biblio + playlists, mais pas l'historique ?

**Bibliothèque + playlists** sont des artistes que tu as **activement choisi** de
posséder. Te les re-recommander n'apporte rien — tu les connais déjà.

**L'historique d'écoute** contient des artistes que tu as entendus
*passivement* (radio, recommandations Spotify, recos d'amis, etc.) sans
forcément les avoir intégrés à ta collection. Si l'algo te les ressort, c'est
au contraire un **signal positif** : "tu connais déjà un peu, c'est probable
que ça te plaise". D'où le **boost historique** plutôt qu'une exclusion.

### 2. Pourquoi deux sources de similarité (Last.fm + Spotify) ?

| Critère | Last.fm | Spotify |
|---|---|---|
| Granularité | Score continu 0–1 | Rang discret 1–40 |
| Population | Utilisateurs Last.fm (audiophiles, indé) | Utilisateurs Spotify (mainstream + tout) |
| Algorithmie | Co-écoutes | Mélange opaque (audio + co-écoutes + édito) |
| Couverture | ~5000 artistes pour ce projet | ~5700 artistes pour ce projet |

Les deux sources se complètent. Le slider `α` ("Last.fm vs Spotify") permet
de privilégier l'une ou l'autre selon ce qu'on cherche : Last.fm tend à
ramener des artistes plus pointus, Spotify est plus mainstream.

### 3. Conversion du rang Spotify en score

Spotify n'expose pas de score numérique. On convertit linéairement :

```
spotify_score(rang) = max(0, 1 - (rang - 1) / 40)
```

- Rang 1 → 1.0
- Rang 20 → 0.525
- Rang 40 → 0.025

**Pourquoi linéaire et pas `1/rang` ?** `1/rang` est très agressif (rang 1 = 1.0,
rang 2 = 0.5) et écrase les rangs au-delà de 5. Le linéaire conserve une
contribution non négligeable pour les artistes en milieu de classement, ce
qui est cohérent avec la perception : un artiste en rang 15 reste pertinent.

### 4. Score final et "fréquence de citation"

```
score(c) = (α × Σ_seeds [poids_seed × match_lastfm(seed, c)]
          + (1-α) × Σ_seeds [poids_seed × spotify_score(rang(seed, c))])
          / Σ_seeds poids_seed
```

La **somme** sur les seeds est centrale : si un candidat est cité par 5 seeds
avec une similarité moyenne, son score s'additionne et bat un candidat cité
par un seul seed même avec une similarité forte. C'est l'effet "souvent cité"
recherché.

**Normalisation** : on divise par `Σ poids_seed` pour obtenir un **score moyen
par seed**. Cela rend les scores comparables entre runs de tailles différentes
(5 seeds vs 30 seeds) : un score de 0.6 signifie "0.6 de similarité moyenne
pondérée à travers les seeds qui pointent vers ce candidat".

### 5. Pondération récent / total des seeds historiques

Les seeds depuis l'historique ne se valent pas : un artiste écouté 200 minutes
le mois dernier est un meilleur point de départ qu'un artiste écouté 30
minutes en 2015.

```
poids(artiste) = β × poids_récent + (1-β) × poids_total

poids_récent = minutes_dans_les_N_derniers_mois / max_de_la_période
poids_total  = minutes_totales / max_total
```

Les deux poids sont **normalisés** par leur max respectif (pas de mélange
d'échelles). Le slider `β` ("récent vs total") laisse l'utilisateur choisir
entre "ce que j'écoute en ce moment" et "ce que j'ai écouté en cumul depuis
2012".

### 6. Boost historique (γ)

```
boost = 1 + γ × min(1, minutes_écoutées / 60)
score_final = score_de_base × boost
```

- `γ = 0` : pas de boost, l'historique n'influence que via les seeds
- `γ = 1` : un artiste écouté ≥ 60 minutes voit son score doublé
- Plafond à 60 min : un artiste massivement écouté ne doit pas dominer
  toute la liste — au-delà, il devrait probablement être en biblio/playlist
  de toute façon

### 7. Seeds inconnus

Si tu sélectionnes un artiste qui n'a **aucun similaire** dans les bases
Last.fm ni Spotify, il est ignoré et listé dans un avertissement. Pas de
scraping à la volée — pour l'inclure, relance les scrapers concernés en
ajoutant l'artiste à `data/Ressources/artistes_liste.csv`.

### 8. Diversité (re-classement MMR)

Sans diversification, le top N peut être très redondant : 5 seeds électro
ramènent souvent les 5 mêmes artistes électro proches. Le slider
`diversity_weight ∈ [0, 1]` active un re-classement façon
**MMR (Maximal Marginal Relevance)** :

```
mmr(c) = λ × score_normalisé(c) - (1-λ) × max_j Jaccard(tags_c, tags_cj_déjà_sélectionné)

avec λ = 1 - diversity_weight
```

- `diversity_weight = 0` : score pur (comportement par défaut, pas de pénalité)
- `diversity_weight = 1` : anti-redondance maximale (le score est ignoré, on
  cherche uniquement à varier les tags)

L'algorithme glouton sélectionne d'abord le meilleur score, puis à chaque
itération choisit le candidat qui maximise `mmr` parmi un pool de
`max(n × 5, 30)` candidats. Le score est normalisé par le max du pool pour
comparer à une similarité Jaccard ∈ [0, 1].

> Note : un artiste sans tags Last.fm a une similarité Jaccard = 0 avec tout le
> monde, donc il "passe" toujours sans pénalité. C'est pourquoi `update_tags.py`
> est important pour que la diversification soit pleinement opérante.

### 9. Feedback utilisateur (👍 / 👎)

Chaque recommandation porte deux boutons :

- **👎 Dislike** : l'artiste est ajouté à `data/Recommandation/feedback.csv`
  avec `vote = -1` et exclu **automatiquement** des futures recommandations
  (au même titre que la biblio et les playlists).
- **👍 Like** : `vote = +1`, mémorisé sans effet automatique. Sert de signal
  pour de l'analyse manuelle (quels seeds → quels likes ?) ou un futur ML.

Si plusieurs votes pour un même artiste, **le plus récent l'emporte**. Les
recommandations sont conservées dans `st.session_state` après calcul, pour
qu'un clic feedback ne déclenche pas un nouveau calcul.

### 10. Couverture des seeds (sync)

Le script `sync_seeds.py` (et le bouton "Synchroniser artistes_liste.csv"
dans la sidebar) détecte les artistes présents dans la bibliothèque ou les
playlists mais absents de `data/Ressources/artistes_liste.csv`, et les y
ajoute. Les scrapers `Artistes_Similaires_LastFM` et `Artistes_Similaires_Spotify`
les traiteront au prochain run.

```bash
uv run python sync_seeds.py --dry-run    # Preview
uv run python sync_seeds.py              # Applique
```

Sans cette synchro, les seeds ajoutés à ta biblio ou tes playlists depuis le
dernier scraping seraient silencieusement ignorés (pas de similaires en base).

### 11. Filtre par genre — modes OR / AND

Le filtre genre supporte deux modes :

- **OR** (défaut) : un candidat passe s'il a **au moins un** des tags choisis.
  Permissif — utile pour explorer un univers musical large.
- **AND** : un candidat passe s'il a **tous** les tags choisis.
  Restrictif — utile pour des combinaisons précises (ex : "rock français",
  "techno minimal").

> ⚠️ Tous les artistes de la base Last.fm n'ont pas leurs tags renseignés
> tant que `update_tags.py` n'a pas tourné — sans tags, le filtre les exclut
> (en OR comme en AND).

### 12. Persistance des sessions

Chaque calcul de recommandations est sauvegardé dans
`data/Recommandation/sessions.csv` avec :

- Timestamp de la session (commun à toutes les recos d'un run)
- Top N (artiste, score, citations, sub-scores Last.fm/Spotify, tags)
- Seeds principaux (top 10 par poids)
- Paramètres utilisés (α, β, γ, λ, fenêtre récente, filtre genre)

L'expander "📚 Sessions précédentes" en bas de l'app permet de retrouver
n'importe quelle session passée et de voir ce qui avait été recommandé avec
quels paramètres. Utile pour comparer l'effet d'un changement de réglage.

### 12. Liens externes & preview audio

Pour chaque recommandation, en plus du tableau et des détails :

- **Liens** : Spotify, Last.fm, YouTube (URLs construites à partir du nom)
- **Aperçu Spotify** : iframe `https://open.spotify.com/embed/artist/{id}`
  affichée si l'ID est trouvé dans l'index. L'index est construit depuis
  `Related_Data_Raw` du CSV Spotify (~58 000 IDs disponibles, donc couvre la
  quasi-totalité des recos).

L'embed permet d'écouter directement quelques top tracks sans quitter l'app
— c'est ce qui change le plus l'usage en pratique.

### 13. Pénalité de popularité (TF-IDF côté candidat)

**Problème :** sans correction, les artistes "génériques" (Daft Punk, Radiohead,
Alain Souchon) qui apparaissent comme similaires de presque tout le monde
trustent les premières positions, parce qu'ils accumulent des contributions de
nombreux seeds. Pourtant ce sont rarement de bonnes recommandations
(soit on les connaît déjà, soit ils sont si "passe-partout" qu'ils n'apportent
rien de pointu).

**Solution :** une pénalité multiplicative basée sur la popularité du candidat
dans la base de similaires (analogue à l'IDF en recherche d'information) :

```
popularité(c) = nombre d'artistes ayant `c` dans leurs similaires (Last.fm + Spotify)
factor(c) = 1 / (1 + ω × log(1 + popularité(c)))
score(c) *= factor(c)
```

- `ω = 0` : pas de pénalité (défaut)
- `ω = 0.3` : pénalité douce (pop=100 → factor 0.42)
- `ω = 0.7` : pénalité moyenne (pop=100 → factor 0.24)
- `ω = 1.5` : forte pénalité (pop=100 → factor 0.13)

**Pourquoi log et pas linéaire ?** Pour que la pénalité augmente vite sur les
premières citations (1 → 50 citations) puis s'aplatisse. Sinon les ultra-pop
(pop > 100) seraient écrasés à zéro et n'apparaîtraient jamais, alors qu'on
veut juste qu'ils ne dominent pas.

**Concrètement** : sur seeds Worakls + Air, sans pénalité on obtient
NTO/Joachim Pastor/Teho... (tous pop=30-50). Avec ω=0.7, des artistes plus
pointus comme Ron Flatter (pop=13) ou Nuspirit Helsinki (pop=25) entrent dans
le top 10.

### 14. Bouton "Ajouter à artistes_liste.csv"

Sur chaque carte de recommandation, un bouton **➕ Ajouter à artistes_liste.csv**
ajoute l'artiste recommandé à la liste des seeds. Au prochain run des scrapers
`Artistes_Similaires_LastFM` et `Artistes_Similaires_Spotify`, l'artiste sera
scrapé et **pourra être utilisé comme seed lui-même** dans les futures recos.

C'est la "boucle de découverte" : tu trouves un artiste intéressant, un clic, et
au prochain scraping il enrichit ton univers de seeds. Sans ce bouton, tu
devrais éditer manuellement le CSV ou le synchroniser depuis la biblio (et il
n'est pas dans la biblio puisque tu viens juste de le découvrir).

### 15. Filtre par genre

Les genres viennent des **tags Last.fm** (`artist.gettoptags`, top 5). Si tu
sélectionnes des genres, un candidat passe s'il a **au moins un** des tags
choisis. Logique OR, pas AND — pour rester permissif (les tags Last.fm sont
bruités).

> ⚠️ Tous les artistes de la base Last.fm n'ont pas leurs tags renseignés
> (problème historique d'import). Lance `update_tags.py` dans
> `Artistes_Similaires_LastFM/` pour les compléter.

---

## Paramètres UI

| Paramètre | Plage | Défaut | Effet |
|---|---|---|---|
| Nombre de recommandations | 1–10 | 5 | Top N final |
| Inclure historique comme seed | bool | true | Active la dérivation seeds depuis écoutes |
| Top N historique | 5–50 | 20 | Nombre d'artistes pris dans le top historique |
| Période 'récent' | 1–24 mois | 12 | Fenêtre temporelle pour le calcul récent |
| Pondération récent vs total | 0–1 | 0.7 | β — 0 = total uniquement, 1 = récent uniquement |
| Last.fm vs Spotify | 0–1 | 0.5 | α — 0 = Spotify seul, 1 = Last.fm seul |
| Boost historique | 0–1 | 0.3 | γ — 0 = pas de boost, 1 = boost fort |
| Diversité (MMR) | 0–1 | 0.0 | 0 = score pur, 1 = anti-redondance maximale |
| Pénalité popularité | 0–2 | 0.0 | ω — dampe les artistes génériques (TF-IDF) |
| Filtre genres | multi | [] | Tags Last.fm |
| Mode filtre | OR / AND | OR | OR = un suffit, AND = tous requis |
| Bouton "Synchroniser" | — | — | Met à jour artistes_liste.csv avec biblio+playlists |
| Bouton ➕ par reco | — | — | Ajoute un artiste recommandé à artistes_liste.csv |

---

## Sortie

Pour chaque recommandation :

| Champ | Description |
|---|---|
| `Score` | Score final (somme pondérée + boost) |
| `Cité par N seeds` | Nombre de seeds distincts qui pointent vers ce candidat |
| `Last.fm` / `Spotify` | Sous-scores avant pondération α |
| `Déjà écouté ?` | ✓ si présent dans l'historique |
| `Minutes histo.` | Total écouté en minutes (0 si jamais) |
| `Genres` | Top 3 tags Last.fm |
| `Seeds qui ont mené à cette reco` | Liste explicite (utile pour comprendre pourquoi) |

---

## Lancement

```bash
cd sources/Recommandation
uv venv .venv --python 3.12
uv pip install -r requirements.txt
uv run streamlit run app.py
```

L'interface s'ouvre sur [http://localhost:8501](http://localhost:8501).

---

## Limites connues

- **Couverture des seeds** : un seed sans données de similarité dans aucune
  base est ignoré. Pour ~5700 artistes en base Spotify, certains de tes
  artistes biblio/playlists peuvent ne pas y être (orthographe différente,
  absence sur Spotify, scraping incomplet).
- **Tags Last.fm partiels** : tant que `update_tags.py` n'a pas été lancé,
  beaucoup d'artistes ont `tags=[]` et le filtre genre est inopérant pour eux.
- **Pas de feedback loop** : aucun mécanisme d'apprentissage à partir des
  recos validées/rejetées par l'utilisateur. À envisager (sauvegarde des
  recos cliquées, exclusion des recos rejetées dans une session, etc.).
- **Boost historique plafonné à 60 min** : choix arbitraire, à ajuster
  si l'effet est trop faible/fort à l'usage.
