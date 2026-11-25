"""Sourcing intelligent (Achats) et note des fournisseurs.

Pour chaque article d'une DA validée :
- base interne : fournisseurs ayant déjà vendu l'article (historique importé + BC de l'application), puis ceux de
  la même désignation / famille, avec prix passés et note fournisseur (délai, service, prix) ;
- Internet : nouveaux fournisseurs (Côte d'Ivoire, Afrique de l'Ouest, international) trouvés par la recherche web
  de Claude, chaque information avec son lien source ; les résultats sont gardés (table SOURCING) pour ne pas
  relancer la même recherche.
Ensuite : ajout des prospects à FOURNISSEURS, lancement du TCP avec les fournisseurs choisis, demandes de cotation.
"""
from __future__ import annotations

import difflib
import json
import re
import unicodedata
from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st

import ia
from logic import as_text, fmt_date, maintenant, nouvel_id, to_date, to_num
from schema import ZONES_SOURCING


# =========================================================================== outils
def _plat(t) -> str:
    t = unicodedata.normalize("NFKD", as_text(t).upper())
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"\b(SARL|SA|SAS|SARLU|SUARL|LTD|LIMITED|CO|COMPANY|ETS|ETABLISSEMENTS|GROUP|GROUPE|CI|CIV)\b", " ", t)
    return " ".join(re.sub(r"[^A-Z0-9]+", " ", t).split())


def meme_fournisseur(a: str, b: str) -> bool:
    pa, pb = _plat(a), _plat(b)
    if not pa or not pb:
        return False
    if pa == pb or (len(min(pa, pb, key=len)) >= 5 and (pa in pb or pb in pa)):
        return True
    return difflib.SequenceMatcher(None, pa, pb).ratio() >= 0.88


def _p(ctx, nom: str, defaut: float) -> float:
    from achats import params
    v = to_num(params(ctx).get(nom))
    return v if v else defaut


# =========================================================================== A. note fournisseur
@st.cache_resource(show_spinner=False, max_entries=2)
def _notes(version, _ctx) -> pd.DataFrame:
    import stats
    attendu = _p(_ctx, "Délai livraison attendu (jours)", 15)
    df = stats.base(_ctx)
    df = df.loc[~df["aberrant"]] if len(df) else df
    act = (df.groupby("four").agg(**{"Nb BC": ("bc", "nunique"), "Achats HT": ("montant", "sum"),
                                     "Dernier achat": ("date", "max")}) if len(df) else pd.DataFrame())
    bc, bl, lv = _ctx.load("BC"), _ctx.load("BC_LIGNES"), _ctx.load("LIVRAISONS")
    bc = bc.loc[bc["Statut"].map(as_text) == "Validé"].copy()
    bc["four"] = bc["Fournisseur"].map(lambda x: " ".join(as_text(x).upper().split()))
    bc["d"] = bc["Date BC"].map(to_date)
    first = lv.groupby(lv["ID_BC"].map(as_text))["Date livraison"].agg(
        lambda s: min((to_date(x) for x in s if to_date(x)), default=None))
    bc["d_liv"] = bc["ID_BC"].map(as_text).map(first)
    bc["delai"] = [(l - d).days if isinstance(l, date) and isinstance(d, date) else None
                   for d, l in zip(bc["d"], bc["d_liv"])]
    delai = bc.dropna(subset=["delai"]).groupby("four")["delai"].mean()
    # taux de service : BC validés depuis plus que le délai attendu
    vieux = set(bc.loc[bc["d"].map(lambda d: isinstance(d, date) and (date.today() - d).days > attendu).astype(bool),
                       "ID_BC"].map(as_text))
    l = bl.loc[bl["ID_BC"].map(as_text).isin(vieux).astype(bool) & (bl["Statut livraison"].map(as_text) != "Retirée")]
    l = l.assign(four=l["Fournisseur"].map(lambda x: " ".join(as_text(x).upper().split())),
                 c=l["Quantité"].map(to_num), v=l["Qté livrée"].map(to_num))
    l["v"] = l[["v", "c"]].min(axis=1)
    g = l.groupby("four")[["c", "v"]].sum()
    service = (g["v"] / g["c"].where(g["c"] > 0)).dropna()
    ecart = (bl.assign(four=bl["Fournisseur"].map(lambda x: " ".join(as_text(x).upper().split())),
                       e=bl["Écart prix %"].map(lambda x: to_num(x) if as_text(x) != "" else None))
             .dropna(subset=["e"]).groupby("four")["e"].mean())
    noms = sorted(set(act.index) | set(delai.index) | set(service.index) | set(ecart.index))
    t = pd.DataFrame(index=noms)
    t = t.join(act).join(delai.rename("Délai moyen (j)")).join(service.rename("Taux de service")) \
         .join(ecart.rename("Écart prix moyen %"))
    s_serv = t["Taux de service"].fillna(0.5)
    s_del = (1 - (t["Délai moyen (j)"] - 7).clip(lower=0) / 38).clip(0, 1).fillna(0.5)
    s_prix = (1 - t["Écart prix moyen %"].clip(lower=0) / 30).clip(0, 1).fillna(0.5)
    t["Note /100"] = (40 * s_serv + 30 * s_del + 30 * s_prix).round(0)
    t["Classe"] = pd.cut(t["Note /100"], [-1, 49.5, 74.5, 101], labels=["C", "B", "A"]).astype(str)
    t["Données"] = [", ".join(x for x, ok in (("service", pd.notna(a)), ("délai", pd.notna(b)), ("prix", pd.notna(c)))
                              if ok) or "historique seul" for a, b, c in
                    zip(t["Taux de service"], t["Délai moyen (j)"], t["Écart prix moyen %"])]
    return t.reset_index(names="Fournisseur")


