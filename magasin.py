"""Magasin : gestion de stock simple, un magasin par chantier.

Le magasinier (rôle « Magasinier », chantier = son magasin) ne voit que son magasin :
- 📥 Réception : import du BL (photo / PDF), lecture IA, BC attendu du chantier, confirmation → livraison
  enregistrée sur le BC et la DA (circuit achats) ET entrée en stock ;
- 📤 Sortie : texte libre ou dicté (« 2 filtres à gasoil pour la niveleuse 3, remis à Konan ») → l'IA retrouve
  l'article dans SON stock et l'engin → confirmation → sortie (stock disponible contrôlé) ;
- 📦 Mon stock : quantités, alertes, historique ;
- 🔁 Transfert : envoi vers un autre magasin, réception confirmée par le magasin destinataire ;
- 📋 Inventaire : comptage (saisi, dicté ou importé d'Excel) → ajustements ; le premier inventaire = stock initial ;
- ❓ Aide.
Toute réception enregistrée dans l'application (magasinier, acheteur, admin) crée l'entrée en stock du magasin du
chantier du BC. Le stock = somme des mouvements (table STOCK_MOUVEMENTS) : traçabilité complète.
Admin, Direction et Acheteur consultent les stocks de tous les magasins (onglet « Stocks »).
"""
from __future__ import annotations

import io
import json
from datetime import date, timedelta

import pandas as pd
import streamlit as st

import ia
from logic import as_text, fmt_date, fmt_montant, maintenant, nouvel_id, to_date, to_num
from schema import TYPES_MOUVEMENT


def _cle(code, des, ref) -> str:
    c = as_text(code)
    return c or "T:" + f"{' '.join(as_text(des).upper().split())}|{' '.join(as_text(ref).upper().split())}"


# =========================================================================== calcul du stock
def _signe(type_) -> int:
    """+1 entrée, -1 sortie, 0 ajustement (quantité signée)."""
    t = as_text(type_)
    if t in TYPES_MOUVEMENT:
        return TYPES_MOUVEMENT[t]
    b = t.lower()
    if "ajustement" in b:
        return 0
    return -1 if any(x in b for x in ("sortie", "perte", "transfert vers")) else 1

@st.cache_resource(show_spinner=False, max_entries=2)
def _mouvements(version, _ctx) -> pd.DataFrame:
    m = _ctx.load("STOCK_MOUVEMENTS").copy()
    if not len(m):
        return pd.DataFrame(columns=list(m.columns) + ["q", "cle", "d"])
    m["q"] = [abs(to_num(q)) * _signe(t) if _signe(t) else to_num(q) for q, t in zip(m["Quantité"], m["Type"])]
    m["cle"] = [_cle(c, d, r) for c, d, r in zip(m["Code Article"], m["Désignation"], m["Référence"])]
    m["d"] = pd.to_datetime(m["Date"].map(to_date), errors="coerce")
    m["Magasin"] = m["Magasin"].map(as_text)
    return m


def mouvements(ctx) -> pd.DataFrame:
    return _mouvements(ctx.version(), ctx)


@st.cache_resource(show_spinner=False, max_entries=2)
def _stock(version, _ctx) -> pd.DataFrame:
    m = _mouvements(version, _ctx)
    if not len(m):
        return pd.DataFrame(columns=["Magasin", "cle", "Code Article", "Désignation", "Référence", "Unité", "Stock",
                                     "Sorties 30 j", "Dernier mouvement"])
    d30 = pd.Timestamp(date.today() - timedelta(days=30))
    m = m.sort_values("d")
    g = m.groupby(["Magasin", "cle"], sort=False)
    t = g.agg(**{"Code Article": ("Code Article", "last"), "Désignation": ("Désignation", "last"),
                 "Référence": ("Référence", "last"), "Unité": ("Unité", "last"), "Stock": ("q", "sum"),
                 "Dernier mouvement": ("d", "max")}).reset_index()
    s30 = (m.loc[(m["d"] >= d30) & (m["Type"].map(as_text) == "Sortie")].groupby(["Magasin", "cle"])["q"].sum() * -1)
    t["Sorties 30 j"] = [float(s30.get((a, b), 0.0)) for a, b in zip(t["Magasin"], t["cle"])]
    t["Stock"] = t["Stock"].round(3)
    return t


def stock(ctx, magasin: str | None = None) -> pd.DataFrame:
    t = _stock(ctx.version(), ctx)
    return t.loc[t["Magasin"] == magasin].copy() if magasin else t.copy()


def disponible(ctx, code: str) -> list[tuple[str, float]]:
    """[(magasin, quantité)] pour un code article en stock (> 0)."""
    t = stock(ctx)
    t = t.loc[(t["Code Article"].map(as_text) == as_text(code)) & (t["Stock"] > 0)]
    return list(zip(t["Magasin"], t["Stock"]))


