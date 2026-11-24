"""Travail de fond de l'IA sur les référentiels, avec garde-fou : journal et annulation.

- Catalogage automatique : à la soumission d'une DA (et sur demande), chaque article hors catalogue est analysé ;
  décision appliquée seulement si la confiance de l'IA est HAUTE (rattachement au même article, ou intégration
  d'un article clairement nouveau) ; sinon il reste dans « Articles à cataloguer » avec la proposition prête.
- Fournisseurs : spécialités déduites des achats (automatique) ; doublons de noms détectés et vérifiés par l'IA,
  fusion seulement après validation de l'administrateur (jamais seule) ; fiches complétées par recherche web
  (champs vides uniquement).
- Journal JOURNAL_IA : chaque action (qui, quoi, pourquoi, confiance) avec les valeurs d'avant ; « Annuler »
  remet les valeurs d'avant. Rien n'est supprimé du Sheet.
"""
from __future__ import annotations

import json

import pandas as pd
import streamlit as st

import ia
from logic import as_text, maintenant, nouvel_id, to_num

AUTEUR = "IA (automatique)"


# =========================================================================== journal
def journaliser(ctx, objet: str, action: str, detail: str, confiance: str, avant: list[dict], par: str = ""):
    """avant : [{"table", "cle", "id", "valeurs"}] — valeurs à remettre en cas d'annulation."""
    ctx.get_store().append("JOURNAL_IA", [{
        "ID_Action": nouvel_id(10), "Date": maintenant(), "Objet": objet, "Action": action, "Détail": detail[:900],
        "Confiance": confiance, "Annulation": json.dumps(avant, ensure_ascii=False, default=str)[:45000],
        "Statut": "Appliqué", "Par": par or AUTEUR}])


def annuler(ctx, id_action: str) -> str:
    j = ctx.load("JOURNAL_IA")
    r = j.loc[j["ID_Action"].map(as_text) == id_action]
    if not len(r) or as_text(r.iloc[0]["Statut"]) != "Appliqué":
        return "Action introuvable ou déjà annulée."
    store = ctx.get_store()
    par_table = {}
    for a in json.loads(as_text(r.iloc[0]["Annulation"]) or "[]"):
        par_table.setdefault((a["table"], a["cle"]), {})[a["id"]] = a["valeurs"]
    for (t, cle), maj in par_table.items():
        store.update_many(t, cle, maj)
    store.update_many("JOURNAL_IA", "ID_Action", {id_action: {"Statut": f"Annulé le {maintenant()[:16]} par "
                                                                      f"{ctx.user.get('Nom', '')}"}})
    ctx.refresh()
    return "Action annulée : les valeurs d'avant sont rétablies."


