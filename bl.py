"""Réception par import des bons de livraison (photo ou PDF).

1. import d'un ou plusieurs BL ; 2. lecture par Claude (fournisseur, n° BL, date, n° de commande, articles,
quantités) ; 3. recherche du BC correspondant (n° de BC cité, fournisseur, articles) ; 4. rapprochement ligne à
ligne et écarts ; 5. enregistrement dans LIVRAISONS (workflow existant) et fiche du document dans DOCUMENTS, le
fichier étant archivé dans Google Drive (documents.py).
Accès : acheteurs et admin (tous les BC), demandeurs et chefs de service (BC de leur chantier ou de leurs DA).
"""
from __future__ import annotations

import difflib
import re
from datetime import date

import pandas as pd
import streamlit as st

import documents
import ia
from logic import as_text, fmt_date, to_num
from sourcing import _plat, meme_fournisseur

TOUS_LES_BC = {"Acheteur", "Admin"}


def _bc_visibles(ctx) -> tuple[pd.DataFrame, pd.DataFrame]:
    from achats import _suivi_bc
    bc, bl = _suivi_bc(ctx)
    u = ctx.user
    if u["Rôle"] not in TOUS_LES_BC:
        da = ctx.load("DA")
        mes_da = set(da.loc[(da["Demandeur"].map(as_text) == u["Nom"])
                            | ((da["Email Demandeur"].map(as_text) == as_text(u.get("Email")))
                               & (as_text(u.get("Email")) != "")), "ID_DA"].map(as_text))
        ch = as_text(u.get("Chantier"))
        bc = bc.loc[bc["ID_DA"].map(as_text).isin(mes_da).astype(bool)
                    | ((bc["Chantier"].map(as_text) == ch) & (ch != ""))]
        bl = bl.loc[bl["ID_BC"].map(as_text).isin(set(bc["_id"])).astype(bool)]
    return bc, bl


def _chiffres(t: str) -> str:
    return re.sub(r"\D", "", as_text(t)).lstrip("0")


def _mots(t: str) -> set[str]:
    return {m for m in _plat(t).split() if len(m) >= 3}


def candidats_bc(doc: dict, bc: pd.DataFrame, bl: pd.DataFrame, n: int = 5) -> list[tuple[float, str, str]]:
    """BC les plus probables pour un document : (score /100, id BC, explication)."""
    refs = [_chiffres(r) for r in (doc.get("references_commande") or []) + [doc.get("numero", "")]]
    refs = [r for r in refs if len(r) >= 3]
    four = as_text(doc.get("fournisseur"))
    lignes_doc = [_mots(f"{as_text(l.get('designation'))} {as_text(l.get('reference'))}") for l in doc.get("lignes") or []]
    out = []
    for _, b in bc.loc[bc["Livraison"] != "Livré"].iterrows():
        s, pourquoi = 0.0, []
        nb = _chiffres(b["N° BC"])
        if nb and any(r == nb or (len(nb) >= 3 and (r.endswith(nb) or nb.endswith(r))) for r in refs):
            s += 60
            pourquoi.append("n° de BC cité")
        if four:
            if meme_fournisseur(four, as_text(b["Fournisseur"])):
                s += 25
                pourquoi.append("même fournisseur")
            else:
                s += 25 * max(0.0, difflib.SequenceMatcher(None, _plat(four), _plat(b["Fournisseur"])).ratio() - 0.5) * 2
        lbc = [_mots(f"{as_text(d)} {as_text(r)}") for d, r in
               zip(*[bl.loc[bl["ID_BC"].map(as_text) == b["_id"], c] for c in ("Désignation", "Référence")])]
        if lignes_doc and lbc:
            ok = sum(any(len(m & x) / max(1, len(m | x)) >= 0.3 for x in lbc) for m in lignes_doc if m)
            frac = ok / max(1, len([m for m in lignes_doc if m]))
            s += 15 * frac
            if frac >= 0.5:
                pourquoi.append(f"{ok} article(s) en commun")
        if s > 10:
            out.append((round(s), b["_id"], ", ".join(pourquoi) or "ressemblance faible"))
    return sorted(out, reverse=True)[:n]


def _ecart(recu: float, reste: float) -> str:
    if recu <= 0:
        return "Non livré sur ce BL"
    if abs(recu - reste) < 1e-9:
        return "Conforme (solde)"
    if recu < reste:
        return f"Partiel : reste {reste - recu:g}"
    return f"Surplus : +{recu - reste:g}"


def _date_doc(doc: dict):
    try:
        return date.fromisoformat(as_text(doc.get("date"))[:10])
    except ValueError:
        return date.today()