def _valeur_unitaire(ctx, code, des, ref, unite) -> float:
    import prix
    s = prix.ref_article(ctx, code, des, ref, unite)
    return round(float(s["moyenne"]), 2) if s else 0.0


def _mvt(ctx, magasin, type_, art: dict, qte: float, **kw) -> dict:
    return {"ID_Mouvement": nouvel_id(10), "Date": kw.pop("date", date.today()).isoformat(), "Magasin": magasin,
            "Type": type_, "Code Article": as_text(art.get("Code Article")), "Désignation": as_text(art.get("Désignation")),
            "Référence": as_text(art.get("Référence")), "Unité": as_text(art.get("Unité")), "Quantité": float(qte),
            "Saisi par": ctx.user["Nom"], "Créé le": maintenant(), **kw}


# =========================================================================== lien achats : réceptions → entrées
def entrees_reception(ctx, rows: list[dict]):
    """Appelé à chaque enregistrement de livraison (LIVRAISONS) : entrée en stock au magasin du chantier."""
    if not rows:
        return
    bl = ctx.load("BC_LIGNES")
    par_id = {as_text(i): r for i, r in zip(bl["ID_LigneBC"], bl.to_dict("records"))}
    out = []
    for r in rows:
        l = par_id.get(as_text(r.get("ID_LigneBC")), {})
        mag = as_text(r.get("Chantier")) or as_text(l.get("Chantier"))
        if not mag or to_num(r.get("Qté livrée")) <= 0:
            continue
        art = {"Code Article": l.get("Code Article"), "Désignation": r.get("Désignation"),
               "Référence": r.get("Référence"), "Unité": r.get("Unité")}
        pu = to_num(l.get("Prix unitaire HT")) * (1 - to_num(l.get("Remise %")) / 100)
        out.append(_mvt(ctx, mag, "Entrée réception", art, to_num(r.get("Qté livrée")),
                        date=to_date(r.get("Date livraison")) or date.today(), ID_BC=as_text(r.get("ID_BC")),
                        **{"N° BC": as_text(r.get("N° BC")), "N° BL": as_text(r.get("N° BL")),
                           "ID_Livraison": as_text(r.get("ID_Livraison")), "Motif": "Réception fournisseur",
                           "Bénéficiaire": as_text(r.get("Fournisseur")), "Valeur unitaire": round(pu, 2)}))
    if out:
        ctx.get_store().append("STOCK_MOUVEMENTS", out)


# =========================================================================== écran magasinier
GUIDE = """Module Magasin de l'application PWC (un magasin par chantier ; le magasinier ne voit que le sien) :
- Réception : bouton « 📥 Réception », prendre le bon de livraison (BL) en photo ou importer le PDF, « Lire » ;
  l'application retrouve le bon de commande (BC), propose les quantités ; vérifier puis « Enregistrer la réception ».
  Le stock augmente et les achats voient la livraison. Un même BL ne peut pas être enregistré deux fois.
- Sortie : « 📤 Sortie », écrire ou dicter (micro du clavier) ce qui sort, l'engin, la personne qui reçoit et
  pourquoi ; vérifier le tableau puis « Enregistrer les sorties ». On ne peut pas sortir plus que le stock.
- Mon stock : quantités disponibles, articles à zéro, historique des mouvements.
- Transfert : envoyer des articles à un autre magasin ; l'autre magasinier confirme la réception.
- Inventaire : compter les articles et saisir les quantités comptées (ou dicter, ou importer un fichier Excel) ;
  l'application corrige le stock. Le premier inventaire sert de stock de départ.
- Un article qui n'est pas dans la liste : prévenir l'administrateur (catalogue)."""


def _magasin_de(ctx) -> str:
    return as_text(ctx.user.get("Chantier"))


