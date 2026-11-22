"""Assistant d'analyse (Claude) : questions en français sur les achats et les DA.

Les chiffres ne sont jamais calculés par l'IA : elle appelle des fonctions pandas de l'application (outils) qui
renvoient des tableaux exacts, puis elle commente. Chaque réponse affiche les tableaux utilisés.
"""
from __future__ import annotations

import json

import pandas as pd
import streamlit as st

import ia
from logic import as_text, to_date

DIM_ACHATS = {"mois": "mois", "trimestre": "trimestre", "annee": "annee", "semaine": "semaine",
              "chantier": "chantier", "famille": "famille", "fournisseur": "four", "article": "article",
              "bc": "bc", "source": "source"}
NOMS = {"four": "Fournisseur", "chantier": "Chantier", "famille": "Famille", "article": "Article", "bc": "N° BC",
        "mois": "Mois", "trimestre": "Trimestre", "annee": "Année", "semaine": "Semaine", "source": "Source"}
_FILTRES = {"type": "object", "description": "Filtres facultatifs (texte contenu, insensible à la casse)",
            "properties": {"chantier": {"type": "string"}, "famille": {"type": "string"},
                           "fournisseur": {"type": "string"}, "article": {"type": "string"},
                           "date_debut": {"type": "string", "description": "AAAA-MM-JJ"},
                           "date_fin": {"type": "string", "description": "AAAA-MM-JJ"}}}


