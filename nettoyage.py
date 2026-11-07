"""
nettoyage.py : procédures standard de nettoyage de données tabulaires.
Chaque fonction renvoie (table_résultat, info) où `info` est un dict décrivant ce qui a été fait.
L'index des lignes est toujours conservé (nécessaire pour réassocier les géométries).
"""
import re
import unicodedata

import numpy as np
import pandas as pd

VALEURS_VIDES = {"", "na", "n/a", "nan", "null", "none", "nul", "-", "--", "?", "nd", "n/d", "nr",
                 "non renseigné", "non renseigne", "non défini", "non defini", "#n/a", "#value!", "#div/0!"}


# ------------------------------------------------------------------ profil
def profil_colonnes(df):
    n = max(len(df), 1)
    lignes = []
    for c in df.columns:
        s = df[c]
        manq = int(s.isna().sum())
        ex = s.dropna()
        lignes.append({
            "colonne": c,
            "type": str(s.dtype),
            "manquants": manq,
            "% manquants": round(100 * manq / n, 1),
            "valeurs distinctes": int(s.nunique(dropna=True)),
            "exemple": str(ex.iloc[0])[:40] if len(ex) else "",
        })
    return pd.DataFrame(lignes, columns=["colonne", "type", "manquants", "% manquants", "valeurs distinctes", "exemple"])


def resume_qualite(df):
    cellules = df.shape[0] * df.shape[1]
    manq = int(df.isna().sum().sum())
    return {
        "lignes": int(df.shape[0]),
        "colonnes": int(df.shape[1]),
        "cellules manquantes": manq,
        "complétude (%)": round(100 * (1 - manq / cellules), 2) if cellules else 100.0,
        "lignes entièrement vides": int(df.isna().all(axis=1).sum()) if df.shape[1] else 0,
        "colonnes entièrement vides": int(df.isna().all(axis=0).sum()),
        "colonnes constantes": int((df.nunique(dropna=True) <= 1).sum()),
        "lignes dupliquées (toutes colonnes)": int(df.duplicated().sum()) if df.shape[1] else 0,
    }


# ------------------------------------------------------------------ noms de colonnes
def _slug(nom):
    nom = unicodedata.normalize("NFKD", str(nom)).encode("ascii", "ignore").decode()
    nom = re.sub(r"[^0-9a-zA-Z]+", "_", nom).strip("_").lower()
    return nom or "colonne"


def normaliser_noms_colonnes(df):
    """Noms en minuscules sans accents ni espaces ; les doublons de noms reçoivent un suffixe."""
    anciens = list(df.columns)
    nouveaux, vus = [], {}
    for a in anciens:
        n = _slug(a)
        if n in vus:
            vus[n] += 1
            n = f"{n}_{vus[n]}"
        else:
            vus[n] = 0
        nouveaux.append(n)
    out = df.copy()
    out.columns = nouveaux
    modifies = sum(a != b for a, b in zip(anciens, nouveaux))
    return out, {"colonnes renommées": modifies, "correspondance": dict(zip(anciens, nouveaux))}, 0


# ------------------------------------------------------------------ textes
def nettoyer_textes(df, espaces=True, vides=True, casse=None):
    """Retire espaces superflus, remplace les marqueurs de vide (NA, N/A, -, ...) par une vraie valeur vide,
    harmonise la casse ('minuscule', 'majuscule', 'titre' ou None)."""
    out = df.copy()
    modifs = 0
    for c in out.columns:
        s = out[c]
        if not (pd.api.types.is_object_dtype(s) or pd.api.types.is_string_dtype(s)):
            continue
        avant = s.copy()
        t = s.astype("object")
        mask = t.notna()
        if espaces:
            t = t.where(~mask, t.map(lambda v: re.sub(r"\s+", " ", v).strip() if isinstance(v, str) else v))
        if vides:
            t = t.map(lambda v: np.nan if isinstance(v, str) and v.strip().lower() in VALEURS_VIDES else v)
        if casse:
            f = {"minuscule": str.lower, "majuscule": str.upper, "titre": str.title}[casse]
            t = t.map(lambda v: f(v) if isinstance(v, str) else v)
        diff = ~((avant.astype("object") == t) | (avant.isna() & pd.Series(t, index=avant.index).isna()))
        modifs += int(diff.sum())
        out[c] = t
    return out, {"cellules modifiées": modifs}, modifs


