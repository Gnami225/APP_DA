"""Prix de référence des articles : historique des BC (onglet PRIX_HISTORIQUE) + BC validés dans l'app.

Principes :
- prix NET (après remise), comparé seulement dans la même unité ;
- valeurs aberrantes écartées (moins de la moitié ou plus du double du prix médian, dès 3 achats) ;
- repères sur tout l'historique : dernier prix, meilleur prix, prix moyen pondéré par les quantités,
  nombre d'achats ; en complément le meilleur prix des 12 derniers mois ;
- familles « non comparables » (transport, frais, réparations, locations) : prix indicatifs, sans alerte.
"""
from __future__ import annotations

import unicodedata
from datetime import date, timedelta

import pandas as pd
import streamlit as st

import graphs

from logic import as_text, fmt_date, fmt_montant, to_date, to_num

FAMILLES_SANS_ALERTE_DEFAUT = ("TRANSPORT & LOGISTIQUE; FRAIS ADMIN. VÉHICULES; RÉPARATIONS & ENTRETIEN ENGINS; "
                               "LOCATION ENGINS & MATÉRIEL")
SEUIL_DEFAUT = 15.0
FENETRE_JOURS = 365

_UNITES = {"u": "unite", "unite": "unite", "unites": "unite", "pce": "unite", "piece": "unite", "forfait": "unite",
           "l": "l", "litre": "l", "litres": "l", "m": "m", "ml": "m", "metre": "m", "m2": "m2", "m²": "m2",
           "m3": "m3", "m³": "m3", "kg": "kg", "tonne": "t", "t": "t", "boite": "boite", "carton": "boite",
           "boite/carton": "boite", "fut": "fut", "f cfa": "fcfa", "fcfa": "fcfa"}


def _plat(t) -> str:
    t = unicodedata.normalize("NFKD", as_text(t).upper())
    return " ".join("".join(c for c in t if not unicodedata.combining(c)).split())


def unite(u) -> str:
    k = _plat(u).lower()
    return _UNITES.get(k, k)


def cle_texte(des, ref) -> str:
    return f"{_plat(des)}|{_plat(ref)}"


def _familles_sans_alerte(p: dict) -> set[str]:
    txt = p.get("Familles sans alerte prix") or FAMILLES_SANS_ALERTE_DEFAUT
    return {_plat(x) for x in txt.replace(",", ";").split(";") if x.strip()}


def seuil(p: dict) -> float:
    s = to_num(p.get("Seuil alerte prix (%)"))
    return s if s > 0 else SEUIL_DEFAUT


# --------------------------------------------------------------------------- base de prix
def base_prix(ctx) -> pd.DataFrame:
    """Tous les prix d'achat connus : historique importé + lignes des BC validés de l'app."""
    h = ctx.load("PRIX_HISTORIQUE")
    a = pd.DataFrame({
        "date": h["Date"].map(to_date), "bc": h["N° BC"].map(as_text), "four": h["Fournisseur"].map(as_text),
        "code": h["Code Article"].map(as_text), "des": h["Désignation"].map(as_text),
        "ref": h["Référence"].map(as_text), "unite": h["Unité"].map(as_text),
        "q": h["Quantité"].map(to_num), "pu": h["PU net HT"].map(to_num), "chantier": h["Chantier"].map(as_text)})
    bc, bl = ctx.load("BC"), ctx.load("BC_LIGNES")
    val = set(bc.loc[bc["Statut"].map(as_text) == "Validé", "ID_BC"].map(as_text))
    bl = bl.loc[bl["ID_BC"].map(as_text).isin(val).astype(bool)]
    deja = set(a["bc"])
    bl = bl.loc[~bl["N° BC"].map(as_text).isin(deja).astype(bool)]  # pas de doublon après archivage
    b = pd.DataFrame({
        "date": bl["Date BC"].map(to_date), "bc": bl["N° BC"].map(as_text), "four": bl["Fournisseur"].map(as_text),
        "code": bl["Code Article"].map(as_text), "des": bl["Désignation"].map(as_text),
        "ref": bl["Référence"].map(as_text), "unite": bl["Unité"].map(as_text), "q": bl["Quantité"].map(to_num),
        "pu": [to_num(p) * (1 - to_num(r) / 100) for p, r in zip(bl["Prix unitaire HT"], bl["Remise %"])],
        "chantier": bl["Chantier"].map(as_text)})
    df = pd.concat([a, b], ignore_index=True)
    df["pu"] = pd.to_numeric(df["pu"], errors="coerce").fillna(0.0)  # séries vides : type texte par défaut
    df["q"] = pd.to_numeric(df["q"], errors="coerce").fillna(0.0)
    df = df.loc[(df["pu"] > 0) & df["date"].notna()].copy()
    df["u"] = df["unite"].map(unite)
    df["cle"] = [c if c else "T:" + cle_texte(d, r) for c, d, r in zip(df["code"], df["des"], df["ref"])]
    return df


