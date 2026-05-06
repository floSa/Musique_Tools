"""
Interface Streamlit du service de recommandation.

Lancement :
    cd sources/Recommandation
    uv run streamlit run app.py
"""
import logging

import pandas as pd
import streamlit as st

# Logging visible dans la console Streamlit (utile pour comprendre les recos)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%H:%M:%S",
)

from engine import history_weights, recommend
from data_provider import (
    get_all_genres,
    get_artist_popularity,
    get_excluded,
    get_history,
    get_history_minutes,
    get_lastfm_similar,
    get_lastfm_tags,
    get_qobuz_portraits,
    get_qobuz_similar,
    get_seeds_pool,
    get_spotify_id_index,
    get_spotify_similar,
    get_tag_similarity_index,
)
import feedback
import session_log
import sync_seeds


# ---------------------------------------------------------------------------
# Configuration page
# ---------------------------------------------------------------------------

st.set_page_config(page_title="Recommandation d'artistes", layout="wide")

st.title("🎵 Découverte d'artistes")
st.caption(
    "Recommande des artistes que tu ne possèdes pas encore (ni en biblio, "
    "ni dans tes playlists), basés sur la similarité Last.fm + Spotify + Qobuz "
    "des artistes que tu aimes déjà."
)

# Init session state
if "recs" not in st.session_state:
    st.session_state.recs = None
if "last_run_seeds" not in st.session_state:
    st.session_state.last_run_seeds = {}

# ---------------------------------------------------------------------------
# Chargement des données (mis en cache)
# ---------------------------------------------------------------------------

with st.spinner("Chargement des données..."):
    history_df = get_history()
    hist_minutes_map = get_history_minutes()
    lastfm_sim = get_lastfm_similar()
    lastfm_tags = get_lastfm_tags()
    spotify_sim = get_spotify_similar()
    spotify_id_index = get_spotify_id_index()
    qobuz_sim = get_qobuz_similar()
    qobuz_portraits = get_qobuz_portraits()
    artist_popularity = get_artist_popularity()
    tag_sim_index = get_tag_similarity_index()
    excluded_static = get_excluded()
    seeds_pool = get_seeds_pool()
    all_genres = get_all_genres()

# Dislikes lus à chaque rerun (non mis en cache pour réactivité)
disliked = feedback.get_disliked()
liked = feedback.get_liked()
fb_stats = feedback.stats()
excluded = excluded_static | disliked

with st.expander("📊 Statistiques des données chargées"):
    cols = st.columns(7)
    cols[0].metric("Pool seeds (biblio + playlists)", len(seeds_pool))
    cols[1].metric("Last.fm en base", len(lastfm_sim))
    cols[2].metric("Spotify en base", len(spotify_sim))
    cols[3].metric("Qobuz en base", len(qobuz_sim))
    cols[4].metric("Artistes historique", len(hist_minutes_map))
    cols[5].metric("👍 likes", fb_stats["likes"])
    cols[6].metric("👎 dislikes", fb_stats["dislikes"])

