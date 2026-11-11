"""Volet Achats : tableaux comparatifs des prix (TCP), bons de commande (BC), fournisseurs,
validation des BC, paramètres société et archivage annuel.

Les fonctions reçoivent `ctx` (fourni par app.py) : load, refresh, get_store, refs, picker, user.
"""
from __future__ import annotations

import io
import json
from datetime import date

import pandas as pd
import streamlit as st

from logic import (as_text, fmt_date, fmt_montant, maintenant, montant_ligne, next_numero_tcp, nouvel_id,
                   numero_bc, to_date, to_num, totaux)
from pdf import build_bc_pdf, build_tcp_pdf
from schema import COLISAGES, CONDITIONS_DEFAUT, PARAMETRES_DEFAUT, STATUTS_BC, TABLES

BC_ACTIFS = {"Émis", "Validé"}  # un BC rejeté ou annulé libère les quantités de la DA
DA_OUVERTES = ["Validée"]  # seules les DA validées partent aux achats


# =========================================================================== paramètres
def init_parametres(store):
    """Crée les paramètres et conditions de règlement par défaut s'ils manquent (une fois)."""
    pr = store.read("PARAMETRES")
    presents = set(pr["Paramètre"].map(as_text))
    manquants = [{"Paramètre": k, "Valeur": v} for k, v in PARAMETRES_DEFAUT.items() if k not in presents]
    if manquants:
        store.append("PARAMETRES", manquants)
    if not len(store.read("CONDITIONS_REGLEMENT")):
        store.append("CONDITIONS_REGLEMENT", [{"Condition": c} for c in CONDITIONS_DEFAUT])


def params(ctx) -> dict:
    pr = ctx.load("PARAMETRES")
    out = dict(PARAMETRES_DEFAUT)
    out.update({as_text(r["Paramètre"]): as_text(r["Valeur"]) for _, r in pr.iterrows() if as_text(r["Paramètre"])})
    return out


def conditions(ctx) -> list[str]:
    c = [as_text(x) for x in ctx.load("CONDITIONS_REGLEMENT")["Condition"] if as_text(x)]
    return c or list(CONDITIONS_DEFAUT)


def fournisseurs(ctx) -> list[str]:
    f = ctx.load("FOURNISSEURS")
    f = f.loc[f["Actif"].map(lambda v: as_text(v).upper() not in ("FALSE", "FAUX", "0", "NON")).astype(bool)]
    return sorted({as_text(n).upper() for n in f["Nom"] if as_text(n)})


def _ajoute_fournisseurs(ctx, noms: list[str]):
    f = ctx.load("FOURNISSEURS")
    connus = {as_text(n).upper() for n in f["Nom"]}
    codes = [as_text(c) for c in f["Code Fournisseur"]]
    n = max([int(c[1:]) for c in codes if c[1:].isdigit()] + [0])
    rows = []
    for nom in dict.fromkeys(x.strip().upper() for x in noms if x and x.strip()):
        if nom not in connus:
            n += 1
            rows.append({"Code Fournisseur": f"F{n:04d}", "Nom": nom, "Actif": "TRUE"})
            connus.add(nom)
    if rows:
        ctx.get_store().append("FOURNISSEURS", rows)


# =========================================================================== suivi DA -> BC
def suivi_lignes(ctx) -> pd.DataFrame:
    """Lignes de DA avec quantités commandées (BC émis ou validés) et reste à commander."""
    lg = ctx.load("HISTORIQUE_ARTICLES").copy()
    bl, bc = ctx.load("BC_LIGNES"), ctx.load("BC")
    actifs = set(bc.loc[bc["Statut"].map(as_text).isin(BC_ACTIFS).astype(bool), "ID_BC"].map(as_text))
    bl = bl.loc[bl["ID_BC"].map(as_text).isin(actifs).astype(bool)]
    cmd = bl.assign(q=bl["Quantité"].map(to_num)).groupby(bl["ID_Ligne_DA"].map(as_text))["q"].sum()
    nums = bl.groupby(bl["ID_Ligne_DA"].map(as_text))["N° BC"].agg(lambda s: ", ".join(dict.fromkeys(map(as_text, s))))
    lg["_id"] = lg["ID_Ligne"].map(as_text)
    lg["Qté DA"] = lg["Quantité"].map(to_num)
    lg["Qté commandée"] = lg["_id"].map(cmd).fillna(0.0)
    lg["Reste à commander"] = (lg["Qté DA"] - lg["Qté commandée"]).clip(lower=0)
    lg["BC"] = lg["_id"].map(nums).fillna("")
    return lg


def da_ouvertes(ctx) -> pd.DataFrame:
    """DA validées ayant encore des quantités à commander."""
    da = ctx.load("DA")
    da = da.loc[da["Statut"].map(as_text).isin(DA_OUVERTES).astype(bool)].copy()
    if not len(da):
        return da
    sl = suivi_lignes(ctx)
    g = sl.groupby(sl["ID_DA"].map(as_text)).agg(lignes=("_id", "size"),
                                                 ouvertes=("Reste à commander", lambda s: int((s > 0).sum())))
    da["_id"] = da["ID_DA"].map(as_text)
    da["Lignes"] = da["_id"].map(g["lignes"]).fillna(0).astype(int)
    da["À commander"] = da["_id"].map(g["ouvertes"]).fillna(0).astype(int)
    return da.loc[da["À commander"] > 0].sort_values("Créé le", ascending=False)


def _choix_da(ctx, key: str, on_change=None) -> dict | None:
    da = da_ouvertes(ctx)
    if not len(da):
        st.info("Aucune DA validée en attente de commande.")
        return None
    r = ctx.refs()["chantiers"]
    lab = {row["_id"]: f"{as_text(row['N° DA'])} · {r.get(as_text(row['Chantier']), as_text(row['Chantier']))} · "
                       f"{as_text(row['Demandeur'])} · {row['À commander']}/{row['Lignes']} ligne(s) à commander"
           for _, row in da.iterrows()}
    id_da = st.selectbox("Demande d'achat (DA validée)", list(lab), format_func=lab.get, index=None,
                         key=key, placeholder="Choisir la DA à commander", on_change=on_change)
    if not id_da:
        return None
    return da.loc[da["_id"] == id_da].iloc[0].to_dict()


def maj_lignes_da(ctx, ids_lignes: set[str]):
    """Recalcule Statut ligne / N° BC / Fournisseur / prix des lignes de DA concernées (lecture directe)."""
    if not ids_lignes:
        return
    store = ctx.get_store()
    bc, bl = store.read("BC"), store.read("BC_LIGNES")
    actifs = set(bc.loc[bc["Statut"].map(as_text).isin(BC_ACTIFS).astype(bool), "ID_BC"].map(as_text))
    bl = bl.loc[bl["ID_BC"].map(as_text).isin(actifs).astype(bool)]
    lg = store.read("HISTORIQUE_ARTICLES")
    lg = lg.loc[lg["ID_Ligne"].map(as_text).isin(ids_lignes).astype(bool)]
    changes = {}
    for _, l in lg.iterrows():
        idl = as_text(l["ID_Ligne"])
        b = bl.loc[(bl["ID_Ligne_DA"].map(as_text) == idl).astype(bool)]
        q = sum(to_num(x) for x in b["Quantité"])
        statut_av = as_text(l["Statut ligne"])
        if statut_av in ("Livrée", "Livrée partielle", "Annulée"):
            statut = statut_av
        elif q <= 0:
            statut = "À commander"
        elif q + 1e-9 < to_num(l["Quantité"]):
            statut = "Commandée partielle"
        else:
            statut = "Commandée"
        last = b.iloc[-1] if len(b) else None
        changes[idl] = {"Statut ligne": statut,
                        "N° BC": ", ".join(dict.fromkeys(as_text(x) for x in b["N° BC"])),
                        "Fournisseur": as_text(last["Fournisseur"]) if last is not None else "",
                        "Prix unitaire HT": to_num(last["Prix unitaire HT"]) if last is not None else ""}
    store.update_many("HISTORIQUE_ARTICLES", "ID_Ligne", changes)


