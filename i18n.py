# -*- coding: utf-8 -*-
"""
Traduction de l'interface (français -> anglais) sans toucher à la logique de l'application.

Principe : le code reste écrit en français ; chaque texte affiché passe par `tr()`, qui cherche sa traduction dans
`translations_en.py` (textes exacts + modèles à trous {} pour les phrases construites avec des variables).
Un texte inconnu reste en français : rien ne casse. Pour voir les textes non traduits, ouvrez l'application avec
`?i18n=debug` à la fin de l'adresse.

Pour ajouter une langue : créez `translations_xx.py` sur le même modèle et ajoutez-la dans LANGUES.
"""
import re
import io
import html
import importlib
import streamlit as st

LANGUES = {"English": "en", "Français": "fr"}
_MODULES = {"en": "translations_en"}
_cache = {}


def lang():
    try:
        return st.session_state.get("lang", "en")
    except Exception:
        return "en"


def _mod():
    return importlib.import_module(_MODULES.get(lang(), "translations_en"))


def _norm(s):
    return re.sub(r"\s+", " ", s).strip()


def _charger(code):
    if code in _cache:
        return _cache[code]
    exact, modeles = {}, []
    if code in _MODULES:
        m = importlib.import_module(_MODULES[code])
        exact = {_norm(k): v for k, v in m.EXACT.items()}
        for fr, en in m.TEMPLATES:
            morceaux = re.split(r"(\{\})", _norm(fr))
            motif = "".join("(.+?)" if p == "{}" else re.escape(p) for p in morceaux)
            modeles.append((re.compile("^" + motif + "$", re.S), en, sum(len(p) for p in morceaux if p != "{}")))
        modeles.sort(key=lambda t: -t[2])           # les modèles les plus précis d'abord
    _cache[code] = (exact, modeles)
    return _cache[code]


def _manque(s):
    try:
        st.session_state.setdefault("_i18n_manques", set()).add(s)
    except Exception:
        pass


def _sur(s):
    """Traduction prudente, pour des textes qui peuvent être des données de l'utilisateur (noms de colonnes...) :
    on ne traduit que les phrases (au moins 8 caractères ou une espace)."""
    if not isinstance(s, str):
        return s
    t = s.strip()
    if len(t) < 8 and " " not in t:
        if lang() == "en" and t in _mod().SAFE_SHORT:
            return tr(s, signaler=False)
        return s
    return tr(s, signaler=False)


def tr(s, signaler=True):
    """Traduit un texte français vers la langue courante. Les espaces de début et de fin sont conservés."""
    if not isinstance(s, str) or not s.strip() or lang() == "fr":
        return s
    exact, modeles = _charger(lang())
    cle = _norm(s)
    av = s[: len(s) - len(s.lstrip())]
    ap = s[len(s.rstrip()):]
    if cle in exact:
        return av + exact[cle] + ap
    for rx, en, _ in modeles:
        m = rx.match(cle)
        if m:
            groupes = [_sur(g) for g in m.groups()]
            try:
                return av + en.format(*groupes) + ap
            except (IndexError, KeyError):
                break
    if "<" in cle and ">" in cle:                    # bloc HTML : on traduit les textes entre balises
        sortie = _tr_noeuds(s)
        if sortie != s:
            return sortie
    if signaler and re.search(r"[àâäçéèêëîïôöùûüœ]", cle, re.I):      # on ne signale que ce qui ressemble à du français
        _manque(cle)
    return s


def _tr_noeuds(html_txt):
    def rep(m):
        t = m.group(1)
        return ">" + tr(t, signaler=False) + "<" if t.strip() else m.group(0)
    return re.sub(r">([^<>]+)<", rep, html_txt)


def tr_html(html_txt):
    """Traduit un rapport HTML : chaque bloc <p>, <h1-3>, <td>, <th>, <title> est traduit en entier (avec ses balises internes)."""
    if lang() == "fr":
        return html_txt

    def rep(m):
        brut = m.group(2)
        simple = "<" not in brut                      # texte sans balise : on enlève l'échappement HTML (&#x27; ...) avant la recherche
        corps_fr = html.unescape(brut) if simple else brut
        corps = _sur(corps_fr) if m.group(1).startswith("<td") else tr(corps_fr)
        if corps == corps_fr:
            return m.group(0)
        return m.group(1) + (html.escape(corps, quote=False) if simple else corps) + m.group(3)
    out = re.sub(r"(<(?:p|h1|h2|h3|td|th|title)\b[^>]*>)(.*?)(</(?:p|h1|h2|h3|td|th|title)>)", rep, html_txt, flags=re.S)
    return out.replace("lang='fr'", "lang='en'") if lang() == "en" else out


def tr_df(df, cellules=False, colonnes_cellules=None):
    """Copie d'un tableau avec les en-têtes traduits ; les cellules seulement si demandé (tableaux de l'application, pas les données utilisateur)."""
    try:
        import pandas as pd
    except ImportError:
        return df
    if lang() == "fr" or not isinstance(df, pd.DataFrame):
        return df
    out = df.copy()
    internes = _mod().INTERNAL_HEADERS
    out.columns = [(tr(c, signaler=False) if (c in internes or cellules) else _sur(c)) if isinstance(c, str) else c for c in out.columns]
    if cellules:
        for c in out.columns:
            if colonnes_cellules and c not in colonnes_cellules:
                continue
            if out[c].dtype == object:
                out[c] = out[c].map(lambda v: tr(v, signaler=False) if isinstance(v, str) else v)
    return out