def onglet_magasin(ctx, magasin: str | None = None):
    mag = magasin or _magasin_de(ctx)
    noms = ctx.refs()["chantiers"]
    if not mag:
        st.warning("Aucun magasin rattaché à votre compte : demandez à l'administrateur de renseigner votre chantier.")
        return
    from assistant_da import salutation
    t = stock(ctx, mag)
    if not magasin:
        st.markdown(f"**{salutation(ctx.user)}, bienvenue au magasin de {noms.get(mag, mag)}.** 👋")
    nt = _transferts_a_recevoir(ctx, mag)
    nt = nt["ID_Transfert"].nunique() if len(nt) else 0
    st.markdown(f"📦 **{int((t['Stock'] > 0).sum())}** article(s) en stock · 🔴 **{int((t['Stock'] <= 0).sum())}** à zéro "
                f"· 🚚 **{_nb_bc_attendus(ctx, mag)}** BC attendu(s) · 🔁 **{nt}** transfert(s) à recevoir")
    if st.session_state.get("mag_msg"):
        st.success(st.session_state.pop("mag_msg"))
    vues = ["📥 Réception", "📤 Sortie", "📦 Mon stock", "🔁 Transfert", "📋 Inventaire", "❓ Aide"]
    v = st.segmented_control("Magasin", vues, default="📦 Mon stock", key=f"mag-vue-{mag}",
                             label_visibility="collapsed") or "📦 Mon stock"
    if v == "📥 Réception":
        import bl
        bl.onglet_bl(ctx, chantier=mag)
    elif v == "📤 Sortie":
        _sorties(ctx, mag)
    elif v == "📦 Mon stock":
        _mon_stock(ctx, mag)
    elif v == "🔁 Transfert":
        _transferts(ctx, mag)
    elif v == "📋 Inventaire":
        _inventaire(ctx, mag)
    else:
        from assistant_da import _aide
        if ia.disponible():
            _aide(ctx.user, GUIDE, cle=f"mag-{mag}", role_ia="l'assistant des magasiniers de chantier",
                  exemple="Ex. : comment je fais une sortie ? comment enregistrer un bon de livraison ?")
        else:
            st.markdown(GUIDE)


def _nb_bc_attendus(ctx, mag) -> int:
    from achats import _suivi_bc
    bc, _ = _suivi_bc(ctx)
    return int(((bc["Chantier"].map(as_text) == mag) & (bc["Livraison"] != "Livré")
                & (bc["Statut"].map(as_text) == "Validé")).sum()) if len(bc) else 0


# ---------------------------------------------------------------- mon stock
def _mon_stock(ctx, mag):
    t = stock(ctx, mag)
    if not len(t):
        st.info("Aucun mouvement pour ce magasin. Commencez par un inventaire (📋) pour saisir le stock de départ, "
                "ou par une réception (📥).")
        return
    q = st.text_input("Rechercher un article", key=f"mag-q-{mag}", placeholder="pneu, ciment, filtre…")
    v = t.copy()
    if q.strip():
        v = v.loc[(v["Désignation"].map(as_text) + " " + v["Référence"].map(as_text)).str.contains(q.strip(), case=False,
                                                                                               regex=False)]
    v["Couverture (j)"] = [round(s / (c / 30), 0) if c > 0 and s > 0 else None for s, c in zip(v["Stock"], v["Sorties 30 j"])]
    v["État"] = ["🔴 Rupture" if s <= 0 else ("🟠 Bas" if c and s / (c / 30) < 7 else "🟢") for s, c in
                 zip(v["Stock"], v["Sorties 30 j"])]
    v["Dernier mouvement"] = v["Dernier mouvement"].map(lambda x: fmt_date(x) if pd.notna(x) else "")
    st.dataframe(v[["État", "Désignation", "Référence", "Stock", "Unité", "Sorties 30 j", "Couverture (j)",
                    "Dernier mouvement", "Code Article"]].sort_values(["Désignation", "Référence"]),
                 hide_index=True, width="stretch",
                 column_config={"Stock": st.column_config.NumberColumn(format="%g"),
                                "Sorties 30 j": st.column_config.NumberColumn(format="%g")})
    st.caption("🟠 Bas = moins de 7 jours de consommation au rythme des 30 derniers jours.")
    with st.expander("Historique des mouvements"):
        m = mouvements(ctx)
        m = m.loc[m["Magasin"] == mag].sort_values("Créé le", ascending=False).head(300)
        st.dataframe(m[["Date", "Type", "Désignation", "Référence", "Quantité", "Unité", "Code_Parc", "Bénéficiaire",
                        "Motif", "N° BC", "N° BL", "Saisi par"]].assign(Date=m["Date"].map(fmt_date)),
                     hide_index=True, width="stretch")
    buf = io.BytesIO()
    t.drop(columns=["cle"]).to_excel(buf, index=False, sheet_name="STOCK")
    st.download_button("Exporter le stock (Excel)", buf.getvalue(), file_name=f"stock_{mag}_{date.today()}.xlsx",
                       key=f"mag-xls-{mag}")


# ---------------------------------------------------------------- sorties
_OUTIL_SORTIES = {
    "name": "extraire_mouvements",
    "description": "Articles et quantités mentionnés.",
    "input_schema": {"type": "object", "properties": {"lignes": {"type": "array", "items": {
        "type": "object", "properties": {
            "designation": {"type": "string"}, "caracteristiques": {"type": "string"},
            "quantite": {"type": "number"}, "engin": {"type": "string", "description": "engin / véhicule tel que dit"},
            "beneficiaire": {"type": "string", "description": "personne ou équipe qui reçoit"},
            "motif": {"type": "string", "description": "usage / raison"}},
        "required": ["designation", "quantite"]}}}, "required": ["lignes"]}}


