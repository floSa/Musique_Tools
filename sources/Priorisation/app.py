"""Streamlit UI pour la Priorisation des albums à récupérer.

Usage :
    cd sources/Priorisation
    uv venv .venv --python 3.12
    uv pip install -r requirements.txt
    uv run streamlit run app.py

Charge tous les `data/Resultats/resultats_final_*.xlsx`, applique un
fichier d'exclusion optionnel, calcule un score par artiste basé sur 6
signaux activables, et affiche les top N artistes (avec toutes leurs
lignes du fichier source). Export xlsx en 1 clic.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

# Permettre l'import des modules locaux quand on lance via `streamlit run`
sys.path.insert(0, str(Path(__file__).parent))

from data_loader import load_results_finaux, load_exclusion, apply_exclusion
from engine import compute_priority_scores, SIGNAL_NAMES


# ---------------------------------------------------------------------------
# Chemins (relatifs au repo)
# ---------------------------------------------------------------------------

ROOT = Path(__file__).parent.parent.parent
DATA = ROOT / "data"
RESULTATS_DIR = DATA / "Resultats"
HISTORY_DIR = DATA / "Historique_Spotify"
LASTFM_DB = DATA / "Artistes_Similaires_LastFM" / "similar_artists.db"
SPOTIFY_DB = DATA / "Artistes_Similaires_Spotify" / "similar_artists.db"
QOBUZ_DB = DATA / "Artistes_Similaires_Qobuz" / "similar_artists.db"
EXCLUSION_DEFAULT = DATA / "Priorisation" / "exclusion.xlsx"
EXPORT_PATH = DATA / "Priorisation" / "priorites_artistes.xlsx"


# ---------------------------------------------------------------------------
# Sidebar — paramètres
# ---------------------------------------------------------------------------

st.set_page_config(page_title="Priorisation albums", layout="wide")
st.title("🎯 Priorisation des albums à récupérer")

st.sidebar.header("Signaux et pondération")
st.sidebar.caption(
    "Coche les signaux à utiliser, ajuste les poids (somme libre, "
    "renormalisée automatiquement)."
)

# Libellés humains
SIGNAL_LABELS = {
    "reco":        "Score Recommandation (Last.fm + Spotify + Qobuz)",
    "multi_plist": "Présence dans plusieurs playlists",
    "possedes":    "Nb albums déjà possédés du même artiste",
    "dispo_bm":    "Dispo BM Lyon (Sources Bibli non vide)",
    "nb_a_recup":  "Nb d'albums à récupérer du même artiste",
    "ecoute":      "Heures d'écoute Spotify (historique total)",
}
# Defaults : reco dominant, écoute faible
DEFAULT_ENABLED = {s: True for s in SIGNAL_NAMES}
DEFAULT_WEIGHTS = {
    "reco":        0.50,
    "multi_plist": 0.15,
    "possedes":    0.10,
    "dispo_bm":    0.10,
    "nb_a_recup":  0.10,
    "ecoute":      0.05,
}

signals_enabled: dict[str, bool] = {}
signal_weights: dict[str, float] = {}
for s in SIGNAL_NAMES:
    cols = st.sidebar.columns([1, 4])
    signals_enabled[s] = cols[0].checkbox(" ", value=DEFAULT_ENABLED[s], key=f"chk_{s}", label_visibility="collapsed")
    signal_weights[s] = cols[1].slider(
        SIGNAL_LABELS[s], 0.0, 1.0,
        value=DEFAULT_WEIGHTS[s], step=0.05, key=f"w_{s}",
        disabled=not signals_enabled[s],
    )

st.sidebar.divider()
top_n = st.sidebar.slider("Top N artistes", min_value=5, max_value=200, value=50, step=5)

st.sidebar.divider()
st.sidebar.subheader("Réglages internes du signal Recommandation")
reco_lfm_w = st.sidebar.slider("Poids Last.fm (dans reco)", 0.0, 1.0, 0.40, step=0.05)
reco_qbz_w = st.sidebar.slider("Poids Qobuz (dans reco)",   0.0, 1.0, 0.20, step=0.05)
reco_spt_w = max(0.0, 1.0 - reco_lfm_w - reco_qbz_w)
st.sidebar.caption(f"Poids Spotify (déduit) = {reco_spt_w:.2f}")

st.sidebar.divider()
st.sidebar.subheader("Exclusion")
uploaded_excl = st.sidebar.file_uploader(
    "Fichier exclusion.xlsx (optionnel)", type=["xlsx"],
    help="Mêmes colonnes que les resultats_final. Les couples "
         "(Artist_A_rechercher, Album_A_rechercher) présents ici sont retirés."
)
if not uploaded_excl and EXCLUSION_DEFAULT.exists():
    st.sidebar.caption(f"Utilisation par défaut : `{EXCLUSION_DEFAULT.relative_to(ROOT)}`")


# ---------------------------------------------------------------------------
# Main — chargement + calcul + affichage
# ---------------------------------------------------------------------------

with st.spinner("Chargement des résultats…"):
    df = load_results_finaux(RESULTATS_DIR)

st.write(f"**{len(df)} lignes** chargées depuis `{RESULTATS_DIR.relative_to(ROOT)}` "
         f"({df['Playlist_source'].nunique() if not df.empty else 0} playlists).")

# Exclusion
df_excl = pd.DataFrame()
if uploaded_excl is not None:
    df_excl = pd.read_excel(uploaded_excl)
elif EXCLUSION_DEFAULT.exists():
    df_excl = load_exclusion(EXCLUSION_DEFAULT)

if not df_excl.empty:
    n_before = len(df)
    df = apply_exclusion(df, df_excl)
    st.info(f"Exclusion appliquée : {n_before - len(df)} lignes retirées.")

if df.empty:
    st.warning("Aucune donnée à prioriser.")
    st.stop()

# Vérifs sources Recommandation
if signals_enabled.get("reco"):
    missing = []
    if not LASTFM_DB.exists():  missing.append("Last.fm")
    if not SPOTIFY_DB.exists(): missing.append("Spotify")
    if not QOBUZ_DB.exists():   missing.append("Qobuz")
    if missing:
        st.warning(f"Bases similarité manquantes : {', '.join(missing)} — "
                   f"le signal `reco` sera partiel.")

# Calcul
with st.spinner("Calcul des scores de priorité…"):
    df_artists, df_lines = compute_priority_scores(
        df,
        signals_enabled=signals_enabled,
        signal_weights=signal_weights,
        top_n=top_n,
        history_dir=HISTORY_DIR,
        lastfm_db=LASTFM_DB,
        spotify_db=SPOTIFY_DB,
        qobuz_db=QOBUZ_DB,
        reco_lastfm_weight=reco_lfm_w,
        reco_qobuz_weight=reco_qbz_w,
    )

st.success(f"Top {top_n} artistes sélectionnés → {len(df_lines)} albums au total")

# Affichage : 2 tabs
tab1, tab2 = st.tabs(["📊 Vue par artiste (top N)", "📋 Toutes les lignes (export)"])

with tab1:
    if df_artists.empty:
        st.info("Aucun artiste à afficher.")
    else:
        # On garde les colonnes utiles
        cols_show = ["Rang_artiste", "Score_prio"] + [
            c for c in df_artists.columns if c.startswith(("raw_", "norm_"))
        ]
        cols_show = [c for c in cols_show if c in df_artists.columns]
        st.dataframe(df_artists[cols_show].head(top_n), use_container_width=True)

with tab2:
    st.dataframe(df_lines, use_container_width=True, height=600)

    # Export
    st.divider()
    if st.button("💾 Exporter en xlsx", type="primary"):
        EXPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        df_lines.to_excel(EXPORT_PATH, index=False)
        st.success(f"Exporté → `{EXPORT_PATH.relative_to(ROOT)}` ({len(df_lines)} lignes)")