# ---------------------------------------------------------------------------
# Sidebar : paramètres
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header("⚙️ Paramètres")

    n_results = st.slider("Nombre de recommandations", 1, 10, 5)

    st.divider()
    st.subheader("🌱 Seeds depuis l'historique")
    use_history_seeds = st.checkbox(
        "Inclure l'historique d'écoute comme seed",
        value=True,
        help="Ajoute automatiquement tes artistes les plus écoutés en plus "
             "de ceux que tu sélectionnes manuellement."
    )

    if use_history_seeds:
        n_history_seeds = st.slider(
            "Nombre d'artistes top historique", 5, 50, 20,
        )
        recent_months = st.slider(
            "Période 'récent' (mois)", 1, 24, 12,
        )
        recent_weight = st.slider(
            "Pondération récent vs total",
            0.0, 1.0, 0.7,
            help="0 = total uniquement, 1 = récent uniquement"
        )
    else:
        n_history_seeds = 0
        recent_months = 12
        recent_weight = 0.7

    st.divider()
    st.subheader("📊 Sources de similarité")
    st.caption(
        "Pondérations relatives des trois sources. La somme n'a pas besoin de "
        "valoir 1 — la formule normalise automatiquement. Le poids Spotify est "
        "déduit (`1 - Last.fm - Qobuz`, clampé ≥ 0)."
    )
    lastfm_weight = st.slider(
        "Poids Last.fm",
        0.0, 1.0, 0.4, step=0.05,
        help="Score de similarité 0–1 basé sur les co-écoutes Last.fm.",
    )
    qobuz_weight = st.slider(
        "Poids Qobuz",
        0.0, 1.0, 0.2, step=0.05,
        help="Section 'Artistes similaires' de Qobuz (rang 1 = plus proche). "
             "Plus pointu, axé public francophone audiophile.",
    )
    spotify_weight_effective = max(0.0, 1.0 - lastfm_weight - qobuz_weight)
    st.caption(f"→ Poids Spotify effectif : **{spotify_weight_effective:.2f}**")

    st.divider()
    st.subheader("🎯 Boost historique")
    history_boost = st.slider(
        "Booster les artistes déjà écoutés",
        0.0, 1.0, 0.3,
        help="0 = pas de boost. Plus la valeur est haute, plus les artistes "
             "présents dans ton historique remontent."
    )

    st.divider()
    st.subheader("🌈 Diversité")
    diversity_weight = st.slider(
        "Anti-redondance",
        0.0, 1.0, 0.0,
        help="0 = score pur (peut donner 5 artistes du même micro-genre). "
             "1 = anti-redondance maximale (varie au maximum les genres). "
             "Re-classement façon MMR sur les tags Last.fm."
    )

    st.divider()
    st.subheader("⭐ Pénalité popularité")
    popularity_penalty = st.slider(
        "Dampener les artistes 'génériques'",
        0.0, 2.0, 0.0,
        step=0.1,
        help="0 = pas de pénalité. Plus la valeur est haute, plus les artistes "
             "qui apparaissent comme similaires de TOUT LE MONDE (Daft Punk, "
             "Radiohead, etc.) sont rétrogradés au profit d'artistes plus "
             "pointus. Typique : 0.3–0.7."
    )

    st.divider()
    st.subheader("🏷️ Filtre genres")
    selected_genres = st.multiselect(
        "Restreindre aux genres",
        all_genres,
        help="Vide = pas de filtre. Sinon, applique le mode choisi sur les tags Last.fm."
    )
    genre_filter_mode = st.radio(
        "Mode du filtre",
        ["OR", "AND"],
        index=0,
        horizontal=True,
        help="OR : un tag suffit. AND : tous les tags choisis doivent être présents.",
    )
    genre_expansion = st.slider(
        "Tolérance genres proches",
        0.0, 1.0, 1.0,
        step=0.05,
        help="1.0 = filtre strict (seul le tag exact). "
             "0.3 = inclut les tags très proches (techno → minimal techno, "
             "tech house). 0.0 = inclut tous les voisins même éloignés. "
             "Basé sur la co-occurrence des tags sur les artistes."
    )

    st.divider()
    st.subheader("🔄 Couverture seeds")
    if st.button("Synchroniser artistes_liste.csv"):
        with st.spinner("Synchronisation..."):
            n_added = sync_seeds.sync(dry_run=False)
        if n_added > 0:
            st.success(f"{n_added} artistes ajoutés. Relance les scrapers pour les traiter.")
        else:
            st.info("Rien à synchroniser — tout est déjà dans artistes_liste.csv.")

# ---------------------------------------------------------------------------
# Sélection des seeds manuels
# ---------------------------------------------------------------------------

manual_seeds = st.multiselect(
    f"🎤 Choisis tes artistes seeds — autocomplétion sur {len(seeds_pool)} artistes "
    "(bibliothèque + playlists)",
    seeds_pool,
)

# ---------------------------------------------------------------------------
# Construction des seeds finaux
# ---------------------------------------------------------------------------

seeds: dict[str, float] = {a: 1.0 for a in manual_seeds}

if use_history_seeds:
    weights = history_weights(history_df, recent_months, recent_weight)
    top_history = sorted(weights.items(), key=lambda x: x[1], reverse=True)[:n_history_seeds]
    for artist, weight in top_history:
        seeds[artist] = max(seeds.get(artist, 0), weight)