# =========================================================================== onglet Achats
def onglet_achats(ctx):
    t1, t2, t3, t4 = st.tabs(["DA à traiter", "Tableaux comparatifs (TCP)", "Bons de commande", "Fournisseurs"])
    with t1:
        _vue_da(ctx)
    with t2:
        _vue_tcp(ctx)
    with t3:
        _vue_bc(ctx)
    with t4:
        _vue_fournisseurs(ctx)


def _vue_da(ctx):
    da = ctx.load("DA")
    if not len(da):
        st.info("Aucune DA.")
        return
    sl = suivi_lignes(ctx)
    g = sl.groupby(sl["ID_DA"].map(as_text)).agg(
        Lignes=("_id", "size"), Ouvertes=("Reste à commander", lambda s: int((s > 0).sum())),
        Cmd=("Qté commandée", lambda s: int((s > 0).sum())))
    v = da.copy()
    v["_id"] = v["ID_DA"].map(as_text)
    v = v.join(g, on="_id")
    v[["Lignes", "Ouvertes", "Cmd"]] = v[["Lignes", "Ouvertes", "Cmd"]].fillna(0).astype(int)

    def etat(r):
        if r["Statut"] not in DA_OUVERTES:
            return "—"
        if r["Ouvertes"] == 0:
            return "Commandée"
        return "Partielle" if r["Cmd"] else "À commander"
    v["Achat"] = v.apply(etat, axis=1)
    k1, k2, k3 = st.columns(3)
    k1.metric("DA validées à commander", int((v["Achat"] == "À commander").sum()))
    k2.metric("Partiellement commandées", int((v["Achat"] == "Partielle").sum()))
    k3.metric("DA en attente de validation", int((v["Statut"] == "Émise").sum()))
    c1, c2 = st.columns(2)
    choix = c1.multiselect("Situation achat", ["À commander", "Partielle", "Commandée", "—"],
                           default=["À commander", "Partielle"], key="ach-da-f")
    cherche = c2.text_input("N° DA, chantier ou demandeur", key="ach-da-q")
    if choix:
        v = v.loc[v["Achat"].isin(choix)]
    if cherche.strip():
        q = cherche.strip().lower()
        v = v.loc[v[["N° DA", "Chantier", "Demandeur"]].astype(str).apply(
            lambda r: q in " ".join(r).lower(), axis=1).astype(bool)]
    v = v.sort_values("Créé le", ascending=False).head(300)
    v["Date"] = v["Date DA"].map(fmt_date)
    st.dataframe(v[["N° DA", "Date", "Chantier", "Demandeur", "Priorité", "Statut", "Achat", "Lignes", "Ouvertes"]]
                 .rename(columns={"Ouvertes": "Lignes à commander"}), hide_index=True, width="stretch")
    if not len(v):
        return
    lab = {r["_id"]: f"{as_text(r['N° DA'])} · {r['Achat']}" for _, r in v.iterrows()}
    id_da = st.selectbox("Détail d'une DA", list(lab), format_func=lab.get, index=None, key="ach-da-sel",
                         placeholder="Choisir une DA")
    if id_da:
        l = sl.loc[sl["ID_DA"].map(as_text) == id_da].sort_values("N° Ligne")
        st.dataframe(l[["N° Ligne", "Désignation", "Référence", "Code_Parc", "Colisage", "Qté DA",
                        "Qté commandée", "Reste à commander", "BC", "Statut ligne"]],
                     hide_index=True, width="stretch")


# --------------------------------------------------------------------------- TCP
def _vue_tcp(ctx):
    if st.session_state.get("tcp_ok"):
        ok = st.session_state["tcp_ok"]
        st.success(f"TCP **{ok['numero']}** enregistré — {len(ok['fours'])} fournisseur(s) retenu(s) : "
                   f"{', '.join(ok['fours'])}.")
        c1, c2, c3 = st.columns(3)
        c1.download_button("Télécharger / imprimer le TCP", ok["pdf"], file_name=f"{ok['numero']}.pdf",
                           mime="application/pdf", type="primary", key="tcp-pdf-ok")
        c2.button("Préparer les BC de ce TCP", key="tcp-to-bc", on_click=_prepare_bc,
                  args=(ok["id_da"], ok["id_tcp"]))
        if c3.button("Nouveau TCP", key="tcp-new"):
            del st.session_state["tcp_ok"]
            st.rerun()
        if st.session_state.get("bc_prepare"):
            st.info("C'est prêt : ouvrez l'onglet « Bons de commande ». La DA et le TCP sont sélectionnés ; "
                    "émettez un BC par fournisseur retenu.")
        return
    mode = st.radio("TCP", ["Nouveau TCP", "TCP enregistrés"], horizontal=True, label_visibility="collapsed",
                    key="tcp-mode")
    if mode == "TCP enregistrés":
        _liste_tcp(ctx)
        return

    da = _choix_da(ctx, "tcp-da")
    if not da:
        return
    sl = suivi_lignes(ctx)
    lignes = sl.loc[(sl["ID_DA"].map(as_text) == da["_id"]) & (sl["Reste à commander"] > 0)].sort_values("N° Ligne")
    nf = int(st.number_input("Nombre de fournisseurs consultés", min_value=1, max_value=12, value=3, step=1,
                             key="tcp-nf"))
    conds, fours = conditions(ctx), fournisseurs(ctx)
    offres = []
    par_ligne = min(nf, 4)
    for debut in range(0, nf, par_ligne):
        cols = st.columns(par_ligne)
        for j in range(debut, min(debut + par_ligne, nf)):
            with cols[j - debut]:
                st.markdown(f"**Fournisseur {j + 1}**")
                nom = st.selectbox("Nom", fours, index=None, accept_new_options=True, key=f"tcp-f{j}-{da['_id']}",
                                   placeholder="Choisir ou saisir")
                delai = st.text_input("Délai de livraison", key=f"tcp-d{j}-{da['_id']}")
                cond = st.selectbox("Conditions de règlement", conds, index=None, accept_new_options=True,
                                    key=f"tcp-c{j}-{da['_id']}", placeholder="Choisir ou saisir")
                obs = st.text_input("Observation", key=f"tcp-o{j}-{da['_id']}")
                offres.append({"Nom": as_text(nom).upper(), "Délai": delai.strip(), "Condition": as_text(cond),
                               "Observation": obs.strip()})
    noms = [o["Nom"] for o in offres]
    if any(not n for n in noms):
        st.caption("Renseignez le nom de chaque fournisseur pour saisir les prix.")
        return
    if len(set(noms)) < len(noms):
        st.error("Un même fournisseur est saisi deux fois.")
        return

    # ---- 1) prix proposés, article par article
    st.markdown("##### 1. Prix unitaires HT proposés (FCFA)")
    st.caption("Laissez vide si le fournisseur n'a pas chiffré l'article. Décochez ✓ pour sortir un article du TCP.")
    grille = pd.DataFrame({
        "Inclure": True, "ID": lignes["_id"].values, "Désignation": lignes["Désignation"].map(as_text).values,
        "Référence": lignes["Référence"].map(as_text).values, "Unité": lignes["Colisage"].map(as_text).values,
        "Quantité": lignes["Reste à commander"].values})
    for j in range(nf):
        grille[f"PU{j}"] = None
    cfg = {"ID": None, "Inclure": st.column_config.CheckboxColumn("✓", width="small"),
           "Quantité": st.column_config.NumberColumn(min_value=0.0, format="%.2f")}
    for j, n in enumerate(noms):
        cfg[f"PU{j}"] = st.column_config.NumberColumn(n, min_value=0.0, step=1.0, format="%.0f")
    ed = st.data_editor(grille, hide_index=True, width="stretch", column_config=cfg,
                        disabled=["Désignation", "Référence", "Unité"],
                        key=f"tcp-grille-{da['_id']}-{'|'.join(noms)}")
    ed = ed.loc[ed["Inclure"].astype(bool) & (ed["Quantité"].map(to_num) > 0)].reset_index(drop=True)
    if not len(ed):
        st.warning("Aucun article inclus.")
        return
    pu = pd.DataFrame({n: ed[f"PU{j}"].map(to_num) for j, n in enumerate(noms)})
    if not (pu > 0).any().any():
        st.caption("Saisissez les prix pour voir la comparaison.")
        return

    # ---- 2) meilleur prix et choix par article
    def meilleur(i):
        r = pu.iloc[i]
        r = r[r > 0]
        return r.idxmin() if len(r) else ""
    best = [meilleur(i) for i in range(len(ed))]
    st.markdown("##### 2. Fournisseur retenu par article")
    st.caption("Par défaut : le moins cher de chaque article. Changez si besoin (délai, qualité, disponibilité…). "
               "Un BC sera émis par fournisseur retenu.")
    choix = pd.DataFrame({
        "Désignation": ed["Désignation"], "Référence": ed["Référence"], "Quantité": ed["Quantité"],
        "Meilleur prix": [f"{b} ({fmt_montant(pu.at[i, b])})" if b else "non chiffré" for i, b in enumerate(best)],
        "Retenu": [b or None for b in best]})
    empreinte = abs(hash(tuple(pu.round(2).to_numpy().ravel().tolist()) + tuple(ed["ID"]))) % 10 ** 8
    ch = st.data_editor(choix, hide_index=True, width="stretch", disabled=["Désignation", "Référence", "Quantité",
                                                                          "Meilleur prix"],
                        column_config={"Retenu": st.column_config.SelectboxColumn("Fournisseur retenu", options=noms)},
                        key=f"tcp-choix-{da['_id']}-{empreinte}")
    retenus = [as_text(x) for x in ch["Retenu"]]
    sans_prix = [ed.at[i, "Désignation"] for i, n in enumerate(retenus) if n and pu.at[i, n] <= 0]
    if sans_prix:
        st.error("Le fournisseur retenu n'a pas chiffré : " + ", ".join(sans_prix))
        return

    # ---- 3) synthèse
    q = ed["Quantité"].map(to_num)
    synth = []
    for n in noms:
        chiffre = pu[n] > 0
        synth.append({"Fournisseur": n, "Articles chiffrés": f"{int(chiffre.sum())}/{len(ed)}",
                      "Total offre HT": (q * pu[n]).sum(),
                      "Articles retenus": sum(1 for r in retenus if r == n),
                      "Montant retenu HT": sum(q[i] * pu.at[i, n] for i, r in enumerate(retenus) if r == n)})
    sy = pd.DataFrame(synth)
    total_best = sum(q[i] * pu.at[i, b] for i, b in enumerate(best) if b)
    total_ret = sy["Montant retenu HT"].sum()
    complets = sy.loc[sy["Articles chiffrés"] == f"{len(ed)}/{len(ed)}"]
    st.markdown("##### 3. Synthèse")
    st.dataframe(sy.assign(**{c: sy[c].map(fmt_montant) for c in ("Total offre HT", "Montant retenu HT")}),
                 hide_index=True, width="stretch")
    m1, m2, m3 = st.columns(3)
    m1.metric("Total retenu HT", f"{fmt_montant(total_ret)} F")
    m2.metric("Meilleur prix par article", f"{fmt_montant(total_best)} F",
              help="Somme des prix les plus bas de chaque article")
    if len(complets):
        mono = complets.sort_values("Total offre HT").iloc[0]
        m3.metric("Meilleure offre d'un seul fournisseur", f"{fmt_montant(mono['Total offre HT'])} F",
                  help=f"{mono['Fournisseur']} (offre complète)")
    non_retenus = [ed.at[i, "Désignation"] for i, r in enumerate(retenus) if not r]
    if non_retenus:
        st.warning("Sans fournisseur retenu (non commandés via ce TCP) : " + ", ".join(non_retenus))
    motif = st.text_input("Motif des choix (facultatif)", key="tcp-motif",
                          placeholder="Ex. : délai, qualité, disponibilité…")
    obs = st.text_input("Observations du TCP (facultatif)", key="tcp-obs")
    if st.button("Enregistrer le TCP", type="primary", key="tcp-save", disabled=not any(retenus)):
        _enregistre_tcp(ctx, da, offres, ed, pu, retenus, best, sy, motif, obs)