# =========================================================================== catalogage automatique
def cataloguer_auto(ctx, id_da: str | None = None, limite: int = 40) -> dict:
    """Analyse les propositions « À analyser » (d'une DA, ou toutes) ; applique les décisions de confiance haute.
    Renvoie {"appliques": n, "a_verifier": n}."""
    import catalogage
    if not ia.disponible():
        return {"appliques": 0, "a_verifier": 0}
    p = ctx.load("ARTICLES_A_CATALOGUER")
    att = p.loc[p["Statut"].map(as_text) == "À analyser"]
    if id_da:
        att = att.loc[att["ID_DA"].map(as_text) == id_da]
    if not len(att):
        return {"appliques": 0, "a_verifier": 0}
    art = ctx.load("ARTICLES")
    familles = sorted(set(art["Famille"].map(as_text)) - {""})
    g = (att.assign(D=att["Désignation"].map(catalogage._norm), R=att["Référence"].map(catalogage._norm))
         .groupby(["D", "R"], sort=False)
         .agg(Colisage=("Colisage", "last"), ids=("ID_Proposition", lambda x: "|".join(map(as_text, x))))
         .reset_index().head(limite))
    decisions = ia.proposer_catalogage([{"designation": d, "reference": r} for d, r in zip(g["D"], g["R"])],
                                       art, familles, ctx.version())
    codes = set(art["Code Article"].map(as_text))
    a_appliquer, n_ver = [], 0
    for d in decisions:
        i = d.get("index")
        if not isinstance(i, int) or not 0 <= i < len(g):
            continue
        ok = as_text(d.get("confiance")) == "haute" and (
            (d.get("decision") == "Rattacher" and as_text(d.get("code")).upper() in codes)
            or (d.get("decision") == "Intégrer" and as_text(d.get("designation"))
                and as_text(d.get("famille")) in familles))
        ids = g.at[i, "ids"]
        if not ok:  # proposition gardée pour l'administrateur
            n_ver += 1
            ctx.get_store().update_many("ARTICLES_A_CATALOGUER", "ID_Proposition", {
                x: {"Commentaire": f"Proposition IA ({as_text(d.get('confiance'))}) : {d.get('decision')} "
                                   f"{as_text(d.get('code')) or as_text(d.get('designation'))} "
                                   f"{as_text(d.get('reference'))} — {as_text(d.get('justification'))}"[:400]}
                for x in ids.split("|")})
            continue
        a_appliquer.append((i, d))
    if not a_appliquer:
        return {"appliques": 0, "a_verifier": n_ver}
    # valeurs d'avant (pour l'annulation)
    lignes_de = dict(zip(p["ID_Proposition"].map(as_text), p["ID_Ligne"].map(as_text)))
    h = ctx.load("HISTORIQUE_ARTICLES")
    hl = {as_text(i): r for i, r in zip(h["ID_Ligne"], h[["Code Article", "Désignation", "Référence"]]
                                         .to_dict("records"))}
    bcl = ctx.load("BC_LIGNES")
    bl = {as_text(i): r for i, r in zip(bcl["ID_LigneBC"], bcl[["Code Article", "Désignation", "Référence"]]
                                         .to_dict("records"))}
    lignes_dec = []
    for i, d in a_appliquer:
        integ = d.get("decision") == "Intégrer"
        lignes_dec.append({
            "ids": g.at[i, "ids"], "Décision": "Intégrer" if integ else "Rattacher",
            "Désignation saisie": g.at[i, "D"], "Désignation retenue": as_text(d.get("designation")).upper() if integ
            else g.at[i, "D"], "Référence retenue": as_text(d.get("reference")).upper() if integ else g.at[i, "R"],
            "Famille": as_text(d.get("famille")) if integ else "", "Colisage": as_text(d.get("colisage"))
            or as_text(g.at[i, "Colisage"]) or "Unité",
            "Code à rattacher": "" if integ else as_text(d.get("code")).upper(), "Article proche du catalogue": ""})
    for (i, d), dec in zip(a_appliquer, lignes_dec):
        avant = []
        for x in dec["ids"].split("|"):
            avant.append({"table": "ARTICLES_A_CATALOGUER", "cle": "ID_Proposition", "id": x,
                          "valeurs": {"Statut": "À analyser", "Code attribué": "", "Désignation retenue": "",
                                      "Référence retenue": "", "Famille": "", "Traité par": "", "Date traitement": ""}})
            lid = lignes_de.get(x, "")
            if lid.startswith(catalogage.PREFIXE_BC):
                k = lid[len(catalogage.PREFIXE_BC):]
                if k in bl:
                    avant.append({"table": "BC_LIGNES", "cle": "ID_LigneBC", "id": k, "valeurs": bl[k]})
            elif lid in hl:
                avant.append({"table": "HISTORIQUE_ARTICLES", "cle": "ID_Ligne", "id": lid, "valeurs": hl[lid]})
        from types import SimpleNamespace
        ctx_ia = SimpleNamespace(**{**vars(ctx), "user": {**ctx.user, "Nom": AUTEUR}}) if hasattr(ctx, "__dict__") else ctx
        catalogage.appliquer(ctx_ia, pd.DataFrame([dec]), ctx.load("ARTICLES"))
        cible = (f"rattaché à {dec['Code à rattacher']}" if dec["Décision"] == "Rattacher"
                 else f"intégré : {dec['Désignation retenue']} · {dec['Référence retenue']} ({dec['Famille']})")
        journaliser(ctx, "Article", f"Catalogage automatique — {dec['Décision']}",
                    f"« {dec['Désignation saisie']} {g.at[i, 'R']} » {cible}. {as_text(d.get('justification'))}",
                    "haute", avant)
    ctx.refresh()
    return {"appliques": len(a_appliquer), "a_verifier": n_ver}


