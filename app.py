import streamlit as st
import pandas as pd
import io
import time
import zipfile
from datetime import datetime
from pathlib import Path

import geopandas as gpd

import geo_utils as gu
import nettoyage as nt
import topologie as tp
import rapport as rp
import i18n

# Traduction de l'interface (anglais par défaut, français au choix) : voir i18n.py et translations_en.py
i18n.installer()
if "lang" not in st.session_state:
    st.session_state["lang"] = "en"

# Dossier des logos : déposez-y vos images (voir assets/logos/LISEZ_MOI_README.txt)
LOGO_DIR = Path(__file__).parent / "assets" / "logos"
EXT_LOGOS = {".png", ".jpg", ".jpeg", ".webp", ".svg", ".gif"}


def liste_logos():
    if not LOGO_DIR.exists():
        return []
    return sorted(p for p in LOGO_DIR.iterdir() if p.suffix.lower() in EXT_LOGOS)


def icone_page():
    p = LOGO_DIR / "logo_labmath.png"
    if p.exists():
        try:
            from PIL import Image
            return Image.open(p)
        except Exception:
            pass
    return "🧬"


def barre_laterale():
    """Logos + choix de la langue, en haut de la barre latérale."""
    for p in liste_logos():
        try:
            st.sidebar.image(str(p), width="stretch")
        except Exception:
            pass
    i18n.selecteur_langue()
    st.sidebar.divider()

# --- CONFIGURATION DE LA PAGE ---
st.set_page_config(
    page_title="Lab_Math Smart Deduplicator",
    page_icon=icone_page(),
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- STYLES CSS PERSONNALISÉS (Branding) ---
# C'est ici que j'ai modifié la couleur du pied de page
st.markdown("""
<style>
    /* Style du pied de page */
    .footer {
        position: fixed;
        left: 0;
        bottom: 0;
        width: 100%;
        background-color: #0E1117; /* Fond noir */
        color: #FAFAFA; /* Texte principal blanc */
        text-align: center;
        padding: 10px;
        font-size: 12px;
        border-top: 1px solid #333;
        z-index: 1000;
    }
    /* C'EST ICI QUE LA COULEUR A CHANGÉ */
    .footer a, .footer-highlight {
        color: #4DA8DA !important; /* <-- BLEU PROFESSIONNEL (au lieu du rouge #FF4B4B) */
        text-decoration: none;
        font-weight: bold;
    }
    /* Style des onglets */
    .stTabs [data-baseweb="tab-list"] {
        gap: 24px;
    }
    .stTabs [data-baseweb="tab"] {
        height: 50px;
        white-space: pre-wrap;
        background-color: #f0f2f6;
        border-radius: 4px 4px 0px 0px;
        padding-left: 10px;
        padding-right: 10px;
    }
    .stTabs [aria-selected="true"] {
        background-color: #FFFFFF;
        border-top: 2px solid #4DA8DA; /* Changé aussi le haut de l'onglet actif en bleu */
    }
    /* Masquer le pied de page par défaut de Streamlit */
    footer {visibility: hidden;}
</style>
""", unsafe_allow_html=True)

# --- FONCTIONS MÉTIERS ---

@st.cache_data(show_spinner=False)
def charger_donnees(fichier):
    """Charge les données et retourne un DataFrame."""
    extension = fichier.name.split('.')[-1].lower()
    try:
        if extension in ['xlsx', 'xls']:
            return pd.read_excel(fichier)
        elif extension == 'csv':
            try:
                return pd.read_csv(fichier)
            except:
                return pd.read_csv(fichier, sep=';')
        elif extension == 'txt':
            return pd.read_csv(fichier, sep='\t')
        elif extension == 'sav':
            # Nécessite pyreadstat
            return pd.read_spss(fichier)
        elif extension == 'dta':
            # Nécessite pyreadstat
            return pd.read_stata(fichier)
    except Exception as e:
        st.error(f"Erreur technique lors du chargement : {e}")
        return None
    return None

@st.cache_data(show_spinner=False)
def charger_geo(kind, fichiers):
    """Charge un jeu de données géospatial (shapefile, KML, KMZ, GeoJSON, GPKG, ZIP).
    `fichiers` est un tuple de (nom, octets). Retourne (GeoDataFrame, notes)."""
    return gu.lire_geospatial(kind, list(fichiers))

def algo_resolution_intelligente(df, cols_cles):
    """
    Algorithme 'Smart Resolve' :
    1. Calcule un score de qualité (nombre de cellules remplies) pour chaque ligne.
    2. Trie les doublons pour mettre la ligne la plus complète en premier.
    3. Garde la première ligne (la meilleure) et rejette les autres.
    """
    # 1. Calcul du Score de Qualité (Densité de l'information)
    df = df.copy()
    # On compte les valeurs non nulles par ligne
    df['__Score_Qualite__'] = df.notna().sum(axis=1)
    
    # 2. Tri intelligent : D'abord par les clés (pour grouper), puis par Score décroissant
    # ascending=[True... pour les clés, False pour le score]
    sort_order = [True] * len(cols_cles) + [False]
    df_sorted = df.sort_values(by=cols_cles + ['__Score_Qualite__'], ascending=sort_order)
    
    # 3. Séparation
    # Base propre : on garde la première occurrence (celle avec le meilleur score grâce au tri)
    df_clean = df_sorted.drop_duplicates(subset=cols_cles, keep='first').copy()
    
    # Base rejetée (les doublons moins bons)
    # On prend les index qui ne sont PAS dans la base propre
    df_rejets = df_sorted[~df_sorted.index.isin(df_clean.index)].copy()
    
    # Nettoyage de la colonne temporaire
    df_clean = df_clean.drop(columns=['__Score_Qualite__'])
    df_rejets = df_rejets.drop(columns=['__Score_Qualite__'])
    
    return df_clean, df_rejets

def to_excel(df):
    """Convertit un DataFrame en fichier Excel binaire pour téléchargement."""
    output = io.BytesIO()
    # Nécessite xlsxwriter
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='Donnees')
    return output.getvalue()

