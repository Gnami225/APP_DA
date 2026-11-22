"""« À faire aujourd'hui » : priorités de l'acheteur (DA en retard de traitement, BC à faire valider, livraisons
en retard ou partielles, prospects à qualifier)."""
from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from logic import as_text, fmt_date, fmt_montant, to_date, to_num


def _jours(v) -> int | None:
    d = to_date(v)
    return (date.today() - d).days if d else None


def a_faire(ctx) -> dict[str, pd.DataFrame]:
    from achats import _suivi_bc, da_ouvertes, params
    p = params(ctx)
    d_da = to_num(p.get("Délai traitement DA (jours)")) or 3
    d_liv = to_num(p.get("Délai livraison attendu (jours)")) or 15
    r = ctx.refs()["chantiers"]
    out = {}
    da = da_ouvertes(ctx)
    if len(da):
        t = pd.DataFrame({
            "N° DA": da["N° DA"].map(as_text), "Chantier": da["Chantier"].map(lambda c: r.get(as_text(c), as_text(c))),
            "Demandeur": da["Demandeur"].map(as_text), "Priorité": da["Priorité"].map(as_text),
            "Validée le": da["Date validation"].map(fmt_date),
            "Attente (j)": [(_jours(v) if as_text(v) else _jours(c)) for v, c in zip(da["Date validation"], da["Créé le"])],
            "Besoin le": da["Date besoin"].map(fmt_date),
            "Lignes à commander": da["À commander"]})
        t["Rang"] = t["Priorité"].map({"Critique": 0, "Urgente": 1}).fillna(2)
        t = t.loc[(t["Attente (j)"].fillna(0) >= d_da) | (t["Rang"] < 2)]
        out["DA à traiter"] = t.sort_values(["Rang", "Attente (j)"], ascending=[True, False]).drop(columns="Rang")
    bc, bl = _suivi_bc(ctx)
    if len(bc):
        e = bc.loc[bc["Statut"].map(as_text) == "Émis"]
        out["BC en attente de validation"] = pd.DataFrame({
            "N° BC": e["N° BC"].map(as_text), "Fournisseur": e["Fournisseur"].map(as_text),
            "Émis le": e["Date BC"].map(fmt_date), "Attente (j)": e["Âge (j)"],
            "Total TTC (F)": e["Total TTC"].map(fmt_montant)}).sort_values("Attente (j)", ascending=False)
        v = bc.loc[(bc["Statut"].map(as_text) == "Validé") & (bc["Livraison"] != "Livré")]
        v = v.loc[(v["Âge (j)"].fillna(0) >= d_liv) | (v["Livraison"] == "Partiel")]
        out["Livraisons à relancer"] = pd.DataFrame({
            "_id": v["_id"], "N° BC": v["N° BC"].map(as_text), "Fournisseur": v["Fournisseur"].map(as_text),
            "Chantier": v["Chantier"].map(lambda c: r.get(as_text(c), as_text(c))),
            "Date BC": v["Date BC"].map(fmt_date), "Âge (j)": v["Âge (j)"], "Livraison": v["Livraison"],
            "Reliquat HT (F)": v["Reliquat HT"].map(fmt_montant)}).sort_values("Âge (j)", ascending=False)
    fo = ctx.load("FOURNISSEURS")
    avec_bc = {" ".join(as_text(x).upper().split()) for x in ctx.load("BC")["Fournisseur"]}
    pr = fo.loc[(fo["Statut"].map(as_text) == "Prospect")
                & ~fo["Nom"].map(lambda x: " ".join(as_text(x).upper().split())).isin(avec_bc)]
    if len(pr):
        out["Prospects à qualifier"] = pd.DataFrame({
            "Fournisseur": pr["Nom"].map(as_text), "Pays": pr["Pays"].map(as_text),
            "Téléphone": pr["Téléphone"].map(as_text), "Email": pr["Email"].map(as_text),
            "Site": pr["Site web"].map(as_text), "Ajouté le": pr["Ajouté le"].map(fmt_date)})
    return out


def vue_a_faire(ctx):
    from achats import _relance, _suivi_bc
    t = a_faire(ctx)
    k = st.columns(4)
    for c, nom in zip(k, ["DA à traiter", "BC en attente de validation", "Livraisons à relancer",
                          "Prospects à qualifier"]):
        c.metric(nom, len(t.get(nom, [])))
    if not any(len(x) for x in t.values()):
        st.success("Rien en retard. 👍")
        return
    for nom, df in t.items():
        if not len(df):
            continue
        st.markdown(f"##### {nom}")
        st.dataframe(df.drop(columns=["_id"], errors="ignore"), hide_index=True, width="stretch",
                     column_config={"Site": st.column_config.LinkColumn()})
        if nom == "Livraisons à relancer":
            lab = dict(zip(df["_id"], df["N° BC"] + " · " + df["Fournisseur"]))
            sel = st.selectbox("Relancer un fournisseur", list(lab), format_func=lab.get, index=None,
                               key="af-rel", placeholder="Choisir le BC à relancer")
            if sel:
                bc, bl = _suivi_bc(ctx)
                l = bl.loc[bl["ID_BC"].map(as_text) == sel]
                g = pd.DataFrame({"Désignation": l["Désignation"].map(as_text), "Référence": l["Référence"].map(as_text),
                                  "Unité": l["Unité"].map(as_text), "Commandé": l["Cmd"], "Déjà livré": l["Liv"],
                                  "Reste": l["Reste"]})
                _relance(ctx, bc.loc[bc["_id"] == sel].iloc[0], g)
    st.caption("Seuils réglables dans Administration > Paramètres : « Délai traitement DA (jours) » et "
               "« Délai livraison attendu (jours) ».")
