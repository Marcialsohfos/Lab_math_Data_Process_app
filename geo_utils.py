"""
geo_utils.py : lecture, contrôle, déduplication et export de données géospatiales
(shapefile, KML, KMZ, GeoJSON, GeoPackage, archives ZIP) pour Lab_Math Smart Deduplicator.
"""
import io
import os
import shutil
import tempfile
import zipfile
from pathlib import Path

import geopandas as gpd
import pandas as pd
import shapely
from shapely import make_valid

# Un .shp sans .shx est lisible : GDAL reconstruit l'index à la volée.
os.environ.setdefault("SHAPE_RESTORE_SHX", "YES")

GEOM_LABEL = "📍 Géométrie (même position)"
SHP_PARTS = {"shp", "shx", "dbf", "prj", "cpg", "sbn", "sbx"}
EXT_GEO = SHP_PARTS | {"kml", "kmz", "geojson", "json", "gpkg", "zip"}
BBOX_CAMEROUN = (8.4, 1.6, 16.3, 13.2)  # lon_min, lat_min, lon_max, lat_max


# ------------------------------------------------------------------ regroupement
def ext_de(nom):
    return nom.rsplit(".", 1)[-1].lower() if "." in nom else ""


def regrouper_fichiers(fichiers):
    """
    Regroupe des fichiers téléversés en jeux de données.
    Les composants d'un même shapefile (.shp, .shx, .dbf, .prj...) forment un seul jeu.
    Retourne {libellé: {"kind": str, "objs": [fichiers]}}.
    """
    jeux = {}
    for f in fichiers:
        ext = ext_de(f.name)
        if ext in SHP_PARTS:
            stem = f.name.rsplit(".", 1)[0]
            cle = f"{stem} (shapefile)"
            jeux.setdefault(cle, {"kind": "shapefile", "objs": []})["objs"].append(f)
        elif ext in EXT_GEO:
            kind = "geojson" if ext in ("geojson", "json") else ext
            jeux[f.name] = {"kind": kind, "objs": [f]}
        else:
            jeux[f.name] = {"kind": "table", "objs": [f]}
    return jeux


# ------------------------------------------------------------------ lecture
def _lire_chemin(chemin, notes):
    """Lit un fichier SIG ; concatène les couches s'il y en a plusieurs."""
    import pyogrio

    chemin = str(chemin)
    try:
        couches = [c[0] for c in pyogrio.list_layers(chemin)]
    except Exception:
        couches = [None]

    def lire(couche):
        kw = {"layer": couche} if couche else {}
        try:
            return gpd.read_file(chemin, engine="pyogrio", **kw)
        except UnicodeDecodeError:
            notes.append("Encodage non UTF-8 détecté : lecture en latin-1.")
            return gpd.read_file(chemin, engine="pyogrio", encoding="latin-1", **kw)

    if len(couches) <= 1:
        return lire(couches[0] if couches else None)

    morceaux = []
    for c in couches:
        g = lire(c)
        if len(g):
            g["__couche__"] = c
            morceaux.append(g)
    if not morceaux:
        return gpd.GeoDataFrame(geometry=[])
    notes.append(f"{len(couches)} couches trouvées, réunies dans une seule table (colonne __couche__).")
    crs = morceaux[0].crs
    return gpd.GeoDataFrame(pd.concat(morceaux, ignore_index=True), crs=crs)


def _bornes_en_degres(gdf):
    if gdf.empty or gdf.geometry.isna().all():
        return False
    minx, miny, maxx, maxy = gdf.total_bounds
    return -180 <= minx <= 180 and -180 <= maxx <= 180 and -90 <= miny <= 90 and -90 <= maxy <= 90