unknown_seeds = [
    s for s in seeds
    if s not in lastfm_sim and s not in spotify_sim and s not in qobuz_sim
]

# ---------------------------------------------------------------------------
# Bouton de calcul + résumé
# ---------------------------------------------------------------------------

col_btn, col_info = st.columns([1, 4])
with col_btn:
    go = st.button("🚀 Recommander", type="primary", disabled=not seeds)
with col_info:
    n_manual = len(manual_seeds)
    n_hist = len(seeds) - n_manual
    msg = f"**{len(seeds)} seeds** — manuels : {n_manual}, historique : {n_hist}"
    if unknown_seeds:
        msg += f" — ⚠️ {len(unknown_seeds)} sans données"
    if disliked:
        msg += f" — 👎 {len(disliked)} artistes exclus"
    st.caption(msg)

if unknown_seeds:
    with st.expander(f"⚠️ {len(unknown_seeds)} seeds ignorés (pas de similaires en base)"):
        st.write(unknown_seeds)
        st.caption(
            "Ajoute-les via le bouton 'Synchroniser artistes_liste.csv' dans la "
            "sidebar, puis relance les scrapers Last.fm/Spotify."
        )

# ---------------------------------------------------------------------------
# Calcul (mémorisé en session_state pour ne pas être perdu au clic feedback)
# ---------------------------------------------------------------------------

if go:
    with st.spinner("Calcul des recommandations..."):
        recs = recommend(
            seeds=seeds,
            lastfm_similar=lastfm_sim,
            spotify_similar=spotify_sim,
            lastfm_tags=lastfm_tags,
            excluded=excluded,
            history_minutes_map=hist_minutes_map,
            lastfm_weight=lastfm_weight,
            history_boost=history_boost,
            genre_filter=selected_genres,
            n_results=n_results,
            diversity_weight=diversity_weight,
            popularity_penalty=popularity_penalty,
            artist_popularity=artist_popularity,
            genre_filter_mode=genre_filter_mode,
            tag_sim_index=tag_sim_index,
            genre_expansion_threshold=genre_expansion,
            qobuz_similar=qobuz_sim,
            qobuz_weight=qobuz_weight,
            qobuz_portraits=qobuz_portraits,
        )
    st.session_state.recs = recs
    st.session_state.last_run_seeds = dict(seeds)
    # Persistance de la session pour ré-exploration ultérieure
    if recs:
        params = {
            "alpha_lastfm": lastfm_weight,
            "alpha_qobuz": qobuz_weight,
            "beta_recent": recent_weight if use_history_seeds else None,
            "gamma_boost": history_boost,
            "lambda_diversity": diversity_weight,
            "omega_popularity": popularity_penalty,
            "recent_months": recent_months if use_history_seeds else None,
            "genre_filter": "|".join(selected_genres) if selected_genres else "",
            "genre_mode": genre_filter_mode,
            "genre_expansion": genre_expansion,
        }
        session_log.save_session(recs, seeds, params)

# ---------------------------------------------------------------------------
# Affichage
# ---------------------------------------------------------------------------

