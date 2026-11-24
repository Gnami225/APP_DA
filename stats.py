"""Statistiques avancées des achats (réservé aux administrateurs).

Base : lignes d'achat de l'historique importé (PRIX_HISTORIQUE) + lignes des BC de l'application (validés, et
émis si demandé). Toutes les mesures sont calculées sur la sélection (période, chantiers, familles, source).

Sections : vue d'ensemble · ABC / XYZ · fournisseurs · prix · processus · contrôles (audit)."""
from __future__ import annotations

import io
import math

import numpy as np
import pandas as pd
import streamlit as st

import assistant
import graphs
from logic import as_text, fmt_montant, to_date, to_num
from schema import LIGNE_BC_RETIREE

SECTIONS = ["🤖 Assistant IA", "Vue d'ensemble", "ABC / XYZ", "Fournisseurs", "Prix", "Processus", "Contrôles (audit)"]
HORS = "HORS CATALOGUE"


# =========================================================================== données
def _lignes(ctx) -> pd.DataFrame:
    art = ctx.load("ARTICLES")
    fam = dict(zip(art["Code Article"].map(as_text), art["Famille"].map(as_text)))
    noms_ch = ctx.refs()["chantiers"]
    h = ctx.load("PRIX_HISTORIQUE")
    a = pd.DataFrame({
        "date": h["Date"].map(to_date), "bc": h["N° BC"].map(as_text), "four": h["Fournisseur"].map(as_text),
        "chantier": h["Chantier"].map(as_text), "code": h["Code Article"].map(as_text),
        "des": h["Désignation"].map(as_text), "ref": h["Référence"].map(as_text), "unite": h["Unité"].map(as_text),
        "q": h["Quantité"].map(to_num), "pu": h["PU net HT"].map(to_num), "statut": "Validé",
        "source": "Historique importé"})
    a["montant"] = a["q"] * a["pu"]
    bc, bl = ctx.load("BC"), ctx.load("BC_LIGNES")
    st_bc = dict(zip(bc["ID_BC"].map(as_text), bc["Statut"].map(as_text)))
    # remise globale du BC (sur le total HT) : répartie sur les lignes -> prix et montants nets réels
    f_rg = {as_text(i): 1 - to_num(r) / 100 for i, r in zip(bc["ID_BC"], bc.get("Remise globale %", pd.Series(
        [0] * len(bc), index=bc.index)))}
    bl = bl.loc[bl["Statut livraison"].map(as_text) != LIGNE_BC_RETIREE]
    bl = bl.loc[~bl["N° BC"].map(as_text).isin(set(a["bc"]))]
    b = pd.DataFrame({
        "date": bl["Date BC"].map(to_date), "bc": bl["N° BC"].map(as_text), "four": bl["Fournisseur"].map(as_text),
        "chantier": bl["Chantier"].map(lambda c: noms_ch.get(as_text(c), as_text(c))),
        "code": bl["Code Article"].map(as_text), "des": bl["Désignation"].map(as_text),
        "ref": bl["Référence"].map(as_text), "unite": bl["Unité"].map(as_text), "q": bl["Quantité"].map(to_num),
        "pu": [to_num(p) * (1 - to_num(r) / 100) * f_rg.get(as_text(i), 1)
               for p, r, i in zip(bl["Prix unitaire HT"], bl["Remise %"], bl["ID_BC"])],
        "montant": [to_num(m) * f_rg.get(as_text(i), 1) for m, i in zip(bl["Montant HT"], bl["ID_BC"])],
        "statut": bl["ID_BC"].map(lambda i: st_bc.get(as_text(i), "")),
        "source": "Application"})
    b = b.loc[b["statut"].isin(["Validé", "Émis"])]
    df = pd.concat([a, b], ignore_index=True)
    df = df.loc[df["date"].notna() & (df["montant"] > 0)].copy()
    for c in ("q", "pu", "montant"):
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0).astype(float)
    df["date"] = pd.to_datetime(df["date"])
    df["mois"] = df["date"].dt.strftime("%Y-%m")
    df["famille"] = df["code"].map(lambda c: fam.get(c, "") or HORS)
    df["four"] = df["four"].str.upper().str.split().str.join(" ").replace("", "NON RENSEIGNÉ")
    try:  # fournisseurs fusionnés (doublons) : regroupés sous le nom retenu
        import auto_ia
        alias = auto_ia.alias_fournisseurs(ctx)
        if alias:
            df["four"] = df["four"].map(lambda x: alias.get(x, x))
    except Exception:
        pass
    df["chantier"] = df["chantier"].replace("", "NON RENSEIGNÉ")
    df["cle"] = [c if c else "T:" + f"{d}|{r}".upper() for c, d, r in zip(df["code"], df["des"], df["ref"])]
    df["article"] = df["des"] + df["ref"].map(lambda r: f" · {r}" if r else "")
    df["unite_n"] = df["unite"].str.lower().str.strip()
    return marquer_aberrants(df)


def marquer_aberrants(df: pd.DataFrame) -> pd.DataFrame:
    """Lignes manifestement erronées (souvent unité ou quantité mal saisies), sur les articles achetés ≥ 3 fois :
    prix plus de 10 fois au-dessus / en dessous du prix médian de l'article, ou quantité plus de 50 fois la quantité
    médiane. Elles faussent les totaux : écartées par défaut des statistiques et listées dans « Contrôles »."""
    k = ["cle", "unite_n"]
    n = df.groupby(k)["pu"].transform("size")
    rp = df["pu"] / df.groupby(k)["pu"].transform("median")
    rq = df["q"] / df.groupby(k)["q"].transform("median").replace(0, np.nan)
    motif = np.where((n >= 3) & (rp >= 10), "prix ≥ 10 × médiane",
                     np.where((n >= 3) & (rp <= 0.1), "prix ≤ 1/10 de la médiane",
                              np.where((n >= 3) & (rq >= 50), "quantité ≥ 50 × médiane", "")))
    return df.assign(aberrant=motif != "", motif=motif, ratio_prix=rp, ratio_qte=rq)


@st.cache_resource(show_spinner=False, max_entries=2)
def _base(version: int, _ctx) -> pd.DataFrame:
    return _lignes(_ctx)


def base(ctx) -> pd.DataFrame:
    ctx.load("ARTICLES")
    return _base(ctx.version(), ctx)


