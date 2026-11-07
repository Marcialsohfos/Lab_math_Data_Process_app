"""
topologie.py : vérification et correction topologique de couches vectorielles (points, lignes, polygones).
Les seuils sont exprimés en mètres / mètres carrés ; les mesures sont faites dans un système métrique
(projection équivalente centrée sur le Cameroun si la couche est en degrés).
"""
import numpy as np
import pandas as pd
import geopandas as gpd
import shapely
from shapely import STRtree
from shapely.geometry import LineString, Point
from shapely.geometry.polygon import orient as _orient
from shapely.ops import nearest_points

CRS_METRIQUE = "+proj=laea +lat_0=6 +lon_0=12.5 +datum=WGS84 +units=m"
FAMILLES = {0: ("Point", "MultiPoint"), 1: ("LineString", "MultiLineString", "LinearRing"), 2: ("Polygon", "MultiPolygon")}


def _metres(gdf):
    """GeoSeries dans un système métrique."""
    if gdf.crs is None or gdf.crs.is_geographic:
        base = gdf if gdf.crs is not None else gdf.set_crs(4326)
        return base.geometry.to_crs(CRS_METRIQUE)
    return gdf.geometry


def _deg_par_metre(gdf):
    return 1.0 if (gdf.crs is not None and not gdf.crs.is_geographic) else 1.0 / 111320.0


def _dim(gdf):
    t = gdf.geometry[gdf.geometry.notna() & ~gdf.geometry.is_empty].geom_type
    if t.empty:
        return None
    s = set(t.str.replace("Multi", "", regex=False))
    if s <= {"Point"}:
        return 0
    if s <= {"LineString", "LinearRing"}:
        return 1
    if s <= {"Polygon"}:
        return 2
    return None  # couche mixte


def _homogene(g, dim):
    """Après make_valid : ne garde que les parties de la dimension voulue."""
    if g is None or dim is None or g.is_empty:
        return g
    if g.geom_type in FAMILLES[dim]:
        return g
    parts = [p for p in shapely.get_parts(g) if p.geom_type in FAMILLES[dim]]
    return shapely.union_all(parts) if parts else None