def _enregistre_tcp(ctx, da, offres, ed, pu, retenus, best, sy, motif, obs):
    store = ctx.get_store()
    p = params(ctx)
    auj = date.today()
    numero = next_numero_tcp(store.read("TCP"), p.get("Préfixe TCP") or "TCP-", auj.year)
    id_tcp = nouvel_id()
    noms = [o["Nom"] for o in offres]
    n_l = len(ed)
    for o in offres:
        nb = int((pu[o["Nom"]] > 0).sum())
        if nb < n_l:
            o["Observation"] = " ; ".join(x for x in [o["Observation"], f"{n_l - nb} article(s) non chiffré(s)"] if x)
    q = ed["Quantité"].map(to_num)
    total_ret = sum(q[i] * pu.at[i, r] for i, r in enumerate(retenus) if r)
    total_best = sum(q[i] * pu.at[i, b] for i, b in enumerate(best) if b)
    fours_ret = list(dict.fromkeys(r for r in retenus if r))
    row = {"ID_TCP": id_tcp, "N° TCP": numero, "Date TCP": auj.isoformat(), "ID_DA": da["_id"],
           "N° DA": as_text(da["N° DA"]), "Chantier": as_text(da["Chantier"]),
           "Fournisseurs consultés": " | ".join(noms), "Fournisseur retenu": " | ".join(fours_ret),
           "Montant retenu HT": round(total_ret), "Moins-disant": "Meilleur prix par article",
           "Montant moins-disant HT": round(total_best), "Motif choix": motif.strip(),
           "Conditions offres": json.dumps(offres, ensure_ascii=False), "Acheteur": ctx.user["Nom"],
           "Statut": "Établi", "N° BC": "", "Observations": obs.strip(), "Créé le": maintenant()}
    hist = ctx.load("HISTORIQUE_ARTICLES")
    codes = dict(zip(hist["ID_Ligne"].map(as_text), hist["Code Article"].map(as_text)))
    lignes_off = []
    for i, l in ed.iterrows():
        for n in noms:
            prix = pu.at[i, n]
            if prix <= 0:
                continue
            lignes_off.append({"ID_Offre": nouvel_id(10), "ID_TCP": id_tcp, "N° TCP": numero, "N° Ligne": i + 1,
                               "ID_Ligne_DA": l["ID"], "Code Article": codes.get(as_text(l["ID"]), ""),
                               "Désignation": l["Désignation"], "Référence": l["Référence"], "Unité": l["Unité"],
                               "Quantité": to_num(l["Quantité"]), "Fournisseur": n, "Prix unitaire HT": prix,
                               "Montant HT": montant_ligne(l["Quantité"], prix),
                               "Retenu": "TRUE" if retenus[i] == n else "FALSE", "Date TCP": auj.isoformat(),
                               "N° DA": row["N° DA"], "Chantier": row["Chantier"]})
    with st.spinner("Enregistrement du TCP…"):
        store.append("TCP_OFFRES", lignes_off)
        store.append("TCP", [row])
        _ajoute_fournisseurs(ctx, noms)
        ctx.refresh()
    pdf = pdf_tcp(ctx, row, lignes_off)
    st.session_state.tcp_ok = {"numero": numero, "pdf": pdf, "id_da": da["_id"], "id_tcp": id_tcp, "fours": fours_ret}
    st.rerun()


def pdf_tcp(ctx, tcp: dict, offres_rows: list[dict] | None = None) -> bytes:
    if offres_rows is None:
        o = ctx.load("TCP_OFFRES")
        offres_rows = o.loc[o["ID_TCP"].map(as_text) == as_text(tcp["ID_TCP"])].to_dict("records")
    try:
        fours = json.loads(as_text(tcp.get("Conditions offres")) or "[]")
    except ValueError:
        fours = []
    if not fours:
        fours = [{"Nom": n} for n in as_text(tcp.get("Fournisseurs consultés")).split(" | ") if n]
    lignes = {}
    for r in sorted(offres_rows, key=lambda r: to_num(r["N° Ligne"])):
        k = as_text(r["ID_Ligne_DA"]) or as_text(r["N° Ligne"])
        l = lignes.setdefault(k, {"Désignation": r["Désignation"], "Référence": r["Référence"], "Unité": r["Unité"],
                                  "Quantité": r["Quantité"], "prix": {}, "retenu": ""})
        l["prix"][as_text(r["Fournisseur"])] = to_num(r["Prix unitaire HT"])
        if as_text(r["Retenu"]).upper() in ("TRUE", "1", "VRAI"):
            l["retenu"] = as_text(r["Fournisseur"])
    p = params(ctx)
    ch = as_text(tcp.get("Chantier"))
    return build_tcp_pdf({**tcp, "Taux TVA": p.get("Taux TVA (%)")}, fours, list(lignes.values()), p,
                         ctx.refs()["chantiers"].get(ch, ch))