def _bcs(df: pd.DataFrame) -> pd.DataFrame:
    """Une ligne par BC : date, fournisseur, chantier, montant HT, nombre de lignes."""
    return (df.groupby("bc").agg(date=("date", "min"), four=("four", "first"), chantier=("chantier", "first"),
                                 montant=("montant", "sum"), lignes=("bc", "size"), source=("source", "first"))
            .reset_index())


# =========================================================================== calculs
def abc(df: pd.DataFrame, col: str, lib: str | None = None) -> pd.DataFrame:
    """Classement ABC : A = éléments faisant 80 % de la dépense, B = 80–95 %, C = le reste."""
    g = df.groupby(col).agg(montant=("montant", "sum"), nom=(lib or col, "first"), achats=("montant", "size"))
    g = g.sort_values("montant", ascending=False).reset_index()
    tot = g["montant"].sum()
    g["part"] = g["montant"] / tot if tot else 0
    g["cum_pct"] = g["part"].cumsum()
    avant = g["cum_pct"] - g["part"]
    g["classe"] = np.where(avant < 0.80, "A", np.where(avant < 0.95, "B", "C"))
    g["rang_pct"] = (np.arange(len(g)) + 1) / max(len(g), 1)
    return g


def xyz(df: pd.DataFrame) -> pd.DataFrame:
    """Régularité de la demande : coefficient de variation des quantités mensuelles sur les mois couverts par les
    données (mois sans achat de l'article = 0). X ≤ 0,5 (régulier) · Y ≤ 1 (variable) · Z > 1 (ponctuel)."""
    # mois réellement couverts par les données (un mois quasi vide gonflerait artificiellement la variabilité)
    nb = df.groupby("mois").size()
    couverts = nb.index[nb >= max(10, 0.2 * nb.median())]
    mois = pd.Index(sorted(couverts)) if len(couverts) >= 3 else pd.period_range(
        df["date"].min(), df["date"].max(), freq="M").strftime("%Y-%m")
    df = df.loc[df["mois"].isin(mois)]
    k = df["cle"] + "§" + df["unite_n"]
    pv = df.assign(k=k).pivot_table(index="k", columns="mois", values="q", aggfunc="sum").reindex(columns=mois,
                                                                                                   fill_value=0)
    pv = pv.fillna(0)
    moy, ec = pv.mean(axis=1), pv.std(axis=1, ddof=0)
    cv = (ec / moy.replace(0, np.nan)).fillna(np.inf)
    out = pd.DataFrame({"cle": [i.split("§")[0] for i in pv.index], "cv": cv.values,
                        "mois_actifs": (pv > 0).sum(axis=1).values})
    out["xyz"] = np.where(out["cv"] <= 0.5, "X", np.where(out["cv"] <= 1.0, "Y", "Z"))
    return out.sort_values("mois_actifs", ascending=False).drop_duplicates("cle")


def hhi(parts: pd.Series) -> float:
    return float((parts ** 2).sum() * 10000)


def stats_prix(df: pd.DataFrame, n_min: int = 3) -> pd.DataFrame:
    """Par article et unité (au moins n_min achats) : dispersion, prix cible (1er quartile), économie potentielle."""
    k = ["cle", "unite_n"]
    d = df.loc[df.groupby(k)["pu"].transform("size") >= n_min].copy()
    if not len(d):
        return pd.DataFrame()
    q25 = d.groupby(k)["pu"].quantile(0.25).rename("p25")
    d = d.join(q25, on=k)
    d["eco"] = (d["pu"] - d["p25"]).clip(lower=0) * d["q"]
    g = d.groupby(k)
    r = g.agg(Article=("article", "first"), Famille=("famille", "first"), Unité=("unite", "last"),
              Achats=("pu", "size"), Fournisseurs=("four", "nunique"), Dépense=("montant", "sum"), Min=("pu", "min"),
              P25=("p25", "first"), Médiane=("pu", "median"), Max=("pu", "max"), moy=("pu", "mean"),
              ec=("pu", "std"), **{"Économie potentielle": ("eco", "sum")}).reset_index()
    r["CV"] = (r["ec"] / r["moy"]).fillna(0)
    r["Max/Min"] = r["Max"] / r["Min"]
    return r.drop(columns=["moy", "ec", "unite_n"])


def prix_atypiques(df: pd.DataFrame) -> pd.DataFrame:
    """Prix atypiques : z robuste = 0,6745 × (prix − médiane) / MAD > 3,5 (règle d'Iglewicz-Hoaglin)."""
    k = ["cle", "unite_n"]
    d = df.loc[df.groupby(k)["pu"].transform("size") >= 4].copy()
    if not len(d):
        return pd.DataFrame()
    d["med"] = d.groupby(k)["pu"].transform("median")
    d["ad"] = (d["pu"] - d["med"]).abs()
    d["mad"] = d.groupby(k)["ad"].transform("median")
    d = d.loc[d["mad"] > 0]
    d["z"] = 0.6745 * (d["pu"] - d["med"]) / d["mad"]
    a = d.loc[d["z"].abs() > 3.5]
    return pd.DataFrame({"Date": a["date"].dt.strftime("%d/%m/%Y"), "N° BC": a["bc"], "Fournisseur": a["four"],
                         "Article": a["article"], "Unité": a["unite"], "Prix unitaire": a["pu"],
                         "Prix médian": a["med"], "Écart": a["pu"] / a["med"] - 1, "z robuste": a["z"]})


def indice_prix(df: pd.DataFrame) -> pd.DataFrame:
    """Indice mensuel des prix (base 100 = prix médian de chaque article sur la période), pondéré par la dépense.
    Seuls les articles achetés au moins 3 fois entrent dans l'indice."""
    k = df["cle"] + "§" + df["unite_n"]
    n = k.map(k.value_counts())
    d = df.loc[n >= 3].assign(k=k[n >= 3])
    if not len(d):
        return pd.DataFrame()
    d["rel"] = d["pu"] / d.groupby("k")["pu"].transform("median")
    g = d.groupby("mois").apply(lambda x: pd.Series({"Indice": 100 * (x["rel"] * x["montant"]).sum()
                                                     / x["montant"].sum(), "Lignes": len(x)}), include_groups=False)
    g = g.loc[g["Lignes"] >= 5].reset_index()
    g["Mois"] = pd.to_datetime(g["mois"] + "-01")
    return g


