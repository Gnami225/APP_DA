"""Statistiques : stocks et consommations des magasins (mouvements de l'application + fiches importées, y compris
l'historique), et couverture des données d'achat (période réellement couverte par chaque source, lignes écartées).
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import streamlit as st

import graphs
from logic import as_text, fmt_montant, to_date, to_num

CARBURANTS = {"GASOIL", "GAZOIL", "GASOLE", "CARBURANT", "ESSENCE", "SUPER", "PETROLE", "HUILE", "LUBRIFIANT",
              "GRAISSE", "ADBLUE"}


def _est_carburant(designation, famille) -> bool:
    """Carburant / lubrifiant d'après la famille du catalogue ou le premier mot de la désignation."""
    f = as_text(famille).upper()
    if any(x in f for x in ("CARBURANT", "LUBRIFIANT", "HUILE")):
        return True
    mots = as_text(designation).upper().replace("-", " ").split()
    return bool(mots) and (mots[0] in CARBURANTS or " ".join(mots[:2]) == "GAS OIL")


def _f(v) -> str:
    return f"{fmt_montant(v)} F"


# =========================================================================== couverture des données d'achat
def ids_reprise(ctx) -> set[str]:
    """ID_BC des BC repris (émis avant l'application, importés par « Reprise de BC antérieurs »)."""
    bc = ctx.load("BC")
    if not len(bc):
        return set()
    m = bc["Acheteur"].map(as_text).str.startswith("Reprise") | bc["Observations"].map(as_text).str.contains(
        "Reprise des BC antérieurs", regex=False)
    return set(bc.loc[m, "ID_BC"].map(as_text))