def tr_excel(octets, feuilles_utilisateur=("Colonnes avant", "Colonnes après")):
    """Traduit un classeur Excel produit par rapport.py : noms de feuilles, en-têtes et cellules.
    Les feuilles qui listent les colonnes de l'utilisateur ne sont pas traduites dans le corps."""
    if lang() == "fr":
        return octets
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(octets))
    for ws in wb.worksheets:
        corps = ws.title not in feuilles_utilisateur
        for ligne in ws.iter_rows():
            for c in ligne:
                if isinstance(c.value, str):
                    if c.row == 1:
                        c.value = tr(c.value, signaler=False)
                    elif corps:
                        c.value = _sur(c.value)
        ws.title = tr(ws.title, signaler=False)[:31]
    sortie = io.BytesIO()
    wb.save(sortie)
    return sortie.getvalue()


# --------------------------------------------------------------------------------------- installation
_ARGS_TEXTE = {"label", "body", "text", "help", "placeholder", "caption", "page_title", "title", "message", "value_label"}
_FONCTIONS_TEXTE = ["write", "markdown", "caption", "header", "subheader", "title", "info", "warning", "error", "success",
                    "toast", "spinner", "button", "checkbox", "selectbox", "multiselect", "radio", "slider", "number_input",
                    "text_input", "text_area", "expander", "download_button", "file_uploader", "metric", "toggle", "progress",
                    "select_slider", "pills", "segmented_control", "status", "popover", "exception"]
_AVEC_OPTIONS = {"selectbox", "multiselect", "radio", "select_slider", "pills", "segmented_control"}


def _traduire_arguments(nom, args, kwargs, d=0):
    """d = 1 pour une méthode de classe (le premier argument est alors self)."""
    args = list(args)
    for i, a in enumerate(args):
        if isinstance(a, str) and i == d:
            args[i] = tr(a)
    for k in list(kwargs):
        v = kwargs[k]
        if k in _ARGS_TEXTE and isinstance(v, str):
            kwargs[k] = tr(v)
    if nom in _AVEC_OPTIONS:
        ff = kwargs.get("format_func")

        def fmt(x, ff=ff):
            y = ff(x) if ff else x
            if not isinstance(y, str):
                return y
            if nom != "multiselect" and y in _mod().OPTIONS_UI:      # les options de multiselect sont des noms de colonnes
                return tr(y, signaler=False)
            return _sur(y)
        kwargs["format_func"] = fmt
    return tuple(args), kwargs


def _envelopper(f, nom, methode=False):
    if getattr(f, "_i18n", False):
        return f
    d = 1 if methode else 0

    def w(*args, **kwargs):
        args, kwargs = _traduire_arguments(nom, args, kwargs, d)
        return f(*args, **kwargs)
    w._i18n = True
    w.__name__ = getattr(f, "__name__", nom)
    return w


def _dataframe(f, methode=False):
    if getattr(f, "_i18n", False):
        return f
    d = 1 if methode else 0

    def w(*args, **kwargs):
        args = list(args)
        if len(args) > d:
            args[d] = tr_df(args[d])
        elif "data" in kwargs:
            kwargs["data"] = tr_df(kwargs["data"])
        return f(*args, **kwargs)
    w._i18n = True
    return w


def _tabs(f, methode=False):
    if getattr(f, "_i18n", False):
        return f
    d = 1 if methode else 0

    def w(*args, **kwargs):
        args = list(args)
        if len(args) > d:
            args[d] = [tr(t) for t in args[d]]
        return f(*args, **kwargs)
    w._i18n = True
    return w


def installer():
    """À appeler une fois, avant toute utilisation de st.* par l'application."""
    from streamlit.delta_generator import DeltaGenerator
    for nom in _FONCTIONS_TEXTE:
        if hasattr(st, nom):
            setattr(st, nom, _envelopper(getattr(st, nom), nom))
        if hasattr(DeltaGenerator, nom):
            setattr(DeltaGenerator, nom, _envelopper(getattr(DeltaGenerator, nom), nom, methode=True))
    for nom in ("dataframe", "table", "data_editor"):
        if hasattr(st, nom):
            setattr(st, nom, _dataframe(getattr(st, nom)))
        if hasattr(DeltaGenerator, nom):
            setattr(DeltaGenerator, nom, _dataframe(getattr(DeltaGenerator, nom), methode=True))
    if hasattr(st, "tabs"):
        st.tabs = _tabs(st.tabs)
    if hasattr(DeltaGenerator, "tabs"):
        DeltaGenerator.tabs = _tabs(DeltaGenerator.tabs, methode=True)


def selecteur_langue(conteneur=None):
    """Sélecteur de langue (anglais par défaut). À placer en haut de la barre latérale."""
    c = conteneur or st.sidebar
    if "lang" not in st.session_state:
        st.session_state["lang"] = "en"
    noms = list(LANGUES)
    courant = [n for n, code in LANGUES.items() if code == st.session_state["lang"]][0]
    choix = c.radio("Language / Langue", noms, index=noms.index(courant), horizontal=True, key="_choix_langue")
    if st.session_state.get("_lang_prev") not in (None, LANGUES[choix]):
        st.session_state["rapport"] = None          # un rapport déjà généré est dans l'ancienne langue
    st.session_state["_lang_prev"] = LANGUES[choix]
    st.session_state["lang"] = LANGUES[choix]
    return st.session_state["lang"]


def afficher_manques():
    if st.query_params.get("i18n") == "debug":
        m = sorted(st.session_state.get("_i18n_manques", []))
        with st.sidebar.expander(f"i18n : {len(m)} untranslated"):
            st.code("\n".join(m) or "none")