def fournisseurs_tcp(ctx, id_tcp: str) -> dict[str, dict]:
    """Fournisseurs retenus d'un TCP -> {nom: {ID_Ligne_DA: prix}} et BC déjà émis pour chacun."""
    o = ctx.load("TCP_OFFRES")
    o = o.loc[(o["ID_TCP"].map(as_text) == id_tcp) & o["Retenu"].map(lambda v: as_text(v).upper() in ("TRUE", "1", "VRAI")).astype(bool)]
    out = {}
    for n, g in o.groupby(o["Fournisseur"].map(as_text)):
        out[n] = {"prix": dict(zip(g["ID_Ligne_DA"].map(as_text), g["Prix unitaire HT"].map(to_num))),
                  "montant": g["Montant HT"].map(to_num).sum()}
    bc = ctx.load("BC")
    bc = bc.loc[(bc["ID_TCP"].map(as_text) == id_tcp) & bc["Statut"].map(as_text).isin(BC_ACTIFS).astype(bool)]
    for n in out:
        out[n]["bc"] = ", ".join(bc.loc[bc["Fournisseur"].map(as_text) == n, "N° BC"].map(as_text))
    return out


def _liste_tcp(ctx):
    tcp = ctx.load("TCP")
    if not len(tcp):
        st.info("Aucun TCP.")
        return
    v = tcp.sort_values("Créé le", ascending=False).head(300).copy()
    v["Date"] = v["Date TCP"].map(fmt_date)
    v["Retenu HT"] = v["Montant retenu HT"].map(fmt_montant)
    st.dataframe(v[["N° TCP", "Date", "N° DA", "Chantier", "Fournisseurs consultés", "Fournisseur retenu",
                    "Retenu HT", "Statut", "N° BC", "Acheteur"]].rename(columns={"Fournisseur retenu": "Retenus"}),
                 hide_index=True, width="stretch")
    lab = {as_text(r["ID_TCP"]): f"{as_text(r['N° TCP'])} · {as_text(r['N° DA'])} · {as_text(r['Statut'])}"
           for _, r in v.iterrows()}
    sel = st.selectbox("TCP", list(lab), format_func=lab.get, index=None, key="tcp-sel", placeholder="Choisir un TCP")
    if not sel:
        return
    t = v.loc[v["ID_TCP"].map(as_text) == sel].iloc[0].to_dict()
    fr = fournisseurs_tcp(ctx, sel)
    st.dataframe(pd.DataFrame([{"Fournisseur retenu": n, "Montant HT": fmt_montant(x["montant"]),
                                "BC émis": x["bc"] or "—"} for n, x in fr.items()]), hide_index=True, width="stretch")
    c1, c2, c3 = st.columns(3)
    c1.download_button("Télécharger / imprimer", pdf_tcp(ctx, t), file_name=f"{as_text(t['N° TCP'])}.pdf",
                       mime="application/pdf", type="primary", key="tcp-dl")
    if as_text(t["Statut"]) in ("Établi", "BC partiel"):
        c2.button("Préparer les BC", key="tcp-bc2", on_click=_prepare_bc, args=(as_text(t["ID_DA"]), sel))
        if c3.button("Abandonner ce TCP", key="tcp-ab"):
            ctx.get_store().update("TCP", "ID_TCP", sel, {"Statut": "Abandonné"})
            ctx.refresh()
            st.rerun()
    if st.session_state.get("bc_prepare"):
        st.info("Ouvrez l'onglet « Bons de commande » : la DA et le TCP y sont déjà sélectionnés.")


def _prepare_bc(id_da: str, id_tcp: str):
    st.session_state["bc-da"] = id_da
    st.session_state["bc-tcp"] = id_tcp
    st.session_state["bc-mode"] = "Nouveau BC"
    st.session_state["bc_prepare"] = True


def maj_statut_tcp(ctx, id_tcp: str):
    """Établi -> BC partiel -> BC émis selon les fournisseurs retenus déjà commandés (lecture directe)."""
    if not id_tcp:
        return
    store = ctx.get_store()
    o, bc = store.read("TCP_OFFRES"), store.read("BC")
    o = o.loc[(o["ID_TCP"].map(as_text) == id_tcp) & o["Retenu"].map(lambda v: as_text(v).upper() in ("TRUE", "1", "VRAI")).astype(bool)]
    ret = set(o["Fournisseur"].map(as_text))
    bc = bc.loc[(bc["ID_TCP"].map(as_text) == id_tcp) & bc["Statut"].map(as_text).isin(BC_ACTIFS).astype(bool)]
    faits = set(bc["Fournisseur"].map(as_text)) & ret
    statut = "Établi" if not faits else ("BC émis" if faits >= ret else "BC partiel")
    store.update("TCP", "ID_TCP", id_tcp, {"Statut": statut, "N° BC": ", ".join(bc["N° BC"].map(as_text))})


# --------------------------------------------------------------------------- BC
def pdf_bc(ctx, bc: dict, lignes: list[dict] | None = None) -> bytes:
    if lignes is None:
        bl = ctx.load("BC_LIGNES")
        lignes = bl.loc[bl["ID_BC"].map(as_text) == as_text(bc["ID_BC"])].to_dict("records")
    ch = as_text(bc.get("Chantier"))
    return build_bc_pdf(bc, lignes, params(ctx), ctx.refs()["chantiers"].get(ch, ch))


def _bc_suivant():
    ok = st.session_state.pop("bc_ok", {}) or {}
    if ok.get("suite"):
        st.session_state["bc-da"], st.session_state["bc-tcp"] = ok["suite"]
    st.session_state["bc-mode"] = "Nouveau BC"