# --- INTERFACE PRINCIPALE ---
S = st.session_state
TYPES_ACCEPTES = ['xlsx', 'xls', 'csv', 'txt', 'sav', 'dta',
                  'shp', 'shx', 'dbf', 'prj', 'cpg', 'kml', 'kmz', 'geojson', 'json', 'gpkg', 'zip']


def maintenant():
    return datetime.now().strftime("%H:%M:%S")


def attrs_de(gdf):
    return pd.DataFrame(gdf.drop(columns=gdf.geometry.name))


def reinitialiser_diagnostic():
    S.search_done = False
    S.resolved = False
    S.cols_cles = []
    S.nb_doublons = 0
    S.topo = None
    S.rapport = None


def init_jeu(df, gdf, notes, signature, nom):
    S.data, S.gdf, S.is_geo = df, gdf, gdf is not None
    S.orig_df = df.copy()
    S.orig_gdf = gdf.copy() if gdf is not None else None
    S.notes, S.signature, S.filename = notes, signature, nom
    S.journal, S.rejets, S.params, S.geo_corrections = [], [], {}, []
    reinitialiser_diagnostic()


def journaliser(etape, detail, av, ap, cellules=0):
    S.journal.append({"heure": maintenant(), "etape": etape, "detail": detail,
                      "lignes_avant": av[0], "lignes_apres": ap[0],
                      "colonnes_avant": av[1], "colonnes_apres": ap[1], "cellules_modifiees": int(cellules)})


def maj_attrs(df_new, etape, detail, cellules=0, motif=None, params=None):
    """Remplace la table de travail ; conserve les lignes retirées (rejets) et réassocie les géométries."""
    av = S.data.shape
    retire = S.data.index.difference(df_new.index)
    if len(retire):
        src = S.gdf if S.is_geo else S.data
        r = src.loc[retire].copy()
        r["__motif__"] = motif or etape
        S.rejets.append(r)
    if S.is_geo:
        # alignement par position : robuste si l'index a été modifié par une étape précédente
        pos = S.gdf.index.get_indexer(df_new.index)
        if (pos < 0).any() or not S.gdf.index.is_unique:
            raise ValueError("Index de la table de travail incohérent avec la géométrie : rechargez le fichier et relancez le traitement.")
        S.gdf = gpd.GeoDataFrame(df_new.copy(), geometry=S.gdf.geometry.iloc[pos].values,
                                 crs=S.gdf.crs, index=df_new.index)
    S.data = df_new
    journaliser(etape, detail, av, S.data.shape, cellules)
    if params:
        S.params[etape] = params
    reinitialiser_diagnostic()


def maj_geo(g_new, etape, detail, params=None, rejets=None):
    av = S.data.shape
    if rejets is not None and len(rejets):
        S.rejets.append(rejets)
    S.gdf = g_new
    S.data = attrs_de(g_new)
    journaliser(etape, detail, av, S.data.shape, 0)
    if params:
        S.params[etape] = params
    reinitialiser_diagnostic()


def rejets_concat():
    if not S.rejets:
        return None
    tout = pd.concat(S.rejets, ignore_index=True)
    if S.is_geo:
        tout = gpd.GeoDataFrame(tout, geometry="geometry", crs=S.gdf.crs) if "geometry" in tout.columns else tout
    return tout


def vue_apercu(df_ou_gdf, masque, ordre=None):
    sous = df_ou_gdf[masque]
    if isinstance(sous, gpd.GeoDataFrame):
        vue = pd.DataFrame(sous.drop(columns=sous.geometry.name))
        vue["géométrie"] = sous.geometry.to_wkt(rounding_precision=6).str.slice(0, 80)
    else:
        vue = sous
    if ordre is not None:
        vue = vue.loc[ordre.loc[vue.index].sort_values(kind="stable").index]
    return vue


def simplifier_synthese(d):
    """Aplatit un dict de synthèse (listes, notes) pour l'affichage et le rapport."""
    out = {}
    for k, v in d.items():
        if k == "notes":
            if v:
                out["notes"] = " ; ".join(v)
        elif isinstance(v, dict):
            out[k] = ", ".join(f"{a} ({b})" for a, b in v.items()) or "aucun"
        else:
            out[k] = v
    return out