# =========================================================================== écran
def onglet_bl(ctx):
    st.caption("Importez le bon de livraison (photo ou PDF) : l'IA le lit, retrouve le bon de commande et prépare "
               "la réception ligne à ligne. Vous vérifiez, puis enregistrez.")
    if st.session_state.get("bl_msg"):
        st.success(st.session_state.pop("bl_msg"))
    if not documents.configure() and ctx.user["Rôle"] == "Admin":
        st.info("Archivage des fichiers dans Google Drive non configuré : les BL sont lus et enregistrés, mais le "
                "fichier n'est pas conservé. Voir apps_script_documents.gs (section [drive] des secrets).")
    bc, bl = _bc_visibles(ctx)
    vue = st.segmented_control("BL", ["Importer des BL", "BL enregistrés"], default="Importer des BL",
                               key="bl-vue", label_visibility="collapsed") or "Importer des BL"
    if vue == "BL enregistrés":
        _historique(ctx, bc)
        return
    if not len(bc.loc[bc["Livraison"] != "Livré"]):
        st.info("Aucun bon de commande en attente de livraison" +
                ("." if ctx.user["Rôle"] in TOUS_LES_BC else " pour votre chantier."))
        return
    v = st.session_state.get("bl_up_v", 0)
    fichiers = st.file_uploader("Bons de livraison", type=["jpg", "jpeg", "png", "webp", "pdf"],
                                accept_multiple_files=True, key=f"bl-up-{v}",
                                help="Plusieurs fichiers possibles ; sur téléphone : appareil photo")
    docs = st.session_state.setdefault("bl_docs", [])
    if fichiers and st.button(f"🔍 Lire {len(fichiers)} document(s)", type="primary", key="bl-lire"):
        deja = set(ctx.load("DOCUMENTS")["Empreinte"].map(as_text))
        for f in fichiers:
            octets = f.getvalue()
            emp = documents.empreinte(octets)
            if emp in deja or emp in {d["empreinte"] for d in docs}:
                st.warning(f"{f.name} : ce fichier a déjà été importé.")
                continue
            lu = {"lignes": []}
            if ia.disponible():
                with st.spinner(f"Lecture de {f.name}…"):
                    try:
                        lu = ia.lire_document(f.name, octets)
                    except ia.IAErreur as e:
                        st.error(f"{f.name} : {e}")
                    except Exception as e:
                        st.error(f"{f.name} : lecture impossible ({e}).")
            docs.append({"nom": f.name, "octets": octets, "empreinte": emp, "lu": lu, "rappro": {}})
        st.session_state.bl_up_v = v + 1
        st.rerun()
    if not docs:
        if not ia.disponible():
            st.caption("IA non configurée : importez le BL puis choisissez le BC et saisissez les quantités.")
        return
    i = st.selectbox("Document", range(len(docs)), key="bl-doc",
                     format_func=lambda k: f"{docs[k]['nom']} — {as_text(docs[k]['lu'].get('fournisseur'))} "
                                           f"{as_text(docs[k]['lu'].get('numero'))}".strip())
    if i is None or i >= len(docs):
        return
    _traiter(ctx, docs, i, bc, bl)