def _vue_bc(ctx):
    if st.session_state.get("bc_ok"):
        ok = st.session_state["bc_ok"]
        st.success(f"Bon de commande **{ok['numero']}** émis — en attente de validation par la direction.")
        c1, c2 = st.columns(2)
        c1.download_button("Télécharger / imprimer le BC", ok["pdf"], file_name=f"{ok['numero']}.pdf",
                           mime="application/pdf", type="primary", key="bc-pdf-ok")
        if ok.get("suite"):
            st.info("Ce BC vient d'un TCP : cliquez sur « Nouveau BC » pour émettre celui du fournisseur suivant "
                    "(la DA et le TCP restent sélectionnés).")
        c2.button("Nouveau BC", type="primary", key="bc-new", on_click=_bc_suivant)
        return
    mode = st.radio("BC", ["Nouveau BC", "Bons de commande émis"], horizontal=True, label_visibility="collapsed",
                    key="bc-mode")
    if mode == "Bons de commande émis":
        _liste_bc(ctx)
        return
    st.session_state.pop("bc_prepare", None)
    da = _choix_da(ctx, "bc-da")
    if not da:
        return
    sl = suivi_lignes(ctx)
    lignes = sl.loc[(sl["ID_DA"].map(as_text) == da["_id"]) & (sl["Reste à commander"] > 0)].sort_values("N° Ligne")

    # ---- TCP éventuel : un BC par fournisseur retenu
    tcp = ctx.load("TCP")
    tcp = tcp.loc[(tcp["ID_DA"].map(as_text) == da["_id"]) & tcp["Statut"].map(as_text).isin(["Établi", "BC partiel"]).astype(bool)]
    lab_tcp = {"": "Aucun — saisie directe des prix"}
    lab_tcp.update({as_text(r["ID_TCP"]): f"{as_text(r['N° TCP'])} · retenus : {as_text(r['Fournisseur retenu'])}"
                    for _, r in tcp.iterrows()})
    if st.session_state.get("bc-tcp") not in lab_tcp:
        st.session_state["bc-tcp"] = ""
    c1, c2 = st.columns(2)
    id_tcp = c1.selectbox("À partir d'un TCP (facultatif)", list(lab_tcp), format_func=lab_tcp.get, key="bc-tcp")
    t = tcp.loc[tcp["ID_TCP"].map(as_text) == id_tcp].iloc[0].to_dict() if id_tcp else None
    prix_tcp, four_def, cond_def = {}, "", ""
    if t:
        fr = fournisseurs_tcp(ctx, id_tcp)
        noms_fr = sorted(fr, key=lambda n: (bool(fr[n]["bc"]), n))  # ceux sans BC d'abord
        four_def = c2.selectbox("Fournisseur retenu du TCP", noms_fr, key=f"bc-tcpf-{id_tcp}",
                                format_func=lambda n: f"{n} — {fmt_montant(fr[n]['montant'])} F HT"
                                                      + (f" (déjà commandé : {fr[n]['bc']})" if fr[n]["bc"] else ""))
        prix_tcp = fr[four_def]["prix"]
        try:
            cond_def = next((as_text(x.get("Condition")) for x in json.loads(as_text(t["Conditions offres"]))
                             if as_text(x.get("Nom")) == four_def), "")
        except ValueError:
            cond_def = ""

    # ---- en-tête
    p = params(ctx)
    fours, conds = fournisseurs(ctx), conditions(ctx)
    if four_def and four_def not in fours:
        fours = [four_def] + fours
    if cond_def and cond_def not in conds:
        conds = [cond_def] + conds
    k = f"{da['_id']}-{id_tcp}-{four_def}"
    c1, c2 = st.columns(2)
    four = c1.selectbox("Fournisseur", fours, index=fours.index(four_def) if four_def else None,
                        accept_new_options=True, key=f"bc-four-{k}", placeholder="Choisir ou saisir")
    date_bc = c2.date_input("Date du BC", value=date.today(), format="DD/MM/YYYY", key=f"bc-date-{k}")
    c1, c2 = st.columns(2)
    cond = c1.selectbox("Conditions de règlement", conds, index=conds.index(cond_def) if cond_def else None,
                        accept_new_options=True, key=f"bc-cond-{k}", placeholder="Choisir ou saisir")
    magasin = c2.text_input("Magasin", key=f"bc-mag-{k}")
    taux = to_num(p.get("Taux TVA (%)"))
    tva_on = st.checkbox(f"Appliquer la TVA ({fmt_montant(taux, 1).rstrip('0').rstrip(',')} %)", value=True,
                         key=f"bc-tva-{k}")
    obs = st.text_input("Observations", key=f"bc-obs-{k}")

    # ---- lignes de la DA
    st.markdown("##### Articles à commander")
    grille = pd.DataFrame({
        "Inclure": True, "ID": lignes["_id"].values, "Code Article": lignes["Code Article"].map(as_text).values,
        "Désignation": lignes["Désignation"].map(as_text).values, "Référence": lignes["Référence"].map(as_text).values,
        "Code parc": lignes["Code_Parc"].map(as_text).values,
        "Unité": lignes["Colisage"].map(as_text).values, "Reste DA": lignes["Reste à commander"].values,
        "Quantité": lignes["Reste à commander"].values,
        "Prix unitaire HT": [prix_tcp.get(i) for i in lignes["_id"]], "Remise %": 0.0})
    if t:  # seules les lignes attribuées à ce fournisseur dans le TCP sont cochées
        grille["Inclure"] = grille["ID"].map(lambda i: i in prix_tcp)
    ed = st.data_editor(
        grille, hide_index=True, width="stretch", key=f"bc-grille-{k}",
        disabled=["Désignation", "Reste DA"],
        column_config={"ID": None, "Code Article": None,
                       "Inclure": st.column_config.CheckboxColumn("✓", width="small"),
                       "Unité": st.column_config.SelectboxColumn(options=sorted(set(COLISAGES) | set(grille["Unité"]))),
                       "Reste DA": st.column_config.NumberColumn(format="%.2f"),
                       "Quantité": st.column_config.NumberColumn(min_value=0.0, format="%.2f"),
                       "Prix unitaire HT": st.column_config.NumberColumn("P.U. HT (FCFA)", min_value=0.0, format="%.0f"),
                       "Remise %": st.column_config.NumberColumn(min_value=0.0, max_value=100.0, format="%.1f")})

    # ---- articles hors DA
    extra_key = f"bc_extra_{k}"
    st.session_state.setdefault(extra_key, [])
    with st.expander(f"Ajouter un article hors DA ({len(st.session_state[extra_key])})"):
        des, ref, code, col_def, cles = ctx.picker(f"bcx-{k}")
        with st.form(f"bcx-form-{k}-{des}-{ref}", clear_on_submit=True):
            a1, a2, a3, a4, a5 = st.columns(5)
            parc_x = a5.text_input("Code parc", placeholder="Facultatif")
            un = a1.selectbox("Unité", COLISAGES, index=COLISAGES.index(col_def) if col_def in COLISAGES else 0)
            q = a2.number_input("Quantité", min_value=0.0, value=1.0, step=1.0)
            pu = a3.number_input("P.U. HT", min_value=0.0, step=100.0)
            rem = a4.number_input("Remise %", min_value=0.0, max_value=100.0, step=1.0)
            if st.form_submit_button("Ajouter au BC"):
                if des and q > 0:
                    st.session_state[extra_key].append({"ID": "", "Code Article": code or "", "Désignation": des,
                                                        "Référence": ref, "Code parc": parc_x.strip().upper(),
                                                        "Unité": un, "Quantité": q,
                                                        "Prix unitaire HT": pu, "Remise %": rem})
                    for c in cles:
                        st.session_state.pop(c, None)
                    st.rerun()
                else:
                    st.error("Désignation et quantité obligatoires.")
        for i, x in enumerate(st.session_state[extra_key]):
            a, b = st.columns([6, 1])
            a.write(f"{x['Désignation']} {x['Référence']} — {x['Quantité']:g} {x['Unité']} × "
                    f"{fmt_montant(x['Prix unitaire HT'])}")
            if b.button("✕", key=f"bcx-del-{k}-{i}"):
                st.session_state[extra_key].pop(i)
                st.rerun()

    sel = ed.loc[ed["Inclure"].astype(bool) & (ed["Quantité"].map(to_num) > 0)].to_dict("records")
    tout = sel + st.session_state[extra_key]
    tot = totaux(tout, taux, tva_on)
    m1, m2, m3 = st.columns(3)
    m1.metric("Total HT", f"{fmt_montant(tot['Total HT'])} F")
    m2.metric("TVA", f"{fmt_montant(tot['Montant TVA'])} F")
    m3.metric("Total TTC", f"{fmt_montant(tot['Total TTC'])} F")
    depasse = [x for x in sel if to_num(x["Quantité"]) > to_num(x["Reste DA"]) + 1e-9]
    ok_dep = True
    if depasse:
        st.warning("Quantité supérieure au reste de la DA pour : " + ", ".join(x["Désignation"] for x in depasse))
        ok_dep = st.checkbox("Je confirme le dépassement (justifier dans Observations)", key=f"bc-dep-{k}")
    if st.button(f"Émettre le BC ({len(tout)} ligne(s))", type="primary", disabled=not tout, key=f"bc-save-{k}"):
        erreurs = []
        if not four:
            erreurs.append("Choisissez ou saisissez le fournisseur.")
        if not cond:
            erreurs.append("Choisissez les conditions de règlement.")
        sans_prix = [x["Désignation"] for x in tout if to_num(x["Prix unitaire HT"]) <= 0]
        if sans_prix:
            erreurs.append("Prix manquant pour : " + ", ".join(sans_prix))
        if depasse and (not ok_dep or not obs.strip()):
            erreurs.append("Dépassement de quantité : cochez la confirmation et justifiez dans Observations.")
        if erreurs:
            for e in erreurs:
                st.error(e)
            return
        entete = {"da": da, "four": as_text(four).upper(), "date": date_bc, "cond": as_text(cond),
                  "parc": ", ".join(dict.fromkeys(as_text(x.get("Code parc")) for x in tout if as_text(x.get("Code parc")))),
                  "magasin": magasin.strip(), "obs": obs.strip(),
                  "taux": taux if tva_on else 0, "tot": tot, "id_tcp": id_tcp, "tcp": t}
        _enregistre_bc(ctx, entete, tout)
        st.session_state.pop(extra_key, None)
        st.rerun()