def lire_geospatial(kind, fichiers):
    """
    fichiers : liste de (nom, octets). Retourne (GeoDataFrame, notes).
    """
    notes = []
    tmp = Path(tempfile.mkdtemp(prefix="geo_"))
    try:
        for nom, data in fichiers:
            (tmp / Path(nom).name).write_bytes(data)

        if kind == "shapefile":
            shp = next(tmp.glob("*.shp"), None)
            if shp is None:
                raise ValueError("Le fichier .shp est absent : ajoutez-le avec ses fichiers associés (.shx, .dbf, .prj).")
            if not shp.with_suffix(".dbf").exists():
                notes.append("Fichier .dbf absent : la table des attributs est vide, seule la géométrie est lue. "
                             "Ajoutez le .dbf pour traiter les doublons sur les attributs.")
            if not shp.with_suffix(".shx").exists():
                notes.append("Fichier .shx absent : l'index a été reconstruit automatiquement.")
            if not shp.with_suffix(".prj").exists():
                notes.append("Fichier .prj absent : le système de coordonnées est inconnu.")
            gdf = _lire_chemin(shp, notes)

        elif kind in ("zip", "kmz"):
            dossier = tmp / "contenu"
            with zipfile.ZipFile(tmp / Path(fichiers[0][0]).name) as z:
                z.extractall(dossier)
            candidats = []
            for motif in ("*.shp", "*.gpkg", "*.geojson", "*.json", "*.kml"):
                candidats += sorted(dossier.rglob(motif))
            if not candidats:
                raise ValueError("Aucun fichier géospatial (.shp, .kml, .geojson, .gpkg) trouvé dans l'archive.")
            if kind == "zip" and any(c.suffix == ".shp" for c in candidats):
                candidats = [c for c in candidats if c.suffix == ".shp"] or candidats
            parts = []
            for c in candidats:
                g = _lire_chemin(c, notes)
                if len(candidats) > 1:
                    g["__fichier__"] = c.name
                parts.append(g)
            if len(parts) > 1:
                notes.append(f"{len(parts)} fichiers dans l'archive, réunis (colonne __fichier__).")
                gdf = gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), crs=parts[0].crs)
            else:
                gdf = parts[0]
        else:  # kml, geojson, gpkg
            gdf = _lire_chemin(tmp / Path(fichiers[0][0]).name, notes)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    gdf = gdf.reset_index(drop=True)
    if gdf.crs is None and _bornes_en_degres(gdf):
        gdf = gdf.set_crs(4326)
        notes.append("Système de coordonnées absent : coordonnées en degrés, WGS 84 (EPSG:4326) supposé.")
    return gdf, notes


# ------------------------------------------------------------------ diagnostic et réparation
def diagnostic_geometrique(gdf):
    geom = gdf.geometry
    nulles = int(geom.isna().sum())
    vides = int((geom.notna() & geom.is_empty).sum())
    valides_mask = geom.notna() & ~geom.is_empty
    invalides = int((valides_mask & ~geom.is_valid).sum())
    d = {
        "lignes": len(gdf),
        "nulles": nulles,
        "vides": vides,
        "invalides": invalides,
        "types": geom[valides_mask].geom_type.value_counts().to_dict(),
        "crs": gdf.crs.to_string() if gdf.crs else None,
        "bornes": [round(float(x), 5) for x in gdf.total_bounds] if valides_mask.any() else None,
        "hors_cameroun": None,
    }
    if gdf.crs is not None and gdf.crs.to_epsg() == 4326 and valides_mask.any():
        lon_min, lat_min, lon_max, lat_max = BBOX_CAMEROUN
        c = geom[valides_mask].centroid if not (geom[valides_mask].geom_type == "Point").all() else geom[valides_mask]
        hors = ~((c.x >= lon_min) & (c.x <= lon_max) & (c.y >= lat_min) & (c.y <= lat_max))
        d["hors_cameroun"] = int(hors.sum())
    return d


def reparer_geometries(gdf):
    g = gdf.copy()
    masque = g.geometry.notna() & ~g.geometry.is_empty & ~g.geometry.is_valid
    n = int(masque.sum())
    if n:
        g.loc[masque, g.geometry.name] = g.loc[masque].geometry.apply(make_valid)
    return g, n


def supprimer_vides(gdf):
    garde = gdf.geometry.notna() & ~gdf.geometry.is_empty
    return gdf[garde].reset_index(drop=True), int((~garde).sum())


# ------------------------------------------------------------------ doublons
def suggerer_decimales(gdf):
    return 6 if (gdf.crs is None or gdf.crs.is_geographic) else 1