def _stats(g: pd.DataFrame, debut: date) -> dict:
    """Repères sur TOUT l'historique (valeurs aberrantes écartées) + meilleur prix des 12 derniers mois."""
    g = g.sort_values("date")
    n_total = len(g)
    if n_total >= 3:  # écarte les erreurs de saisie / unités différentes
        med = g["pu"].median()
        g = g.loc[(g["pu"] >= med / 2) & (g["pu"] <= med * 2)]
    recents = g.loc[g["date"] >= debut]
    poids = g["q"].where(g["q"] > 0, 1.0)
    best = g.loc[g["pu"].idxmin()]
    last = g.iloc[-1]
    b12 = recents.loc[recents["pu"].idxmin()] if len(recents) else None
    return {"n": len(g), "n_total": n_total, "ecartes": n_total - len(g), "recent": bool(len(recents)),
            "moyenne": float((g["pu"] * poids).sum() / poids.sum()),
            "meilleur": float(best["pu"]), "meilleur_four": best["four"], "meilleur_date": best["date"],
            "meilleur_12": float(b12["pu"]) if b12 is not None else None,
            "meilleur_12_four": b12["four"] if b12 is not None else "",
            "dernier": float(last["pu"]), "dernier_four": last["four"], "dernier_date": last["date"],
            "premier_date": g.iloc[0]["date"], "unite": as_text(last["unite"])}


@st.cache_resource(show_spinner=False, max_entries=2)
def _references(version: int, _ctx) -> dict:
    df = base_prix(_ctx)
    debut = date.today() - timedelta(days=FENETRE_JOURS)
    out: dict = {}
    for (cle, u), g in df.groupby(["cle", "u"], sort=False):
        out.setdefault(cle, {})[u] = _stats(g, debut)
    art = _ctx.load("ARTICLES")
    fam = {as_text(c): _plat(f) for c, f in zip(art["Code Article"], art["Famille"])}
    return {"refs": out, "fam": fam}


def references(ctx) -> dict:
    ctx.load("ARTICLES")
    return _references(ctx.version(), ctx)


def ref_article(ctx, code, des, ref, un) -> dict | None:
    """Repères de prix d'un article (par code, sinon par désignation + référence) dans l'unité demandée.
    Si l'article n'a été acheté que dans une autre unité, renvoie cette unité avec autre_unite=True."""
    R = references(ctx)
    par_u = R["refs"].get(as_text(code)) if as_text(code) else None
    if not par_u:
        par_u = R["refs"].get("T:" + cle_texte(des, ref))
    if not par_u:
        return None
    u = unite(un)
    if u in par_u:
        s = dict(par_u[u], autre_unite=False)
    else:
        s = dict(max(par_u.values(), key=lambda x: x["n"]), autre_unite=True)
    s["indicatif"] = R["fam"].get(as_text(code), "") in _familles_sans_alerte(_params(ctx))
    return s


def _params(ctx) -> dict:
    pr = ctx.load("PARAMETRES")
    return {as_text(k): as_text(v) for k, v in zip(pr["Paramètre"], pr["Valeur"])}


# --------------------------------------------------------------------------- écarts
def ecart(pu_net: float, s: dict | None) -> float | None:
    """Écart en % du prix net saisi par rapport au prix moyen de référence."""
    if not s or s.get("autre_unite") or not pu_net or pu_net <= 0 or s["moyenne"] <= 0:
        return None
    return (pu_net / s["moyenne"] - 1) * 100


def niveau(e: float | None, s: dict | None, seuil_pct: float) -> str:
    if e is None:
        return ""
    if s and s.get("indicatif"):
        return "indicatif"
    if e > seuil_pct:
        return "rouge"
    return "orange" if e > 5 else "vert"


PASTILLE = {"rouge": "🔴", "orange": "🟠", "vert": "🟢", "indicatif": "⚪", "": ""}


def fmt_ecart(e: float | None, niv: str) -> str:
    if e is None:
        return ""
    return f"{PASTILLE.get(niv, '')} {e:+.0f} %".strip()