def _enregistre_bc(ctx, e: dict, lignes: list[dict]):
    store = ctx.get_store()
    with st.spinner("Émission du BC…"):
        pr = store.read("PARAMETRES")  # lecture directe : numéro toujours à jour
        pv = dict(PARAMETRES_DEFAUT)
        pv.update({as_text(r["Paramètre"]): as_text(r["Valeur"]) for _, r in pr.iterrows()})
        num = int(to_num(pv.get("Prochain N° BC")) or 1)
        existants = set(store.read("BC")["N° BC"].map(as_text))
        numero = numero_bc(pv.get("Préfixe BC", "BC-"), num)
        while numero in existants:
            num += 1
            numero = numero_bc(pv.get("Préfixe BC", "BC-"), num)
        store.update("PARAMETRES", "Paramètre", "Prochain N° BC", {"Valeur": str(num + 1)})
        id_bc = nouvel_id()
        da = e["da"]
        d = e["date"].isoformat()
        rows = []
        for i, l in enumerate(lignes, start=1):
            rows.append({"ID_LigneBC": nouvel_id(10), "ID_BC": id_bc, "N° BC": numero, "N° Ligne": i,
                         "ID_Ligne_DA": as_text(l.get("ID")), "Code Article": as_text(l.get("Code Article")),
                         "Désignation": as_text(l["Désignation"]), "Référence": as_text(l.get("Référence")),
                         "Code_Parc": as_text(l.get("Code parc")).upper(), "Unité": as_text(l.get("Unité")), "Quantité": to_num(l["Quantité"]),
                         "Prix unitaire HT": to_num(l["Prix unitaire HT"]), "Remise %": to_num(l.get("Remise %")),
                         "Montant HT": montant_ligne(l["Quantité"], l["Prix unitaire HT"], l.get("Remise %")),
                         "Fournisseur": e["four"], "Date BC": d, "Chantier": as_text(da["Chantier"]),
                         "N° DA": as_text(da["N° DA"])})
        bc = {"ID_BC": id_bc, "N° BC": numero, "Date BC": d, "ID_DA": da["_id"], "N° DA": as_text(da["N° DA"]),
              "Chantier": as_text(da["Chantier"]), "Code Parc": e["parc"], "Magasin": e["magasin"],
              "Fournisseur": e["four"], "Condition règlement": e["cond"], "Total HT": e["tot"]["Total HT"],
              "Taux TVA": e["taux"], "Montant TVA": e["tot"]["Montant TVA"], "Total TTC": e["tot"]["Total TTC"],
              "Statut": "Émis", "Acheteur": ctx.user["Nom"], "Validé par": "", "Date validation": "",
              "Motif rejet": "", "ID_TCP": e["id_tcp"], "N° TCP": as_text((e["tcp"] or {}).get("N° TCP")),
              "Observations": e["obs"], "Créé le": maintenant()}
        store.append("BC_LIGNES", rows)
        store.append("BC", [bc])
        maj_statut_tcp(ctx, e["id_tcp"])
        _ajoute_fournisseurs(ctx, [e["four"]])
        maj_lignes_da(ctx, {r["ID_Ligne_DA"] for r in rows if r["ID_Ligne_DA"]})
        ctx.refresh()
    st.session_state.bc_ok = {"numero": numero, "pdf": build_bc_pdf(bc, rows, params(ctx),
                                                                    ctx.refs()["chantiers"].get(bc["Chantier"], bc["Chantier"]))}
    if e["id_tcp"]:  # TCP multi-fournisseurs : on reste sur la DA et le TCP pour le BC suivant
        st.session_state.bc_ok["suite"] = (da["_id"], e["id_tcp"])
    for key in ("bc-da", "bc-tcp"):
        st.session_state.pop(key, None)


def _filtre_bc(ctx, bc: pd.DataFrame, key: str, defaut=None) -> pd.DataFrame:
    c1, c2, c3 = st.columns(3)
    sts = c1.multiselect("Statut", STATUTS_BC, default=defaut, key=f"{key}-st", placeholder="Tous")
    fo = c2.multiselect("Fournisseur", sorted(set(bc["Fournisseur"].map(as_text)) - {""}), key=f"{key}-fo",
                        placeholder="Tous")
    q = c3.text_input("N° BC, N° DA ou chantier", key=f"{key}-q")
    if sts:
        bc = bc.loc[bc["Statut"].isin(sts)]
    if fo:
        bc = bc.loc[bc["Fournisseur"].map(as_text).isin(fo)]
    if q.strip():
        s = q.strip().lower()
        bc = bc.loc[bc[["N° BC", "N° DA", "Chantier"]].astype(str).apply(
            lambda r: s in " ".join(r).lower(), axis=1).astype(bool)]
    return bc.sort_values("Créé le", ascending=False)


def _table_bc(bc: pd.DataFrame) -> pd.DataFrame:
    v = bc.copy()
    v["Date"] = v["Date BC"].map(fmt_date)
    v["Total TTC (F)"] = v["Total TTC"].map(fmt_montant)
    return v


def _liste_bc(ctx):
    bc = ctx.load("BC")
    if not len(bc):
        st.info("Aucun bon de commande.")
        return
    f = _filtre_bc(ctx, bc, "lbc").head(300)
    v = _table_bc(f)
    st.dataframe(v[["N° BC", "Date", "N° DA", "Chantier", "Fournisseur", "Total TTC (F)", "Statut", "Acheteur",
                    "Validé par", "Motif rejet"]], hide_index=True, width="stretch")
    lab = {as_text(r["ID_BC"]): f"{as_text(r['N° BC'])} · {as_text(r['Fournisseur'])} · {as_text(r['Statut'])}"
           for _, r in f.iterrows()}
    sel = st.selectbox("Bon de commande", list(lab), format_func=lab.get, index=None, key="lbc-sel",
                       placeholder="Choisir un BC")
    if sel:
        detail_bc(ctx, f.loc[f["ID_BC"].map(as_text) == sel].iloc[0].to_dict(), "lbc", annuler=True)


def detail_bc(ctx, b: dict, key: str, annuler: bool = False):
    bl = ctx.load("BC_LIGNES")
    l = bl.loc[bl["ID_BC"].map(as_text) == as_text(b["ID_BC"])].sort_values("N° Ligne")
    st.markdown(f"**{as_text(b['N° BC'])}** · {as_text(b['Fournisseur'])} · DA {as_text(b['N° DA'])} · "
                f"**{as_text(b['Statut'])}**")
    st.caption(f"{fmt_date(b['Date BC'])} · {as_text(b['Condition règlement'])} · émis par {as_text(b['Acheteur'])}"
               + (f" · TCP {as_text(b['N° TCP'])}" if as_text(b.get("N° TCP")) else ""))
    v = l[["Désignation", "Référence", "Code_Parc", "Unité", "Quantité", "Prix unitaire HT", "Remise %",
           "Montant HT"]].rename(columns={"Code_Parc": "Code parc"}).copy()
    v["Prix unitaire HT"] = v["Prix unitaire HT"].map(fmt_montant)
    v["Montant HT"] = v["Montant HT"].map(fmt_montant)
    st.dataframe(v, hide_index=True, width="stretch")
    st.caption(f"Total HT {fmt_montant(b['Total HT'])} · TVA {fmt_montant(b['Montant TVA'])} · "
               f"**Total TTC {fmt_montant(b['Total TTC'])} FCFA**")
    if as_text(b.get("Motif rejet")):
        st.warning(f"Motif du rejet : {as_text(b['Motif rejet'])}")
    statut = as_text(b["Statut"])
    nom = f"{as_text(b['N° BC'])}{'_ANNULE' if statut == 'Annulé' else ''}.pdf"
    c1, c2 = st.columns(2)
    c1.download_button("Télécharger / imprimer le BC", pdf_bc(ctx, b, l.to_dict("records")), file_name=nom,
                       mime="application/pdf", type="primary", key=f"{key}-pdf")
    if annuler and statut in ("Émis", "Rejeté"):
        if c2.button("Annuler ce BC (libère les quantités de la DA)", key=f"{key}-ann"):
            ctx.get_store().update("BC", "ID_BC", as_text(b["ID_BC"]), {"Statut": "Annulé"})
            maj_lignes_da(ctx, {as_text(x) for x in l["ID_Ligne_DA"] if as_text(x)})
            maj_statut_tcp(ctx, as_text(b.get("ID_TCP")))
            ctx.refresh()
            st.rerun()