# ------------------------------------------------------------------ types
def convertir_types(df, seuil=0.9, numeriques=True, dates=True):
    """Convertit en nombres ou en dates les colonnes texte dont au moins `seuil` des valeurs s'y prêtent."""
    out = df.copy()
    conv = {}
    for c in out.columns:
        s = out[c]
        if not (pd.api.types.is_object_dtype(s) or pd.api.types.is_string_dtype(s)):
            continue
        base = s.dropna()
        if base.empty:
            continue
        if numeriques:
            t = pd.to_numeric(base.astype(str).str.replace(",", ".", regex=False).str.replace(r"\s", "", regex=True), errors="coerce")
            if t.notna().mean() >= seuil:
                out[c] = pd.to_numeric(s.astype(str).str.replace(",", ".", regex=False).str.replace(r"\s", "", regex=True), errors="coerce")
                out.loc[s.isna(), c] = np.nan
                conv[c] = "nombre"
                continue
        if dates:
            try:
                d = pd.to_datetime(base, errors="coerce", dayfirst=True, format="mixed")
            except Exception:
                continue
            if d.notna().mean() >= seuil and base.astype(str).str.contains(r"[-/]").mean() > 0.8:
                out[c] = pd.to_datetime(s, errors="coerce", dayfirst=True, format="mixed")
                conv[c] = "date"
    return out, {"colonnes converties": conv}, 0


# ------------------------------------------------------------------ lignes et colonnes
def supprimer_lignes_vides(df, seuil_pct=None, colonnes=None):
    """Supprime les lignes entièrement vides et, si `seuil_pct` est donné, celles dont la part de cases vides dépasse ce seuil."""
    cols = list(colonnes) if colonnes else list(df.columns)
    if not cols:
        return df, {"supprimées": 0}, pd.DataFrame()
    part = df[cols].isna().mean(axis=1) * 100
    totalement = part >= 100
    masque = totalement.copy()
    if seuil_pct is not None:
        masque = masque | (part > seuil_pct)
    return df[~masque], {"supprimées": int(masque.sum()), "dont entièrement vides": int(totalement.sum())}, df[masque]


def supprimer_colonnes(df, vides_pct=None, constantes=False, toujours_vides=True):
    part = df.isna().mean() * 100
    a_retirer = []
    if toujours_vides:
        a_retirer += list(part[part >= 100].index)
    if vides_pct is not None:
        a_retirer += [c for c in part[part > vides_pct].index if c not in a_retirer]
    if constantes:
        a_retirer += [c for c in df.columns if df[c].nunique(dropna=True) <= 1 and c not in a_retirer]
    return df.drop(columns=a_retirer), {"colonnes supprimées": a_retirer}, 0


# ------------------------------------------------------------------ valeurs aberrantes
def _bornes(s, methode, seuil):
    s = s.dropna()
    if methode == "iqr":
        q1, q3 = s.quantile(0.25), s.quantile(0.75)
        iqr = q3 - q1
        return q1 - seuil * iqr, q3 + seuil * iqr
    m, sd = s.mean(), s.std()
    return m - seuil * sd, m + seuil * sd


def detecter_aberrantes(df, methode="iqr", seuil=1.5, colonnes=None):
    cols = colonnes or list(df.select_dtypes("number").columns)
    lignes = []
    for c in cols:
        s = df[c]
        if s.dropna().nunique() < 4:
            continue
        lo, hi = _bornes(s, methode, seuil)
        n = int(((s < lo) | (s > hi)).sum())
        if n:
            lignes.append({"colonne": c, "borne basse": round(float(lo), 4), "borne haute": round(float(hi), 4), "valeurs aberrantes": n})
    return pd.DataFrame(lignes)


def traiter_aberrantes(df, methode="iqr", seuil=1.5, action="plafonner", colonnes=None):
    """action : 'plafonner' (ramène à la borne), 'vider' (met la valeur à vide, à imputer ensuite)."""
    out = df.copy()
    cols = colonnes or list(out.select_dtypes("number").columns)
    total, detail = 0, {}
    for c in cols:
        s = out[c]
        if s.dropna().nunique() < 4:
            continue
        lo, hi = _bornes(s, methode, seuil)
        masque = (s < lo) | (s > hi)
        n = int(masque.sum())
        if not n:
            continue
        if action == "plafonner":
            out[c] = s.clip(lower=lo, upper=hi)
        else:
            out.loc[masque, c] = np.nan
        detail[c] = n
        total += n
    return out, {"valeurs traitées par colonne": detail, "action": action}, total