def geo_diag_complet(gdf, avec_topologie=True):
    d = gu.diagnostic_geometrique(gdf)
    res = {"objets": d["lignes"], "géométries absentes": d["nulles"], "géométries vides": d["vides"],
           "géométries invalides": d["invalides"], "système de coordonnées": d["crs"] or "inconnu",
           "types": ", ".join(f"{k} ({v})" for k, v in d["types"].items()), "étendue": d["bornes"]}
    if d["hors_cameroun"] is not None:
        res["objets hors Cameroun"] = d["hors_cameroun"]
    if avec_topologie:
        try:
            synth, _ = tp.controle_topologique(gdf)
            for k, v in simplifier_synthese(synth).items():
                if k not in res:
                    res[k] = v
        except Exception as e:
            res["contrôle topologique"] = f"non réalisé ({e})"
    return res


def construire_rapport():
    qa, qp = nt.resume_qualite(S.orig_df), nt.resume_qualite(S.data)
    pa, pp = nt.profil_colonnes(S.orig_df), nt.profil_colonnes(S.data)
    rej = rejets_concat()
    motifs = None
    if rej is not None and "__motif__" in rej.columns:
        motifs = rej["__motif__"].value_counts().rename_axis("Motif").reset_index(name="Nombre de lignes")
    meta = {"Base traitée": S.filename, "Date du rapport": datetime.now().strftime("%d/%m/%Y %H:%M"),
            "Type de données": "géospatiales" if S.is_geo else "tabulaires",
            "Lignes à l'origine": qa["lignes"], "Lignes finales": qp["lignes"],
            "Colonnes à l'origine": qa["colonnes"], "Colonnes finales": qp["colonnes"]}
    if S.is_geo:
        meta["Système de coordonnées final"] = S.gdf.crs.to_string() if S.gdf.crs else "inconnu"
    geo = None
    if S.is_geo:
        geo = {"avant": geo_diag_complet(S.orig_gdf), "apres": geo_diag_complet(S.gdf), "corrections": S.geo_corrections}
    params = S.params
    if i18n.lang() != "fr":      # noms d'étapes et de paramètres dans la langue du rapport
        params = {i18n.tr(k, signaler=False): ({i18n.tr(a, signaler=False): b for a, b in v.items()} if isinstance(v, dict) else v)
                  for k, v in S.params.items()}
    args = (meta, S.journal, qa, qp, pa, pp, geo, motifs, params)
    html_fr = rp.construire_html(*args)
    xlsx_fr = rp.construire_excel(*args)
    return i18n.tr_html(html_fr).encode("utf-8"), i18n.tr_excel(xlsx_fr)