def _articles_stock(t: pd.DataFrame) -> pd.DataFrame:
    """Le stock du magasin au format catalogue (pour la recherche d'articles)."""
    return pd.DataFrame({"Code Article": t["cle"], "Désignation": t["Désignation"], "Référence": t["Référence"],
                         "Famille": "", "Colisage": t["Unité"]})


def _libelle(r) -> str:
    return (f"{as_text(r['Désignation'])}" + (f" · {as_text(r['Référence'])}" if as_text(r["Référence"]) else "")
            + f" — dispo {to_num(r['Stock']):g} {as_text(r['Unité'])}")


def _engin(ctx, texte: str) -> str:
    t = as_text(texte).upper()
    if not t:
        return ""
    p = ctx.load("PARC")
    for c, d in zip(p["Code_Parc"].map(as_text), p["Désignation engin"].map(as_text)):
        if c and (c.upper() == t or c.upper() in t.replace(" ", "") or t in c.upper()):
            return c
    return t


def _sorties(ctx, mag):
    t = stock(ctx, mag)
    t = t.loc[t["Stock"] > 0]
    if not len(t):
        st.info("Aucun article en stock dans ce magasin.")
        return
    lab = {r["cle"]: _libelle(r) for _, r in t.iterrows()}
    panier = st.session_state.setdefault(f"mag-sort-{mag}", [])
    v = st.session_state.get(f"mag-sort-v-{mag}", 0)
    if ia.disponible():
        with st.container(border=True):
            st.markdown("**🎙️ Dites ou écrivez ce qui sort** (touche 🎤 du clavier pour dicter)")
            txt = st.text_area("Sortie", key=f"mag-s-txt-{mag}-{v}", height=80, label_visibility="collapsed",
                               placeholder="Ex. : 2 filtres à gasoil pour la niveleuse NIV-03, remis à Konan, "
                                           "et 5 sacs de ciment pour le dallage")
            if st.button("✨ Préparer la sortie", type="primary", key=f"mag-s-go-{mag}-{v}", disabled=not txt.strip()):
                with st.spinner("Recherche des articles dans votre stock…"):
                    try:
                        r = ia.structure(ia.CONTEXTE_PWC + " Un magasinier de chantier décrit une sortie de stock. "
                                         "Extrais chaque article sorti, sa quantité, l'engin, la personne qui reçoit "
                                         "et le motif.", txt, _OUTIL_SORTIES, rapide=True)
                        arts = _articles_stock(t)
                        for l in r.get("lignes", []):
                            c = ia.candidats(arts, f"{l.get('designation', '')} {l.get('caracteristiques', '')}", 1,
                                             ("stock", mag, ctx.version()))
                            panier.append({"cle": c[0]["code"] if c else None, "Quantité": float(to_num(l.get("quantite"))),
                                           "Code_Parc": _engin(ctx, l.get("engin")),
                                           "Bénéficiaire": as_text(l.get("beneficiaire")),
                                           "Motif": as_text(l.get("motif")), "Dit": as_text(l.get("designation"))})
                        st.session_state[f"mag-sort-v-{mag}"] = v + 1
                        st.rerun()
                    except ia.IAErreur as e:
                        st.error(str(e))
    with st.expander("➕ Ajouter un article à la main", expanded=not ia.disponible() and not panier):
        with st.form(f"mag-s-f-{mag}-{len(panier)}", clear_on_submit=True):
            c = st.selectbox("Article", list(lab), format_func=lab.get, index=None, placeholder="Chercher dans mon stock")
            a, b = st.columns(2)
            q = a.number_input("Quantité", min_value=0.0, step=1.0, value=1.0)
            parc = b.text_input("Engin (code parc)", placeholder="Facultatif")
            ben = a.text_input("Remis à")
            mot = b.text_input("Motif / usage")
            if st.form_submit_button("Ajouter") and c:
                panier.append({"cle": c, "Quantité": q, "Code_Parc": parc.strip().upper(), "Bénéficiaire": ben.strip(),
                               "Motif": mot.strip(), "Dit": ""})
                st.rerun()
    if not panier:
        return
    st.markdown("##### Sortie à enregistrer — vérifiez")
    g = pd.DataFrame(panier)
    g["Article"] = g["cle"].map(lambda c: lab.get(c))
    ed = st.data_editor(g[["Article", "Quantité", "Code_Parc", "Bénéficiaire", "Motif", "Dit"]], hide_index=True,
                        width="stretch", num_rows="dynamic", key=f"mag-s-ed-{mag}-{v}-{len(panier)}",
                        column_config={"Article": st.column_config.SelectboxColumn(options=list(lab.values()),
                                                                                   required=True, width="large"),
                                       "Quantité": st.column_config.NumberColumn(min_value=0.0, format="%g"),
                                       "Code_Parc": st.column_config.TextColumn("Engin"),
                                       "Bénéficiaire": st.column_config.TextColumn("Remis à"),
                                       "Dit": st.column_config.TextColumn("Compris", disabled=True)})
    inv = {l: c for c, l in lab.items()}
    lignes = [(inv.get(r["Article"]), to_num(r["Quantité"]), r) for _, r in ed.iterrows()]
    erreurs = [f"Article non reconnu : « {r['Dit'] or '?'} » — choisissez-le dans la liste." for c, q, r in lignes if not c]
    tot = {}
    for c, q, _r in lignes:
        if c:
            tot[c] = tot.get(c, 0) + q
    dispo = dict(zip(t["cle"], t["Stock"]))
    erreurs += [f"Stock insuffisant : {lab[c].split(' — ')[0]} ({q:g} demandés, {dispo[c]:g} disponibles)."
                for c, q in tot.items() if q > dispo.get(c, 0) + 1e-9]
    erreurs += ["Quantité manquante sur une ligne." for c, q, _r in lignes if c and q <= 0]
    for e in erreurs:
        st.error(e)
    a, b = st.columns([2, 1])
    if a.button(f"✅ Enregistrer les sorties ({len(lignes)})", type="primary", disabled=bool(erreurs) or not lignes,
                key=f"mag-s-save-{mag}"):
        par = t.set_index("cle")
        rows = [_mvt(ctx, mag, "Sortie", {**par.loc[c].to_dict(), "Code Article": par.loc[c, "Code Article"]}, q,
                     Code_Parc=as_text(r["Code_Parc"]).upper(), **{"Bénéficiaire": as_text(r["Bénéficiaire"]),
                                                                 "Motif": as_text(r["Motif"])},
                     **{"Valeur unitaire": _valeur_unitaire(ctx, par.loc[c, "Code Article"], par.loc[c, "Désignation"],
                                                            par.loc[c, "Référence"], par.loc[c, "Unité"])})
                for c, q, r in lignes]
        ctx.get_store().append("STOCK_MOUVEMENTS", rows)
        ctx.refresh(False)
        st.session_state[f"mag-sort-{mag}"] = []
        st.session_state.mag_msg = f"{len(rows)} sortie(s) enregistrée(s)."
        st.rerun()
    if b.button("Vider", key=f"mag-s-raz-{mag}"):
        st.session_state[f"mag-sort-{mag}"] = []
        st.rerun()