def colonnes_ref(s: dict | None) -> dict:
    """Textes courts pour les tableaux : dernier prix, meilleur prix (tout l'historique), meilleur des 12 derniers
    mois, prix moyen pondéré (tout l'historique), nombre d'achats et période couverte."""
    if not s:
        return {"Dernier prix": "", "Meilleur prix": "", "Meilleur 12 mois": "", "Prix moyen": "", "Achats": ""}
    suf = f" /{s['unite']}" if s.get("autre_unite") else ""
    ind = " (indicatif)" if s.get("indicatif") else ""
    return {"Dernier prix": f"{fmt_montant(s['dernier'])}{suf} · {fmt_date(s['dernier_date'])} · "
                            f"{as_text(s['dernier_four'])[:16]}",
            "Meilleur prix": f"{fmt_montant(s['meilleur'])}{suf} · {as_text(s['meilleur_four'])[:16]} · "
                             f"{fmt_date(s['meilleur_date'])}",
            "Meilleur 12 mois": (f"{fmt_montant(s['meilleur_12'])}{suf} · {as_text(s['meilleur_12_four'])[:16]}"
                                 if s.get("meilleur_12") else "aucun achat"),
            "Prix moyen": f"{fmt_montant(s['moyenne'])}{suf}{ind}",
            "Achats": f"{s['n']} depuis {fmt_date(s['premier_date'])[-7:]}" + (" ⚠" if s["n"] == 1 else "")}


def controle_lignes(ctx, lignes: list[dict], seuil_pct: float) -> pd.DataFrame:
    """Tableau de contrôle des prix saisis (TCP / BC) : un enregistrement par ligne avec référence."""
    out = []
    for l in lignes:
        s = ref_article(ctx, l.get("Code Article"), l.get("Désignation"), l.get("Référence"), l.get("Unité"))
        pu = to_num(l.get("Prix unitaire HT")) * (1 - to_num(l.get("Remise %")) / 100)
        e = ecart(pu, s)
        niv = niveau(e, s, seuil_pct)
        out.append({"Désignation": as_text(l.get("Désignation")), "Référence": as_text(l.get("Référence")),
                    "P.U. net saisi": pu, **colonnes_ref(s), "Écart vs moyenne": fmt_ecart(e, niv),
                    "_e": e, "_niv": niv, "_ref": s["moyenne"] if s and not s.get("autre_unite") else None})
    return pd.DataFrame(out)


def montant_estime(ctx, lignes: list[dict]) -> tuple[float, int]:
    """Montant estimé d'une DA (quantité × prix moyen) et nombre de lignes estimées."""
    tot, n = 0.0, 0
    for l in lignes:
        s = ref_article(ctx, l.get("Code Article"), l.get("Désignation"), l.get("Référence"), l.get("Colisage"))
        if s and not s.get("autre_unite"):
            tot += to_num(l.get("Quantité")) * s["moyenne"]
            n += 1
    return tot, n


# --------------------------------------------------------------------------- import / analyses
COLS_IMPORT = ["Date", "N° BC", "Fournisseur", "Chantier", "Code Article", "Désignation", "Référence", "Famille",
               "Unité", "Quantité", "PU HT", "Remise %", "PU net HT", "Libellé d'origine", "Source"]


def importer(ctx, fichier) -> str:
    """Ajoute à PRIX_HISTORIQUE les lignes d'un fichier Excel (même colonnes), sans doublon."""
    df = pd.read_excel(fichier, sheet_name=0, dtype=object).fillna("")
    manquantes = [c for c in ("Date", "Code Article", "PU net HT") if c not in df.columns]
    if manquantes:
        return f"Colonnes manquantes : {', '.join(manquantes)}."
    for c in COLS_IMPORT:
        if c not in df.columns:
            df[c] = ""
    df["Date"] = df["Date"].map(lambda v: (to_date(v).isoformat() if to_date(v) else ""))
    df = df.loc[(df["Date"] != "") & (df["PU net HT"].map(to_num) > 0)]
    store = ctx.get_store()
    exist = store.read("PRIX_HISTORIQUE")
    cle = lambda d: d["N° BC"].map(as_text) + "|" + d["Code Article"].map(as_text) + "|" + d["PU net HT"].map(
        lambda v: f"{to_num(v):.2f}") + "|" + d["Date"].map(as_text)
    deja = set(cle(exist)) if len(exist) else set()
    nouv = df.loc[~cle(df).isin(deja)]
    if len(nouv):
        rows = [{c: (r[c] if not isinstance(r[c], float) or r[c] == r[c] else "") for c in COLS_IMPORT}
                for _, r in nouv.iterrows()]
        for i in range(0, len(rows), 1000):
            store.append("PRIX_HISTORIQUE", rows[i:i + 1000])
        ctx.refresh()
    return f"{len(nouv)} prix importé(s), {len(df) - len(nouv)} déjà présent(s)."