def benford_test(montants: pd.Series) -> tuple[pd.DataFrame, float, float, int]:
    """1er chiffre des montants ≥ 10 : fréquences observées / Benford, MAD (Nigrini) et p-valeur du χ² (8 ddl)."""
    m = montants[montants >= 10]
    d1 = m.map(lambda x: int(str(int(x))[0]))
    n = len(d1)
    obs = d1.value_counts().reindex(range(1, 10), fill_value=0)
    att = pd.Series([math.log10(1 + 1 / d) for d in range(1, 10)], index=range(1, 10))
    t = pd.DataFrame({"chiffre": range(1, 10), "n": obs.values, "observe": obs.values / max(n, 1), "attendu": att.values})
    mad = float((t["observe"] - t["attendu"]).abs().mean())
    chi2 = float((((obs - n * att) ** 2) / (n * att)).sum()) if n else 0.0
    x = chi2 / 2  # loi du χ² à 8 ddl : P(X > chi2) = e^-x Σ_{i<4} x^i / i!
    p = math.exp(-x) * sum(x ** i / math.factorial(i) for i in range(4))
    return t, mad, p, n


def nigrini(mad: float) -> str:
    if mad < 0.006:
        return "Conformité forte"
    if mad < 0.012:
        return "Conformité acceptable"
    if mad < 0.015:
        return "Conformité limite"
    return "Non-conformité : à investiguer"


# =========================================================================== interface
def onglet_statistiques(ctx):
    st.caption("Tableau de bord quantitatif des achats — historique importé et BC de l'application. "
               "Section réservée aux administrateurs.")
    df0 = base(ctx)
    if not len(df0):
        st.info("Aucune donnée d'achat : importez l'historique des prix ou validez des BC.")
        return
    df_tout = _filtres(df0)
    ecarter = st.session_state.get("sta-ab", True)
    df = df_tout.loc[~df_tout["aberrant"]] if ecarter else df_tout
    if not len(df):
        st.warning("Aucune ligne d'achat pour cette sélection.")
        return
    sec = st.segmented_control("Section", SECTIONS, default="Vue d'ensemble", key="sta-sec",
                               label_visibility="collapsed") or "Vue d'ensemble"
    {"🤖 Assistant IA": lambda d: assistant.ui(ctx, d, "sta-chat", "achats filtrés par la période et les filtres "
                                                                    "ci-dessus, et toutes les DA"),
     "Vue d'ensemble": _vue_ensemble, "ABC / XYZ": _vue_abc, "Fournisseurs": _vue_fournisseurs,
     "Prix": _vue_prix, "Processus": lambda d: _vue_processus(ctx, d),
     "Contrôles (audit)": lambda d: _vue_controles(d, df_tout)}[sec](df)
    st.divider()
    _export(df)


GRAINS = {"Semaine": "W", "Mois": "M", "Trimestre": "Q", "Année": "Y"}


def periode(dates: pd.Series, grain: str) -> pd.Series:
    """Libellé de période triable : 2026-S07, 2026-07, 2026-T3, 2026."""
    if grain == "Semaine":
        iso = dates.dt.isocalendar()
        return iso["year"].astype(str) + "-S" + iso["week"].astype(str).str.zfill(2)
    if grain == "Trimestre":
        return dates.dt.year.astype(str) + "-T" + dates.dt.quarter.astype(str)
    if grain == "Année":
        return dates.dt.year.astype(str)
    return dates.dt.strftime("%Y-%m")


def _options(df: pd.DataFrame, col: str, m: pd.Series) -> list[str]:
    """Valeurs proposées dans un filtre : celles qui restent possibles avec les autres filtres (filtres en cascade)."""
    return sorted(df.loc[m, col].dropna().unique())


def _filtres(df: pd.DataFrame) -> pd.DataFrame:
    # ---- slicer de période (mois de début -> mois de fin) + raccourcis
    mois = sorted(df["mois"].unique())
    def depuis(n: int) -> tuple[str, str]:  # n mois calendaires jusqu'au dernier mois avec des achats
        debut = (pd.Period(mois[-1], "M") - (n - 1)).strftime("%Y-%m")
        return next((x for x in mois if x >= debut), mois[-1]), mois[-1]

    raccourcis = {"Tout": (mois[0], mois[-1]), "12 derniers mois": depuis(12), "6 derniers mois": depuis(6),
                  "3 derniers mois": depuis(3),
                  "Année en cours": (next((x for x in mois if x.startswith(mois[-1][:4])), mois[0]), mois[-1])}
    per = st.session_state.get("sta-per")
    if not (isinstance(per, (tuple, list)) and len(per) == 2 and per[0] in mois and per[1] in mois):
        st.session_state["sta-per"] = (mois[0], mois[-1])
    r = st.segmented_control("Raccourci de période", list(raccourcis), key="sta-racc", label_visibility="collapsed")
    if r and r != st.session_state.get("_sta_racc_applique"):  # nouveau raccourci : il positionne le curseur
        st.session_state["sta-per"] = raccourcis[r]
    st.session_state["_sta_racc_applique"] = r
    if len(mois) > 1:
        deb, fin = st.select_slider("Période (mois)", options=mois, key="sta-per",
                                    format_func=lambda m: f"{m[5:]}/{m[:4]}")
    else:
        deb = fin = mois[0]
    m = (df["mois"] >= deb) & (df["mois"] <= fin)
    # ---- filtres en cascade (chantier → famille → fournisseur → désignation → référence) : chaque liste ne
    # propose que les valeurs possibles avec les choix des filtres précédents ; un choix devenu impossible est retiré
    cols = {"sta-ch": "chantier", "sta-fa": "famille", "sta-fo": "four", "sta-de": "des", "sta-re": "ref"}
    opts, mm = {}, m.copy()
    for k, c in cols.items():
        opts[k] = [x for x in _options(df, c, mm) if x]
        choix = [v for v in (st.session_state.get(k) or []) if v in opts[k]]
        if st.session_state.get(k) and choix != st.session_state[k]:
            st.session_state[k] = choix
        if choix:
            mm &= df[c].isin(choix)
    c1, c2, c3 = st.columns(3)
    c1.multiselect("Chantiers", opts["sta-ch"], key="sta-ch", placeholder="Tous")
    c2.multiselect("Familles d'articles", opts["sta-fa"], key="sta-fa", placeholder="Toutes")
    c3.multiselect("Fournisseurs", opts["sta-fo"], key="sta-fo", placeholder="Tous")
    c4, c5, c6 = st.columns([2, 2, 1])
    c4.multiselect("Désignations", opts["sta-de"], key="sta-de", placeholder="Toutes")
    refs = opts["sta-re"]
    c5.multiselect("Références", refs, key="sta-re", placeholder="Toutes" if refs else "—",
                   help="Liste limitée aux références des désignations, familles et chantiers choisis")
    src = c6.selectbox("Source", ["Toutes", "Historique importé", "Application"], key="sta-src")
    t1, t2 = st.columns(2)
    emis = t1.toggle("Inclure les BC émis non encore validés (dépense engagée)", value=True, key="sta-emis")
    nb_ab = int(df["aberrant"].sum())
    t2.toggle(f"Écarter les lignes aberrantes ({nb_ab} sur la base)", value=True, key="sta-ab",
              help="Prix ou quantité manifestement mal saisis (unité, zéros en trop). Détail dans « Contrôles ».")
    sel = {k: st.session_state.get(k) or [] for k in cols}
    for k, c in cols.items():
        if sel[k]:
            m &= df[c].isin(sel[k])
    if src != "Toutes":
        m &= df["source"] == src
    if not emis:
        m &= df["statut"] == "Validé"
    actifs = [f"{len(v)} {n}" for v, n in ((sel["sta-ch"], "chantier(s)"), (sel["sta-fa"], "famille(s)"),
                                            (sel["sta-fo"], "fournisseur(s)"), (sel["sta-de"], "désignation(s)"),
                                            (sel["sta-re"], "référence(s)")) if v]
    st.caption(f"Sélection : {deb[5:]}/{deb[:4]} → {fin[5:]}/{fin[:4]}"
               + (" · " + " · ".join(actifs) if actifs else " · aucun autre filtre")
               + f" · {int(m.sum())} lignes d'achat")
    return df.loc[m]