# ================================================================= CONTRÔLE
def controle_topologique(gdf, seuil_aire_m2=1.0, seuil_longueur_m=0.5, tol_dangle_m=5.0,
                         chevauchement_min_m2=1.0, seuil_trou_m2=100.0, max_elements=20000):
    """Retourne (synthèse: dict, anomalies: DataFrame). Chaque anomalie : numéro de ligne, type, détail."""
    g = gdf.geometry
    ok = g.notna() & ~g.is_empty
    vals = g.values
    synth, anom = {}, []
    notes = []

    def ajouter(idx, probleme, detail=""):
        for i in idx[:500]:
            anom.append({"ligne": int(i) if not isinstance(i, tuple) else str(i), "problème": probleme,
                         "détail": detail if not callable(detail) else detail(i)})

    synth["géométries absentes"] = int(g.isna().sum())
    synth["géométries vides"] = int((g.notna() & g.is_empty).sum())
    ajouter(list(gdf.index[g.isna() | (g.notna() & g.is_empty)]), "Géométrie absente ou vide")

    # validité
    inval = ok & ~g.is_valid
    synth["géométries invalides"] = int(inval.sum())
    raisons = {i: shapely.is_valid_reason(gdf.geometry.loc[i]) for i in gdf.index[inval][:500]}
    ajouter(list(gdf.index[inval]), "Géométrie invalide", lambda i: raisons.get(i, ""))

    # sommets répétés
    sans = shapely.remove_repeated_points(vals, tolerance=0)
    rep = ok.values & (shapely.get_num_coordinates(vals) > shapely.get_num_coordinates(sans))
    synth["sommets répétés (lignes/polygones)"] = int(rep.sum())
    ajouter(list(gdf.index[rep]), "Sommets consécutifs identiques")

    metr = _metres(gdf)
    dim = _dim(gdf)
    synth["type de couche"] = {0: "points", 1: "lignes", 2: "polygones"}.get(dim, "mixte")
    synth["objets multi-parties"] = int((ok & g.geom_type.fillna("").str.startswith("Multi")).sum())

    if dim == 1:
        lon = metr.length
        court = ok & (lon <= seuil_longueur_m)
        synth[f"lignes de longueur ≤ {seuil_longueur_m} m"] = int(court.sum())
        ajouter(list(gdf.index[court]), "Ligne de longueur nulle ou quasi nulle")
    if dim == 2:
        aire = metr.area
        micro = ok & (aire <= seuil_aire_m2)
        synth[f"polygones de surface ≤ {seuil_aire_m2} m²"] = int(micro.sum())
        ajouter(list(gdf.index[micro]), "Micro-polygone (surface très faible)")

    # ---- polygones : chevauchements et trous
    if dim == 2:
        valides = gdf[ok]
        if len(valides) > max_elements:
            notes.append(f"Chevauchements non testés : plus de {max_elements} polygones.")
        elif len(valides) > 1:
            geoms = shapely.make_valid(valides.geometry.values)
            tree = STRtree(geoms)
            a, b = tree.query(geoms, predicate="intersects")
            sel = a < b
            a, b = a[sel], b[sel]
            if len(a):
                inter = shapely.intersection(geoms[a], geoms[b])
                ser = gpd.GeoSeries(inter, crs=gdf.crs)
                if gdf.crs is None or gdf.crs.is_geographic:
                    ser = (ser.set_crs(4326) if ser.crs is None else ser).to_crs(CRS_METRIQUE)
                aires = ser.area
                reels = np.asarray(aires.values) > chevauchement_min_m2
                synth["paires de polygones qui se chevauchent"] = int(reels.sum())
                idx = valides.index
                for i, j, s in list(zip(a[reels], b[reels], np.asarray(aires.values)[reels]))[:300]:
                    anom.append({"ligne": int(idx[i]), "problème": "Chevauchement de polygones",
                                 "détail": f"avec la ligne {int(idx[j])} (≈ {s:,.1f} m²)".replace(",", " ")})
            else:
                synth["paires de polygones qui se chevauchent"] = 0
            # trous dans l'union
            union = shapely.union_all(geoms)
            trous = []
            for p in shapely.get_parts(union):
                if p.geom_type == "Polygon":
                    trous += [shapely.Polygon(r) for r in p.interiors]
            if trous:
                at = gpd.GeoSeries(trous, crs=gdf.crs)
                at = at.to_crs(CRS_METRIQUE).area if (gdf.crs is not None and gdf.crs.is_geographic) else at.area
                synth[f"trous entre polygones ≤ {seuil_trou_m2} m²"] = int((at <= seuil_trou_m2).sum())
            else:
                synth[f"trous entre polygones ≤ {seuil_trou_m2} m²"] = 0

    # ---- lignes : extrémités libres, sous-passages, croisements
    if dim == 1:
        lignes = gdf[ok]
        if len(lignes) > max_elements:
            notes.append(f"Réseau non testé : plus de {max_elements} lignes.")
        else:
            info = _extremites(lignes, tol_dangle_m)
            synth["extrémités libres (dangles)"] = info["libres"]
            synth[f"sous-passages (extrémité à ≤ {tol_dangle_m} m d'une autre ligne)"] = info["sous_passages"]
            for i in info["liste_sp"][:300]:
                anom.append({"ligne": int(i), "problème": "Sous-passage (extrémité non reliée)", "détail": f"à moins de {tol_dangle_m} m d'une autre ligne"})
            geoms = shapely.make_valid(lignes.geometry.values)
            tree = STRtree(geoms)
            a, b = tree.query(geoms, predicate="crosses")
            synth["croisements de lignes (carrefours non noeudés ou ouvrages)"] = int((a < b).sum())

    synth["notes"] = notes
    return synth, pd.DataFrame(anom, columns=["ligne", "problème", "détail"])