def notes_fournisseurs(ctx) -> pd.DataFrame:
    return _notes(ctx.version(), ctx)


def note_de(notes: pd.DataFrame, nom: str) -> dict:
    n = " ".join(as_text(nom).upper().split())
    r = notes.loc[notes["Fournisseur"] == n]
    if not len(r):
        r = notes.loc[notes["Fournisseur"].map(lambda x: meme_fournisseur(x, n))]
    return r.iloc[0].to_dict() if len(r) else {}


def vue_notes(ctx):
    t = notes_fournisseurs(ctx)
    if not len(t):
        st.info("Pas encore de données d'achat pour noter les fournisseurs.")
        return
    st.caption("Note /100 = 40 % taux de service (quantités livrées / commandées, BC validés plus anciens que le "
               "délai attendu) + 30 % délai moyen de première livraison (≤ 7 j = plein, ≥ 45 j = 0) + 30 % prix "
               "(écart moyen au prix de référence : ≤ 0 % = plein, ≥ +30 % = 0). Critère sans donnée = 50 %. "
               "A ≥ 75, B ≥ 50, C < 50.")
    v = t.sort_values(["Note /100", "Achats HT"], ascending=False).copy()
    v["Dernier achat"] = v["Dernier achat"].map(lambda x: fmt_date(x) if pd.notna(x) else "")
    st.dataframe(v, hide_index=True, width="stretch", column_config={
        "Achats HT": st.column_config.NumberColumn(format="%.0f"),
        "Délai moyen (j)": st.column_config.NumberColumn(format="%.1f"),
        "Taux de service": st.column_config.ProgressColumn(min_value=0, max_value=1, format="percent"),
        "Écart prix moyen %": st.column_config.NumberColumn(format="%+.1f"),
        "Note /100": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f")})


# =========================================================================== base interne
def interne(ctx, l: dict, notes: pd.DataFrame, df: pd.DataFrame, fam_de: dict) -> pd.DataFrame:
    """Fournisseurs de la base pour un article : historique de l'article, même désignation, même famille,
    spécialités déclarées."""
    code, des, ref = as_text(l.get("Code Article")), as_text(l.get("Désignation")).upper(), as_text(l.get("Référence")).upper()
    d12 = pd.Timestamp(date.today() - timedelta(days=365))
    lignes = []

    def ajoute(sous: pd.DataFrame, origine: str):
        for four, g in sous.groupby("four"):
            g = g.sort_values("date")
            r12 = g.loc[g["date"] >= d12]
            lignes.append({"Fournisseur": four, "Origine": origine, "Dernier prix": g["pu"].iloc[-1],
                           "Meilleur prix (historique)": g["pu"].min(),
                           "Prix moyen (historique)": (g["montant"].sum() / g["q"].sum()) if g["q"].sum() > 0 else None,
                           "Meilleur prix 12 mois": r12["pu"].min() if len(r12) else None,
                           "Premier achat": g["date"].iloc[0],
                           "Unité": g["unite"].iloc[-1], "Nb achats": len(g),
                           "Dernier achat": g["date"].iloc[-1]})

    if len(df):
        art = df.loc[df["code"] == code] if code else df.loc[df["cle"] == "T:" + f"{des}|{ref}"]
        ajoute(art, "A déjà fourni cet article")
        vus = set(art["four"])
        meme = df.loc[(df["des"].str.upper() == des) & ~df["four"].isin(vus)]
        ajoute(meme, "Même désignation, autre référence")
        vus |= set(meme["four"])
        fam = fam_de.get(code, "")
        if fam:
            f = df.loc[(df["famille"] == fam) & ~df["four"].isin(vus)]
            top = f.groupby("four")["montant"].sum().nlargest(5).index
            for four in top:
                lignes.append({"Fournisseur": four, "Origine": f"Fournisseur de la famille {fam}",
                               "Nb achats": int((f["four"] == four).sum()),
                               "Dernier achat": f.loc[f["four"] == four, "date"].max()})
    fo = ctx.load("FOURNISSEURS")
    connus = {x["Fournisseur"] for x in lignes}
    mots = [m for m in re.split(r"\W+", des) if len(m) >= 4]
    for _, r in fo.iterrows():
        nom = " ".join(as_text(r["Nom"]).upper().split())
        spec = as_text(r.get("Spécialités")).upper()
        if nom and nom not in connus and spec and (any(m in spec for m in mots)
                                                    or (fam_de.get(code) and fam_de[code] in spec)):
            lignes.append({"Fournisseur": nom, "Origine": "Spécialité déclarée"
                           + (" (prospect)" if as_text(r.get("Statut")) == "Prospect" else "")})
    t = pd.DataFrame(lignes)
    if not len(t):
        return t
    for c in ("Dernier prix", "Meilleur prix (historique)", "Prix moyen (historique)", "Meilleur prix 12 mois",
              "Unité", "Nb achats", "Premier achat", "Dernier achat"):
        if c not in t:
            t[c] = None
    t["Note /100"] = [note_de(notes, f).get("Note /100") for f in t["Fournisseur"]]
    t["Classe"] = [note_de(notes, f).get("Classe", "") for f in t["Fournisseur"]]
    for c in ("Premier achat", "Dernier achat"):
        t[c] = t[c].map(lambda x: fmt_date(x) if pd.notna(x) else "")
    return t


# =========================================================================== Internet
_OUTIL_FOURNISSEURS = {
    "name": "proposer_fournisseurs",
    "description": "Fournisseurs trouvés sur le web pour l'article.",
    "input_schema": {"type": "object", "properties": {"fournisseurs": {"type": "array", "items": {
        "type": "object", "properties": {
            "nom": {"type": "string"}, "pays": {"type": "string"}, "ville": {"type": "string"},
            "zone": {"type": "string", "enum": ZONES_SOURCING},
            "type": {"type": "string", "enum": ["Fabricant", "Distributeur / importateur", "Revendeur",
                                                "Plateforme en ligne", "Autre"]},
            "telephone": {"type": "string"}, "email": {"type": "string"}, "site_web": {"type": "string"},
            "produits": {"type": "string", "description": "ce qu'il propose en rapport avec l'article"},
            "prix_indicatif": {"type": "string", "description": "prix vu sur la source, avec devise, sinon vide"},
            "delai_import": {"type": "string", "description": "hors Côte d'Ivoire : délai et mode de transport "
                                                              "estimés jusqu'à Abidjan"},
            "source_url": {"type": "string", "description": "page web où l'information a été trouvée"},
            "fiabilite": {"type": "string", "enum": ["haute", "moyenne", "faible"]},
            "remarque": {"type": "string"}},
        "required": ["nom", "pays", "zone", "source_url", "fiabilite"]}},
        "synthese": {"type": "string", "description": "2 phrases : disponibilité locale, intérêt de l'import"}},
        "required": ["fournisseurs"]}}


def cle_article(l: dict) -> str:
    code = as_text(l.get("Code Article"))
    return code or "T:" + f"{as_text(l.get('Désignation')).upper()}|{as_text(l.get('Référence')).upper()}"


def cache(ctx, cle: str, zones: list[str]) -> dict | None:
    t = ctx.load("SOURCING")
    t = t.loc[(t["Clé article"].map(as_text) == cle) & (t["Zones"].map(as_text) == " + ".join(zones))]
    if not len(t):
        return None
    r = t.sort_values("Date").iloc[-1]
    try:
        d = datetime.fromisoformat(as_text(r["Date"])[:19])
    except ValueError:
        return None
    if (datetime.now() - d).days > _p(ctx, "Sourcing : validité des résultats (jours)", 60):
        return None
    try:
        return {**json.loads(as_text(r["Résultats"])), "date": as_text(r["Date"])[:10], "par": as_text(r["Par"])}
    except ValueError:
        return None


def chercher_web(ctx, l: dict, zones: list[str]) -> dict:
    fo = ctx.load("FOURNISSEURS")
    connus = [as_text(n) for n in fo["Nom"] if as_text(n)]
    consigne = {
        "article": {"designation": as_text(l.get("Désignation")), "reference": as_text(l.get("Référence")),
                    "quantite": float(to_num(l.get("Reste à commander")) or to_num(l.get("Quantité")) or 0),
                    "unite": as_text(l.get("Colisage")), "usage": as_text(l.get("Usage")),
                    "engin": as_text(l.get("Code_Parc"))},
        "zones_a_couvrir": zones,
        "fournisseurs_deja_connus_a_exclure": connus[:300]}
    sysm = (ia.CONTEXTE_PWC + " Tu es acheteur sourcing. Trouve de NOUVEAUX fournisseurs réels pour l'article, "
            "dans les zones demandées : Côte d'Ivoire d'abord (Abidjan, San-Pedro…), puis Afrique de l'Ouest "
            "(Ghana, Sénégal, Burkina, Mali, Togo, Bénin, Nigeria), puis International (Chine, Turquie, Émirats, "
            "Europe, Inde) pour les pièces d'engins et équipements. Privilégie fabricants, distributeurs agréés et "
            "importateurs établis. N'invente rien : chaque fournisseur doit venir d'une page consultée, indiquée "
            "dans source_url ; laisse vide un contact non trouvé. 3 à 8 fournisseurs au total, en excluant les "
            "fournisseurs déjà connus.")
    res, urls, nb = ia.recherche_web(sysm, json.dumps(consigne, ensure_ascii=False), _OUTIL_FOURNISSEURS,
                                     int(_p(ctx, "Sourcing : recherches web max par article", 3)))
    out = []
    for f in res.get("fournisseurs", [])[:10]:
        nom = " ".join(as_text(f.get("nom")).upper().split())
        if not nom:
            continue
        deja = next((c for c in connus if meme_fournisseur(c, nom)), "")
        src = as_text(f.get("source_url"))
        verifie = any(src and (src.rstrip("/") in u or u.rstrip("/") in src) for u in urls) if urls else False
        out.append({**{k: as_text(v) for k, v in f.items()}, "nom": nom, "deja_en_base": deja,
                    "source_verifiee": verifie})
    r = {"fournisseurs": out, "synthese": as_text(res.get("synthese")), "nb_recherches": nb}
    ctx.get_store().append("SOURCING", [{
        "ID_Sourcing": nouvel_id(10), "Clé article": cle_article(l), "Désignation": as_text(l.get("Désignation")),
        "Référence": as_text(l.get("Référence")), "Zones": " + ".join(zones), "Date": maintenant(),
        "Résultats": json.dumps(r, ensure_ascii=False)[:45000], "Nb recherches": nb, "Par": ctx.user["Nom"]}])
    return {**r, "date": maintenant()[:10], "par": ctx.user["Nom"]}


def ajouter_prospects(ctx, props: list[dict]) -> int:
    f = ctx.load("FOURNISSEURS")
    connus = [as_text(n) for n in f["Nom"]]
    codes = [as_text(c) for c in f["Code Fournisseur"]]
    n = max([int(c[1:]) for c in codes if c[1:].isdigit()] + [0])
    rows = []
    for p in props:
        if any(meme_fournisseur(c, p["nom"]) for c in connus):
            continue
        n += 1
        rows.append({"Code Fournisseur": f"F{n:04d}", "Nom": p["nom"], "Téléphone": as_text(p.get("telephone")),
                     "Email": as_text(p.get("email")), "Adresse": as_text(p.get("ville")), "Actif": "TRUE",
                     "Spécialités": as_text(p.get("produits"))[:300], "Statut": "Prospect",
                     "Pays": as_text(p.get("pays")), "Ville": as_text(p.get("ville")),
                     "Site web": as_text(p.get("site_web")), "Source": as_text(p.get("source_url")),
                     "Ajouté le": maintenant()})
        connus.append(p["nom"])
    if rows:
        ctx.get_store().append("FOURNISSEURS", rows)
        ctx.refresh()
    return len(rows)


# =========================================================================== écran
def _preparer_tcp(ctx, id_da: str, noms: list[str], nouveaux: list[dict]):
    """Callback (avant l'affichage) : prépare le TCP avec les fournisseurs choisis."""
    from achats import _ajoute_fournisseurs
    ajouter_prospects(ctx, nouveaux)
    _ajoute_fournisseurs(ctx, noms)
    ctx.refresh()
    st.session_state["tcp-mode"] = "Nouveau TCP"
    st.session_state["tcp-da"] = id_da
    import tcp_ia
    S = tcp_ia._etat(id_da)
    for n in noms[:12]:
        tcp_ia._ajouter(S, n)
    tcp_ia._bump(S)
    st.session_state.src_msg = ("TCP prêt : ouvrez l'onglet « Tableaux comparatifs (TCP) » — la DA et les "
                                "fournisseurs sont déjà renseignés.")
    st.session_state.src_tcp_pret = True


def vue_sourcing(ctx):
    from achats import _choix_da, suivi_lignes
    import stats
    st.caption("Pour chaque article de la DA : fournisseurs de notre base (prix passés, note), puis nouveaux "
               "fournisseurs trouvés sur Internet par l'IA. Choisissez ensuite qui consulter (TCP, demande de prix).")
    if st.session_state.get("src_msg"):
        st.success(st.session_state.pop("src_msg"))
    da = _choix_da(ctx, "src-da")
    if not da:
        return
    sl = suivi_lignes(ctx)
    lignes = sl.loc[(sl["ID_DA"].map(as_text) == da["_id"]) & (sl["Reste à commander"] > 0)].sort_values("N° Ligne")
    if not len(lignes):
        st.info("Tout est déjà commandé sur cette DA.")
        return
    notes = notes_fournisseurs(ctx)
    df = stats.base(ctx)
    df = df.loc[~df["aberrant"]] if len(df) else df
    art = ctx.load("ARTICLES")
    fam_de = dict(zip(art["Code Article"].map(as_text), art["Famille"].map(as_text)))
    recs = lignes.to_dict("records")
    lab = [f"{as_text(l['Désignation'])}" + (f" · {as_text(l['Référence'])}" if as_text(l["Référence"]) else "")
           + f" — {to_num(l['Reste à commander']):g} {as_text(l['Colisage'])}" for l in recs]

    c1, c2 = st.columns([3, 2])
    choisis = c1.multiselect("Articles", range(len(recs)), default=list(range(len(recs))), format_func=lambda i: lab[i],
                             key=f"src-art-{da['_id']}")
    zones = c2.multiselect("Zones de recherche web", ZONES_SOURCING, default=ZONES_SOURCING, key="src-zones")
    sel_k = f"src-sel-{da['_id']}"
    selection = st.session_state.setdefault(sel_k, {})  # nom -> {pays, email}
    web_k = f"src-web-{da['_id']}"
    web = st.session_state.setdefault(web_k, {})

    if ia.disponible():
        a, b = st.columns([2, 1])
        relancer = b.checkbox("Ignorer les résultats récents", key="src-force",
                              help="Relance la recherche même si l'article a été cherché récemment (coût IA)")
        nmax = int(_p(ctx, "Sourcing : articles max par lancement", 10))
        nrech = int(_p(ctx, "Sourcing : recherches web max par article", 3))
        en_cache = 0 if relancer else sum(1 for i in choisis if cache(ctx, cle_article(recs[i]), zones))
        a_chercher = max(0, min(len(choisis), nmax) - en_cache)
        st.caption(f"💰 Estimation : {a_chercher} article(s) à chercher × {nrech} recherche(s) max ≈ "
                   f"{0.10 * nrech / 3 * a_chercher:.2f} à {0.20 * nrech / 3 * a_chercher:.2f} $"
                   + (f" ({en_cache} déjà en mémoire, gratuits)" if en_cache else "")
                   + (f" — limité à {nmax} articles par lancement" if len(choisis) > nmax else ""))
        if a.button(f"🌐 Rechercher de nouveaux fournisseurs sur Internet ({min(len(choisis), nmax)} article(s))",
                    type="primary", disabled=not choisis or not zones, key=f"src-go-{da['_id']}"):
            choisis = choisis[:nmax]
            barre = st.progress(0.0, text="Recherche web…")
            for n, i in enumerate(choisis):
                l = recs[i]
                barre.progress(n / len(choisis), text=f"Recherche : {lab[i]}")
                r = None if relancer else cache(ctx, cle_article(l), zones)
                try:
                    web[i] = r or chercher_web(ctx, l, zones)
                    web[i]["cache"] = bool(r)
                except ia.IAErreur as e:
                    st.error(f"{lab[i]} : {e}")
            barre.empty()
            ctx.refresh(False)
    else:
        st.caption("Recherche Internet indisponible : IA non configurée (section [ia] des secrets).")

    for i in choisis:
        l = recs[i]
        with st.expander(f"**{lab[i]}**", expanded=len(choisis) <= 3):
            t = interne(ctx, l, notes, df, fam_de)
            st.markdown("###### Base interne")
            if len(t):
                t.insert(0, "Consulter", t["Fournisseur"].isin(selection))
                ed = st.data_editor(t, hide_index=True, width="stretch", key=f"src-int-{da['_id']}-{i}",
                                    disabled=[c for c in t.columns if c != "Consulter"],
                                    column_config={"Consulter": st.column_config.CheckboxColumn("✓", width="small"),
                                                   "Dernier prix": st.column_config.NumberColumn(format="%.0f"),
                                                   "Meilleur prix (historique)": st.column_config.NumberColumn(format="%.0f"),
                                                   "Prix moyen (historique)": st.column_config.NumberColumn(format="%.0f"),
                                                   "Meilleur prix 12 mois": st.column_config.NumberColumn(format="%.0f"),
                                                   "Note /100": st.column_config.NumberColumn(format="%.0f")})
                for f_, c in zip(ed["Fournisseur"], ed["Consulter"]):
                    if c:
                        selection.setdefault(f_, {"pays": "Côte d'Ivoire"})
                    else:
                        selection.pop(f_, None)
            else:
                st.caption("Aucun fournisseur connu pour cet article ni sa famille.")
            r = web.get(i)
            if r:
                st.markdown("###### Internet" + (f" — résultats du {fmt_date(r.get('date'))}"
                                                  f"{' (gardés en mémoire)' if r.get('cache') else ''}"))
                if r.get("synthese"):
                    st.caption(r["synthese"])
                w = pd.DataFrame(r.get("fournisseurs") or [])
                if not len(w):
                    st.caption("Aucun nouveau fournisseur trouvé.")
                    continue
                for c in ("ville", "type", "telephone", "email", "site_web", "produits", "prix_indicatif",
                          "delai_import", "remarque", "deja_en_base"):
                    if c not in w:
                        w[c] = ""
                v = pd.DataFrame({
                    "Consulter": w["nom"].isin(selection), "Fournisseur": w["nom"], "Zone": w["zone"],
                    "Pays / ville": (w["pays"] + " · " + w["ville"]).str.strip(" ·"), "Type": w["type"],
                    "Produits": w["produits"], "Prix vu": w["prix_indicatif"], "Délai import": w["delai_import"],
                    "Téléphone": w["telephone"], "Email": w["email"], "Site": w["site_web"],
                    "Source": w["source_url"],
                    "Fiabilité": w["fiabilite"] + w["source_verifiee"].map(lambda x: "" if x else " ⚠ source à vérifier"),
                    "Déjà en base": w["deja_en_base"]})
                ed = st.data_editor(v, hide_index=True, width="stretch", key=f"src-web-{da['_id']}-{i}-{r.get('date')}",
                                    disabled=[c for c in v.columns if c != "Consulter"],
                                    column_config={"Consulter": st.column_config.CheckboxColumn("✓", width="small"),
                                                   "Site": st.column_config.LinkColumn(),
                                                   "Source": st.column_config.LinkColumn(display_text="ouvrir")})
                for (_, x), c in zip(w.iterrows(), ed["Consulter"]):
                    nom = " ".join(as_text(x["deja_en_base"] or x["nom"]).upper().split())
                    if c:
                        selection.setdefault(nom, {**x.to_dict(), "web": not x["deja_en_base"]})
                    else:
                        selection.pop(nom, None)

    # ---- consultation
    st.markdown("##### Fournisseurs à consulter")
    if not selection:
        st.caption("Cochez ✓ les fournisseurs à consulter dans les tableaux ci-dessus.")
        return
    noms = list(selection)
    st.write(", ".join(f"**{n}**" + (" (nouveau)" if selection[n].get("web") else "") for n in noms))
    nouveaux = [selection[n] for n in noms if selection[n].get("web")]
    c1, c2, c3 = st.columns(3)
    if nouveaux and c1.button(f"➕ Ajouter {len(nouveaux)} prospect(s) à la base", key="src-add"):
        k = ajouter_prospects(ctx, nouveaux)
        for p in nouveaux:
            p["web"] = False
        st.session_state.src_msg = f"{k} prospect(s) ajouté(s) à la base fournisseurs (statut « Prospect »)."
        st.rerun()
    c2.button(f"📊 Lancer le TCP avec ces {min(len(noms), 12)} fournisseurs", key="src-tcp", type="primary",
              on_click=_preparer_tcp, args=(ctx, da["_id"], noms, nouveaux))
    if st.session_state.pop("src_tcp_pret", False):
        st.rerun(scope="app")
    if ia.disponible() and c3.button("✉️ Rédiger les demandes de cotation", key="src-rfq"):
        fo = ctx.load("FOURNISSEURS")
        mails = dict(zip(fo["Nom"].map(lambda x: as_text(x).upper()), fo["Email"].map(as_text)))
        with st.spinner("Rédaction des demandes de prix…"):
            try:
                st.session_state[f"src-rfq-{da['_id']}"] = [
                    {**e, "email": as_text(selection.get(e["fournisseur"], {}).get("email"))
                     or mails.get(as_text(e["fournisseur"]).upper(), "")}
                    for e in ia.demandes_cotation(
                        [{"nom": n, "pays": as_text(selection[n].get("pays")) or "Côte d'Ivoire"} for n in noms],
                        [{"designation": as_text(recs[i]["Désignation"]), "reference": as_text(recs[i]["Référence"]),
                          "quantite": float(to_num(recs[i]["Reste à commander"])), "unite": as_text(recs[i]["Colisage"])}
                         for i in choisis],
                        {"nom": ctx.user["Nom"], "societe": "PWC (Public Work Contractor), Abidjan",
                         "reference_da": as_text(da.get("N° DA"))})]
            except ia.IAErreur as e:
                st.error(str(e))
    for j, e in enumerate(st.session_state.get(f"src-rfq-{da['_id']}") or []):
        with st.expander(f"✉️ {e['fournisseur']} — {e['objet']}"):
            st.text_area("Email", f"Objet : {e['objet']}\n\n{e['corps']}", height=280, key=f"src-rfq-t-{j}",
                         label_visibility="collapsed")
            if e.get("email"):
                from urllib.parse import quote
                st.link_button(f"Ouvrir dans la messagerie ({e['email']})",
                               f"mailto:{e['email']}?subject={quote(e['objet'])}&body={quote(e['corps'][:1500])}")
    if st.button("Vider la sélection", key="src-raz"):
        st.session_state[sel_k] = {}
        st.rerun()