def _f(v) -> str:
    return f"{fmt_montant(v)} F"


def _pct(v) -> str:
    return f"{v:.0%}".replace(".", ",")


# --------------------------------------------------------------------------- vue d'ensemble
def _vue_ensemble(df: pd.DataFrame):
    assistant.commentaire(df, "sta-comm")
    bcs = _bcs(df)
    tot = df["montant"].sum()
    parts = df.groupby("four")["montant"].sum() / tot
    k = st.columns(4)
    k[0].metric("Dépense HT", _f(tot))
    k[1].metric("Bons de commande", f"{len(bcs)}")
    k[2].metric("Fournisseurs actifs", f"{df['four'].nunique()}")
    k[3].metric("Articles distincts", f"{df['cle'].nunique()}")
    k = st.columns(4)
    k[0].metric("Montant moyen par BC", _f(bcs["montant"].mean()), help=f"Médiane : {_f(bcs['montant'].median())}")
    k[1].metric("Part des 5 premiers fournisseurs", _pct(parts.nlargest(5).sum()))
    h = hhi(parts)
    k[2].metric("Concentration fournisseurs (HHI)", f"{h:,.0f}".replace(",", " "),
                help="Indice de Herfindahl-Hirschman : < 1 500 faible, 1 500–2 500 modérée, > 2 500 forte")
    mois = df.groupby("mois")["montant"].sum().sort_index()
    if len(mois) >= 6:
        r3, p3 = mois.iloc[-3:].mean(), mois.iloc[-6:-3].mean()
        k[3].metric("Dépense mensuelle (3 derniers mois)", _f(r3), f"{(r3 / p3 - 1):+.0%} vs 3 mois précédents"
                    if p3 else None, delta_color="off")
    else:
        k[3].metric("Dépense mensuelle moyenne", _f(mois.mean()))
    _depense_par_periode(df)
    c1, c2 = st.columns(2)
    with c1:
        t = df.groupby("chantier", as_index=False)["montant"].sum().rename(columns={"chantier": "Chantier",
                                                                                     "montant": "Dépense HT"})
        graphs.barres(t, "Chantier", "Dépense HT", "Dépense par chantier", "sta-ch", unite="F CFA", top=12)
    with c2:
        t = df.groupby("famille", as_index=False)["montant"].sum().rename(columns={"famille": "Famille",
                                                                                    "montant": "Dépense HT"})
        graphs.barres(t, "Famille", "Dépense HT", "Dépense par famille", "sta-fam", unite="F CFA", top=12)
    graphs.histogramme(np.log10(bcs["montant"].clip(lower=1)), "Distribution des montants de BC (échelle log10)",
                       "sta-hbc", unite="log10(montant HT) — 4 = 10 000 F, 5 = 100 000 F, 6 = 1 M F", pas=0.25,
                       repere=float(np.log10(bcs["montant"].median())), repere_txt="médiane")


def _depense_par_periode(df: pd.DataFrame):
    """Dépense par semaine / mois / trimestre / année, ventilée par la dimension choisie, avec variations."""
    c1, c2 = st.columns(2)
    grain = c1.segmented_control("Pas de temps", list(GRAINS), default="Mois", key="sta-grain") or "Mois"
    vent = c2.segmented_control("Ventiler par", ["Famille", "Chantier", "Fournisseur", "Aucune"], default="Famille",
                                key="sta-vent") or "Famille"
    col = {"Famille": "famille", "Chantier": "chantier", "Fournisseur": "four"}.get(vent)
    p = periode(df["date"], grain)
    d = df.assign(Période=p, Série=df[col] if col else "Dépense")
    g = d.groupby(["Période", "Série"], as_index=False)["montant"].sum().rename(columns={"montant": "Dépense HT",
                                                                                         "Série": vent})
    tab = d.groupby("Période").agg(**{"Dépense HT": ("montant", "sum"), "BC": ("bc", "nunique"),
                                      "Lignes": ("montant", "size")}).reset_index()
    tab["Variation"] = tab["Dépense HT"].pct_change()
    tab["Cumul"] = tab["Dépense HT"].cumsum()
    graphs.empile_mois(g, "Période", vent, "Dépense HT", f"Dépense par {grain.lower()}"
                       + (f", par {vent.lower()}" if col else ""), "sta-per-g", unite="F CFA",
                       table=tab.assign(**{"Dépense HT": tab["Dépense HT"].map(fmt_montant),
                                           "Cumul": tab["Cumul"].map(fmt_montant),
                                           "Variation": tab["Variation"].map(
                                               lambda v: "" if pd.isna(v) or not np.isfinite(v) else f"{v:+.0%}")}))