# ------------------------------------------------------------------ imputation
METHODES_NUM = ["médiane", "moyenne", "mode", "voisins proches (KNN)", "interpolation linéaire",
                "propagation avant", "propagation arrière", "constante", "aucune"]
METHODES_CAT = ["mode", "propagation avant", "propagation arrière", "constante", "aucune"]


def _est_entier(s):
    v = s.dropna()
    return len(v) > 0 and np.allclose(v, np.round(v))


def imputer(df, num="médiane", cat="mode", k=5, const_num=0.0, const_cat="Non renseigné", exclure=None, seuil_max_pct=100):
    """Impute les valeurs manquantes. Les colonnes manquantes à plus de `seuil_max_pct` % ne sont pas imputées."""
    out = df.copy()
    exclure = set(exclure or [])
    part = df.isna().mean() * 100
    candidates = [c for c in df.columns if c not in exclure and df[c].isna().any() and part[c] <= seuil_max_pct and part[c] < 100]
    num_cols = [c for c in candidates if pd.api.types.is_numeric_dtype(df[c]) and not pd.api.types.is_bool_dtype(df[c])]
    cat_cols = [c for c in candidates if c not in num_cols]
    detail, notes = {}, []

    # ---- numériques
    if num != "aucune" and num_cols:
        if num == "voisins proches (KNN)":
            from sklearn.impute import KNNImputer
            toutes_num = [c for c in df.columns if c not in exclure and pd.api.types.is_numeric_dtype(df[c])
                          and not pd.api.types.is_bool_dtype(df[c]) and df[c].notna().any()]
            if len(toutes_num) < 2:
                notes.append("KNN impossible avec moins de deux colonnes numériques : médiane utilisée à la place.")
                num = "médiane"
            else:
                X = df[toutes_num].astype(float)
                mu, sd = X.mean(), X.std().replace(0, 1).fillna(1)
                Z = (X - mu) / sd
                imp = KNNImputer(n_neighbors=max(1, int(k)), weights="distance")
                Zi = pd.DataFrame(imp.fit_transform(Z), columns=toutes_num, index=df.index)
                Xi = Zi * sd + mu
                for c in num_cols:
                    manq = df[c].isna()
                    vals = Xi.loc[manq, c]
                    if _est_entier(df[c]):
                        vals = vals.round()
                    out.loc[manq, c] = vals.astype(out[c].dtype) if pd.api.types.is_integer_dtype(out[c]) else vals
                    detail[c] = (int(manq.sum()), f"KNN (k={int(k)})")
        if num != "voisins proches (KNN)":
            for c in num_cols:
                s = df[c]
                manq = s.isna()
                n = int(manq.sum())
                if num == "médiane":
                    v = s.median(); out[c] = s.fillna(round(v) if _est_entier(s) else v)
                elif num == "moyenne":
                    v = s.mean(); out[c] = s.fillna(round(v) if _est_entier(s) else v)
                elif num == "mode":
                    m = s.mode(dropna=True); out[c] = s.fillna(m.iloc[0]) if len(m) else s
                elif num == "constante":
                    out[c] = s.fillna(const_num)
                elif num == "interpolation linéaire":
                    out[c] = s.astype(float).interpolate(method="linear", limit_direction="both")
                    if _est_entier(s):
                        out[c] = out[c].round()
                elif num == "propagation avant":
                    out[c] = s.ffill()
                elif num == "propagation arrière":
                    out[c] = s.bfill()
                restant = int(out[c].isna().sum())
                detail[c] = (n - restant, num)

    # ---- catégorielles
    if cat != "aucune" and cat_cols:
        for c in cat_cols:
            s = df[c]
            n = int(s.isna().sum())
            if cat == "mode":
                m = s.mode(dropna=True); out[c] = s.fillna(m.iloc[0]) if len(m) else s
            elif cat == "constante":
                out[c] = s.astype("object").fillna(const_cat)
            elif cat == "propagation avant":
                out[c] = s.ffill()
            elif cat == "propagation arrière":
                out[c] = s.bfill()
            detail[c] = (n - int(out[c].isna().sum()), cat)

    total = sum(v[0] for v in detail.values())
    return out, {"colonnes imputées": {c: {"cellules": v[0], "méthode": v[1]} for c, v in detail.items()},
                 "notes": notes}, total