def _traiter(ctx, docs: list, i: int, bc: pd.DataFrame, bl: pd.DataFrame):
    from achats import _enregistre_livraisons, _ligne_livraison
    d = docs[i]
    lu = d["lu"]
    c1, c2 = st.columns([1, 2])
    with c1:
        if d["nom"].lower().endswith(".pdf"):
            st.caption(f"📄 {d['nom']} (PDF)")
        else:
            st.image(d["octets"], width="stretch")
    with c2:
        st.markdown(f"**{as_text(lu.get('type_document')).capitalize() or 'Document'}** "
                    f"{as_text(lu.get('numero'))} · {as_text(lu.get('fournisseur'))} · "
                    f"{fmt_date(lu.get('date')) or 'date non lue'}")
        if lu.get("references_commande"):
            st.caption("Commande citée : " + ", ".join(map(as_text, lu["references_commande"])))
        if lu.get("remarques"):
            st.caption(f"⚠ {lu['remarques']}")
        cands = candidats_bc(lu, bc, bl)
        lab = {b["_id"]: f"{as_text(b['N° BC'])} · {as_text(b['Fournisseur'])} · {fmt_date(b['Date BC'])} · "
                         f"{b['Livraison']}" for _, b in bc.loc[bc["Livraison"] != "Livré"].iterrows()}
        score = {c[1]: c for c in cands}
        ordre = [c[1] for c in cands] + [k for k in lab if k not in score]
        if cands:
            st.caption(f"BC le plus probable : {lab[cands[0][1]].split(' · ')[0]} — score {cands[0][0]}/100 "
                       f"({cands[0][2]})." + (" Vérifiez : score faible." if cands[0][0] < 60 else ""))
        idb = st.selectbox("Bon de commande correspondant", ordre, key=f"bl-bc-{d['empreinte']}",
                           format_func=lambda k: lab[k] + (f" — {score[k][0]}/100" if k in score else ""))
    if not idb:
        return
    b = bc.loc[bc["_id"] == idb].iloc[0]
    lg = bl.loc[bl["ID_BC"].map(as_text) == idb].sort_values("N° Ligne")
    if idb not in d["rappro"] and lu.get("lignes") and ia.disponible():
        with st.spinner("Rapprochement des lignes du BL avec le BC…"):
            try:
                d["rappro"][idb] = ia.rapprocher(lu, [
                    {"id": as_text(x["ID_LigneBC"]), "designation": as_text(x["Désignation"]),
                     "reference": as_text(x["Référence"]), "quantite_commandee": float(x["Cmd"]),
                     "unite": as_text(x["Unité"])} for _, x in lg.iterrows()])
            except ia.IAErreur as e:
                st.error(str(e))
                d["rappro"][idb] = {}
    m = d["rappro"].get(idb, {})
    g = pd.DataFrame({"ID": lg["ID_LigneBC"].map(as_text).values, "Désignation": lg["Désignation"].map(as_text).values,
                      "Référence": lg["Référence"].map(as_text).values, "Unité": lg["Unité"].map(as_text).values,
                      "Commandé": lg["Cmd"].values, "Déjà livré": lg["Liv"].values, "Reste": lg["Reste"].values})
    g["Lu sur le BL"] = [as_text(m[x].get("designation")) if x in m else "" for x in g["ID"]]
    g["Qté reçue"] = [float(to_num(m[x].get("quantite"))) if x in m and m[x].get("quantite") is not None else 0.0
                      for x in g["ID"]]
    ed = st.data_editor(g, hide_index=True, width="stretch", key=f"bl-ed-{d['empreinte']}-{idb}",
                        disabled=["Désignation", "Référence", "Unité", "Commandé", "Déjà livré", "Reste",
                                  "Lu sur le BL"],
                        column_config={"ID": None, "Qté reçue": st.column_config.NumberColumn(min_value=0.0,
                                                                                              format="%.2f")})
    ed["Écart"] = [_ecart(to_num(q), r) for q, r in zip(ed["Qté reçue"], ed["Reste"])]
    hors = [as_text(l.get("designation")) for k, l in enumerate(lu.get("lignes") or [])
            if k not in {_index(lu, m[x]) for x in m}]
    alertes = [f"{r['Désignation']} : {r['Écart']}" for _, r in ed.iterrows()
               if r["Écart"].startswith(("Partiel", "Surplus"))]
    if hors:
        alertes.append("Sur le BL mais pas dans le BC : " + ", ".join(hors))
    st.dataframe(ed.loc[ed["Qté reçue"] > 0, ["Désignation", "Qté reçue", "Reste", "Écart"]], hide_index=True,
                 width="stretch")
    for a in alertes:
        st.warning(a)
    c1, c2, c3 = st.columns(3)
    n_bl = c1.text_input("N° BL", value=as_text(lu.get("numero")), key=f"bl-num-{d['empreinte']}")
    d_liv = c2.date_input("Date de livraison", value=_date_doc(lu), format="DD/MM/YYYY", key=f"bl-d-{d['empreinte']}")
    obs = c3.text_input("Observation / réserves", key=f"bl-o-{d['empreinte']}")
    recu = ed.loc[ed["Qté reçue"].map(to_num) > 0]
    surplus = recu.loc[recu["Qté reçue"].map(to_num) > recu["Reste"] + 1e-9]
    ok = True
    if len(surplus):
        ok = st.checkbox("Je confirme les quantités en surplus", key=f"bl-sur-{d['empreinte']}")
    lv = ctx.load("LIVRAISONS")
    doublon = n_bl.strip() and bool(((lv["N° BL"].map(as_text).str.upper() == n_bl.strip().upper())
                                     & (lv["ID_BC"].map(as_text) == idb)).any())
    if doublon:
        st.error(f"Le BL {n_bl} est déjà enregistré sur ce BC.")
    a, z = st.columns([2, 1])
    if a.button(f"✅ Enregistrer la réception ({len(recu)} ligne(s))", type="primary",
                disabled=not len(recu) or doublon or not ok, key=f"bl-save-{d['empreinte']}"):
        fiche = documents.fiche(ctx, {
            "Type": "Bon de livraison", "N° document": n_bl.strip(), "Date document": d_liv.isoformat(),
            "Fournisseur": as_text(b["Fournisseur"]), "ID_BC": idb, "N° BC": as_text(b["N° BC"]),
            "ID_DA": as_text(b["ID_DA"]), "N° DA": as_text(b["N° DA"]), "Chantier": as_text(b["Chantier"]),
            "Nom fichier": d["nom"], "Empreinte": d["empreinte"], "Lu par IA": "Oui" if lu.get("lignes") else "Non",
            "Écarts": " ; ".join(alertes)[:1000]})
        if documents.configure():
            with st.spinner("Archivage du document dans Google Drive…"):
                try:
                    nom, octets, mime = documents.preparer(d["nom"], d["octets"])
                    nom = f"BL {n_bl.strip() or 'sans n°'} - {as_text(b['N° BC'])} - {as_text(b['Fournisseur'])} - {nom}"
                    fiche["ID Drive"], fiche["Lien"] = documents.envoyer(
                        nom, octets, mime, f"{d_liv:%Y}/{d_liv:%m}", f"{as_text(b['N° BC'])} / DA {as_text(b['N° DA'])}")
                    fiche["Nom fichier"] = nom
                except documents.DocErreur as e:
                    st.warning(f"{e} La réception est enregistrée sans le fichier.")
        ctx.get_store().append("DOCUMENTS", [fiche])
        rows = []
        idx = lg.set_index(lg["ID_LigneBC"].map(as_text))
        for _, r in recu.iterrows():
            row = _ligne_livraison(ctx, b, idx.loc[r["ID"]], to_num(r["Qté reçue"]), d_liv,
                                   (obs.strip() + f" (BL {n_bl.strip()})" if n_bl.strip() else obs.strip()).strip())
            row.update({"N° BL": n_bl.strip(), "ID_Document": fiche["ID_Document"], "Écart": r["Écart"]})
            rows.append(row)
        _enregistre_livraisons(ctx, rows, {idb})
        docs.pop(i)
        st.session_state.bl_msg = (f"BL {n_bl or ''} enregistré sur {as_text(b['N° BC'])} ({len(rows)} ligne(s))"
                                   + (" — écarts signalés." if alertes else "."))
        st.rerun()
    if z.button("Retirer ce document", key=f"bl-rm-{d['empreinte']}"):
        docs.pop(i)
        st.rerun()