# --------------------------------------------------------------------------- ABC / XYZ
def _vue_abc(df: pd.DataFrame):
    dim = st.radio("Classer", ["Articles", "Fournisseurs", "Familles", "Chantiers"], horizontal=True, key="sta-abc")
    col, lib, ent = {"Articles": ("cle", "article", "articles"), "Fournisseurs": ("four", None, "fournisseurs"),
                     "Familles": ("famille", None, "familles"), "Chantiers": ("chantier", None, "chantiers")}[dim]
    g = abc(df, col, lib)
    r = g.groupby("classe").agg(n=("nom", "size"), dep=("montant", "sum")).reindex(["A", "B", "C"]).fillna(0)
    k = st.columns(3)
    for i, c in enumerate("ABC"):
        k[i].metric(f"Classe {c}", f"{int(r.at[c, 'n'])} {ent} ({_pct(r.at[c, 'n'] / max(len(g), 1))})",
                    f"{_pct(r.at[c, 'dep'] / g['montant'].sum())} de la dépense", delta_color="off")
    graphs.pareto(g, f"Courbe de Pareto — {dim.lower()}", "sta-par", ent)
    t = g.assign(**{"Dépense HT": g["montant"].map(fmt_montant), "Part": g["part"].map(lambda x: f"{x:.1%}"),
                    "Cumul": g["cum_pct"].map(lambda x: f"{x:.1%}")})
    t = t.rename(columns={"nom": dim[:-1], "classe": "Classe", "achats": "Lignes"})
    cols = [dim[:-1], "Classe", "Dépense HT", "Part", "Cumul", "Lignes"]
    if dim == "Articles":
        x = xyz(df)
        g2 = g.merge(x, left_on="cle", right_on="cle", how="left")
        t["XYZ"] = g2["xyz"].fillna("Z").values
        t["CV mensuel"] = g2["cv"].map(lambda v: "∞" if not np.isfinite(v) else f"{v:.2f}").values
        cols += ["XYZ", "CV mensuel"]
        _matrice_abc_xyz(g2)
    with st.expander(f"Tableau ABC complet ({len(t)} {ent})"):
        st.dataframe(t[cols], hide_index=True, width="stretch")


def _matrice_abc_xyz(g: pd.DataFrame):
    g = g.assign(xyz=g["xyz"].fillna("Z"))
    tot = g["montant"].sum()
    m = g.groupby(["classe", "xyz"]).agg(n=("cle", "size"), dep=("montant", "sum")).reset_index()
    full = pd.MultiIndex.from_product([["A", "B", "C"], ["X", "Y", "Z"]], names=["classe", "xyz"])
    m = m.set_index(["classe", "xyz"]).reindex(full, fill_value=0).reset_index()
    m["part"] = m["dep"] / tot if tot else 0
    m["txt"] = m.apply(lambda r: f"{int(r['n'])} art.\n{r['part']:.0%} dép.", axis=1)
    m["Valeur"] = m["xyz"].map({"X": "X régulier", "Y": "Y variable", "Z": "Z ponctuel"})
    m["Classe"] = m["classe"].map({"A": "A (80 %)", "B": "B (15 %)", "C": "C (5 %)"})
    graphs.carte_chaleur(m, "Valeur", "Classe", "part", "txt", "Matrice ABC × XYZ (part de la dépense)", "sta-mx",
                         ordre_x=["X régulier", "Y variable", "Z ponctuel"],
                         ordre_y=["A (80 %)", "B (15 %)", "C (5 %)"])
    st.caption("**AX / AY** : contrats-cadres et prix négociés, réapprovisionnement planifié · **AZ** : achats "
               "importants et ponctuels, mise en concurrence systématique (TCP) · **BX–BY** : accords annuels, "
               "stock de sécurité · **C** : regroupement des commandes, catalogue fournisseur, achats simplifiés. "
               "XYZ : coefficient de variation des quantités mensuelles (X ≤ 0,5 ; Y ≤ 1 ; Z > 1).")


# --------------------------------------------------------------------------- fournisseurs
def _vue_fournisseurs(df: pd.DataFrame):
    tot = df["montant"].sum()
    g = abc(df, "four")
    parts = g.set_index("nom")["part"]
    bcs = _bcs(df)
    k = st.columns(4)
    k[0].metric("Fournisseurs pour 80 % de la dépense", f"{int((g['classe'] == 'A').sum())} / {len(g)}")
    k[1].metric("Part du 1er fournisseur", _pct(parts.iloc[0]), parts.index[0], delta_color="off")
    k[2].metric("HHI", f"{hhi(parts):,.0f}".replace(",", " "),
                help="< 1 500 faible, 1 500–2 500 modérée, > 2 500 forte concentration")
    queue = g.loc[g["classe"] == "C"]
    k[3].metric("Dépense de queue (classe C)", _pct(queue["montant"].sum() / tot),
                f"{len(queue)} fournisseurs", delta_color="off")
    pf = (df.groupby("four").agg(**{"Dépense HT": ("montant", "sum"), "Articles": ("cle", "nunique"),
                                    "Mois actifs": ("mois", "nunique"), "Chantiers": ("chantier", "nunique")})
          .join(bcs.groupby("four").agg(BC=("bc", "size"), **{"BC moyen": ("montant", "mean")})).reset_index()
          .rename(columns={"four": "Fournisseur"}).sort_values("Dépense HT", ascending=False))
    graphs.barres(pf, "Fournisseur", "Dépense HT", "Principaux fournisseurs", "sta-f-top", unite="F CFA",
                  infos=["BC", "Articles", "Mois actifs"], table=pf.assign(**{
                      "Dépense HT": pf["Dépense HT"].map(fmt_montant), "BC moyen": pf["BC moyen"].map(fmt_montant)}))
    # concentration par famille
    rows = []
    for f, x in df.groupby("famille"):
        p = x.groupby("four")["montant"].sum().sort_values(ascending=False) / x["montant"].sum()
        rows.append({"Famille": f, "Dépense HT": x["montant"].sum(), "Fournisseurs": len(p),
                     "Fournisseur principal": p.index[0], "Part du principal": p.iloc[0], "HHI": hhi(p)})
    cf = pd.DataFrame(rows).sort_values("Dépense HT", ascending=False)
    cf["Risque"] = np.where(cf["Part du principal"] >= 0.9, "🔴 quasi mono-source",
                            np.where(cf["HHI"] > 2500, "🟠 concentrée", "🟢 diversifiée"))
    st.markdown("##### Concentration fournisseurs par famille")
    st.dataframe(cf.assign(**{"Dépense HT": cf["Dépense HT"].map(fmt_montant),
                              "Part du principal": cf["Part du principal"].map(lambda v: f"{v:.0%}"),
                              "HHI": cf["HHI"].map(lambda v: f"{v:,.0f}".replace(",", " "))}),
                 hide_index=True, width="stretch")
    # articles mono-source
    a = df.groupby("cle").agg(article=("article", "first"), four=("four", "nunique"), principal=("four", "first"),
                              dep=("montant", "sum"), n=("montant", "size"))
    mono = a.loc[(a["four"] == 1) & (a["n"] >= 3)].sort_values("dep", ascending=False).head(25)
    st.markdown("##### Articles récurrents achetés chez un seul fournisseur (dépendance)")
    st.caption("Achetés au moins 3 fois, toujours chez le même fournisseur : à mettre en concurrence ou à sécuriser.")
    st.dataframe(mono.rename(columns={"article": "Article", "principal": "Fournisseur", "n": "Achats"})
                 .assign(**{"Dépense HT": mono["dep"].map(fmt_montant)})[["Article", "Fournisseur", "Achats",
                                                                          "Dépense HT"]],
                 hide_index=True, width="stretch")
    nouv = df.groupby("four")["date"].min().dt.strftime("%Y-%m").value_counts().sort_index()
    nv = pd.DataFrame({"Mois": nouv.index, "Fournisseurs": nouv.values, "Type": "Nouveaux"})
    graphs.empile_mois(nv, "Mois", "Type", "Fournisseurs", "Nouveaux fournisseurs par mois (1er achat)", "sta-nf")