def cle_geometrie(gdf, decimales=6):
    """
    Clé texte de la géométrie : 2D, arrondie à `decimales`, normalisée
    (deux géométries identiques à l'arrondi près ont la même clé).
    Les géométries absentes ou vides reçoivent une clé unique (jamais en doublon).
    """
    g = gdf.geometry.values
    g = shapely.force_2d(g)
    try:
        g = shapely.set_precision(g, 10.0 ** (-decimales))
    except Exception:
        pass
    g = shapely.normalize(g)
    cles = pd.Series(shapely.to_wkb(g, hex=True), index=gdf.index, dtype="object")
    absentes = gdf.geometry.isna() | gdf.geometry.is_empty
    for i in gdf.index[absentes]:
        cles.loc[i] = f"__sans_geometrie_{i}"
    return cles


def table_cles(gdf, cols_attr, utiliser_geom, decimales):
    """Construit la table sur laquelle on cherche les doublons ; retourne (table, colonnes_cles)."""
    attrs = pd.DataFrame(gdf.drop(columns=gdf.geometry.name))
    t = attrs[list(cols_attr)].copy() if cols_attr else pd.DataFrame(index=gdf.index)
    cles = list(cols_attr)
    if utiliser_geom:
        t["__geom__"] = cle_geometrie(gdf, decimales)
        cles.append("__geom__")
    return t, cles


def cle_texte(table, cles):
    """Concatène les colonnes clés en texte (valeurs manquantes = chaîne vide), compatible pandas 2 et 3."""
    out = pd.Series("", index=table.index, dtype="object")
    for i, c in enumerate(cles):
        morceau = table[c].map(lambda v: "" if pd.isna(v) else str(v)).astype("object")
        out = morceau if i == 0 else out + "|" + morceau
    return out


def resolution_intelligente_geo(gdf, cols_attr, utiliser_geom, decimales):
    """
    Même logique que l'application : parmi les doublons, on garde la ligne la plus complète.
    Score = nombre d'attributs renseignés + 1 si la géométrie est présente.
    Retourne (base_propre, rejets), dans l'ordre d'origine.
    """
    index_origine = gdf.index                     # on le restitue : l'application réassocie les géométries par cet index
    g = gdf.reset_index(drop=True)
    t, cles = table_cles(g, cols_attr, utiliser_geom, decimales)
    attrs = pd.DataFrame(g.drop(columns=g.geometry.name))
    score = attrs.notna().sum(axis=1) + (g.geometry.notna() & ~g.geometry.is_empty).astype(int)
    ordre = score.sort_values(ascending=False, kind="stable").index
    garde = t.loc[ordre].drop_duplicates(subset=cles, keep="first").index
    garde_mask = g.index.isin(garde)
    propre, rejets = g[garde_mask].copy(), g[~garde_mask].copy()
    propre.index = index_origine[garde_mask]
    rejets.index = index_origine[~garde_mask]
    return propre, rejets


# ------------------------------------------------------------------ export
FORMATS_EXPORT = {
    "Shapefile zippé (.shp, .shx, .dbf, .prj)": ("zip", "application/zip"),
    "KML (.kml)": ("kml", "application/vnd.google-earth.kml+xml"),
    "GeoPackage (.gpkg)": ("gpkg", "application/geopackage+sqlite3"),
    "GeoJSON (.geojson)": ("geojson", "application/geo+json"),
    "Excel (.xlsx, attributs + WKT)": ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    "CSV (.csv, attributs + WKT)": ("csv", "text/csv"),
}


def vers_table_wkt(gdf):
    t = pd.DataFrame(gdf.drop(columns=gdf.geometry.name))
    t["geometrie_wkt"] = gdf.geometry.to_wkt()
    if gdf.crs is not None:
        t["crs"] = gdf.crs.to_string()
    return t


def _noms_shapefile(cols):
    """Noms de champs de 10 caractères maximum, uniques. Retourne {ancien: nouveau}."""
    pris, mapping = set(), {}
    for c in cols:
        base = str(c)[:10]
        n, k = base, 1
        while n.lower() in pris:
            suf = str(k)
            n = base[: 10 - len(suf)] + suf
            k += 1
        pris.add(n.lower())
        mapping[c] = n
    return mapping