def _prep(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d["trimestre"] = d["date"].dt.to_period("Q").astype(str)
    d["annee"] = d["date"].dt.year.astype(str)
    d["semaine"] = d["date"].dt.strftime("%G-S%V")
    return d


def _filtrer(d: pd.DataFrame, f: dict | None) -> pd.DataFrame:
    f = f or {}
    for k, col in (("chantier", "chantier"), ("famille", "famille"), ("fournisseur", "four"), ("article", "article")):
        v = as_text(f.get(k))
        if v:
            d = d.loc[d[col].str.contains(v, case=False, regex=False, na=False)]
    if as_text(f.get("date_debut")):
        d = d.loc[d["date"] >= pd.Timestamp(f["date_debut"])]
    if as_text(f.get("date_fin")):
        d = d.loc[d["date"] <= pd.Timestamp(f["date_fin"])]
    return d


def _sortie(t: pd.DataFrame, entete: str = "") -> tuple[str, pd.DataFrame]:
    t = t.round(2)
    return (entete + t.to_csv(index=False, sep=";"))[:50000], t


def outils_achats(df: pd.DataFrame) -> dict:
    d0 = _prep(df)

    def analyser_achats(mesures=None, grouper_par=None, filtres=None, top=30, trier_par=None, croissant=False):
        d = _filtrer(d0, filtres)
        if d.empty:
            return "Aucune ligne d'achat pour ces filtres.", None
        cols = [DIM_ACHATS[g] for g in (grouper_par or []) if g in DIM_ACHATS]
        agg = {"Dépense HT (F)": ("montant", "sum"), "Lignes": ("montant", "size"), "Nb BC": ("bc", "nunique"),
               "Quantité": ("q", "sum"), "Nb fournisseurs": ("four", "nunique"),
               "PU min": ("pu", "min"), "PU max": ("pu", "max")}
        if not cols:
            t = pd.DataFrame([{k: getattr(d[c], f)() for k, (c, f) in agg.items()}])
        else:
            t = d.groupby(cols, as_index=False).agg(**agg).rename(columns=NOMS)
        t["PU moyen pondéré"] = t["Dépense HT (F)"] / t["Quantité"].where(t["Quantité"] > 0)
        t["Part de la dépense %"] = 100 * t["Dépense HT (F)"] / d["montant"].sum()
        if mesures:
            garder = [NOMS[c] for c in cols] + [m for m in mesures if m in t.columns]
            t = t[[c for c in garder if c in t.columns] or t.columns]
        tri = trier_par if trier_par in t.columns else ("Dépense HT (F)" if "Dépense HT (F)" in t.columns else None)
        if cols and any(c in ("mois", "trimestre", "annee", "semaine") for c in cols) and not trier_par:
            t = t.sort_values([NOMS[c] for c in cols])
        elif tri:
            t = t.sort_values(tri, ascending=bool(croissant))
        n = len(t)
        t = t.head(int(top or 30))
        return _sortie(t, f"{n} groupe(s), {len(t)} affiché(s). Période des lignes : {d['date'].min():%d/%m/%Y} – "
                          f"{d['date'].max():%d/%m/%Y}. Dépense totale filtrée : {d['montant'].sum():,.0f} F\n")

    def lister_valeurs(dimension, contient=""):
        col = DIM_ACHATS.get(dimension)
        if not col:
            return f"Dimension inconnue : {dimension}", None
        d = d0 if not contient else d0.loc[d0[col].astype(str).str.contains(contient, case=False, regex=False)]
        t = d.groupby(col, as_index=False)["montant"].sum().sort_values("montant", ascending=False).head(80)
        return _sortie(t.rename(columns={col: NOMS.get(col, col), "montant": "Dépense HT (F)"}))

    def historique_prix(article, fournisseur=""):
        d = _filtrer(d0, {"article": article, "fournisseur": fournisseur})
        if d.empty:
            return "Aucun achat trouvé pour cet article (essayez lister_valeurs).", None
        t = d.sort_values("date")[["date", "bc", "four", "chantier", "article", "unite", "q", "pu", "montant"]].tail(60)
        t["date"] = t["date"].dt.strftime("%d/%m/%Y")
        return _sortie(t.rename(columns={"date": "Date", "bc": "N° BC", "four": "Fournisseur", "chantier": "Chantier",
                                         "article": "Article", "unite": "Unité", "q": "Quantité",
                                         "pu": "PU net HT", "montant": "Montant HT"}))

    groupes = {"type": "array", "items": {"type": "string", "enum": list(DIM_ACHATS)}}
    return {
        "analyser_achats": ({
            "name": "analyser_achats",
            "description": "Agrège les lignes de bons de commande (historique + application, HT, lignes aberrantes "
                           "exclues) : dépense, nb BC, quantités, prix unitaires, part %. Grouper par 0 à 3 dimensions.",
            "input_schema": {"type": "object", "properties": {
                "grouper_par": groupes, "filtres": _FILTRES,
                "mesures": {"type": "array", "items": {"type": "string", "enum": [
                    "Dépense HT (F)", "Lignes", "Nb BC", "Quantité", "Nb fournisseurs", "PU min", "PU max",
                    "PU moyen pondéré", "Part de la dépense %"]}},
                "trier_par": {"type": "string"}, "croissant": {"type": "boolean"},
                "top": {"type": "integer", "description": "nombre de lignes renvoyées (défaut 30)"}}}},
            analyser_achats),
        "lister_valeurs": ({
            "name": "lister_valeurs",
            "description": "Valeurs existantes d'une dimension (orthographe exacte des chantiers, fournisseurs, "
                           "familles, articles), avec leur dépense. À utiliser avant de filtrer si le nom est incertain.",
            "input_schema": {"type": "object", "properties": {
                "dimension": {"type": "string", "enum": ["chantier", "famille", "fournisseur", "article"]},
                "contient": {"type": "string"}}, "required": ["dimension"]}}, lister_valeurs),
        "historique_prix": ({
            "name": "historique_prix",
            "description": "Détail chronologique des achats d'un article (prix unitaires, fournisseurs, BC).",
            "input_schema": {"type": "object", "properties": {
                "article": {"type": "string", "description": "texte contenu dans la désignation · référence"},
                "fournisseur": {"type": "string"}}, "required": ["article"]}}, historique_prix),
    }


def outils_da(ctx, refs: dict) -> dict:
    da, lg = ctx.load("DA"), ctx.load("HISTORIQUE_ARTICLES")
    d0 = pd.DataFrame({
        "N° DA": da["N° DA"].map(as_text), "date": pd.to_datetime(da["Date DA"].map(to_date)),
        "statut": da["Statut"].map(as_text),
        "chantier": da["Chantier"].map(lambda c: refs["chantiers"].get(as_text(c), as_text(c))),
        "service": da["Service Demandeur"].map(lambda c: refs["services"].get(as_text(c), as_text(c))),
        "demandeur": da["Demandeur"].map(as_text), "priorite": da["Priorité"].map(as_text),
        "ID_DA": da["ID_DA"].map(as_text)})
    nb = lg.groupby(lg["ID_DA"].map(as_text)).size()
    d0["lignes"] = d0["ID_DA"].map(lambda i: int(nb.get(i, 0)))
    d0["mois"] = d0["date"].dt.strftime("%Y-%m")
    dims = ["statut", "chantier", "service", "demandeur", "priorite", "mois"]

    def analyser_da(grouper_par=None, filtres=None, top=40):
        d = d0
        for k, v in (filtres or {}).items():
            if k in dims and as_text(v):
                d = d.loc[d[k].str.contains(as_text(v), case=False, regex=False, na=False)]
            elif k == "date_debut" and v:
                d = d.loc[d["date"] >= pd.Timestamp(v)]
            elif k == "date_fin" and v:
                d = d.loc[d["date"] <= pd.Timestamp(v)]
        if d.empty:
            return "Aucune DA pour ces filtres.", None
        g = [x for x in (grouper_par or []) if x in dims]
        t = (d.groupby(g, as_index=False).agg(**{"Nb DA": ("N° DA", "size"), "Nb lignes": ("lignes", "sum")})
             if g else pd.DataFrame([{"Nb DA": len(d), "Nb lignes": int(d["lignes"].sum())}]))
        t = t.sort_values(g if "mois" in g else "Nb DA", ascending="mois" in g).head(int(top or 40))
        return _sortie(t)

    return {"analyser_da": ({
        "name": "analyser_da",
        "description": "Compte les demandes d'achat (DA) et leurs lignes, groupées par statut, chantier, service, "
                       "demandeur, priorité ou mois. Statuts : Émise, Validée Resp. service, Validée DG, Rejetée, "
                       "En cours d'achat, Commandée, Livrée, Clôturée, Annulée…",
        "input_schema": {"type": "object", "properties": {
            "grouper_par": {"type": "array", "items": {"type": "string", "enum": dims}},
            "filtres": {"type": "object", "properties": {k: {"type": "string"} for k in
                                                         dims + ["date_debut", "date_fin"]}},
            "top": {"type": "integer"}}}}, analyser_da)}


SYSTEME = (ia.CONTEXTE_PWC + " Tu es l'analyste achats de la direction. Pour CHAQUE chiffre, appelle les outils : "
           "n'invente ni ne calcules de tête aucun montant. Si un nom est incertain, appelle d'abord lister_valeurs. "
           "Réponse : synthèse en 2-3 phrases, puis les chiffres clés (montants arrondis, en F CFA, séparateur des "
           "milliers espace), puis si utile 1 à 3 recommandations concrètes. Pas de tableau markdown de plus de 12 "
           "lignes : l'application affiche déjà les tableaux complets sous ta réponse.")


def ui(ctx, df: pd.DataFrame | None, cle: str, perimetre: str):
    """Chat d'analyse. df = lignes d'achat (stats.base filtrée) ; None = seulement les DA."""
    if not ia.disponible():
        st.info("Assistant IA non configuré : ajoutez la section [ia] avec la clé API dans les secrets.")
        return
    outils = dict(outils_da(ctx, ctx.refs()))
    if df is not None and len(df):
        outils.update(outils_achats(df))
    hist = st.session_state.setdefault(cle, [])
    st.caption(f"Posez vos questions en français (ou dictez-les). Périmètre : {perimetre}. Les chiffres viennent des "
               "calculs de l'application ; les tableaux utilisés sont affichés sous chaque réponse.")
    exemples = ["Quels sont les 10 fournisseurs les plus importants et leur part ?",
                "Comment évolue la dépense par trimestre et par famille ?",
                "Quels articles ont les plus gros écarts de prix entre fournisseurs ?",
                "Combien de DA sont en attente de validation, par chantier ?"]
    choix = None
    cols = st.columns(len(exemples))
    for i, e in enumerate(exemples):
        if cols[i].button(e, key=f"{cle}-ex{i}", width="stretch"):
            choix = e
    for m in hist:
        with st.chat_message(m["role"]):
            st.markdown(m["texte"])
            for t in m.get("traces", []):
                if t["table"] is not None and len(t["table"]):
                    with st.expander(f"Données : {t['outil']} {json.dumps(t['parametres'], ensure_ascii=False)}"):
                        st.dataframe(t["table"], hide_index=True, width="stretch")
    q = st.chat_input("Votre question sur les achats, les prix, les fournisseurs, les DA…", key=f"{cle}-in") or choix
    c1, c2 = st.columns([1, 4])
    if hist and c1.button("Nouvelle conversation", key=f"{cle}-raz"):
        st.session_state[cle] = []
        st.rerun()
    if not q:
        return
    hist.append({"role": "user", "texte": q})
    msgs = [{"role": m["role"], "content": m["texte"]} for m in hist[-12:]]
    while msgs and msgs[0]["role"] != "user":
        msgs.pop(0)
    with st.spinner("Analyse en cours (calculs par l'application)…"):
        try:
            rep, traces = ia.agent(SYSTEME + f" Périmètre des données : {perimetre}.", msgs, outils)
        except ia.IAErreur as e:
            rep, traces = f"⚠️ {e}", []
    hist.append({"role": "assistant", "texte": rep or "—", "traces": traces})
    st.rerun()


def commentaire(df: pd.DataFrame, cle: str):
    """Commentaire de la période rédigé par l'IA à partir d'indicateurs calculés."""
    if not ia.disponible() or not len(df):
        return
    if st.button("✨ Commentaire de la période (IA)", key=f"{cle}-go"):
        d = _prep(df)
        tot = d["montant"].sum()
        ind = {
            "periode": f"{d['date'].min():%d/%m/%Y} – {d['date'].max():%d/%m/%Y}",
            "depense_ht": round(tot), "nb_bc": int(d["bc"].nunique()), "nb_fournisseurs": int(d["four"].nunique()),
            "par_trimestre": d.groupby("trimestre")["montant"].sum().round().to_dict(),
            "top_fournisseurs_pct": (100 * d.groupby("four")["montant"].sum().nlargest(8) / tot).round(1).to_dict(),
            "top_familles_pct": (100 * d.groupby("famille")["montant"].sum().nlargest(8) / tot).round(1).to_dict(),
            "top_chantiers_pct": (100 * d.groupby("chantier")["montant"].sum().nlargest(8) / tot).round(1).to_dict(),
            "top_articles": d.groupby("article")["montant"].sum().nlargest(8).round().to_dict()}
        with st.spinner("Rédaction du commentaire…"):
            try:
                st.session_state[cle] = ia.commentaire_periode(ind)
            except ia.IAErreur as e:
                st.error(str(e))
    if st.session_state.get(cle):
        st.info(st.session_state[cle])