# --------------------------------------------------------------------------- prix
def _vue_prix(df: pd.DataFrame):
    sp = stats_prix(df)
    if not len(sp):
        st.info("Pas assez d'achats répétés (3 au moins par article) sur la sélection.")
        return
    tot = df["montant"].sum()
    eco = sp["Économie potentielle"].sum()
    at = prix_atypiques(df)
    k = st.columns(4)
    k[0].metric("Articles suivis (≥ 3 achats)", f"{len(sp)}", f"{_pct(sp['Dépense'].sum() / tot)} de la dépense",
                delta_color="off")
    k[1].metric("Économie potentielle", _f(eco), f"{_pct(eco / tot)} de la dépense", delta_color="off",
                help="Si chaque achat avait été payé au plus au 1er quartile (P25) des prix de l'article")
    k[2].metric("Volatilité médiane des prix (CV)", f"{sp['CV'].median():.0%}")
    k[3].metric("Prix atypiques", f"{len(at)}", help="z robuste > 3,5 (médiane / MAD)")
    ip = indice_prix(df)
    graphs.indice(ip, "Mois", "Indice", "Indice des prix d'achat (base 100 = prix médian de chaque article)",
                  "sta-ip")
    st.caption("Indice pondéré par la dépense, sur les articles achetés au moins 3 fois ; > 100 = mois payé plus cher "
               "que le prix médian habituel. Mois avec moins de 5 lignes non affichés.")
    graphs.nuage(sp, "Dépense", "CV", "Article", "Dépense × volatilité des prix", "sta-nu", "Dépense HT (F, log)",
                 "Coefficient de variation du prix", repere_y=0.25, infos=["Achats", "Fournisseurs"])
    st.caption("En haut à droite : articles à fort enjeu et prix instables — priorité de négociation. Ligne : CV = 25 %.")
    top = sp.sort_values("Économie potentielle", ascending=False).head(25)
    graphs.barres(top.assign(**{"Économie potentielle (F)": top["Économie potentielle"]}), "Article",
                  "Économie potentielle (F)", "Articles à plus fort potentiel d'économie", "sta-eco", top=25,
                  infos=["Achats", "Fournisseurs"], table=top.assign(**{
                      c: top[c].map(fmt_montant) for c in ("Dépense", "Min", "P25", "Médiane", "Max",
                                                           "Économie potentielle")}).assign(
                      CV=top["CV"].map(lambda v: f"{v:.0%}"))
                  [["Article", "Unité", "Achats", "Fournisseurs", "Dépense", "Min", "P25", "Médiane", "Max", "CV",
                    "Économie potentielle"]])
    # comparatif fournisseurs sur un même article
    multi = df.groupby(["cle", "unite_n"]).filter(lambda x: x["four"].nunique() >= 2)
    if len(multi):
        cmp = (multi.groupby(["cle", "unite_n", "four"]).agg(article=("article", "first"), unite=("unite", "last"),
                                                              med=("pu", "median"), dep=("montant", "sum"))
               .reset_index())
        res = []
        for (c, u), x in cmp.groupby(["cle", "unite_n"]):
            x = x.sort_values("med")
            res.append({"Article": x["article"].iloc[0], "Unité": x["unite"].iloc[0], "Dépense": x["dep"].sum(),
                        "Moins cher": x["four"].iloc[0], "Prix médian moins cher": x["med"].iloc[0],
                        "Plus cher": x["four"].iloc[-1], "Prix médian plus cher": x["med"].iloc[-1],
                        "Écart": x["med"].iloc[-1] / x["med"].iloc[0] - 1})
        r = pd.DataFrame(res).sort_values("Dépense", ascending=False).head(30)
        st.markdown("##### Écarts de prix entre fournisseurs sur un même article")
        st.dataframe(r.assign(**{c: r[c].map(fmt_montant) for c in ("Dépense", "Prix médian moins cher",
                                                                     "Prix médian plus cher")},
                              Écart=r["Écart"].map(lambda v: f"{v:+.0%}")), hide_index=True, width="stretch")
    if len(at):
        st.markdown("##### Prix atypiques (à vérifier : erreur de saisie, unité, urgence…)")
        st.dataframe(at.assign(**{"Prix unitaire": at["Prix unitaire"].map(fmt_montant),
                                  "Prix médian": at["Prix médian"].map(fmt_montant),
                                  "Écart": at["Écart"].map(lambda v: f"{v:+.0%}"),
                                  "z robuste": at["z robuste"].map(lambda v: f"{v:.1f}")})
                     .sort_values("z robuste"), hide_index=True, width="stretch")


# --------------------------------------------------------------------------- processus
def _jours(a, b) -> pd.Series:
    return (pd.to_datetime(pd.Series(b).map(to_date)) - pd.to_datetime(pd.Series(a).map(to_date))).dt.days