recs = st.session_state.recs
if recs is not None:
    if not recs:
        st.warning(
            "Aucune recommandation. Tente d'élargir les seeds, de réduire le "
            "filtre genre, ou de relancer un scraping."
        )
    else:
        st.success(f"{len(recs)} recommandations.")

        # Tableau récapitulatif
        df_display = pd.DataFrame([
            {
                "#": i + 1,
                "Artiste": r.artist,
                "Score": round(r.score, 3),
                "Cité par": r.citations,
                "Last.fm": round(r.lastfm_score, 3),
                "Spotify": round(r.spotify_score, 3),
                "Qobuz": round(r.qobuz_score, 3),
                "Histo.": "✓" if r.in_history else "",
                "Min.": int(r.history_minutes),
                "Genres": ", ".join(r.tags[:3]) if r.tags else "",
            }
            for i, r in enumerate(recs)
        ])
        st.dataframe(df_display, use_container_width=True, hide_index=True)

        st.subheader("📋 Détails et feedback")
        for i, r in enumerate(recs, 1):
            current_vote = feedback.get_vote(r.artist)
            vote_badge = ""
            if current_vote == 1:
                vote_badge = " 👍"
            elif current_vote == -1:
                vote_badge = " 👎"

            with st.expander(f"#{i} — **{r.artist}** (score {r.score:.3f}){vote_badge}"):
                cols = st.columns([2, 3, 2])

                with cols[0]:
                    st.markdown(f"**Score Last.fm :** {r.lastfm_score:.3f}")
                    st.markdown(f"**Score Spotify :** {r.spotify_score:.3f}")
                    st.markdown(f"**Score Qobuz :** {r.qobuz_score:.3f}")
                    st.markdown(f"**Cité par :** {r.citations} seeds")
                    if r.in_history:
                        st.markdown(f"**Historique :** {int(r.history_minutes)} min")
                    if r.tags:
                        st.markdown(f"**Tags :** {', '.join(r.tags)}")
                    if r.portrait:
                        with st.expander("📖 Portrait Qobuz"):
                            st.markdown(r.portrait)

                with cols[1]:
                    st.markdown("**Seeds qui ont mené à cette reco :**")
                    st.write(r.citing_seeds)

                with cols[2]:
                    st.markdown("**Feedback**")
                    bcols = st.columns(2)
                    if bcols[0].button("👍", key=f"like_{i}_{r.artist}"):
                        feedback.save_feedback(r.artist, 1)
                        st.rerun()
                    if bcols[1].button("👎", key=f"dislike_{i}_{r.artist}"):
                        feedback.save_feedback(r.artist, -1)
                        st.rerun()
                    st.caption(
                        "👎 → exclu des recos futures.\n\n"
                        "👍 → mémorisé (sans effet auto)."
                    )

                    st.markdown("**Liens**")
                    artist_q = r.artist.replace(" ", "+")
                    st.markdown(
                        f"[🎧 Spotify](https://open.spotify.com/search/{artist_q})  •  "
                        f"[📻 Last.fm](https://www.last.fm/music/{artist_q})  •  "
                        f"[🔍 YouTube](https://www.youtube.com/results?search_query={artist_q})"
                    )

                    st.markdown("**Action**")
                    if st.button(
                        "➕ Ajouter à artistes_liste.csv",
                        key=f"add_{i}_{r.artist}",
                        help="Ajoute cet artiste à la liste des seeds. Il sera "
                             "scrapé par Last.fm/Spotify au prochain run et "
                             "pourra alors être utilisé comme seed lui-même."
                    ):
                        added = sync_seeds.add_artist(r.artist)
                        if added:
                            st.success(f"✓ {r.artist} ajouté")
                        else:
                            st.info(f"{r.artist} était déjà dans la liste")

                # Player Spotify embed si on a l'ID de l'artiste
                spotify_id = spotify_id_index.get(r.artist)
                if spotify_id:
                    st.markdown("**Aperçu Spotify**")
                    embed_url = f"https://open.spotify.com/embed/artist/{spotify_id}"
                    st.components.v1.iframe(embed_url, height=152, scrolling=False)

# ---------------------------------------------------------------------------
# Sessions précédentes (persistance)
# ---------------------------------------------------------------------------

with st.expander("📚 Sessions précédentes", expanded=False):
    sessions_df = session_log.list_sessions()
    if sessions_df.empty:
        st.caption("Aucune session enregistrée. Lance une reco pour la sauvegarder.")
    else:
        st.caption(f"{len(sessions_df)} sessions sauvegardées.")
        # Sélecteur
        session_ids = sessions_df["session_id"].tolist()
        labels = [
            f"{row['session_id']} — top: {row['top_artist']} ({row['n_recos']} recos)"
            for _, row in sessions_df.iterrows()
        ]
        selected_idx = st.selectbox(
            "Charger une session",
            range(len(session_ids)),
            format_func=lambda i: labels[i],
        )
        if selected_idx is not None:
            sid = session_ids[selected_idx]
            details = session_log.load_session(sid)
            st.markdown(f"**Paramètres :** `{details.iloc[0]['params']}`")
            st.markdown(f"**Seeds :** `{details.iloc[0]['seeds']}`")
            st.dataframe(
                details[["rank", "artist", "score", "citations", "tags"]],
                use_container_width=True,
                hide_index=True,
            )