def _extremites(lignes, tol_m):
    """Compte les extrémités libres et celles qui pourraient être accrochées à une autre ligne."""
    metr = _metres(lignes)
    wkb = shapely.to_wkb(shapely.normalize(metr.values))
    _, uniq = np.unique(wkb, return_index=True)          # lignes identiques comptées une seule fois
    uniq = np.sort(uniq)
    lignes = lignes.iloc[uniq]
    geoms = metr.values[uniq]
    pts, proprio = [], []
    for k, g in enumerate(geoms):
        for part in shapely.get_parts(g):
            if part.geom_type != "LineString" or part.is_closed:
                continue
            c = part.coords
            pts += [(round(c[0][0], 2), round(c[0][1], 2)), (round(c[-1][0], 2), round(c[-1][1], 2))]
            proprio += [k, k]
    if not pts:
        return {"libres": 0, "sous_passages": 0, "liste_sp": []}
    s = pd.Series(list(zip(pts, proprio)))
    cnt = pd.Series(pts).value_counts()
    libres_idx = [i for i, p in enumerate(pts) if cnt[p] == 1]
    tree = STRtree(geoms)
    sous = []
    for i in libres_idx:
        p = Point(pts[i])
        cand = tree.query(p.buffer(tol_m))
        for c in cand:
            if c == proprio[i]:
                continue
            if shapely.distance(p, geoms[c]) <= tol_m:
                sous.append(proprio[i])
                break
    return {"libres": len(libres_idx), "sous_passages": len(sous), "liste_sp": [lignes.index[k] for k in sous]}