# =========================================================================== fournisseurs
def specialites_auto(ctx) -> int:
    """Spécialités vides complétées par les familles réellement achetées (sans IA, faible risque)."""
    import stats
    df = stats.base(ctx)
    if not len(df):
        return 0
    df = df.loc[~df["aberrant"] & (df["famille"] != stats.HORS)]
    top = df.groupby(["four", "famille"])["montant"].sum().reset_index().sort_values("montant", ascending=False)
    fam_de = top.groupby("four")["famille"].apply(lambda x: ", ".join(list(x)[:3])).to_dict()
    f = ctx.load("FOURNISSEURS")
    maj, avant = {}, []
    for _, r in f.iterrows():
        nom = " ".join(as_text(r["Nom"]).upper().split())
        if not as_text(r.get("Spécialités")) and fam_de.get(nom):
            maj[as_text(r["Code Fournisseur"])] = {"Spécialités": fam_de[nom]}
            avant.append({"table": "FOURNISSEURS", "cle": "Code Fournisseur", "id": as_text(r["Code Fournisseur"]),
                          "valeurs": {"Spécialités": ""}})
    if maj:
        ctx.get_store().update_many("FOURNISSEURS", "Code Fournisseur", maj)
        journaliser(ctx, "Fournisseur", "Spécialités déduites des achats",
                    f"{len(maj)} fiche(s) complétée(s) à partir des familles achetées.", "haute", avant)
        ctx.refresh()
    return len(maj)


def groupes_doublons(ctx) -> list[list[dict]]:
    """Groupes de fournisseurs actifs aux noms proches (détection locale, sans IA)."""
    from sourcing import _plat, meme_fournisseur
    import stats
    f = ctx.load("FOURNISSEURS")
    f = f.loc[(f["Statut"].map(as_text) != "Fusionné")]
    df = stats.base(ctx)
    achats = df.groupby("four")["montant"].sum().to_dict() if len(df) else {}
    noms = [(as_text(c), " ".join(as_text(n).upper().split()), as_text(t), as_text(e))
            for c, n, t, e in zip(f["Code Fournisseur"], f["Nom"], f["Téléphone"], f["Email"]) if as_text(n)]
    vus, groupes = set(), []
    for i, (c, n, t, e) in enumerate(noms):
        if c in vus:
            continue
        g = [(c, n, t, e)]
        for c2, n2, t2, e2 in noms[i + 1:]:
            if c2 not in vus and (meme_fournisseur(n, n2) or (_plat(n) and _plat(n) == _plat(n2))
                                  or (t and t == t2) or (e and e.lower() == e2.lower())):
                g.append((c2, n2, t2, e2))
        if len(g) > 1:
            vus |= {x[0] for x in g}
            groupes.append([{"code": x[0], "nom": x[1], "telephone": x[2], "email": x[3],
                             "achats_ht": round(achats.get(x[1], 0))} for x in g])
    return groupes[:60]