def _vue_processus(ctx, df: pd.DataFrame):
    st.caption("Indicateurs du circuit dans l'application (DA, validations, TCP, BC, livraisons) — hors historique "
               "importé et hors filtres familles.")
    da, lg = ctx.load("DA"), ctx.load("HISTORIQUE_ARTICLES")
    bc, bl, off = ctx.load("BC"), ctx.load("BC_LIGNES"), ctx.load("TCP_OFFRES")
    if not len(da) and not len(bc):
        st.info("Pas encore de données de processus dans l'application.")
        return
    jv = ctx.load("VALIDATIONS_DA")
    rej = set(jv.loc[jv["Décision"].map(as_text) == "Rejetée", "ID_DA"].map(as_text)) | set(
        da.loc[da["Statut"].map(as_text) == "Rejetée", "ID_DA"].map(as_text))
    d1 = _jours(da["Date DA"], da["Date visa Resp. service"]).dropna()
    d2 = _jours(da["Date visa Resp. service"], da["Date validation"]).dropna()
    # DA validée -> 1er BC
    bl_ok = bl.loc[bl["ID_Ligne_DA"].map(as_text) != ""]
    prem = bl_ok.assign(d=bl_ok["Date BC"].map(to_date)).groupby(bl_ok["N° DA"].map(as_text))["d"].min()
    dv = da.set_index(da["N° DA"].map(as_text))["Date validation"].map(to_date)
    d3 = pd.Series({n: (prem[n] - dv[n]).days for n in prem.index if n in dv.index and dv[n] and prem[n]}, dtype=float)
    liv = bl.loc[bl["Date livraison"].map(as_text) != ""]
    d4 = _jours(liv["Date BC"], liv["Date livraison"]).dropna()
    hors = (lg["Code Article"].map(as_text) == "").mean() if len(lg) else 0
    avec_tcp = (bc["N° TCP"].map(as_text) != "").mean() if len(bc) else 0
    k = st.columns(4)
    k[0].metric("DA", f"{len(da)}", f"{_pct(len(rej) / max(len(da), 1))} rejetées au moins une fois",
                delta_color="off")
    k[1].metric("DA urgentes / critiques", _pct(da["Priorité"].isin(["Urgente", "Critique"]).mean() if len(da) else 0))
    k[2].metric("Articles hors catalogue", _pct(hors), help="Part des lignes de DA saisies librement")
    k[3].metric("BC issus d'un TCP", _pct(avec_tcp))
    k = st.columns(4)
    med = lambda s: f"{s.median():.1f} j (P90 {s.quantile(0.9):.0f} j)" if len(s) else "–"
    k[0].metric("Visa responsable de service", med(d1))
    k[1].metric("Validation DG", med(d2))
    k[2].metric("DA validée → BC", med(d3))
    k[3].metric("BC → livraison", med(d4))
    c1, c2 = st.columns(2)
    with c1:
        graphs.histogramme(d3, "Délai DA validée → BC (jours)", "sta-d3", unite="jours", pas=1,
                           repere=float(d3.median()) if len(d3) else None, repere_txt="médiane")
    with c2:
        graphs.histogramme(d4, "Délai BC → livraison (jours)", "sta-d4", unite="jours", pas=1,
                           repere=float(d4.median()) if len(d4) else None, repere_txt="médiane")
    # TCP : économies de mise en concurrence
    if len(off):
        o = off.assign(p=off["Prix unitaire HT"].map(to_num), q=off["Quantité"].map(to_num),
                       r=off["Retenu"].map(lambda v: as_text(v).upper() in ("TRUE", "1", "VRAI")))
        o = o.loc[o["p"] > 0]
        g = o.groupby(["ID_TCP", "N° Ligne"])
        res = g.apply(lambda x: pd.Series({"q": x["q"].iloc[0], "moy": x["p"].mean(), "max": x["p"].max(),
                                           "min": x["p"].min(),
                                           "ret": x.loc[x["r"], "p"].iloc[0] if x["r"].any() else np.nan}),
                      include_groups=False).dropna()
        if len(res):
            k = st.columns(3)
            k[0].metric("Économie vs offre moyenne (TCP)", _f(((res["moy"] - res["ret"]) * res["q"]).sum()))
            k[1].metric("Économie vs offre la plus chère", _f(((res["max"] - res["ret"]) * res["q"]).sum()))
            k[2].metric("Surcoût vs moins-disant", _f(((res["ret"] - res["min"]) * res["q"]).sum()),
                        help="Choix d'un fournisseur autre que le moins cher (délai, qualité…)")
    # livraisons
    act = bc.loc[bc["Statut"].map(as_text) == "Validé"]
    bla = bl.loc[bl["ID_BC"].map(as_text).isin(set(act["ID_BC"].map(as_text))).astype(bool)]
    if len(bla):
        cmd = (bla["Quantité"].map(to_num) * bla["Prix unitaire HT"].map(to_num)).sum()
        lv = (bla["Qté livrée"].map(to_num) * bla["Prix unitaire HT"].map(to_num)).sum()
        k = st.columns(3)
        k[0].metric("Taux de service (montant livré / commandé)", _pct(lv / cmd if cmd else 0))
        k[1].metric("Lignes entièrement livrées", _pct((bla["Statut livraison"].map(as_text) == "Livré").mean()))
        k[2].metric("BC validés en attente de livraison", f"{int((act['Statut livraison'].map(as_text) != 'Livré').sum())}")