# ---------------------------------------------------------------- transferts
def _transferts_a_recevoir(ctx, mag) -> pd.DataFrame:
    m = mouvements(ctx)
    if not len(m):
        return m
    return m.loc[(m["Type"].map(as_text) == "Sortie transfert") & (m["Magasin lié"].map(as_text) == mag)
                 & (m["Statut"].map(as_text) == "En transit")]


def _transferts(ctx, mag):
    noms = ctx.refs()["chantiers"]
    arr = _transferts_a_recevoir(ctx, mag)
    if len(arr):
        st.markdown(f"##### 📥 Transferts à recevoir ({arr['ID_Transfert'].nunique()})")
        for idt, g in arr.groupby("ID_Transfert"):
            src = as_text(g["Magasin"].iloc[0])
            with st.container(border=True):
                st.caption(f"De {noms.get(src, src)} · envoyé le {fmt_date(g['Date'].iloc[0])} par "
                           f"{as_text(g['Saisi par'].iloc[0])}")
                e = pd.DataFrame({"ID": g["ID_Mouvement"].values, "Désignation": g["Désignation"].values,
                                  "Référence": g["Référence"].values, "Unité": g["Unité"].values,
                                  "Envoyé": g["Quantité"].map(to_num).values, "Reçu": g["Quantité"].map(to_num).values})
                ed = st.data_editor(e, hide_index=True, width="stretch", key=f"mag-tr-r-{idt}",
                                    disabled=["Désignation", "Référence", "Unité", "Envoyé"], column_config={"ID": None})
                if st.button("✅ Confirmer la réception", key=f"mag-tr-ok-{idt}", type="primary"):
                    rows = []
                    for (_, x), (_, r) in zip(g.iterrows(), ed.iterrows()):
                        if to_num(r["Reçu"]) > 0:
                            rows.append(_mvt(ctx, mag, "Entrée transfert", x.to_dict(), to_num(r["Reçu"]),
                                             ID_Transfert=idt, **{"Magasin lié": src, "Motif": "Transfert reçu",
                                                                  "Valeur unitaire": x.get("Valeur unitaire", "")}))
                    s = ctx.get_store()
                    if rows:
                        s.append("STOCK_MOUVEMENTS", rows)
                    s.update_many("STOCK_MOUVEMENTS", "ID_Mouvement", {i: {"Statut": "Reçu"} for i in g["ID_Mouvement"]})
                    ctx.refresh(False)
                    st.session_state.mag_msg = "Transfert reçu : stock mis à jour."
                    st.rerun()
    st.markdown("##### 📤 Envoyer vers un autre magasin")
    t = stock(ctx, mag)
    t = t.loc[t["Stock"] > 0]
    if not len(t):
        st.info("Aucun article en stock à transférer.")
        return
    dest = st.selectbox("Magasin destinataire", [c for c in noms if c != mag], format_func=lambda c: noms.get(c, c),
                        index=None, key=f"mag-tr-dest-{mag}")
    lab = {r["cle"]: _libelle(r) for _, r in t.iterrows()}
    sel = st.multiselect("Articles", list(lab), format_func=lab.get, key=f"mag-tr-art-{mag}")
    if not sel or not dest:
        return
    e = pd.DataFrame({"cle": sel, "Article": [lab[c] for c in sel], "Quantité": 0.0})
    ed = st.data_editor(e, hide_index=True, width="stretch", key=f"mag-tr-ed-{mag}-{len(sel)}",
                        disabled=["Article"], column_config={"cle": None})
    motif = st.text_input("Motif", key=f"mag-tr-mot-{mag}")
    dispo = dict(zip(t["cle"], t["Stock"]))
    trop = [lab[c] for c, q in zip(ed["cle"], ed["Quantité"]) if to_num(q) > dispo[c] + 1e-9]
    for x in trop:
        st.error(f"Stock insuffisant : {x}")
    ok = ed.loc[ed["Quantité"].map(to_num) > 0]
    if st.button(f"🚚 Envoyer ({len(ok)} article(s))", type="primary", disabled=bool(trop) or ok.empty,
                 key=f"mag-tr-go-{mag}"):
        idt = nouvel_id(8)
        par = t.set_index("cle")
        rows = [_mvt(ctx, mag, "Sortie transfert", {**par.loc[c].to_dict(), "Code Article": par.loc[c, "Code Article"]},
                     to_num(q), ID_Transfert=idt, Statut="En transit",
                     **{"Magasin lié": dest, "Motif": motif.strip() or "Transfert",
                        "Valeur unitaire": _valeur_unitaire(ctx, par.loc[c, "Code Article"], par.loc[c, "Désignation"],
                                                            par.loc[c, "Référence"], par.loc[c, "Unité"])})
                for c, q in zip(ok["cle"], ok["Quantité"])]
        ctx.get_store().append("STOCK_MOUVEMENTS", rows)
        ctx.refresh(False)
        st.session_state.mag_msg = f"Transfert envoyé à {noms.get(dest, dest)} : en attente de sa confirmation."
        st.rerun()