def fusionner(ctx, codes: list[str], nom_retenu: str, justification: str = "") -> str:
    """Fusion validée par l'admin : le nom retenu est gardé sur la fiche principale (la plus active) ; les autres
    fiches passent « Fusionné » (inactives) ; les BC de l'application sont renommés. L'historique importé reste
    tel quel mais est regroupé dans les analyses (alias)."""
    f = ctx.load("FOURNISSEURS")
    f = f.loc[f["Code Fournisseur"].map(as_text).isin(codes)]
    if len(f) < 2:
        return "Sélection insuffisante."
    nom_retenu = " ".join(as_text(nom_retenu).upper().split())
    bc = ctx.load("BC")
    nb_bc = bc["Fournisseur"].map(lambda x: " ".join(as_text(x).upper().split())).value_counts().to_dict()
    f = f.assign(n=f["Nom"].map(lambda x: nb_bc.get(" ".join(as_text(x).upper().split()), 0))).sort_values(
        "n", ascending=False)
    principal = f.iloc[0]
    autres = f.iloc[1:]
    noms_autres = {" ".join(as_text(x).upper().split()) for x in f["Nom"]} - {nom_retenu}
    store = ctx.get_store()
    avant = [{"table": "FOURNISSEURS", "cle": "Code Fournisseur", "id": as_text(r["Code Fournisseur"]),
              "valeurs": {"Nom": as_text(r["Nom"]), "Statut": as_text(r.get("Statut")), "Actif": as_text(r["Actif"]),
                          "Source": as_text(r.get("Source"))}} for _, r in f.iterrows()]
    maj = {as_text(principal["Code Fournisseur"]): {"Nom": nom_retenu, "Statut": as_text(principal.get("Statut"))
                                                    if as_text(principal.get("Statut")) != "Prospect" or principal["n"]
                                                    else "Prospect"}}
    for _, r in autres.iterrows():
        maj[as_text(r["Code Fournisseur"])] = {"Statut": "Fusionné", "Actif": "FALSE", "Source": f"→ {nom_retenu}"}
    # contacts manquants repris des fiches fusionnées
    for c in ("Contact", "Téléphone", "Email", "NCC", "Adresse", "Spécialités", "Site web", "Pays", "Ville"):
        if c in f.columns and not as_text(principal.get(c)):
            v = next((as_text(x) for x in autres[c] if as_text(x)), "")
            if v:
                maj[as_text(principal["Code Fournisseur"])][c] = v
                avant[0]["valeurs"][c] = ""
    store.update_many("FOURNISSEURS", "Code Fournisseur", maj)
    bcs = bc.loc[bc["Fournisseur"].map(lambda x: " ".join(as_text(x).upper().split())).isin(noms_autres)]
    if len(bcs):
        avant += [{"table": "BC", "cle": "ID_BC", "id": as_text(r["ID_BC"]), "valeurs": {"Fournisseur": as_text(r["Fournisseur"])}}
                  for _, r in bcs.iterrows()]
        store.update_many("BC", "ID_BC", {as_text(i): {"Fournisseur": nom_retenu} for i in bcs["ID_BC"]})
    journaliser(ctx, "Fournisseur", "Fusion de doublons (validée par l'administrateur)",
                f"{', '.join(sorted(noms_autres))} → {nom_retenu}. {justification}", "validée", avant,
                par=f"{ctx.user.get('Nom', '')} (proposition IA)")
    ctx.refresh()
    return f"Fusion faite : {len(autres)} fiche(s) regroupée(s) sous « {nom_retenu} », {len(bcs)} BC renommé(s)."


def alias_fournisseurs(ctx) -> dict[str, str]:
    """Nom fusionné -> nom retenu (pour regrouper l'historique dans les analyses)."""
    f = ctx.load("FOURNISSEURS")
    f = f.loc[(f["Statut"].map(as_text) == "Fusionné") & f["Source"].map(as_text).str.startswith("→ ")]
    return {" ".join(as_text(n).upper().split()): as_text(s)[2:].strip() for n, s in zip(f["Nom"], f["Source"])}


_OUTIL_FICHE = {
    "name": "fiche_fournisseur",
    "description": "Coordonnées publiques du fournisseur.",
    "input_schema": {"type": "object", "properties": {
        "trouve": {"type": "boolean"}, "telephone": {"type": "string"}, "email": {"type": "string"},
        "site_web": {"type": "string"}, "ville": {"type": "string"}, "pays": {"type": "string"},
        "adresse": {"type": "string"}, "activite": {"type": "string"}, "source_url": {"type": "string"}},
        "required": ["trouve"]}}


