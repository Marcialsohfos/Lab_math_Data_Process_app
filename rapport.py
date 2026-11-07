"""
rapport.py : rapport de traitement (HTML autonome + classeur Excel) pour tout type de base.
"""
import html
import io
import json
from datetime import datetime

import pandas as pd

CSS = """
body{font-family:Arial,Helvetica,sans-serif;max-width:1000px;margin:30px auto;padding:0 20px;color:#222;line-height:1.45}
h1{color:#0b3d6b;border-bottom:3px solid #0b3d6b;padding-bottom:8px}
h2{color:#0b3d6b;margin-top:34px;border-bottom:1px solid #ccc;padding-bottom:4px}
table{border-collapse:collapse;width:100%;margin:10px 0;font-size:13px}
th{background:#d9e2f3;text-align:left}
th,td{border:1px solid #bbb;padding:5px 8px;vertical-align:top}
tr:nth-child(even) td{background:#f7f9fc}
.meta td:first-child{width:260px;font-weight:bold;background:#eef2f8}
.ok{color:#1a7f37;font-weight:bold}.warn{color:#b35900;font-weight:bold}
.small{font-size:12px;color:#555}
@media print{body{margin:0}}
"""


def _table(df, index=False):
    if df is None or len(df) == 0:
        return "<p class='small'>Aucune donnée.</p>"
    return df.to_html(index=index, escape=True, border=0, na_rep="")


def _dict_table(d):
    if not d:
        return "<p class='small'>Aucune donnée.</p>"
    lignes = "".join(f"<tr><td>{html.escape(str(k))}</td><td>{html.escape(str(v))}</td></tr>" for k, v in d.items())
    return f"<table class='meta'>{lignes}</table>"


def _comparer(avant, apres):
    cles = list(avant.keys())
    return pd.DataFrame({"indicateur": cles, "avant": [str(avant[k]) for k in cles], "après": [str(apres.get(k, "")) for k in cles]})


def construire_html(meta, journal, qualite_avant, qualite_apres, profil_avant, profil_apres,
                    geo=None, rejets_motifs=None, parametres=None):
    j = pd.DataFrame(journal)
    if len(j):
        j = j.rename(columns={"heure": "Heure", "etape": "Étape", "detail": "Détail", "lignes_avant": "Lignes avant",
                              "lignes_apres": "Lignes après", "colonnes_avant": "Colonnes avant", "colonnes_apres": "Colonnes après",
                              "cellules_modifiees": "Cellules modifiées"})
    h = [f"<!DOCTYPE html><html lang='fr'><head><meta charset='utf-8'><title>Rapport de traitement</title><style>{CSS}</style></head><body>"]
    h.append("<h1>Rapport de traitement des données</h1>")
    h.append("<h2>1. Identification</h2>" + _dict_table(meta))

    h.append("<h2>2. Résultat en bref</h2>")
    dl = qualite_apres["lignes"] - qualite_avant["lignes"]
    h.append(f"<p>La base comptait <b>{qualite_avant['lignes']}</b> lignes et <b>{qualite_avant['colonnes']}</b> colonnes. "
             f"Elle en compte maintenant <b>{qualite_apres['lignes']}</b> et <b>{qualite_apres['colonnes']}</b> "
             f"({'aucune ligne retirée' if dl == 0 else f'{abs(dl)} lignes retirées'}). "
             f"La complétude passe de <b>{qualite_avant['complétude (%)']} %</b> à <b>{qualite_apres['complétude (%)']} %</b>. "
             f"{len(journal)} étape(s) de traitement ont été appliquées.</p>")
    h.append(_table(_comparer(qualite_avant, qualite_apres)))

    h.append("<h2>3. Journal des étapes</h2>")
    h.append(_table(j) if len(j) else "<p class='small'>Aucune étape appliquée.</p>")

    if rejets_motifs is not None and len(rejets_motifs):
        h.append("<h2>4. Lignes retirées (rejets)</h2><p>Les lignes retirées sont conservées dans le fichier des rejets, avec leur motif.</p>")
        h.append(_table(rejets_motifs))

    if geo:
        h.append("<h2>5. Contrôle géométrique et topologique</h2>")
        for titre, cle in (("Avant traitement", "avant"), ("Après traitement", "apres")):
            if geo.get(cle):
                h.append(f"<h3>{titre}</h3>" + _dict_table(geo[cle]))
        if geo.get("corrections"):
            h.append("<h3>Corrections topologiques appliquées</h3>" + _table(pd.DataFrame(geo["corrections"])))

    h.append("<h2>6. Valeurs manquantes par colonne</h2><h3>Avant</h3>")
    h.append(_table(profil_avant.sort_values("% manquants", ascending=False).head(30)))
    h.append("<h3>Après</h3>")
    h.append(_table(profil_apres.sort_values("% manquants", ascending=False).head(30)))
    h.append("<p class='small'>Les 30 colonnes les plus incomplètes sont affichées.</p>")

    if parametres:
        h.append("<h2>7. Paramètres utilisés (reproductibilité)</h2>")
        h.append("<pre style='background:#f4f4f4;padding:10px;font-size:12px;white-space:pre-wrap'>"
                 + html.escape(json.dumps(parametres, ensure_ascii=False, indent=2, default=str)) + "</pre>")
    h.append(f"<p class='small'>Rapport généré le {datetime.now():%d/%m/%Y à %H:%M} par Lab_Math Smart Deduplicator Pro.</p>")
    h.append("</body></html>")
    return "".join(h)


def construire_excel(meta, journal, qualite_avant, qualite_apres, profil_avant, profil_apres,
                     geo=None, rejets_motifs=None, parametres=None):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as w:
        pd.DataFrame(list(meta.items()), columns=["Élément", "Valeur"]).to_excel(w, sheet_name="Identification", index=False)
        _comparer(qualite_avant, qualite_apres).to_excel(w, sheet_name="Qualité avant-après", index=False)
        (pd.DataFrame(journal) if journal else pd.DataFrame(columns=["etape"])).to_excel(w, sheet_name="Journal", index=False)
        profil_avant.to_excel(w, sheet_name="Colonnes avant", index=False)
        profil_apres.to_excel(w, sheet_name="Colonnes après", index=False)
        if rejets_motifs is not None and len(rejets_motifs):
            rejets_motifs.to_excel(w, sheet_name="Rejets par motif", index=False)
        if geo:
            lignes = []
            for cle in ("avant", "apres"):
                for k, v in (geo.get(cle) or {}).items():
                    lignes.append({"moment": cle, "indicateur": k, "valeur": str(v)})
            pd.DataFrame(lignes).to_excel(w, sheet_name="Géométrie", index=False)
            if geo.get("corrections"):
                pd.DataFrame(geo["corrections"]).to_excel(w, sheet_name="Corrections topologie", index=False)
        if parametres:
            pd.DataFrame([{"paramètre": k, "valeur": json.dumps(v, ensure_ascii=False, default=str)} for k, v in parametres.items()]
                         ).to_excel(w, sheet_name="Paramètres", index=False)
    return buf.getvalue()