def exporter_geo(gdf, libelle_format):
    """Retourne (octets, extension, mime, notes)."""
    ext, mime = FORMATS_EXPORT[libelle_format]
    nom_geom = gdf.geometry.name
    g = gdf.drop(columns=[c for c in gdf.columns if str(c).startswith("__") and c != nom_geom], errors="ignore")
    notes = []

    if ext in ("xlsx", "csv"):
        t = vers_table_wkt(g)
        if ext == "xlsx":
            buf = io.BytesIO()
            with pd.ExcelWriter(buf, engine="xlsxwriter") as w:
                t.to_excel(w, index=False, sheet_name="Donnees")
            return buf.getvalue(), ext, mime, notes
        return t.to_csv(index=False).encode("utf-8-sig"), ext, mime, notes

    if ext in ("geojson", "kml") and g.crs is not None and g.crs.to_epsg() != 4326:
        g = g.to_crs(4326)
        notes.append("Coordonnées converties en WGS 84 (EPSG:4326), requis pour ce format.")
    # les dates avec heure ne passent pas partout : conversion en texte
    for c in g.columns:
        if c != nom_geom and pd.api.types.is_datetime64_any_dtype(g[c]):
            g[c] = g[c].astype(str).replace("NaT", None)

    tmp = Path(tempfile.mkdtemp(prefix="exp_"))
    try:
        if ext == "zip":
            attrs = [c for c in g.columns if c != nom_geom]
            mapping = _noms_shapefile(attrs)
            changes = {a: b for a, b in mapping.items() if a != b}
            if changes:
                notes.append("Noms de champs raccourcis à 10 caractères (limite du Shapefile) : "
                             + ", ".join(f"{a} → {b}" for a, b in list(changes.items())[:12]) + (" ..." if len(changes) > 12 else ""))
            g = g.rename(columns=mapping)
            familles = g.geometry.dropna().geom_type.str.replace("Multi", "", regex=False)
            types = sorted(familles.unique())
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                groupes = [(None, g)] if len(types) <= 1 else [(t, g[g.geometry.geom_type.fillna("").str.replace("Multi", "", regex=False) == t]) for t in types]
                if len(types) > 1:
                    notes.append("La couche mélange plusieurs types de géométrie : un shapefile distinct a été créé pour chacun ("
                                 + ", ".join(types) + ").")
                for t, sous in groupes:
                    stem = "donnees" if t is None else f"donnees_{t.lower()}"
                    dossier = tmp / stem
                    dossier.mkdir()
                    sous.to_file(dossier / f"{stem}.shp", encoding="utf-8")
                    for p in sorted(dossier.iterdir()):
                        z.write(p, p.name)
            return buf.getvalue(), ext, mime, notes
        driver = {"gpkg": "GPKG", "geojson": "GeoJSON", "kml": "KML"}[ext]
        chemin = tmp / f"donnees.{ext}"
        g.to_file(chemin, driver=driver)
        return chemin.read_bytes(), ext, mime, notes
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


FORMATS_TABLE = {
    "Excel (.xlsx)": ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    "CSV (.csv, UTF-8)": ("csv", "text/csv"),
    "SPSS (.sav)": ("sav", "application/octet-stream"),
    "Stata (.dta)": ("dta", "application/octet-stream"),
    "JSON (.json)": ("json", "application/json"),
}


def exporter_table(df, libelle_format):
    """Retourne (octets, extension, mime, notes) pour une table sans géométrie."""
    ext, mime = FORMATS_TABLE[libelle_format]
    notes = []
    if ext == "xlsx":
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="xlsxwriter") as w:
            df.to_excel(w, index=False, sheet_name="Donnees")
        return buf.getvalue(), ext, mime, notes
    if ext == "csv":
        return df.to_csv(index=False).encode("utf-8-sig"), ext, mime, notes
    if ext == "json":
        return df.to_json(orient="records", force_ascii=False, date_format="iso").encode("utf-8"), ext, mime, notes
    import pyreadstat
    d = df.copy()
    for c in d.columns:
        if d[c].dtype == "object" or str(d[c].dtype).startswith("str"):
            d[c] = d[c].astype(object).where(d[c].notna(), None)
    tmp = Path(tempfile.mkdtemp(prefix="exp_"))
    try:
        chemin = tmp / f"donnees.{ext}"
        if ext == "sav":
            pyreadstat.write_sav(d, str(chemin))
        else:
            pyreadstat.write_dta(d, str(chemin))
        return chemin.read_bytes(), ext, mime, notes
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