def couverture(ctx) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(bilan par source, lignes écartées des statistiques avec leur motif)."""
    h = ctx.load("PRIX_HISTORIQUE")
    a = pd.DataFrame({"Source": "Historique importé", "date": h["Date"].map(to_date), "bc": h["N° BC"].map(as_text),
                      "Fournisseur": h["Fournisseur"].map(as_text), "Désignation": h["Désignation"].map(as_text),
                      "q": h["Quantité"].map(to_num), "pu": h["PU net HT"].map(to_num)})
    bl = ctx.load("BC_LIGNES")
    rep = ids_reprise(ctx)
    from schema import LIGNE_BC_RETIREE
    bl = bl.loc[bl["Statut livraison"].map(as_text) != LIGNE_BC_RETIREE]
    bcs = ctx.load("BC")
    f_rg = ({as_text(i): 1 - to_num(r) / 100 for i, r in zip(bcs["ID_BC"], bcs["Remise globale %"])}
            if "Remise globale %" in bcs.columns else {})
    b = pd.DataFrame({"Source": bl["ID_BC"].map(lambda i: "Reprise BC antérieurs" if as_text(i) in rep
                                                else "Application"),
                      "date": bl["Date BC"].map(to_date), "bc": bl["N° BC"].map(as_text),
                      "Fournisseur": bl["Fournisseur"].map(as_text), "Désignation": bl["Désignation"].map(as_text),
                      "q": bl["Quantité"].map(to_num),
                      "pu": [to_num(p) * (1 - to_num(r) / 100) * f_rg.get(as_text(i), 1)
                             for p, r, i in zip(bl["Prix unitaire HT"], bl["Remise %"], bl["ID_BC"])]})
    from logic import num_bc
    a = a.loc[~a["bc"].map(num_bc).isin(set(b["bc"].map(num_bc)))]  # BC repris dans l'application : comptés une fois
    d = pd.concat([a, b], ignore_index=True)
    d["montant"] = d["q"] * d["pu"]
    d["Motif"] = np.where(d["date"].isna(), "sans date", np.where(d["montant"] <= 0, "quantité ou prix nul", ""))
    ok = d.loc[d["Motif"] == ""]
    rows = []
    for s in ["Historique importé", "Reprise BC antérieurs", "Application"]:
        x, tout = ok.loc[ok["Source"] == s], d.loc[d["Source"] == s]
        if not len(tout):
            continue
        dts = pd.to_datetime(x["date"])
        mois = sorted(dts.dt.strftime("%Y-%m").unique()) if len(x) else []
        trous = []
        if len(mois) > 1:
            tous = pd.period_range(mois[0], mois[-1], freq="M").strftime("%Y-%m")
            trous = [m for m in tous if m not in set(mois)]
        rows.append({"Source": s, "Du": dts.min().date() if len(x) else None, "Au": dts.max().date() if len(x) else None,
                     "BC": x["bc"].nunique(), "Lignes comptées": len(x), "Montant HT": x["montant"].sum(),
                     "Lignes écartées": len(tout) - len(x),
                     "Mois sans achat": ", ".join(f"{m[5:]}/{m[2:4]}" for m in trous[:12])
                     + (" …" if len(trous) > 12 else "")})
    return pd.DataFrame(rows), d.loc[d["Motif"] != "", ["Source", "date", "bc", "Fournisseur", "Désignation", "q", "pu",
                                                        "Motif"]]


def panneau_couverture(ctx):
    t, ecartees = couverture(ctx)
    if not len(t):
        return
    tot = t["Lignes écartées"].sum()
    du, au = min(x for x in t["Du"] if x), max(x for x in t["Au"] if x)
    with st.expander(f"🧾 Couverture des données d'achat : du {du:%d/%m/%Y} au {au:%d/%m/%Y}"
                     + (f" · ⚠️ {tot} ligne(s) non comptée(s)" if tot else " · toutes les lignes sont comptées")):
        st.dataframe(t.assign(**{"Montant HT": t["Montant HT"].map(fmt_montant),
                                 "Du": t["Du"].map(lambda x: f"{x:%d/%m/%Y}" if x else ""),
                                 "Au": t["Au"].map(lambda x: f"{x:%d/%m/%Y}" if x else "")}),
                     hide_index=True, width="stretch")
        # trou entre les sources (ex. fin de l'historique -> premiers BC de l'application)
        bornes = t.dropna(subset=["Du", "Au"]).sort_values("Du")
        for (_, p), (_, n) in zip(bornes.iloc[:-1].iterrows(), bornes.iloc[1:].iterrows()):
            if (n["Du"] - p["Au"]).days > 7:
                st.warning(f"Aucun achat enregistré entre le {p['Au']:%d/%m/%Y} ({p['Source']}) et le "
                           f"{n['Du']:%d/%m/%Y} ({n['Source']}) : BC manquants ? Importez-les par « Reprise de BC "
                           "antérieurs » (Administration).")
        st.caption("Les statistiques comptent les BC validés et émis (dépense engagée). Les lignes sans date ou sans "
                   "prix ne peuvent pas être comptées ; les lignes aberrantes (prix ou quantité mal saisis) sont "
                   "écartées par défaut — détail dans « Contrôles (audit) ».")
        if len(ecartees):
            st.markdown(f"**Lignes non comptées ({len(ecartees)})** — à corriger dans le Sheet (PRIX_HISTORIQUE) ou "
                        "dans le BC :")
            st.dataframe(ecartees.rename(columns={"date": "Date", "bc": "N° BC", "q": "Quantité", "pu": "PU HT"}),
                         hide_index=True, width="stretch", height=min(400, 38 + 35 * len(ecartees)))


# =========================================================================== stocks et consommations
def _base_stock(ctx) -> pd.DataFrame:
    import magasin
    m = magasin.mouvements(ctx).copy()
    if not len(m):
        return m
    noms = ctx.refs()["chantiers"]
    art = ctx.load("ARTICLES")
    fam = dict(zip(art["Code Article"].map(as_text), art["Famille"].map(as_text)))
    m = m.loc[m["d"].notna()].copy()
    m["Magasin_nom"] = m["Magasin"].map(lambda c: noms.get(c, c))
    m["mois"] = m["d"].dt.strftime("%Y-%m")
    m["Article"] = m["Désignation"].map(as_text) + m["Référence"].map(lambda r: f" · {as_text(r)}" if as_text(r) else "")
    m["Famille"] = m["Code Article"].map(lambda c: fam.get(as_text(c), "") or "Hors catalogue")
    # engin : code parc, sinon l'engin / bénéficiaire tel qu'écrit (ex. « Porte char (AA-266-CK) »)
    m["Engin"] = [as_text(c) or as_text(b)[:40] or "(non renseigné)" for c, b in zip(m["Code_Parc"], m["Bénéficiaire"])]
    m["Qté"] = m["Quantité"].map(lambda x: abs(to_num(x)))
    m["PU"] = m["Valeur unitaire"].map(to_num)
    m["Valeur"] = m["Qté"] * m["PU"]
    m["Source_m"] = np.where(m["hist"], "Historique (fiches)", np.where(m["Source"].map(as_text) != "",
                                                                         m["Source"].map(as_text), "Application"))
    return m


def vue_stocks(ctx, achats: pd.DataFrame | None = None):
    import magasin
    m = _base_stock(ctx)
    if not len(m):
        st.info("Aucun mouvement de stock : les magasins commencent par l'inventaire, les réceptions ou l'import des "
                "fiches (onglet Magasin › 📄 Fiches (IA)).")
        return
    mois = sorted(m["mois"].unique())
    c1, c2 = st.columns([3, 2])
    if len(mois) > 1:
        deb, fin = c1.select_slider("Période (mois)", options=mois, value=(mois[0], mois[-1]), key="sts-per",
                                    format_func=lambda x: f"{x[5:]}/{x[:4]}")
    else:
        deb = fin = mois[0]
    mags = sorted(m["Magasin_nom"].unique())
    sel_m = c2.multiselect("Magasins", mags, key="sts-mag", placeholder="Tous")
    c3, c4, c5 = st.columns(3)
    k = (m["mois"] >= deb) & (m["mois"] <= fin) & (m["Magasin_nom"].isin(sel_m) if sel_m else True)
    fams = sorted(m.loc[k, "Famille"].unique())
    sel_f = c3.multiselect("Familles", fams, key="sts-fam", placeholder="Toutes")
    if sel_f:
        k &= m["Famille"].isin(sel_f)
    arts = sorted(m.loc[k, "Article"].unique())
    sel_a = c4.multiselect("Articles", arts, key="sts-art", placeholder="Tous")
    if sel_a:
        k &= m["Article"].isin(sel_a)
    engins = sorted(m.loc[k & (m["Type"].map(as_text) == "Sortie"), "Engin"].unique())
    sel_e = c5.multiselect("Engins", engins, key="sts-eng", placeholder="Tous")
    v = m.loc[k]
    so = v.loc[v["Type"].map(as_text) == "Sortie"]
    if sel_e:
        so = so.loc[so["Engin"].isin(sel_e)]
    nh = int(v["hist"].sum())
    st.caption(f"{deb[5:]}/{deb[:4]} → {fin[5:]}/{fin[:4]} · {len(v)} mouvement(s) dont {nh} historique(s) (fiches "
               "importées : comptées ici, sans effet sur le stock actuel)")

    # ---- indicateurs
    t = magasin.stock(ctx)
    if sel_m:
        noms = ctx.refs()["chantiers"]
        t = t.loc[t["Magasin"].map(lambda c: noms.get(c, c)).isin(sel_m)]
    t = t.copy()
    t["PU"] = [magasin._valeur_unitaire(ctx, c, d, r, u) for c, d, r, u in
               zip(t["Code Article"], t["Désignation"], t["Référence"], t["Unité"])] if len(t) else []
    t["Valeur"] = t["Stock"].clip(lower=0) * t["PU"] if len(t) else []
    n_mois = max(1, len([x for x in mois if deb <= x <= fin]))
    q = st.columns(4)
    q[0].metric("Consommation valorisée", _f(so["Valeur"].sum()), help="Sorties × prix moyen d'achat de l'article")
    q[1].metric("Consommation mensuelle moyenne", _f(so["Valeur"].sum() / n_mois))
    q[2].metric("Valeur du stock actuel", _f(t["Valeur"].sum() if len(t) else 0))
    q[3].metric("Engins / bénéficiaires servis", int(so.loc[so["Engin"] != "(non renseigné)", "Engin"].nunique()))
    sans_prix = so.loc[so["PU"] <= 0, "Article"].nunique()
    if sans_prix:
        st.caption(f"{sans_prix} article(s) consommé(s) sans prix d'achat connu : quantités comptées, valeur à 0.")

    if not len(so):
        st.info("Aucune sortie sur cette sélection.")
    else:
        # ---- consommation dans le temps
        a, b = st.columns(2)
        unite = a.segmented_control("Mesure", ["Valeur (F)", "Quantité"], default="Valeur (F)", key="sts-mes") \
            or "Valeur (F)"
        vent = b.segmented_control("Ventiler par", ["Magasin", "Famille", "Engin", "Article"], default="Magasin",
                                   key="sts-vent") or "Magasin"
        col = {"Magasin": "Magasin_nom", "Famille": "Famille", "Engin": "Engin", "Article": "Article"}[vent]
        val = "Valeur" if unite == "Valeur (F)" else "Qté"
        if val == "Qté" and so["Unité"].map(as_text).str.lower().nunique() > 1 and not sel_a:
            st.caption("Quantités d'unités différentes additionnées : choisissez un article (ex. GASOIL) pour une "
                       "lecture exacte.")
        g = so.groupby(["mois", col], as_index=False)[val].sum().rename(columns={col: vent, val: unite})
        graphs.empile_mois(g, "mois", vent, unite, f"Consommation par mois, par {vent.lower()}", "sts-g1",
                           unite="F CFA" if val == "Valeur" else "")
        c1, c2 = st.columns(2)
        with c1:
            e = so.groupby("Engin", as_index=False).agg(**{"Valeur (F)": ("Valeur", "sum"), "Sorties": ("Qté", "size"),
                                                           "Dernière": ("d", "max")})
            graphs.barres(e, "Engin", "Valeur (F)", "Consommation par engin", "sts-eng-g", unite="F CFA", top=15,
                          table=e.assign(Dernière=e["Dernière"].dt.strftime("%d/%m/%Y")))
        with c2:
            r = so.groupby("Article", as_index=False).agg(**{"Valeur (F)": ("Valeur", "sum"), "Quantité": ("Qté", "sum"),
                                                             "Unité": ("Unité", "last")})
            graphs.barres(r, "Article", "Valeur (F)", "Articles les plus consommés", "sts-art-g", unite="F CFA", top=15)

        # ---- carburant : litres par engin et par mois
        carb = so.loc[[_est_carburant(d, f) for d, f in zip(so["Désignation"], so["Famille"])]]
        if len(carb):
            st.markdown("##### ⛽ Carburant et lubrifiants")
            pv = carb.pivot_table(index=["Engin", "Article"], columns="mois", values="Qté", aggfunc="sum", fill_value=0)
            pv["Total"] = pv.sum(axis=1)
            pv = pv.sort_values("Total", ascending=False)
            tot = carb.groupby("Article").agg(q=("Qté", "sum"), u=("Unité", "last"))
            st.caption("Quantités sorties par engin, par produit et par mois — total : "
                       + " · ".join(f"{a} {r.q:,.0f} {as_text(r.u)}".replace(",", " ") for a, r in tot.iterrows()))
            st.dataframe(pv.reset_index(), hide_index=True, width="stretch",
                         column_config={c: st.column_config.NumberColumn(format="%.0f") for c in pv.columns})

        with st.expander("Détail par bénéficiaire / n° de bon"):
            d = so.assign(Bénéficiaire=so["Bénéficiaire"].map(as_text).replace("", "(non renseigné)"))
            st.dataframe(d.groupby(["Bénéficiaire"]).agg(Sorties=("Qté", "size"), **{"Valeur (F)": ("Valeur", "sum")})
                         .sort_values("Valeur (F)", ascending=False).round(0), width="stretch")

    # ---- achats vs consommation par chantier
    if achats is not None and len(achats):
        st.markdown("##### Achats et consommation par chantier")
        noms = ctx.refs()["chantiers"]
        ach = achats.loc[(achats["mois"] >= deb) & (achats["mois"] <= fin)]
        ga = ach.groupby("chantier")["montant"].sum()
        gs = so.groupby("Magasin_nom")["Valeur"].sum()
        stk = (t.assign(n=t["Magasin"].map(lambda c: noms.get(c, c))).groupby("n")["Valeur"].sum()
               if len(t) else pd.Series(dtype=float))
        ch = sorted(set(gs.index) | set(stk.index))
        tab = pd.DataFrame({"Chantier / magasin": ch, "Achats HT (période)": [ga.get(c, 0.0) for c in ch],
                            "Consommé (période)": [gs.get(c, 0.0) for c in ch],
                            "Stock actuel": [stk.get(c, 0.0) for c in ch]})
        tab["Consommé / acheté"] = np.where(tab["Achats HT (période)"] > 0,
                                            tab["Consommé (période)"] / tab["Achats HT (période)"], np.nan)
        st.dataframe(tab, hide_index=True, width="stretch", column_config={
            **{c: st.column_config.NumberColumn(format="%.0f") for c in ("Achats HT (période)", "Consommé (période)",
                                                                         "Stock actuel")},
            "Consommé / acheté": st.column_config.NumberColumn(format="percent")})
        st.caption("Achats : BC du chantier (tous articles, y compris ce qui ne passe pas par le magasin). "
                   "Consommé : sorties de son magasin, au prix moyen d'achat.")

    # ---- dormants, ruptures, écarts d'inventaire
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("##### Articles dormants (aucune sortie depuis 90 jours)")
        if len(t):
            d90 = pd.Timestamp(date.today() - timedelta(days=90))
            mm = m.loc[m["Type"].map(as_text) == "Sortie"]
            der = mm.groupby(["Magasin", "cle"])["d"].max()
            vieux = np.array([pd.isna(der.get((a, b), pd.NaT)) or der.get((a, b)) < d90
                              for a, b in zip(t["Magasin"], t["cle"])], dtype=bool)
            dorm = t.loc[(t["Stock"] > 0).to_numpy() & vieux]
            noms = ctx.refs()["chantiers"]
            st.dataframe(dorm.assign(Magasin=dorm["Magasin"].map(lambda c: noms.get(c, c)))
                         [["Magasin", "Désignation", "Référence", "Stock", "Unité", "Valeur"]]
                         .sort_values("Valeur", ascending=False), hide_index=True, width="stretch",
                         column_config={"Valeur": st.column_config.NumberColumn("Valeur (F)", format="%.0f")})
            st.caption(f"Valeur immobilisée : {_f(dorm['Valeur'].sum())}")
    with c2:
        st.markdown("##### Écarts d'inventaire")
        aj = v.loc[v["Type"].map(as_text) == "Ajustement inventaire"].copy()
        if len(aj):
            aj["Écart (F)"] = aj["Quantité"].map(to_num) * aj["PU"]
            st.dataframe(aj.groupby("Magasin_nom").agg(Ajustements=("Qté", "size"),
                                                        Pertes=("Écart (F)", lambda x: x[x < 0].sum()),
                                                        Surplus=("Écart (F)", lambda x: x[x > 0].sum()))
                         .round(0), width="stretch")
        else:
            st.caption("Aucun ajustement d'inventaire sur la période.")