def _index(lu: dict, ligne: dict) -> int:
    for k, l in enumerate(lu.get("lignes") or []):
        if all(l.get(c) == ligne.get(c) for c in ("designation", "quantite")):
            return k
    return -1


def _historique(ctx, bc: pd.DataFrame):
    t = ctx.load("DOCUMENTS")
    t = t.loc[t["Type"].map(as_text) == "Bon de livraison"]
    if ctx.user["Rôle"] not in TOUS_LES_BC:
        t = t.loc[t["ID_BC"].map(as_text).isin(set(bc["_id"])).astype(bool)
                  | (t["Saisi par"].map(as_text) == ctx.user["Nom"])]
    if not len(t):
        st.info("Aucun BL importé.")
        return
    q = st.text_input("Rechercher (N° BL, BC, DA, fournisseur)", key="bl-h-q")
    if q.strip():
        t = t.loc[t[["N° document", "N° BC", "N° DA", "Fournisseur"]].astype(str)
                  .apply(lambda r: q.strip().lower() in " ".join(r).lower(), axis=1).astype(bool)]
    t = t.sort_values("Créé le", ascending=False).head(300)
    v = pd.DataFrame({"N° BL": t["N° document"].map(as_text), "Date": t["Date document"].map(fmt_date),
                      "Fournisseur": t["Fournisseur"].map(as_text), "N° BC": t["N° BC"].map(as_text),
                      "N° DA": t["N° DA"].map(as_text), "Écarts": t["Écarts"].map(as_text),
                      "Lu par IA": t["Lu par IA"].map(as_text), "Saisi par": t["Saisi par"].map(as_text),
                      "Archivé": t["ID Drive"].map(lambda x: "✓" if as_text(x) else "")})
    st.dataframe(v, hide_index=True, width="stretch")
    lab = dict(zip(t["ID_Document"].map(as_text), v["N° BL"] + " · " + v["N° BC"] + " · " + v["Fournisseur"]))
    sel = st.selectbox("Ouvrir un BL", list(lab), format_func=lab.get, index=None, key="bl-h-sel")
    if sel:
        documents.bouton_telecharger(ctx, t.loc[t["ID_Document"].map(as_text) == sel].iloc[0].to_dict(), f"blh-{sel}")