def completer_fiche(ctx, code: str) -> str:
    """Recherche web des coordonnées : seuls les champs VIDES sont remplis ; journalisé et annulable."""
    f = ctx.load("FOURNISSEURS")
    r = f.loc[f["Code Fournisseur"].map(as_text) == code]
    if not len(r):
        return "Fournisseur introuvable."
    r = r.iloc[0]
    res, _urls, _n = ia.recherche_web(
        ia.CONTEXTE_PWC + " Trouve les coordonnées publiques de cette entreprise (fournisseur de PWC, Côte d'Ivoire "
        "en priorité). N'invente rien : laisse vide ce qui n'est pas trouvé ; trouve = false si l'entreprise n'est "
        "pas identifiée avec certitude.",
        json.dumps({"nom": as_text(r["Nom"]), "ville_connue": as_text(r.get("Ville")) or as_text(r.get("Adresse")),
                    "specialites": as_text(r.get("Spécialités"))}, ensure_ascii=False), _OUTIL_FICHE, 3)
    if not res.get("trouve"):
        return f"{as_text(r['Nom'])} : non identifié avec certitude sur le web."
    champs = {"Téléphone": "telephone", "Email": "email", "Site web": "site_web", "Ville": "ville", "Pays": "pays",
              "Adresse": "adresse"}
    maj = {c: as_text(res.get(k)) for c, k in champs.items() if not as_text(r.get(c)) and as_text(res.get(k))}
    if not as_text(r.get("Spécialités")) and as_text(res.get("activite")):
        maj["Spécialités"] = as_text(res["activite"])[:300]
    if not maj:
        return f"{as_text(r['Nom'])} : rien de nouveau à compléter."
    if not as_text(r.get("Source")):
        maj["Source"] = as_text(res.get("source_url"))
    ctx.get_store().update_many("FOURNISSEURS", "Code Fournisseur", {code: maj})
    journaliser(ctx, "Fournisseur", "Fiche complétée par recherche web",
                f"{as_text(r['Nom'])} : {', '.join(maj)} (source : {as_text(res.get('source_url'))})", "web",
                [{"table": "FOURNISSEURS", "cle": "Code Fournisseur", "id": code, "valeurs": {c: "" for c in maj}}])
    ctx.refresh()
    return f"{as_text(r['Nom'])} : {len(maj)} champ(s) complété(s)."