# ================================================================= CORRECTION
def corriger_topologie(gdf, reparer=True, sommets=True, supprimer_vides=True, micro=False,
                       seuil_aire_m2=1.0, seuil_longueur_m=0.5, exploser=False, orienter=False,
                       grille_decimales=None, chevauchements=False, chevauchement_min_m2=1.0,
                       accrocher=False, tol_dangle_m=5.0):
    """Applique les corrections demandées dans un ordre sûr. Retourne (gdf_corrigé, journal, rejets)."""
    g = gdf.copy()
    nom = g.geometry.name
    dim = _dim(g)
    journal = []
    rejets = []

    def enreg(action, n, detail=""):
        journal.append({"action": action, "objets concernés": int(n), "détail": detail})

    if exploser and g.geom_type.dropna().str.startswith("Multi").any():
        n0 = len(g)
        g = g.explode(index_parts=False).reset_index(drop=True)
        enreg("Éclatement des multi-parties", len(g) - n0, f"{n0} → {len(g)} objets (attributs dupliqués)")

    if reparer:
        masque = g.geometry.notna() & ~g.geometry.is_empty & ~g.geometry.is_valid
        n = int(masque.sum())
        if n:
            nouv = shapely.make_valid(g.geometry[masque].values)
            g.loc[masque, nom] = [(_homogene(x, dim) if dim is not None else x) for x in nouv]
        enreg("Réparation des géométries invalides", n, "make_valid, parties de dimension différente retirées")

    if sommets:
        vals = g.geometry.values
        ok = g.geometry.notna() & ~g.geometry.is_empty
        sans = shapely.remove_repeated_points(vals, tolerance=0)
        rep = ok.values & (shapely.get_num_coordinates(vals) > shapely.get_num_coordinates(sans))
        if rep.any():
            g.loc[rep, nom] = sans[rep]
        enreg("Suppression des sommets répétés", int(rep.sum()))

    if grille_decimales is not None:
        ok = g.geometry.notna() & ~g.geometry.is_empty
        arr = shapely.set_precision(g.geometry[ok].values, 10.0 ** (-grille_decimales))
        g.loc[ok, nom] = list(arr)
        enreg("Calage sur grille de précision", int(ok.sum()), f"{grille_decimales} décimales")

    if orienter and dim == 2:
        ok = g.geometry.notna() & ~g.geometry.is_empty

        def orienter_geom(x):
            if x.geom_type == "Polygon":
                return _orient(x, sign=-1.0)
            if x.geom_type == "MultiPolygon":
                return shapely.MultiPolygon([_orient(p, sign=-1.0) for p in x.geoms])
            return x
        g.loc[ok, nom] = g.geometry[ok].map(orienter_geom)
        enreg("Orientation des polygones", int(ok.sum()), "anneau extérieur dans le sens horaire (norme Shapefile)")

    if chevauchements and dim == 2 and len(g) > 1:
        ok_idx = list(g.index[g.geometry.notna() & ~g.geometry.is_empty])
        geoms = list(shapely.make_valid(g.geometry.loc[ok_idx].values))
        tree = STRtree(geoms)
        a, b = tree.query(np.array(geoms, dtype=object), predicate="intersects")
        n = 0
        crs = g.crs
        for i, j in sorted(zip(a, b)):
            if i >= j:
                continue
            inter = geoms[i].intersection(geoms[j])
            if inter.is_empty:
                continue
            s = gpd.GeoSeries([inter], crs=crs)
            ar = (s.to_crs(CRS_METRIQUE) if (crs is not None and crs.is_geographic) else s).area.iloc[0]
            if ar <= chevauchement_min_m2:
                continue
            ai, aj = geoms[i].area, geoms[j].area
            petit = i if ai <= aj else j
            geoms[petit] = _homogene(geoms[petit].difference(inter), 2)
            n += 1
        if n:
            for k, idx in enumerate(ok_idx):
                g.at[idx, nom] = geoms[k]
        enreg("Résolution des chevauchements", n, "la zone commune est retirée du polygone de plus petite surface")

    if accrocher and dim == 1:
        pas = _deg_par_metre(g)
        tol = tol_dangle_m * pas
        ok = list(g.index[g.geometry.notna() & ~g.geometry.is_empty & (g.geom_type == "LineString")])
        geoms = list(g.geometry.loc[ok].values)
        tree = STRtree(np.array(geoms, dtype=object))
        coords_ext = {}
        for k, x in enumerate(geoms):
            if not x.is_closed:
                for pos in (0, -1):
                    c = x.coords[pos]
                    coords_ext.setdefault((round(c[0], 7), round(c[1], 7)), []).append(k)
        n = 0
        for key, prop in coords_ext.items():
            if len(prop) != 1:
                continue
            k = prop[0]
            p = Point(key)
            meilleur, dist = None, tol
            for c in tree.query(p.buffer(tol)):
                if c == k:
                    continue
                q = nearest_points(p, geoms[c])[1]
                d = p.distance(q)
                if 0 < d <= dist:
                    meilleur, dist = q, d
            if meilleur is not None:
                x = geoms[k]
                cs = list(x.coords)
                pos = 0 if (round(cs[0][0], 7), round(cs[0][1], 7)) == key else -1
                cs[pos] = (meilleur.x, meilleur.y)
                geoms[k] = LineString(cs)
                n += 1
        if n:
            for k, idx in enumerate(ok):
                g.at[idx, nom] = geoms[k]
        enreg("Accrochage des extrémités libres", n, f"tolérance {tol_dangle_m} m")

    # suppressions (en dernier, pour pouvoir tracer les rejets)
    if supprimer_vides:
        vide = g.geometry.isna() | g.geometry.is_empty
        if vide.any():
            r = g[vide].copy(); r["__motif__"] = "Géométrie absente ou vide"; rejets.append(r)
        g = g[~vide]
        enreg("Suppression des géométries absentes ou vides", int(vide.sum()))

    if micro and dim in (1, 2) and len(g):
        m = _metres(g)
        mauvais = (m.area <= seuil_aire_m2) if dim == 2 else (m.length <= seuil_longueur_m)
        if mauvais.any():
            r = g[mauvais].copy(); r["__motif__"] = "Micro-géométrie"; rejets.append(r)
        g = g[~mauvais]
        enreg("Suppression des micro-géométries", int(mauvais.sum()),
              f"surface ≤ {seuil_aire_m2} m²" if dim == 2 else f"longueur ≤ {seuil_longueur_m} m")

    rej = pd.concat(rejets) if rejets else None
    return g.reset_index(drop=True), journal, rej