# ---------------------------------------------------------------- inventaire
def _inventaire(ctx, mag):
    t = stock(ctx, mag)
    art = ctx.load("ARTICLES")
    cat = {as_text(c): (as_text(d), as_text(r), as_text(u)) for c, d, r, u in
           zip(art["Code Article"], art["Désignation"], art["Référence"], art["Colisage"])}
    premier = not len(t)
    st.caption(("Premier inventaire : il fixe le **stock de départ** du magasin. " if premier else "")
               + "Saisissez les quantités **comptées** ; l'application calcule l'écart avec le stock de l'application "
                 "et le corrige. Laissez vide ce qui n'a pas été compté.")
    comptes = st.session_state.setdefault(f"mag-inv-{mag}", {})  # cle -> {art..., Compté}
    v = st.session_state.get(f"mag-inv-v-{mag}", 0)
    c1, c2 = st.columns(2)
    with c1:
        if ia.disponible():
            txt = st.text_area("🎙️ Dicter le comptage", key=f"mag-inv-txt-{mag}-{v}", height=80,
                               placeholder="Ex. : pneus 315/80 R22.5 : 6 ; ciment 42.5 : 40 sacs ; filtre à gasoil "
                                           "1R-0762 : 3")
            if st.button("✨ Reconnaître", key=f"mag-inv-ia-{mag}-{v}", disabled=not txt.strip()):
                with st.spinner("Reconnaissance des articles…"):
                    try:
                        from schema import COLISAGES
                        for l in ia.analyser_besoin(txt, art, ctx.load("PARC"), COLISAGES, ctx.version()):
                            k = _cle(l["Code Article"], l["Désignation"], l["Référence"])
                            comptes[k] = {"Code Article": l["Code Article"], "Désignation": l["Désignation"],
                                          "Référence": l["Référence"], "Unité": l["Colisage"],
                                          "Compté": float(l["Quantité"])}
                        st.session_state[f"mag-inv-v-{mag}"] = v + 1
                        st.rerun()
                    except ia.IAErreur as e:
                        st.error(str(e))
    with c2:
        f = st.file_uploader("📄 Importer un comptage Excel", type=["xlsx"], key=f"mag-inv-up-{mag}-{v}",
                             help="Colonnes : Code Article, Quantité (ou Désignation, Référence, Unité, Quantité)")
        if f is not None and st.button("Importer", key=f"mag-inv-imp-{mag}-{v}"):
            x = pd.read_excel(f)
            n = 0
            for _, r in x.iterrows():
                code = as_text(r.get("Code Article")).upper()
                d, rf, u = cat.get(code, (as_text(r.get("Désignation")).upper(), as_text(r.get("Référence")).upper(),
                                          as_text(r.get("Unité"))))
                if not d or pd.isna(r.get("Quantité")):
                    continue
                comptes[_cle(code if code in cat else "", d, rf)] = {
                    "Code Article": code if code in cat else "", "Désignation": d, "Référence": rf,
                    "Unité": as_text(r.get("Unité")) or u or "Unité", "Compté": float(to_num(r.get("Quantité")))}
                n += 1
            st.session_state[f"mag-inv-v-{mag}"] = v + 1
            st.session_state.mag_msg = f"{n} ligne(s) de comptage importée(s) : vérifiez puis validez."
            st.rerun()
        modele = io.BytesIO()
        pd.DataFrame({"Code Article": [], "Désignation": [], "Référence": [], "Unité": [], "Quantité": []}).to_excel(
            modele, index=False)
        st.download_button("Modèle Excel", modele.getvalue(), file_name="modele_comptage.xlsx", key=f"mag-inv-mod-{mag}")
    lab = {c: f"{d}" + (f" · {r}" if r else "") + f" ({c})" for c, (d, r, _u) in cat.items()}
    ajout = st.selectbox("➕ Ajouter un article du catalogue au comptage", list(lab), format_func=lab.get, index=None,
                         key=f"mag-inv-add-{mag}-{v}", placeholder="Chercher un article")
    if ajout:
        d, r, u = cat[ajout]
        comptes.setdefault(ajout, {"Code Article": ajout, "Désignation": d, "Référence": r, "Unité": u or "Unité",
                                   "Compté": None})
        st.session_state[f"mag-inv-v-{mag}"] = v + 1
        st.rerun()
    # tableau : stock de l'application + articles ajoutés
    lignes = {r["cle"]: {"Code Article": r["Code Article"], "Désignation": r["Désignation"], "Référence": r["Référence"],
                         "Unité": r["Unité"], "Stock app": r["Stock"], "Compté": None} for _, r in t.iterrows()}
    for k, x in comptes.items():
        lignes.setdefault(k, {**x, "Stock app": 0.0})
        lignes[k]["Compté"] = x.get("Compté")
    if not lignes:
        st.info("Ajoutez des articles au comptage (dictée, import Excel ou liste ci-dessus).")
        return
    g = pd.DataFrame([{"cle": k, **x} for k, x in lignes.items()]).sort_values(["Désignation", "Référence"])
    ed = st.data_editor(g, hide_index=True, width="stretch", key=f"mag-inv-ed-{mag}-{v}",
                        disabled=["Code Article", "Désignation", "Référence", "Unité", "Stock app"],
                        column_config={"cle": None, "Stock app": st.column_config.NumberColumn(format="%g"),
                                       "Compté": st.column_config.NumberColumn(min_value=0.0, format="%g")})
    fait = ed.loc[ed["Compté"].notna()].copy()
    fait["Écart"] = fait["Compté"].map(to_num) - fait["Stock app"].map(to_num)
    ec = fait.loc[fait["Écart"].abs() > 1e-9]
    st.caption(f"{len(fait)} article(s) compté(s), {len(ec)} écart(s).")
    if len(ec):
        st.dataframe(ec[["Désignation", "Référence", "Unité", "Stock app", "Compté", "Écart"]], hide_index=True,
                     width="stretch")
    if st.button(f"✅ Valider l'inventaire ({len(ec)} correction(s))", type="primary", disabled=ec.empty,
                 key=f"mag-inv-go-{mag}"):
        rows = []
        for _, r in ec.iterrows():
            if premier or to_num(r["Stock app"]) == 0 and r["cle"] not in set(t["cle"]):
                rows.append(_mvt(ctx, mag, "Stock initial", r.to_dict(), to_num(r["Compté"]), Motif="Inventaire initial",
                                 **{"Valeur unitaire": _valeur_unitaire(ctx, r["Code Article"], r["Désignation"],
                                                                        r["Référence"], r["Unité"])}))
            else:
                rows.append(_mvt(ctx, mag, "Ajustement inventaire", r.to_dict(), to_num(r["Écart"]),
                                 Motif=f"Inventaire : compté {to_num(r['Compté']):g}, app {to_num(r['Stock app']):g}"))
        ctx.get_store().append("STOCK_MOUVEMENTS", rows)
        ctx.refresh(False)
        st.session_state[f"mag-inv-{mag}"] = {}
        st.session_state[f"mag-inv-v-{mag}"] = v + 1
        st.session_state.mag_msg = f"Inventaire validé : {len(rows)} correction(s) de stock."
        st.rerun()