# =========================================================================== écran administrateur
def onglet_ia_referentiels(ctx):
    st.caption("L'IA entretient le catalogue et les fournisseurs : ce qui est certain est appliqué automatiquement "
               "(et annulable), le reste vous est proposé. Rien n'est supprimé.")
    if st.session_state.get("aia_msg"):
        st.success(st.session_state.pop("aia_msg"))
    if not ia.disponible():
        st.info("IA non configurée (section [ia] des secrets).")
    vue = st.segmented_control("Vue", ["📦 Catalogue", "🏢 Fournisseurs", "📜 Journal IA"], default="📦 Catalogue",
                               key="aia-vue", label_visibility="collapsed") or "📦 Catalogue"
    if vue == "📦 Catalogue":
        p = ctx.load("ARTICLES_A_CATALOGUER")
        n = int((p["Statut"].map(as_text) == "À analyser").sum())
        st.markdown(f"**{n}** article(s) hors catalogue en attente.")
        st.caption("À chaque soumission de DA, les nouveaux articles hors catalogue sont traités automatiquement. Ce "
                   "bouton traite tout ce qui est en attente (40 libellés par passage).")
        if st.button("🤖 Cataloguer automatiquement maintenant", type="primary", disabled=not n or not ia.disponible(),
                     key="aia-cat"):
            with st.spinner("L'IA analyse les articles hors catalogue…"):
                try:
                    r = cataloguer_auto(ctx)
                    st.session_state.aia_msg = (f"{r['appliques']} article(s) catalogué(s) automatiquement (confiance "
                                                f"haute) ; {r['a_verifier']} laissé(s) à votre décision dans "
                                                "« Articles à cataloguer » avec la proposition de l'IA.")
                    st.rerun()
                except ia.IAErreur as e:
                    st.error(str(e))
    elif vue == "🏢 Fournisseurs":
        a, b = st.columns(2)
        if a.button("🏷️ Compléter les spécialités (d'après les achats)", key="aia-spec"):
            st.session_state.aia_msg = f"{specialites_auto(ctx)} fiche(s) fournisseur complétée(s)."
            st.rerun()
        if b.button("🔍 Rechercher les doublons", key="aia-dbl", disabled=not ia.disponible()):
            with st.spinner("Détection des noms proches puis vérification par l'IA…"):
                g = groupes_doublons(ctx)
                try:
                    v = ia.verifier_doublons_fournisseurs(g) if g else []
                except ia.IAErreur as e:
                    st.error(str(e))
                    v = []
                st.session_state["aia-dbl-res"] = [(g[x["index"]], x) for x in v
                                                   if isinstance(x.get("index"), int) and x["index"] < len(g)
                                                   and x.get("meme_societe")]
        res = st.session_state.get("aia-dbl-res")
        if res is not None:
            if not res:
                st.success("Aucun doublon de fournisseur détecté.")
            for k, (grp, v) in enumerate(res):
                with st.container(border=True):
                    noms_ok = set(v.get("noms") or [x["nom"] for x in grp])
                    st.markdown(f"**Même société ?** {', '.join(x['nom'] for x in grp)}")
                    st.caption(as_text(v.get("justification")))
                    sel = st.multiselect("Fiches à regrouper", [x["code"] for x in grp],
                                         default=[x["code"] for x in grp if x["nom"] in noms_ok or not v.get("noms")],
                                         format_func=lambda c, g_=grp: next(f"{x['nom']} ({c}) — achats "
                                                                            f"{x['achats_ht']:,.0f} F".replace(",", " ")
                                                                            for x in g_ if x["code"] == c),
                                         key=f"aia-f-sel-{k}")
                    nom = st.text_input("Nom retenu", value=as_text(v.get("nom_retenu")), key=f"aia-f-nom-{k}")
                    if st.button("Fusionner", key=f"aia-f-go-{k}", disabled=len(sel) < 2 or not nom.strip()):
                        st.session_state.aia_msg = fusionner(ctx, sel, nom, as_text(v.get("justification")))
                        st.session_state["aia-dbl-res"] = [x for j, x in enumerate(res) if j != k]
                        st.rerun()
        st.markdown("###### Compléter des fiches par recherche web (champs vides seulement)")
        f = ctx.load("FOURNISSEURS")
        f = f.loc[f["Statut"].map(as_text) != "Fusionné"]
        vides = f.loc[(f["Téléphone"].map(as_text) == "") | (f["Email"].map(as_text) == "")]
        lab = dict(zip(vides["Code Fournisseur"].map(as_text), vides["Nom"].map(as_text)))
        sel = st.multiselect(f"Fournisseurs sans téléphone ou email ({len(lab)})", list(lab), format_func=lab.get,
                             max_selections=10, key="aia-web-sel")
        if st.button(f"🌐 Compléter {len(sel)} fiche(s)", disabled=not sel or not ia.disponible(), key="aia-web-go"):
            out = []
            barre = st.progress(0.0)
            for i, c in enumerate(sel):
                barre.progress(i / len(sel), text=lab[c])
                try:
                    out.append(completer_fiche(ctx, c))
                except ia.IAErreur as e:
                    out.append(f"{lab[c]} : {e}")
            st.session_state.aia_msg = " · ".join(out)
            st.rerun()
    else:
        j = ctx.load("JOURNAL_IA")
        if not len(j):
            st.info("Aucune action automatique pour l'instant.")
            return
        j = j.sort_values("Date", ascending=False).head(300)
        st.dataframe(j[["Date", "Objet", "Action", "Détail", "Confiance", "Statut", "Par"]], hide_index=True,
                     width="stretch")
        ann = j.loc[j["Statut"].map(as_text) == "Appliqué"]
        lab = {as_text(i): f"{as_text(d)[:16]} · {as_text(a)} · {as_text(t)[:80]}" for i, d, a, t in
               zip(ann["ID_Action"], ann["Date"], ann["Action"], ann["Détail"])}
        sel = st.selectbox("Annuler une action", list(lab), format_func=lab.get, index=None, key="aia-ann")
        if sel and st.button("↩️ Annuler cette action", key="aia-ann-go"):
            st.session_state.aia_msg = annuler(ctx, sel)
            st.rerun()