# =========================================================================== validation BC (direction)
def onglet_validation_bc(ctx):
    bc = ctx.load("BC")
    if not len(bc):
        st.info("Aucun bon de commande.")
        return
    k1, k2, k3 = st.columns(3)
    k1.metric("BC à valider", int((bc["Statut"] == "Émis").sum()))
    k2.metric("Montant à valider (TTC)",
              f"{fmt_montant(bc.loc[bc['Statut'] == 'Émis', 'Total TTC'].map(to_num).sum())} F")
    k3.metric("BC validés", int((bc["Statut"] == "Validé").sum()))
    f = _filtre_bc(ctx, bc, "vbc", defaut=["Émis"]).head(300)
    version = st.session_state.get("vbc_v", 0)
    v = _table_bc(f)
    v.insert(0, "✓", False)
    tout = st.checkbox("Tout cocher", key=f"vbc-tout-{version}")
    if tout:
        v["✓"] = True
    ed = st.data_editor(v[["✓", "ID_BC", "N° BC", "Date", "N° DA", "Chantier", "Fournisseur", "Total TTC (F)",
                           "Statut", "Acheteur"]], hide_index=True, width="stretch",
                        key=f"vbc-ed-{version}-{tout}", disabled=["N° BC", "Date", "N° DA", "Chantier", "Fournisseur",
                                                                    "Total TTC (F)", "Statut", "Acheteur"],
                        column_config={"ID_BC": None, "✓": st.column_config.CheckboxColumn("✓", width="small")})
    coches = ed.loc[ed["✓"].astype(bool)]
    n = len(coches)
    b1, b2, b3 = st.columns([1, 1, 2])
    motif = b3.text_input("Motif", key=f"vbc-motif-{version}", label_visibility="collapsed",
                          placeholder="Motif du rejet (obligatoire pour rejeter)")
    action = None
    if b1.button(f"✅ Valider ({n})", disabled=n == 0, type="primary", key="vbc-ok"):
        action = "Validé"
    if b2.button(f"❌ Rejeter ({n})", disabled=n == 0, key="vbc-ko"):
        action = "Rejeté"
    if action == "Rejeté" and not motif.strip():
        st.error("Indiquez le motif du rejet avant de cliquer sur Rejeter.")
        action = None
    if action:
        non_emis = coches.loc[coches["Statut"] != "Émis", "N° BC"].tolist()
        coches = coches.loc[coches["Statut"] == "Émis"]
        changes = {as_text(i): {"Statut": action, "Validé par": ctx.user["Nom"],
                                "Date validation": date.today().isoformat(),
                                "Motif rejet": motif.strip() if action == "Rejeté" else ""}
                   for i in coches["ID_BC"]}
        if changes:
            ctx.get_store().update_many("BC", "ID_BC", changes)
            if action == "Rejeté":
                bl = ctx.load("BC_LIGNES")
                ids = set(bl.loc[bl["ID_BC"].map(as_text).isin(changes).astype(bool), "ID_Ligne_DA"].map(as_text)) - {""}
                maj_lignes_da(ctx, ids)
                for it in set(bc.loc[bc["ID_BC"].map(as_text).isin(changes).astype(bool), "ID_TCP"].map(as_text)) - {""}:
                    maj_statut_tcp(ctx, it)
            ctx.refresh()
        st.session_state.vbc_v = version + 1
        st.session_state.vbc_msg = (f"{len(changes)} BC passé(s) en « {action} »."
                                    + (f" Ignorés (déjà traités) : {', '.join(non_emis)}." if non_emis else ""))
        st.rerun()
    if st.session_state.get("vbc_msg"):
        st.success(st.session_state.pop("vbc_msg"))
    lab = {as_text(r["ID_BC"]): f"{as_text(r['N° BC'])} · {as_text(r['Fournisseur'])} · {as_text(r['Statut'])}"
           for _, r in f.iterrows()}
    sel = st.selectbox("Voir le détail d'un BC", list(lab), format_func=lab.get, index=None, key="vbc-sel",
                       placeholder="Choisir un BC")
    if sel:
        detail_bc(ctx, f.loc[f["ID_BC"].map(as_text) == sel].iloc[0].to_dict(), "vbc")


# =========================================================================== fournisseurs
def _vue_fournisseurs(ctx):
    f = ctx.load("FOURNISSEURS")
    with st.form("four-add", clear_on_submit=True):
        st.markdown("##### Ajouter un fournisseur")
        c1, c2, c3 = st.columns(3)
        nom = c1.text_input("Nom")
        contact = c2.text_input("Contact")
        tel = c3.text_input("Téléphone")
        c1, c2, c3 = st.columns(3)
        email = c1.text_input("Email")
        ncc = c2.text_input("NCC")
        adr = c3.text_input("Adresse")
        spec = st.text_input("Spécialités (familles d'articles fournies)")
        if st.form_submit_button("Ajouter", type="primary"):
            if not nom.strip():
                st.error("Le nom est obligatoire.")
            elif nom.strip().upper() in {as_text(n).upper() for n in f["Nom"]}:
                st.error("Ce fournisseur existe déjà.")
            else:
                codes = [as_text(c) for c in f["Code Fournisseur"]]
                n = max([int(c[1:]) for c in codes if c[1:].isdigit()] + [0]) + 1
                ctx.get_store().append("FOURNISSEURS", [{
                    "Code Fournisseur": f"F{n:04d}", "Nom": nom.strip().upper(), "Contact": contact.strip(),
                    "Téléphone": tel.strip(), "Email": email.strip(), "NCC": ncc.strip(), "Adresse": adr.strip(),
                    "Actif": "TRUE", "Spécialités": spec.strip()}])
                ctx.refresh()
                st.rerun()
    if not len(f):
        st.caption("Les fournisseurs saisis dans un TCP ou un BC sont aussi ajoutés automatiquement.")
        return
    cols = TABLES["FOURNISSEURS"]
    v = f[cols].copy()
    for c in cols:
        v[c] = v[c].map(as_text)
    v["Actif"] = v["Actif"].map(lambda x: x.upper() not in ("FALSE", "FAUX", "0", "NON"))
    version = st.session_state.get("four_v", 0)
    ed = st.data_editor(v.sort_values("Nom"), hide_index=True, width="stretch", key=f"four-ed-{version}",
                        disabled=["Code Fournisseur"])
    if st.button("Enregistrer les modifications", key="four-save"):
        avant, apres = v.set_index("Code Fournisseur"), ed.set_index("Code Fournisseur")
        changes = {}
        for c in apres.index:
            diff = {col: (("TRUE" if apres.at[c, col] else "FALSE") if col == "Actif" else as_text(apres.at[c, col]))
                    for col in cols[1:] if (bool(apres.at[c, col]) != bool(avant.at[c, col]) if col == "Actif"
                                            else as_text(apres.at[c, col]) != as_text(avant.at[c, col]))}
            if diff:
                changes[c] = diff
        if changes:
            ctx.get_store().update_many("FOURNISSEURS", "Code Fournisseur", changes)
            ctx.refresh()
            st.session_state.four_v = version + 1
            st.rerun()