# --------------------------------------------------------------------------- contrôles
def _vue_controles(df: pd.DataFrame, df_tout: pd.DataFrame):
    ab = df_tout.loc[df_tout["aberrant"]].sort_values("montant", ascending=False)
    if len(ab):
        st.markdown(f"##### Lignes aberrantes : {len(ab)} lignes, {fmt_montant(ab['montant'].sum())} F de montant "
                    "calculé")
        st.caption("Prix ou quantité très éloignés de l'habitude pour l'article (erreur d'unité, zéros en trop…). "
                   "À corriger dans la source ; écartées des statistiques tant que l'option est cochée.")
        st.dataframe(ab.assign(Date=ab["date"].dt.strftime("%d/%m/%Y"), PU=ab["pu"].map(fmt_montant),
                               Montant=ab["montant"].map(fmt_montant),
                               Qté=ab["q"].map(lambda v: f"{v:g}"))
                     [["Date", "bc", "four", "article", "unite", "Qté", "PU", "Montant", "motif"]]
                     .rename(columns={"bc": "N° BC", "four": "Fournisseur", "article": "Article", "unite": "Unité",
                                      "motif": "Motif"}), hide_index=True, width="stretch", height=260)
    bcs = _bcs(df)
    t, mad, p, n = benford_test(df["montant"])
    # doublons de BC : même fournisseur, montant à 0,5 % près, à moins de 7 jours
    dbl = []
    for f, x in bcs.sort_values("date").groupby("four"):
        x = x.reset_index(drop=True)
        for i in range(len(x)):
            for j in range(i + 1, len(x)):
                if (x.at[j, "date"] - x.at[i, "date"]).days > 7:
                    break
                a, b = x.at[i, "montant"], x.at[j, "montant"]
                if a and abs(a - b) / a <= 0.005:
                    dbl.append({"Fournisseur": f, "BC 1": x.at[i, "bc"], "BC 2": x.at[j, "bc"],
                                "Date 1": x.at[i, "date"].strftime("%d/%m/%Y"),
                                "Date 2": x.at[j, "date"].strftime("%d/%m/%Y"), "Montant HT": fmt_montant(a)})
    # fractionnement : plusieurs BC le même jour, même fournisseur et même chantier
    fr = (bcs.groupby(["four", "chantier", bcs["date"].dt.date]).agg(BC=("bc", lambda s: ", ".join(s)),
                                                                     n=("bc", "size"), total=("montant", "sum"))
          .reset_index())
    fr = fr.loc[fr["n"] >= 2].sort_values("total", ascending=False)
    # lignes en double dans un même BC
    lgd = df.loc[df.duplicated(["bc", "cle", "q", "pu"], keep=False)]
    lgd = lgd.groupby(["bc", "article"]).agg(fois=("q", "size"), q=("q", "first"), pu=("pu", "first")).reset_index()
    ronds = (bcs["montant"] % 100000 == 0).mean() if len(bcs) else 0
    we = bcs.loc[bcs["date"].dt.dayofweek >= 5]
    k = st.columns(5)
    k[0].metric("Benford (MAD)", f"{mad:.4f}", nigrini(mad), delta_color="off")
    k[1].metric("Doublons potentiels de BC", f"{len(dbl)}")
    k[2].metric("Fractionnements possibles", f"{len(fr)}")
    k[3].metric("Lignes en double dans un BC", f"{len(lgd)}")
    k[4].metric("BC au montant rond (100 000)", _pct(ronds), f"{len(we)} BC datés un week-end", delta_color="off")
    c1, c2 = st.columns([3, 2])
    with c1:
        graphs.benford(t, f"Loi de Benford sur {n} montants de lignes", "sta-bf")
    with c2:
        st.markdown("##### Lecture")
        st.markdown(f"- **MAD** = {mad:.4f} → **{nigrini(mad)}** (seuils de Nigrini : 0,006 / 0,012 / 0,015).\n"
                    f"- **χ² (8 ddl)** : p-valeur = {p:.3f}" + (" → écart significatif au seuil de 5 %." if p < 0.05
                                                               else " → pas d'écart significatif au seuil de 5 %.")
                    + "\n- Un excès sur un chiffre signale des montants à examiner (seuils d'approbation, prix "
                      "arrondis, saisies répétées). Le test porte sur les montants ≥ 10 F.")
        ex = t.assign(e=t["observe"] - t["attendu"]).sort_values("e", ascending=False).iloc[0]
        if ex["e"] > 0.02:
            st.info(f"Chiffre le plus sur-représenté : **{int(ex['chiffre'])}** ({ex['observe']:.1%} observé contre "
                    f"{ex['attendu']:.1%} attendu).")
    if dbl:
        st.markdown("##### Doublons potentiels de BC (même fournisseur, même montant à 0,5 % près, ≤ 7 jours)")
        st.dataframe(pd.DataFrame(dbl), hide_index=True, width="stretch")
    if len(fr):
        st.markdown("##### Fractionnements possibles (plusieurs BC le même jour, même fournisseur et chantier)")
        st.dataframe(fr.rename(columns={"four": "Fournisseur", "chantier": "Chantier", "date": "Date",
                                        "n": "Nombre de BC"}).assign(total=fr["total"].map(fmt_montant))
                     .rename(columns={"total": "Total HT"}), hide_index=True, width="stretch")
    if len(lgd):
        st.markdown("##### Lignes identiques en double dans un même BC")
        st.dataframe(lgd.rename(columns={"bc": "N° BC", "article": "Article", "fois": "Occurrences", "q": "Quantité",
                                         "pu": "Prix unitaire"}), hide_index=True, width="stretch")
    if len(we):
        with st.expander(f"BC datés un samedi ou un dimanche ({len(we)})"):
            st.dataframe(we.assign(date=we["date"].dt.strftime("%d/%m/%Y"), montant=we["montant"].map(fmt_montant))
                         .rename(columns={"bc": "N° BC", "date": "Date", "four": "Fournisseur",
                                          "chantier": "Chantier", "montant": "Montant HT"})
                         [["N° BC", "Date", "Fournisseur", "Chantier", "Montant HT"]], hide_index=True,
                         width="stretch")


# --------------------------------------------------------------------------- export
def _export(df: pd.DataFrame):
    if st.button("Préparer l'export Excel des statistiques", key="sta-exp"):
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as xw:
            df.drop(columns=["cle", "unite_n"]).assign(date=df["date"].dt.strftime("%Y-%m-%d")).to_excel(
                xw, sheet_name="Lignes", index=False)
            for nom, col, lib in (("ABC articles", "cle", "article"), ("ABC fournisseurs", "four", None),
                                  ("ABC familles", "famille", None)):
                abc(df, col, lib).drop(columns=["rang_pct"]).to_excel(xw, sheet_name=nom, index=False)
            x = xyz(df)
            abc(df, "cle", "article").merge(x, on="cle", how="left").to_excel(xw, sheet_name="ABC-XYZ", index=False)
            stats_prix(df).to_excel(xw, sheet_name="Prix", index=False)
            prix_atypiques(df).to_excel(xw, sheet_name="Prix atypiques", index=False)
            benford_test(df["montant"])[0].to_excel(xw, sheet_name="Benford", index=False)
        st.session_state["sta_xls"] = buf.getvalue()
    if st.session_state.get("sta_xls"):
        st.download_button("Télécharger les statistiques (Excel)", st.session_state["sta_xls"],
                           file_name="statistiques_achats.xlsx", key="sta-dl")