def archive_vers_historique(ctx, bc: pd.DataFrame, bl: pd.DataFrame):
    """Avant de retirer une année de la base : les prix des BC validés passent dans PRIX_HISTORIQUE."""
    val = set(bc.loc[bc["Statut"].map(as_text) == "Validé", "ID_BC"].map(as_text))
    bl = bl.loc[bl["ID_BC"].map(as_text).isin(val).astype(bool)]
    art = ctx.load("ARTICLES")
    fam = dict(zip(art["Code Article"].map(as_text), art["Famille"].map(as_text)))
    rows = [{"Date": as_text(r["Date BC"])[:10], "N° BC": as_text(r["N° BC"]), "Fournisseur": as_text(r["Fournisseur"]),
             "Chantier": as_text(r["Chantier"]), "Code Article": as_text(r["Code Article"]),
             "Désignation": as_text(r["Désignation"]), "Référence": as_text(r["Référence"]),
             "Famille": fam.get(as_text(r["Code Article"]), ""), "Unité": as_text(r["Unité"]),
             "Quantité": to_num(r["Quantité"]), "PU HT": to_num(r["Prix unitaire HT"]),
             "Remise %": to_num(r["Remise %"]),
             "PU net HT": round(to_num(r["Prix unitaire HT"]) * (1 - to_num(r["Remise %"]) / 100), 2),
             "Libellé d'origine": "", "Source": "BC app (archivé)"} for _, r in bl.iterrows()
            if to_num(r["Prix unitaire HT"]) > 0]
    if rows:
        ctx.get_store().append("PRIX_HISTORIQUE", rows)


def vue_evolution(ctx):
    """Analyses : évolution du prix d'un article dans le temps."""
    df = base_prix(ctx)
    st.markdown("##### Évolution des prix d'un article")
    if not len(df):
        st.info("Base de prix vide : importez l'historique (Administration > Paramètres).")
        return
    lib = (df.assign(l=df["des"] + df["ref"].map(lambda r: f" · {r}" if r else ""))
           .groupby("cle").agg(l=("l", "last"), n=("pu", "size")))
    lib = lib.sort_values("n", ascending=False)
    cle = st.selectbox("Article", lib.index.tolist(), index=None, key="prix-evo",
                       format_func=lambda c: f"{lib.at[c, 'l']} ({lib.at[c, 'n']} achat(s))",
                       placeholder="Choisir un article (les plus achetés en tête)")
    if not cle:
        return
    g = df.loc[df["cle"] == cle].sort_values("date")
    for u, gu in g.groupby("unite"):
        s = ref_article(ctx, gu["code"].iloc[0], gu["des"].iloc[0], gu["ref"].iloc[0], u)
        if s:
            c1, c2, c3, c4, c5 = st.columns(5)
            c1.metric(f"Dernier prix ({u})", f"{fmt_montant(s['dernier'])} F", help=f"{s['dernier_four']} · "
                                                                                  f"{fmt_date(s['dernier_date'])}")
            c2.metric("Meilleur prix (historique)", f"{fmt_montant(s['meilleur'])} F",
                      help=f"{s['meilleur_four']} · {fmt_date(s['meilleur_date'])}")
            c3.metric("Meilleur prix 12 mois", f"{fmt_montant(s['meilleur_12'])} F" if s.get("meilleur_12") else "–",
                      help=s.get("meilleur_12_four") or "aucun achat sur les 12 derniers mois")
            c4.metric("Prix moyen pondéré (historique)", f"{fmt_montant(s['moyenne'])} F")
            c5.metric("Achats retenus", f"{s['n']}/{s['n_total']}",
                      help="Prix aberrants écartés du calcul (moins de la moitié ou plus du double du prix médian)")
        gc = gu.assign(Date=pd.to_datetime(gu["date"]), **{"P.U. net HT": gu["pu"], "N° BC": gu["bc"],
                                                            "Fournisseur": gu["four"]})
        graphs.courbe(gc, "Date", "P.U. net HT", f"Prix payés par {u}", f"prix-{cle}-{u}", unite=f"F CFA / {u}",
                      infos=["N° BC", "Fournisseur"])
    st.markdown("##### Détail des achats")
    st.dataframe(g.assign(Date=g["date"].map(fmt_date), PU=g["pu"].map(fmt_montant))
                 [["Date", "bc", "four", "chantier", "q", "unite", "PU"]]
                 .rename(columns={"bc": "N° BC", "four": "Fournisseur", "chantier": "Chantier", "q": "Qté",
                                  "unite": "Unité", "PU": "P.U. net HT"}).iloc[::-1],
                 hide_index=True, width="stretch")