# =========================================================================== vue Stocks (admin, direction, acheteur)
def vue_stocks(ctx):
    noms = ctx.refs()["chantiers"]
    t = stock(ctx)
    m = mouvements(ctx)
    if not len(t):
        st.info("Aucun mouvement de stock pour l'instant : les magasiniers commencent par l'inventaire de départ.")
    mags = sorted(set(t["Magasin"]) | set(noms)) if len(t) else sorted(noms)
    c1, c2 = st.columns([2, 3])
    sel = c1.selectbox("Magasin", ["Tous"] + mags, format_func=lambda c: "Tous les magasins" if c == "Tous"
                       else noms.get(c, c), key="stk-mag")
    v = t if sel == "Tous" else t.loc[t["Magasin"] == sel]
    if len(v):
        v = v.copy()
        v["PU moyen"] = [_valeur_unitaire(ctx, c, d, r, u) for c, d, r, u in
                         zip(v["Code Article"], v["Désignation"], v["Référence"], v["Unité"])]
        v["Valeur (F)"] = (v["Stock"].clip(lower=0) * v["PU moyen"]).round(0)
        k = st.columns(4)
        k[0].metric("Valeur du stock", f"{fmt_montant(v['Valeur (F)'].sum())} F")
        k[1].metric("Articles en stock", int((v["Stock"] > 0).sum()))
        k[2].metric("Ruptures", int((v["Stock"] <= 0).sum()))
        tr = m.loc[(m["Type"].map(as_text) == "Sortie transfert") & (m["Statut"].map(as_text) == "En transit")] if len(m) else m
        k[3].metric("Transferts en cours", tr["ID_Transfert"].nunique() if len(tr) else 0)
        q = c2.text_input("Rechercher", key="stk-q", placeholder="article, référence…")
        if q.strip():
            v = v.loc[(v["Désignation"].map(as_text) + " " + v["Référence"].map(as_text)).str.contains(q.strip(), case=False,
                                                                                                   regex=False)]
        st.dataframe(v.assign(Magasin=v["Magasin"].map(lambda c: noms.get(c, c)),
                              **{"Dernier mouvement": v["Dernier mouvement"].map(lambda x: fmt_date(x) if pd.notna(x) else "")})
                     [["Magasin", "Désignation", "Référence", "Stock", "Unité", "Sorties 30 j", "PU moyen", "Valeur (F)",
                       "Dernier mouvement", "Code Article"]].sort_values(["Magasin", "Désignation"]),
                     hide_index=True, width="stretch",
                     column_config={"Valeur (F)": st.column_config.NumberColumn(format="%.0f"),
                                    "PU moyen": st.column_config.NumberColumn(format="%.0f")})
    if len(m):
        mm = m if sel == "Tous" else m.loc[m["Magasin"] == sel]
        d90 = pd.Timestamp(date.today() - timedelta(days=90))
        so = mm.loc[(mm["Type"].map(as_text) == "Sortie") & (mm["d"] >= d90)].copy()
        if len(so):
            so["Valeur"] = so["Quantité"].map(to_num) * so["Valeur unitaire"].map(to_num)
            with st.expander("Consommations des 90 derniers jours (par engin et par article)", expanded=False):
                a, b = st.columns(2)
                a.dataframe(so.groupby(so["Code_Parc"].map(lambda x: as_text(x) or "(sans engin)"))
                            .agg(Sorties=("ID_Mouvement", "size"), **{"Valeur (F)": ("Valeur", "sum")})
                            .sort_values("Valeur (F)", ascending=False).round(0), width="stretch")
                b.dataframe(so.groupby(["Désignation", "Référence"])
                            .agg(Quantité=("q", lambda x: -x.sum()), **{"Valeur (F)": ("Valeur", "sum")})
                            .sort_values("Valeur (F)", ascending=False).head(30).round(0), width="stretch")
        with st.expander("Derniers mouvements"):
            st.dataframe(mm.sort_values("Créé le", ascending=False).head(300)
                         [["Date", "Magasin", "Type", "Désignation", "Référence", "Quantité", "Unité", "Code_Parc",
                           "Bénéficiaire", "Motif", "N° BC", "N° BL", "Saisi par"]]
                         .assign(Date=lambda x: x["Date"].map(fmt_date), Magasin=lambda x: x["Magasin"].map(lambda c: noms.get(c, c))),
                         hide_index=True, width="stretch")
    if ctx.user["Rôle"] == "Admin":
        with st.expander("🛠️ Agir sur un magasin (administrateur)"):
            cible = st.selectbox("Magasin", mags, format_func=lambda c: noms.get(c, c), index=None, key="stk-agir")
            if cible:
                onglet_magasin(ctx, cible)