def main():
    barre_laterale()
    st.title("🧬 Smart Deduplicator Pro")
    st.write("### Nettoyage, doublons, topologie et export de vos données (tabulaires et géospatiales)")
    st.markdown("---")

    if 'data' not in S:
        S.data = None
        S.gdf = None
        S.is_geo = False
        S.notes = []
        S.signature = None
        S.search_done = False
        S.resolved = False
        st.info("👋 Bienvenue. Formats supportés : **.xlsx, .csv, .txt, .sav (SPSS), .dta (Stata)** et, pour les données "
                "géospatiales, **.shp (avec .shx, .dbf, .prj), .kml, .kmz, .geojson, .gpkg ou une archive .zip**.")

    uploaded_files = st.file_uploader(
        "📂 Téléchargez votre base de données ici (pour un shapefile, ajoutez tous ses fichiers : .shp, .shx, .dbf, .prj)",
        type=TYPES_ACCEPTES, accept_multiple_files=True)

    if uploaded_files:
        jeux = gu.regrouper_fichiers(uploaded_files)
        libelles = list(jeux.keys())
        choix = st.selectbox("🗂️ Plusieurs jeux de données détectés : lequel traiter ?", libelles) if len(libelles) > 1 else libelles[0]
        jeu = jeux[choix]
        signature = (choix, tuple(sorted((f.name, f.size) for f in jeu["objs"])))

        if S.data is None or S.signature != signature:
            with st.spinner("🔄 Analyse et chargement du fichier en cours..."):
                try:
                    if jeu["kind"] == "table":
                        df_charge, gdf_charge, notes = charger_donnees(jeu["objs"][0]), None, []
                    else:
                        gdf_charge, notes = charger_geo(jeu["kind"], tuple((f.name, f.getvalue()) for f in jeu["objs"]))
                        df_charge = attrs_de(gdf_charge)
                except Exception as e:
                    st.error(f"Erreur technique lors du chargement : {e}")
                    df_charge, gdf_charge, notes = None, None, []
                if df_charge is not None:
                    init_jeu(df_charge, gdf_charge, notes, signature, choix)
                    st.toast("Fichier chargé avec succès !", icon="✅")

        if S.data is not None:
            df, est_geo, gdf = S.data, S.is_geo, S.gdf
            st.success(f"✅ **Base de travail :** {S.filename} | **{len(df):,}** lignes | **{len(df.columns)}** colonnes".replace(",", " ")
                       + (" (attributs) + géométrie" if est_geo else "") + f" | {len(S.journal)} étape(s) appliquée(s)")
            for note in S.notes:
                st.warning("ℹ️ " + note)
            if st.button("↩️ Revenir aux données d'origine (annule tous les traitements)"):
                init_jeu(S.orig_df.copy(), S.orig_gdf.copy() if S.orig_gdf is not None else None, S.notes, S.signature, S.filename)
                st.rerun()

            noms = ["📊 1. Diagnostic", "🧹 2. Nettoyage & Imputation", "🔍 3. Doublons"]
            if est_geo:
                noms.append("🗺️ 4. Géométrie & Topologie")
            noms.append("📤 5. Export & Rapport")
            onglets = st.tabs(noms)
            tab_diag, tab_net, tab_dup = onglets[0], onglets[1], onglets[2]
            tab_geo = onglets[3] if est_geo else None
            tab_exp = onglets[-1]

            # ===================== 1. DIAGNOSTIC =====================
            with tab_diag:
                st.subheader("État de la base")
                q = nt.resume_qualite(df)
                c = st.columns(4)
                c[0].metric("Lignes", f"{q['lignes']:,}".replace(",", " "))
                c[1].metric("Colonnes", q["colonnes"])
                c[2].metric("Complétude", f"{q['complétude (%)']} %")
                c[3].metric("Cellules vides", f"{q['cellules manquantes']:,}".replace(",", " "))
                c = st.columns(4)
                c[0].metric("Lignes entièrement vides", q["lignes entièrement vides"])
                c[1].metric("Colonnes entièrement vides", q["colonnes entièrement vides"])
                c[2].metric("Colonnes constantes", q["colonnes constantes"])
                c[3].metric("Lignes dupliquées (toutes colonnes)", q["lignes dupliquées (toutes colonnes)"])
                st.write("#### Colonnes")
                if len(df.columns):
                    st.dataframe(nt.profil_colonnes(df), width="stretch", hide_index=True)
                else:
                    st.info("La table n'a aucun attribut (shapefile sans .dbf ?). Seule la géométrie peut être contrôlée.")
                ab = nt.detecter_aberrantes(df) if len(df.select_dtypes("number").columns) else pd.DataFrame()
                if len(ab):
                    st.write("#### Valeurs numériques atypiques (méthode IQR, seuil 1,5)")
                    st.dataframe(ab, width="stretch", hide_index=True)
                if est_geo:
                    st.write("#### Géométrie")
                    d = gu.diagnostic_geometrique(gdf)
                    st.write(f"Types : {', '.join(f'{k} ({v})' for k, v in d['types'].items()) or 'aucun'} | CRS : {d['crs'] or 'inconnu'} | "
                             f"absentes ou vides : {d['nulles'] + d['vides']} | invalides : {d['invalides']}")

            # ===================== 2. NETTOYAGE =====================
            with tab_net:
                st.subheader("Procédures de nettoyage")
                st.caption("Cochez les procédures voulues puis appliquez. Elles s'exécutent dans cet ordre : noms de colonnes, textes, types, "
                           "lignes et colonnes, valeurs atypiques, imputation. Chaque étape est enregistrée dans le rapport.")
                with st.expander("1️⃣ Noms de colonnes et textes", expanded=True):
                    opt_noms = st.checkbox("Normaliser les noms de colonnes (minuscules, sans accents ni espaces)", value=False, key="o_noms",
                                           help="Attention aux shapefiles : les noms sont de toute façon limités à 10 caractères à l'export.")
                    opt_txt = st.checkbox("Nettoyer les textes : retirer les espaces superflus et transformer NA, N/A, null, -, ? en cases vides", value=True, key="o_txt")
                    casse = st.selectbox("Harmoniser la casse des textes", ["Ne pas modifier", "minuscule", "majuscule", "titre"], key="o_casse")
                    opt_types = st.checkbox("Convertir automatiquement les colonnes texte en nombres ou en dates quand 90 % des valeurs s'y prêtent", value=True, key="o_types")
                with st.expander("2️⃣ Lignes et colonnes vides", expanded=True):
                    opt_lvides = st.checkbox("Supprimer les lignes entièrement vides", value=True, key="o_lvides")
                    c1, c2 = st.columns([1, 2])
                    opt_lseuil = c1.checkbox("Supprimer les lignes trop incomplètes", value=False, key="o_lseuil")
                    seuil_l = c2.slider("Une ligne est supprimée si plus de … % de ses cases sont vides", 10, 99, 70, key="o_lseuil_v", disabled=not opt_lseuil)
                    opt_cvides = st.checkbox("Supprimer les colonnes entièrement vides", value=True, key="o_cvides")
                    c1, c2 = st.columns([1, 2])
                    opt_cseuil = c1.checkbox("Supprimer les colonnes trop incomplètes", value=False, key="o_cseuil")
                    seuil_c = c2.slider("Une colonne est supprimée si plus de … % de ses cases sont vides", 10, 99, 90, key="o_cseuil_v", disabled=not opt_cseuil)
                    opt_const = st.checkbox("Supprimer les colonnes constantes (une seule valeur)", value=False, key="o_const")
                with st.expander("3️⃣ Valeurs numériques atypiques"):
                    c1, c2, c3 = st.columns(3)
                    act_ab = c1.selectbox("Traitement", ["Aucun", "Plafonner aux bornes", "Mettre à vide (puis imputer)"], key="o_ab")
                    meth_ab = c2.selectbox("Méthode de détection", ["iqr", "zscore"], key="o_abm",
                                           format_func=lambda x: "Écart interquartile (IQR)" if x == "iqr" else "Écart-type (z-score)")
                    seuil_ab = c3.number_input("Seuil", 0.5, 10.0, 1.5 if meth_ab == "iqr" else 3.0, 0.1, key="o_abs")
                with st.expander("4️⃣ Imputation des valeurs manquantes", expanded=True):
                    c1, c2 = st.columns(2)
                    m_num = c1.selectbox("Colonnes numériques", nt.METHODES_NUM, index=0, key="o_mn")
                    m_cat = c2.selectbox("Colonnes texte / catégories", nt.METHODES_CAT, index=0, key="o_mc")
                    c1, c2, c3 = st.columns(3)
                    k_knn = c1.number_input("Nombre de voisins (KNN)", 1, 30, 5, key="o_k", disabled=m_num != "voisins proches (KNN)")
                    cn = c2.number_input("Valeur constante (numérique)", value=0.0, key="o_cn", disabled=m_num != "constante")
                    cc = c3.text_input("Valeur constante (texte)", "Non renseigné", key="o_cc", disabled=m_cat != "constante")
                    excl = st.multiselect("Colonnes à ne pas imputer (identifiants, par exemple)", list(df.columns), key="o_excl")
                    seuil_imp = st.slider("Ne pas imputer les colonnes dont plus de … % des cases sont vides", 10, 100, 100, key="o_simp",
                                          help="100 % : toutes les colonnes partiellement renseignées sont imputées.")
                    st.caption("KNN : chaque valeur manquante est estimée à partir des lignes les plus proches sur les autres colonnes numériques (distance pondérée, variables standardisées).")

                if st.button("🧹 Appliquer le nettoyage", type="primary", width="stretch"):
                    messages = []
                    with st.spinner("Nettoyage en cours..."):
                        cur = S.data
                        if opt_noms:
                            cur2, info, _ = nt.normaliser_noms_colonnes(cur)
                            maj_attrs(cur2, "Normalisation des noms de colonnes", f"{info['colonnes renommées']} colonne(s) renommée(s)")
                            cur = S.data
                        if opt_txt or casse != "Ne pas modifier":
                            cur2, info, n = nt.nettoyer_textes(cur, espaces=opt_txt, vides=opt_txt, casse=None if casse == "Ne pas modifier" else casse)
                            maj_attrs(cur2, "Nettoyage des textes", "espaces superflus et marqueurs de vide" + ("" if casse == "Ne pas modifier" else f", casse « {casse} »"), n, params={"casse": casse})
                            cur = S.data
                        if opt_types:
                            cur2, info, _ = nt.convertir_types(cur)
                            conv = info["colonnes converties"]
                            maj_attrs(cur2, "Conversion des types", ", ".join(f"{c} → {t}" for c, t in conv.items()) or "aucune colonne convertie")
                            cur = S.data
                        if opt_lvides or opt_lseuil:
                            cur2, info, _ = nt.supprimer_lignes_vides(cur, seuil_l if opt_lseuil else None)
                            maj_attrs(cur2, "Suppression des lignes vides ou trop incomplètes",
                                      f"{info['supprimées']} ligne(s) (dont {info['dont entièrement vides']} entièrement vide(s))",
                                      motif="Ligne vide ou trop incomplète", params={"seuil_lignes_pct": seuil_l if opt_lseuil else None})
                            cur = S.data
                        if opt_cvides or opt_cseuil or opt_const:
                            cur2, info, _ = nt.supprimer_colonnes(cur, seuil_c if opt_cseuil else None, opt_const, opt_cvides)
                            lst = info["colonnes supprimées"]
                            maj_attrs(cur2, "Suppression de colonnes", (", ".join(map(str, lst[:15])) + (" ..." if len(lst) > 15 else "")) if lst else "aucune colonne supprimée")
                            cur = S.data
                        if act_ab != "Aucun":
                            action = "plafonner" if act_ab.startswith("Plafonner") else "vider"
                            cur2, info, n = nt.traiter_aberrantes(cur, meth_ab, seuil_ab, action)
                            maj_attrs(cur2, "Traitement des valeurs atypiques", f"{n} valeur(s), méthode {meth_ab}, seuil {seuil_ab}, action : {act_ab}", n,
                                      params={"méthode": meth_ab, "seuil": seuil_ab, "action": act_ab})
                            cur = S.data
                        if m_num != "aucune" or m_cat != "aucune":
                            cur2, info, n = nt.imputer(cur, m_num, m_cat, k_knn, cn, cc, excl, seuil_imp)
                            det = "; ".join(f"{c}: {v['cellules']} ({v['méthode']})" for c, v in list(info["colonnes imputées"].items())[:12])
                            maj_attrs(cur2, "Imputation des valeurs manquantes", f"{n} cellule(s). {det}", n,
                                      params={"numérique": m_num, "catégories": m_cat, "k": int(k_knn), "colonnes exclues": excl})
                            messages += info["notes"]
                    for m in messages:
                        st.warning(m)
                    st.toast("Nettoyage appliqué.", icon="🧹")
                    st.rerun()

                if S.journal:
                    st.write("#### Étapes déjà appliquées")
                    st.dataframe(i18n.tr_df(pd.DataFrame(S.journal)[["heure", "etape", "detail", "lignes_apres", "cellules_modifiees"]]
                                 .rename(columns={"heure": "Heure", "etape": "Étape", "detail": "Détail", "lignes_apres": "Lignes", "cellules_modifiees": "Cellules modifiées"}),
                                 cellules=True, colonnes_cellules=["Étape", "Détail"]),
                                 width="stretch", hide_index=True)

            # ===================== 3. DOUBLONS =====================
            with tab_dup:
                st.subheader("Recherche et résolution des doublons")
                st.write("Sélectionnez les variables qui définissent l'unicité d'un enregistrement (ex. identifiant, ou nom + date)."
                         + (" Pour les données géospatiales, vous pouvez aussi choisir la **position** (deux objets au même endroit sont en doublon)." if est_geo else ""))
                options = list(df.columns) + ([gu.GEOM_LABEL] if est_geo else [])
                col1, col2 = st.columns([3, 1])
                with col1:
                    cols_sel = st.multiselect("Variables identifiantes (clé primaire) :", options=options, placeholder="Choisissez une ou plusieurs colonnes...", key="d_cols")
                    decimales = 6
                    if est_geo and gu.GEOM_LABEL in cols_sel:
                        decimales = st.slider("Précision de comparaison des positions (décimales des coordonnées)", 0, 9, gu.suggerer_decimales(gdf), key="d_dec",
                                              help="6 décimales en degrés ≈ 0,1 m ; 5 ≈ 1 m ; 4 ≈ 10 m. En mètres (système projeté) : 1 décimale = 10 cm.")
                with col2:
                    st.write("")
                    st.write("")
                    lancer = st.button("Lancer la recherche 🔎", type="primary", width="stretch", disabled=not cols_sel)
                if lancer:
                    utiliser_geom = est_geo and gu.GEOM_LABEL in cols_sel
                    cols_attr = [c for c in cols_sel if c != gu.GEOM_LABEL]
                    if est_geo:
                        table, cles = gu.table_cles(gdf, cols_attr, utiliser_geom, decimales)
                        nb = int(table.duplicated(subset=cles).sum())
                    else:
                        nb = int(df.duplicated(subset=cols_attr).sum())
                    S.cols_cles, S.cols_attr, S.utiliser_geom, S.decimales = cols_sel, cols_attr, utiliser_geom, decimales
                    S.nb_doublons, S.search_done, S.resolved = nb, True, False
                if S.search_done:
                    nb, keys = S.nb_doublons, S.cols_cles
                    st.divider()
                    if nb > 0:
                        st.warning(f"⚠️ **{nb:,} ligne(s) en doublon** selon : *{', '.join(keys)}*.".replace(",", " "))
                        if est_geo and S.utiliser_geom and not S.cols_attr:
                            st.info("ℹ️ La recherche porte sur la position seule. Deux enregistrements différents peuvent légitimement partager un point : "
                                    "vérifiez avec les attributs avant de supprimer.")
                        with st.expander("👁️ Aperçu des lignes concernées (groupées)"):
                            if est_geo:
                                table, cles = gu.table_cles(gdf, S.cols_attr, S.utiliser_geom, S.decimales)
                                masque = table.duplicated(subset=cles, keep=False)
                                ordre = table["__geom__"] if "__geom__" in table else gu.cle_texte(table, cles)
                                vue = vue_apercu(gdf, masque, ordre)
                            else:
                                vue = df[df.duplicated(subset=S.cols_attr, keep=False)].sort_values(by=S.cols_attr)
                            st.dataframe(vue.head(1000), width="stretch")
                            if len(vue) > 1000:
                                st.caption("Aperçu limité aux 1000 premières lignes.")
                        st.markdown("""<div style="background-color:#f0f2f6;padding:15px;border-radius:5px;margin:10px 0;">
                        <strong>🧠 Algorithme de complétude :</strong> pour chaque groupe de doublons, la ligne qui contient le plus d'informations est conservée.
                        Les autres sont retirées de la base de travail et conservées dans le fichier des rejets.</div>""", unsafe_allow_html=True)
                        if st.button("🚀 Appliquer la résolution intelligente", type="primary", width="stretch"):
                            if est_geo:
                                propre, _ = gu.resolution_intelligente_geo(gdf, S.cols_attr, S.utiliser_geom, S.decimales)
                                nouveau = attrs_de(propre)
                                nouveau.index = propre.index
                            else:
                                nouveau, _ = algo_resolution_intelligente(df, S.cols_attr)
                            maj_attrs(nouveau, "Résolution des doublons", f"clé : {', '.join(keys)}", motif="Doublon (ligne moins complète)",
                                      params={"clé": keys, "décimales": S.decimales if est_geo else None})
                            st.toast("Doublons résolus.", icon="✨")
                            st.rerun()
                    else:
                        st.success(f"✅ Aucun doublon selon : *{', '.join(keys)}*.")

            # ===================== 4. GÉOMÉTRIE & TOPOLOGIE =====================
            if est_geo:
                with tab_geo:
                    st.subheader("Contrôle et correction topologique")
                    with st.expander("⚙️ Seuils de contrôle", expanded=False):
                        c1, c2, c3 = st.columns(3)
                        sa = c1.number_input("Micro-polygone : surface ≤ (m²)", 0.0, 1e6, 1.0, key="t_sa")
                        sl = c2.number_input("Ligne quasi nulle : longueur ≤ (m)", 0.0, 1e4, 0.5, key="t_sl")
                        td = c3.number_input("Tolérance d'accrochage des extrémités (m)", 0.1, 500.0, 5.0, key="t_td")
                        c1, c2 = st.columns(2)
                        cm = c1.number_input("Chevauchement minimal retenu (m²)", 0.0, 1e6, 1.0, key="t_cm")
                        stn = c2.number_input("Trou entre polygones ≤ (m²)", 0.0, 1e6, 100.0, key="t_st")
                    if st.button("🔬 Lancer le contrôle topologique", type="primary"):
                        with st.spinner("Analyse topologique..."):
                            S.topo = tp.controle_topologique(S.gdf, sa, sl, td, cm, stn)
                    if S.topo is not None:
                        synth, anom = S.topo
                        resume = simplifier_synthese(synth)
                        st.dataframe(i18n.tr_df(pd.DataFrame({"Contrôle": list(resume.keys()), "Résultat": [str(v) for v in resume.values()]}), cellules=True), width="stretch", hide_index=True)
                        if len(anom):
                            with st.expander(f"Détail des anomalies ({len(anom)} affichées au plus)"):
                                st.dataframe(anom, width="stretch", hide_index=True)
                    st.divider()
                    st.write("#### Corrections")
                    c1, c2 = st.columns(2)
                    with c1:
                        k_rep = st.checkbox("Réparer les géométries invalides", True, key="k_rep")
                        k_som = st.checkbox("Supprimer les sommets répétés", True, key="k_som")
                        k_vid = st.checkbox("Supprimer les géométries absentes ou vides", True, key="k_vid")
                        k_mic = st.checkbox("Supprimer les micro-géométries (seuils ci-dessus)", False, key="k_mic")
                        k_exp = st.checkbox("Éclater les multi-parties en objets simples", False, key="k_exp")
                    with c2:
                        k_ori = st.checkbox("Orienter les polygones (norme Shapefile)", False, key="k_ori")
                        k_gri = st.checkbox("Caler les coordonnées sur une grille de précision", False, key="k_gri")
                        dec_gri = st.number_input("Décimales de la grille", 0, 9, 7, key="k_dec", disabled=not k_gri)
                        k_chev = st.checkbox("Résoudre les chevauchements de polygones (retire la zone commune au plus petit)", False, key="k_chev")
                        k_acc = st.checkbox("Accrocher les extrémités libres des lignes (tolérance ci-dessus)", False, key="k_acc")
                    if st.button("🛠️ Appliquer les corrections topologiques", width="stretch"):
                        with st.spinner("Correction en cours..."):
                            g_new, jr, rej = tp.corriger_topologie(
                                S.gdf, reparer=k_rep, sommets=k_som, supprimer_vides=k_vid, micro=k_mic, seuil_aire_m2=sa, seuil_longueur_m=sl,
                                exploser=k_exp, orienter=k_ori, grille_decimales=int(dec_gri) if k_gri else None, chevauchements=k_chev,
                                chevauchement_min_m2=cm, accrocher=k_acc, tol_dangle_m=td)
                            S.geo_corrections += [dict(x, heure=maintenant()) for x in jr]
                            actives = "; ".join(f"{x['action']} ({x['objets concernés']})" for x in jr if x["objets concernés"]) or "aucun changement"
                            maj_geo(g_new, "Corrections topologiques", actives, params={"corrections": [x["action"] for x in jr]}, rejets=rej)
                        st.toast("Corrections appliquées.", icon="🛠️")
                        st.rerun()
                    st.divider()
                    st.write("#### Système de coordonnées")
                    c1, c2, c3 = st.columns([2, 2, 1])
                    code_epsg = c1.number_input("Code EPSG", 1000, 999999, 4326, 1, key="g_epsg")
                    with c2:
                        st.write("")
                        st.write("")
                        if gdf.crs is None:
                            if st.button("📌 Définir ce système (données sans CRS)", width="stretch"):
                                maj_geo(gdf.set_crs(int(code_epsg)), "Définition du système de coordonnées", f"EPSG:{int(code_epsg)}")
                                st.rerun()
                        elif st.button("🔄 Reprojeter vers ce système", width="stretch"):
                            maj_geo(gdf.to_crs(int(code_epsg)), "Reprojection", f"vers EPSG:{int(code_epsg)}")
                            st.rerun()
                    c3.caption("4326 : WGS 84 (degrés). 32632 / 32633 : UTM 32N / 33N (mètres).")

            # ===================== 5. EXPORT & RAPPORT =====================
            with tab_exp:
                st.subheader("Base finale, rejets et rapport")
                rej = rejets_concat()
                k1, k2, k3 = st.columns(3)
                k1.metric("Lignes à l'origine", f"{len(S.orig_df):,}".replace(",", " "))
                k2.metric("Base finale", f"{len(df):,}".replace(",", " "), delta=f"{len(df) - len(S.orig_df)}", delta_color="off")
                k3.metric("Lignes rejetées", 0 if rej is None else len(rej))

                catalogue = gu.FORMATS_EXPORT if est_geo else gu.FORMATS_TABLE
                formats = st.multiselect("Formats d'export de la base finale", list(catalogue.keys()),
                                         default=[list(catalogue.keys())[0], list(catalogue.keys())[1]], key="x_fmt")
                final = S.gdf if est_geo else S.data
                fichiers_zip = {}

                def exporter(obj, fmt):
                    return gu.exporter_geo(obj, fmt) if est_geo else gu.exporter_table(obj, fmt)

                for fmt in formats:
                    try:
                        octets, ext, mime, notes = exporter(final, fmt)
                        fichiers_zip[f"base_finale.{ext}"] = octets
                        c1, c2 = st.columns([1, 3])
                        c1.download_button(f"📥 Base finale ({ext})", octets, f"Base_finale.{ext}", mime, key=f"dl_{ext}_{fmt}", width="stretch")
                        c2.caption(" ".join(notes) if notes else f"Format : {fmt}")
                    except Exception as e:
                        st.error(f"Export impossible en « {fmt} » : {e}")

                if rej is not None and len(rej):
                    st.write("##### Lignes rejetées (avec le motif)")
                    try:
                        fmt_rej = formats[0] if formats else list(catalogue.keys())[0]
                        rej_t = rej.copy()
                        if "__motif__" in rej_t.columns:
                            rej_t["__motif__"] = rej_t["__motif__"].map(lambda v: i18n.tr(v, signaler=False) if isinstance(v, str) else v)
                        octets, ext, mime, _ = exporter(rej_t, fmt_rej)
                        fichiers_zip[f"rejets_audit.{ext}"] = octets
                        st.download_button(f"🗑️ Rejets ({ext})", octets, f"Rejets_audit.{ext}", mime, key="dl_rej")
                    except Exception as e:
                        st.error(f"Export des rejets impossible : {e}")
                        try:
                            o, ext, mime, _ = gu.exporter_table(pd.DataFrame(rej.drop(columns=rej.geometry.name, errors="ignore")), "CSV (.csv, UTF-8)")
                            fichiers_zip["rejets_audit.csv"] = o
                            st.download_button("🗑️ Rejets (.csv, sans géométrie)", o, "Rejets_audit.csv", mime, key="dl_rej2")
                        except Exception:
                            pass

                st.divider()
                st.write("#### 📝 Rapport de traitement")
                st.caption("Le rapport décrit chaque étape (lignes avant/après, cellules modifiées, paramètres), la qualité avant et après, "
                           "les lignes rejetées et, pour les données géospatiales, le contrôle géométrique et topologique.")
                if st.button("Générer le rapport", type="primary"):
                    with st.spinner("Rédaction du rapport..."):
                        S.rapport = construire_rapport()
                if S.rapport:
                    html_b, xlsx_b = S.rapport
                    fichiers_zip["rapport_traitement.html"] = html_b
                    fichiers_zip["rapport_traitement.xlsx"] = xlsx_b
                    c1, c2 = st.columns(2)
                    c1.download_button("📄 Rapport (HTML, imprimable en PDF)", html_b, "Rapport_traitement.html", "text/html", width="stretch")
                    c2.download_button("📊 Rapport détaillé (Excel)", xlsx_b, "Rapport_traitement.xlsx",
                                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", width="stretch")
                if fichiers_zip:
                    buf = io.BytesIO()
                    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                        for n, b in fichiers_zip.items():
                            z.writestr(n, b)
                    st.divider()
                    st.download_button("📦 Tout télécharger en une archive (.zip)", buf.getvalue(), "Resultats_traitement.zip", "application/zip", type="primary")


    # --- PIED DE PAGE (Footer Fixe) ---
    # J'ai ajouté des spans avec la classe 'footer-highlight' pour cibler précisément les textes à colorer
    if i18n.lang() == "fr":
        ligne1 = ('Power by <b>Lab_Math</b>, du groupe <b>SCSM et CIE</b> (<a href="http://www.scsmaubmar.org" target="_blank">www.scsmaubmar.org</a>) et copyright 2026.')
        ou = "ou"
    else:
        ligne1 = ('Powered by <b>Lab_Math</b>, part of the <b>SCSM et CIE</b> group (<a href="http://www.scsmaubmar.org" target="_blank">www.scsmaubmar.org</a>). Copyright 2026.')
        ou = "or"
    st.markdown(f"""
    <div class="footer">
        <p>{ligne1}</p>
        <p><span class="footer-highlight">📞 Contact:</span> +237 620 30 74 39 / +237 674 65 18 56 &nbsp; | &nbsp; <span class="footer-highlight">✉️ Email:</span> <a href="mailto:scsm@scsmaubmar.org">scsm@scsmaubmar.org</a> {ou} <a href="mailto:marcialteda@scsmaubmar.org">marcialteda@scsmaubmar.org</a></p>
    </div>
    """, unsafe_allow_html=True)
    i18n.afficher_manques()

if __name__ == "__main__":
    main()