# =========================================================================== paramètres (admin)
def onglet_parametres(ctx):
    p = params(ctx)
    st.markdown("##### Paramètres de l'application")
    st.caption("Ces informations apparaissent sur les bons de commande et les TCP.")
    with st.form("param"):
        vals = {}
        cles = list(PARAMETRES_DEFAUT)
        for i in range(0, len(cles), 2):
            cols = st.columns(2)
            for c, cle in zip(cols, cles[i:i + 2]):
                vals[cle] = c.text_input(cle, value=p.get(cle, ""))
        if st.form_submit_button("Enregistrer les paramètres", type="primary"):
            if not to_num(vals["Prochain N° BC"]) or to_num(vals["Taux TVA (%)"]) < 0:
                st.error("« Prochain N° BC » et « Taux TVA » doivent être des nombres.")
            else:
                store = ctx.get_store()
                pr = store.read("PARAMETRES")
                presents = set(pr["Paramètre"].map(as_text))
                changes = {k: {"Valeur": v.strip()} for k, v in vals.items() if k in presents and v.strip() != p.get(k, "")}
                nouveaux = [{"Paramètre": k, "Valeur": v.strip()} for k, v in vals.items() if k not in presents]
                store.update_many("PARAMETRES", "Paramètre", changes)
                store.append("PARAMETRES", nouveaux)
                ctx.refresh()
                st.success("Paramètres enregistrés.")
                st.rerun()
    st.markdown("##### Conditions de règlement")
    cond = pd.DataFrame({"Condition": conditions(ctx)})
    ed = st.data_editor(cond, num_rows="dynamic", hide_index=True, width="stretch", key="cond-ed")
    if st.button("Enregistrer les conditions", key="cond-save"):
        liste = [as_text(x) for x in ed["Condition"] if as_text(x)]
        ctx.get_store().rewrite("CONDITIONS_REGLEMENT", pd.DataFrame({"Condition": list(dict.fromkeys(liste))}))
        ctx.refresh()
        st.rerun()


# =========================================================================== archivage annuel (admin)
_ARCHIVE = {"DA": "Date DA", "HISTORIQUE_ARTICLES": "DA_Date", "TCP": "Date TCP", "TCP_OFFRES": "Date TCP",
            "BC": "Date BC", "BC_LIGNES": "Date BC"}


def onglet_archivage(ctx):
    st.markdown("##### Archivage annuel")
    st.caption("Télécharge toutes les données d'une année dans un fichier Excel, puis les retire de la base "
               "pour que l'app reste rapide. Les DA validées pas encore entièrement commandées sont conservées.")
    annees = set()
    for t, col in _ARCHIVE.items():
        annees |= {d.year for d in ctx.load(t)[col].map(to_date) if d}
    annees = sorted(a for a in annees if a < date.today().year)
    if not annees:
        st.info("Rien à archiver : seules les années passées peuvent être archivées.")
        return
    an = st.selectbox("Année à archiver", annees, index=len(annees) - 1, key="arch-an")
    ouvertes = set(da_ouvertes(ctx)["ID_DA"].map(as_text)) if len(ctx.load("DA")) else set()
    parts, restes = {}, {}
    for t, col in _ARCHIVE.items():
        df = ctx.load(t)
        dans = df[col].map(lambda v: (to_date(v) or date(1900, 1, 1)).year == an).astype(bool)
        if t in ("DA", "HISTORIQUE_ARTICLES"):
            dans &= ~df["ID_DA"].map(as_text).isin(ouvertes).astype(bool)
        parts[t], restes[t] = df.loc[dans], df.loc[~dans]
    st.dataframe(pd.DataFrame({"Onglet": list(parts), "Lignes archivées": [len(x) for x in parts.values()],
                               "Lignes conservées": [len(x) for x in restes.values()]}),
                 hide_index=True, width="stretch")
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        for t, df in parts.items():
            df.to_excel(xw, sheet_name=t[:31], index=False)
    st.download_button(f"1. Télécharger l'archive {an} (Excel)", buf.getvalue(), file_name=f"Archive_DA_Achats_{an}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", type="primary",
                       key="arch-dl")
    ok = st.checkbox(f"J'ai téléchargé et rangé l'archive {an} en lieu sûr (Drive, serveur…)", key="arch-ok")
    conf = st.text_input(f"Tapez {an} pour confirmer", key="arch-conf")
    if st.button(f"2. Retirer l'année {an} de la base", disabled=not (ok and conf.strip() == str(an)), key="arch-go"):
        with st.spinner("Archivage…"):
            store = ctx.get_store()
            for t, df in restes.items():
                if len(parts[t]):
                    store.rewrite(t, df)
            ctx.refresh()
        st.success(f"Année {an} retirée de la base. Conservez bien le fichier d'archive.")


# =========================================================================== analyses achats
def analyses_achats(ctx):
    bc, bl, tcp = ctx.load("BC"), ctx.load("BC_LIGNES"), ctx.load("TCP")
    st.markdown("#### Achats")
    if not len(bc):
        st.info("Pas encore de bon de commande.")
        return
    val = bc.loc[bc["Statut"].map(as_text) == "Validé"].copy()
    val["HT"] = val["Total HT"].map(to_num)
    actifs = set(val["ID_BC"].map(as_text))
    blv = bl.loc[bl["ID_BC"].map(as_text).isin(actifs).astype(bool)].copy()
    blv["Montant"] = blv["Montant HT"].map(to_num)
    blv["PU"] = blv["Prix unitaire HT"].map(to_num)
    blv["Qté"] = blv["Quantité"].map(to_num)
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("BC validés", len(val))
    k2.metric("Montant HT validé", f"{fmt_montant(val['HT'].sum())} F")
    k3.metric("BC en attente", int((bc["Statut"] == "Émis").sum()))
    k4.metric("DA validées à commander", len(da_ouvertes(ctx)))

    val["Mois"] = val["Date BC"].map(lambda v: (to_date(v) or date(1900, 1, 1)).strftime("%Y-%m"))
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("##### Montant HT par fournisseur")
        st.dataframe(val.groupby("Fournisseur").agg(BC=("ID_BC", "size"), HT=("HT", "sum"))
                     .sort_values("HT", ascending=False).assign(HT=lambda d: d["HT"].map(fmt_montant)), width="stretch")
    with c2:
        st.markdown("##### Montant HT par chantier et par mois")
        pv = val.pivot_table(index="Chantier", columns="Mois", values="HT", aggfunc="sum", fill_value=0)
        st.dataframe(pv.map(fmt_montant), width="stretch")

    st.markdown("##### Prix par article (BC validés)")
    if len(blv):
        pr = (blv.groupby(["Désignation", "Référence", "Unité"])
              .agg(Commandes=("ID_BC", "nunique"), Qté=("Qté", "sum"), Fournisseurs=("Fournisseur", "nunique"),
                   **{"PU min": ("PU", "min"), "PU moyen": ("PU", "mean"), "PU max": ("PU", "max")}, Montant=("Montant", "sum"))
              .sort_values("Montant", ascending=False).reset_index())
        des = st.selectbox("Filtrer sur une désignation", sorted(pr["Désignation"].unique()), index=None,
                           key="an-ach-des", placeholder="Toutes")
        if des:
            pr = pr.loc[pr["Désignation"] == des]
        aff = pr.copy()
        for c in ("PU min", "PU moyen", "PU max", "Montant"):
            aff[c] = aff[c].map(fmt_montant)
        st.dataframe(aff.head(200), hide_index=True, width="stretch")
        if des:
            hist = blv.loc[blv["Désignation"] == des].sort_values("Date BC")
            st.dataframe(hist[["Date BC", "N° BC", "Référence", "Fournisseur", "Qté", "Unité", "PU"]]
                         .assign(PU=lambda d: d["PU"].map(fmt_montant)), hide_index=True, width="stretch")

    # délai DA validée -> BC
    da = ctx.load("DA")
    dv = dict(zip(da["ID_DA"].map(as_text), da["Date validation"].map(to_date)))
    delais = [(to_date(r["Date BC"]) - dv[as_text(r["ID_DA"])]).days for _, r in val.iterrows()
              if dv.get(as_text(r["ID_DA"])) and to_date(r["Date BC"])]
    tc = tcp.loc[tcp["Statut"].map(as_text) != "Abandonné"]
    c1, c2, c3 = st.columns(3)
    c1.metric("Délai moyen DA validée → BC", f"{sum(delais) / len(delais):.1f} j" if delais else "–")
    c2.metric("BC issus d'un TCP", f"{(val['ID_TCP'].map(as_text) != '').mean():.0%}" if len(val) else "–")
    ecart = (tc["Montant retenu HT"].map(to_num) - tc["Montant moins-disant HT"].map(to_num)).sum() if len(tc) else 0
    c3.metric("Surcoût des choix vs meilleur prix par article (TCP)", f"{fmt_montant(ecart)} F",
              help="Montant retenu − somme des prix les plus bas, sur les TCP non abandonnés")
