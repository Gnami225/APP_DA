"""Articles saisis hors catalogue : file d'attente « à cataloguer », analyse puis intégration au catalogue.

Chaque ligne de DA sans code article crée une proposition (onglet ARTICLES_A_CATALOGUER). L'administrateur
les voit regroupées par désignation + référence et, pour chaque groupe :
- Intégrer : nouvel article du catalogue (code généré selon la famille) ;
- Rattacher : l'article existe déjà sous un autre libellé, on reprend son code ;
- Rejeter : l'article reste hors catalogue.
Dans les deux premiers cas, les lignes de DA et de BC concernées reçoivent le code, pour l'analytique."""
from __future__ import annotations

import difflib
import io
import re
import unicodedata
from datetime import date

import pandas as pd
import streamlit as st

from logic import as_text, maintenant, nouvel_id

DECISIONS = ["", "Intégrer", "Rattacher", "Rejeter"]
PREFIXE_BC = "BC:"  # proposition issue d'une ligne de BC (reprise de BC antérieurs) : ID_LigneBC après le préfixe


def _norm(t) -> str:
    return " ".join(as_text(t).upper().split())


def _plat(t) -> str:
    t = unicodedata.normalize("NFKD", _norm(t))
    return "".join(c for c in t if not unicodedata.combining(c))


def index_catalogue(art: pd.DataFrame) -> dict[str, str]:
    """« DÉSIGNATION|RÉFÉRENCE » -> code article."""
    return {f"{_norm(d)}|{_norm(r)}": as_text(c) for c, d, r in
            zip(art["Code Article"], art["Désignation"], art["Référence"]) if as_text(c)}


def enregistrer(ctx, da: dict, lignes: list[dict], user: dict):
    """Ajoute à la file « à cataloguer » les lignes de DA sans code article."""
    rows = [{"ID_Proposition": nouvel_id(10), "Date": maintenant(), "Désignation": _norm(l.get("Désignation")),
             "Référence": _norm(l.get("Référence")), "Colisage": as_text(l.get("Colisage")),
             "Saisi par": user.get("Nom", ""), "Service": as_text(da.get("Service Demandeur")),
             "Chantier": as_text(da.get("Chantier")), "N° DA": as_text(da.get("N° DA")),
             "ID_DA": as_text(da.get("ID_DA")), "ID_Ligne": as_text(l.get("ID_Ligne")), "Statut": "À analyser"}
            for l in lignes if not as_text(l.get("Code Article")) and as_text(l.get("Désignation"))]
    if rows:
        ctx.get_store().append("ARTICLES_A_CATALOGUER", rows)


def nouveau_code(art: pd.DataFrame, famille: str, deja: set[str]) -> str:
    """Code suivant pour la famille : même préfixe que ses articles (ex. PNE-0045), sinon 3 lettres de la famille."""
    codes = [as_text(c) for c, f in zip(art["Code Article"], art["Famille"]) if _norm(f) == _norm(famille)]
    prefixes = [c.split("-")[0] for c in codes if "-" in c]
    if prefixes:
        pref = max(set(prefixes), key=prefixes.count)
    else:
        lettres = re.sub(r"[^A-Z]", "", _plat(famille)) or "ART"
        pref = lettres[:3]
    tous = set(art["Code Article"].map(as_text)) | deja
    nums = [int(m.group(1)) for c in tous if (m := re.fullmatch(rf"{re.escape(pref)}-(\d+)", c))]
    n = max(nums, default=0) + 1
    while f"{pref}-{n:04d}" in tous:
        n += 1
    return f"{pref}-{n:04d}"


def nb_a_analyser(ctx) -> int:
    p = ctx.load("ARTICLES_A_CATALOGUER")
    return int((p["Statut"].map(as_text) == "À analyser").sum())


def _rattrapage(ctx) -> int:
    """Crée les propositions pour les lignes de DA hors catalogue saisies avant cette fonction."""
    lg, da = ctx.load("HISTORIQUE_ARTICLES"), ctx.load("DA")
    deja = set(ctx.load("ARTICLES_A_CATALOGUER")["ID_Ligne"].map(as_text))
    lg = lg.loc[(lg["Code Article"].map(as_text) == "") & (lg["Statut ligne"].map(as_text) != "Annulée")
                & ~lg["ID_Ligne"].map(as_text).isin(deja)]
    entetes = {as_text(d["ID_DA"]): d for d in da.to_dict("records")}
    n = 0
    for id_da, g in lg.groupby(lg["ID_DA"].map(as_text)):
        d = entetes.get(id_da)
        if d:
            enregistrer(ctx, d, g.to_dict("records"), {"Nom": as_text(d.get("Demandeur"))})
            n += len(g)
    return n


def onglet_a_cataloguer(ctx):
    st.caption("Articles saisis librement dans les DA (hors catalogue). Pour chaque article : **Intégrer** "
               "(nouveau code dans le catalogue), **Rattacher** à un article existant (indiquer son code) ou "
               "**Rejeter**. Les lignes de DA et de BC concernées reçoivent automatiquement le code.")
    if st.session_state.get("cat_msg"):
        st.success(st.session_state.pop("cat_msg"))
    p = ctx.load("ARTICLES_A_CATALOGUER")
    art = ctx.load("ARTICLES")
    attente = p.loc[p["Statut"].map(as_text) == "À analyser"]
    k1, k2, k3 = st.columns(3)
    k1.metric("Articles à analyser", attente.groupby(["Désignation", "Référence"]).ngroups if len(attente) else 0)
    k2.metric("Demandes concernées", len(attente))
    k3.metric("Déjà intégrés / rattachés", int(p["Statut"].isin(["Intégré", "Rattaché"]).sum()))
    if st.button("Rechercher les articles hors catalogue des DA déjà saisies", key="cat-rattrapage"):
        n = _rattrapage(ctx)
        ctx.refresh(False)
        st.session_state.cat_msg = f"{n} ligne(s) de DA hors catalogue ajoutée(s) à la liste."
        st.rerun()
    if len(attente):
        _table_decisions(ctx, attente, art)
    else:
        st.info("Aucun article hors catalogue en attente.")
    if len(p):
        buf = io.BytesIO()
        p.to_excel(buf, index=False, sheet_name="ARTICLES_A_CATALOGUER")
        st.download_button("Exporter la liste (Excel)", buf.getvalue(), file_name="articles_a_cataloguer.xlsx",
                           key="cat-xls")
        traites = p.loc[p["Statut"].map(as_text) != "À analyser"]
        if len(traites):
            with st.expander(f"Propositions traitées ({len(traites)})"):
                st.dataframe(traites[["Date traitement", "Désignation", "Référence", "Statut", "Code attribué",
                                      "Désignation retenue", "Référence retenue", "Famille", "N° DA", "Traité par"]]
                             .sort_values("Date traitement", ascending=False), hide_index=True, width="stretch")


def _table_decisions(ctx, attente: pd.DataFrame, art: pd.DataFrame):
    libelles = [f"{as_text(c)} · {as_text(d)}" + (f" · {as_text(r)}" if as_text(r) else "")
                for c, d, r in zip(art["Code Article"], art["Désignation"], art["Référence"])]
    plats = [_plat(x.split(" · ", 1)[1]) if " · " in x else "" for x in libelles]
    familles = sorted(set(art["Famille"].map(as_text)) - {""})

    def proche(des, ref):
        m = difflib.get_close_matches(_plat(f"{des} · {ref}" if ref else des), plats, n=1, cutoff=0.6)
        return libelles[plats.index(m[0])] if m else ""

    g = (attente.assign(Désignation=attente["Désignation"].map(_norm), Référence=attente["Référence"].map(_norm))
         .groupby(["Désignation", "Référence"], sort=False)
         .agg(Colisage=("Colisage", "last"), Demandes=("ID_Proposition", "size"),
              Demandeurs=("Saisi par", lambda x: ", ".join(dict.fromkeys(map(as_text, x)))),
              **{"DA / BC": ("N° DA", lambda x: ", ".join(dict.fromkeys(map(as_text, x))))},
              ids=("ID_Proposition", lambda x: "|".join(map(as_text, x))))
         .reset_index().sort_values("Demandes", ascending=False))
    v = pd.DataFrame({
        "ids": g["ids"], "Désignation saisie": g["Désignation"], "Référence saisie": g["Référence"],
        "Demandes": g["Demandes"], "DA / BC": g["DA / BC"], "Demandeurs": g["Demandeurs"],
        "Article proche du catalogue": [proche(d, r) for d, r in zip(g["Désignation"], g["Référence"])],
        "Décision": "", "Désignation retenue": g["Désignation"], "Référence retenue": g["Référence"],
        "Famille": "", "Colisage": g["Colisage"].map(as_text), "Code à rattacher": ""})
    version = st.session_state.get("cat_v", 0)
    from schema import COLISAGES
    import ia
    if ia.disponible():
        cle = f"cat-ia-{version}"
        if st.button("✨ Proposer les décisions avec l'IA", key=f"{cle}-go",
                     help="L'IA compare chaque article au catalogue et propose Rattacher / Intégrer (à vérifier)"):
            with st.spinner("L'IA analyse les articles hors catalogue…"):
                try:
                    st.session_state[cle] = ia.proposer_catalogage(
                        [{"designation": d, "reference": r} for d, r in zip(v["Désignation saisie"],
                                                                            v["Référence saisie"])][:60],
                        art, familles, ctx.version())
                except ia.IAErreur as e:
                    st.error(str(e))
        props = st.session_state.get(cle) or []
        for d in props:
            i = d.get("index")
            if not isinstance(i, int) or not 0 <= i < len(v):
                continue
            if d.get("decision") == "Rattacher" and as_text(d.get("code")):
                v.iat[i, v.columns.get_loc("Décision")] = "Rattacher"
                v.iat[i, v.columns.get_loc("Code à rattacher")] = as_text(d["code"]).upper()
            elif d.get("decision") == "Intégrer":
                v.iat[i, v.columns.get_loc("Décision")] = "Intégrer"
                for c, ch in (("Désignation retenue", "designation"), ("Référence retenue", "reference")):
                    if as_text(d.get(ch)):
                        v.iat[i, v.columns.get_loc(c)] = as_text(d[ch]).upper()
                if as_text(d.get("famille")) in familles:
                    v.iat[i, v.columns.get_loc("Famille")] = as_text(d["famille"])
        if props:
            v.insert(v.columns.get_loc("Décision"), "Avis IA",
                     [next((as_text(d.get("justification")) for d in props if d.get("index") == i), "")
                      for i in range(len(v))])
            st.caption("Propositions de l'IA pré-remplies : vérifiez-les avant d'appliquer.")
        version = f"{version}-{len(props)}"
    ed = st.data_editor(
        v, hide_index=True, width="stretch", key=f"cat-ed-{version}",
        disabled=["Désignation saisie", "Référence saisie", "Demandes", "DA / BC", "Demandeurs",
                  "Article proche du catalogue", "Avis IA"],
        column_config={
            "ids": None,
            "Décision": st.column_config.SelectboxColumn(options=DECISIONS, width="small"),
            "Famille": st.column_config.SelectboxColumn(options=familles, help="Obligatoire pour « Intégrer »"),
            "Colisage": st.column_config.SelectboxColumn(options=sorted(set(COLISAGES) | set(v["Colisage"]) - {""})),
            "Code à rattacher": st.column_config.TextColumn(
                help="Pour « Rattacher » : code de l'article existant (ex. CAR-0007). Vide = article proche proposé")})
    a_faire = ed.loc[ed["Décision"].map(as_text) != ""]
    if st.button(f"Appliquer les décisions ({len(a_faire)})", type="primary", disabled=not len(a_faire),
                 key="cat-go"):
        msg, erreurs = appliquer(ctx, a_faire, art)
        for e in erreurs:
            st.error(e)
        if msg:
            st.session_state.cat_v = version + 1
            st.session_state.cat_msg = msg + (" Certaines lignes n'ont pas été traitées (voir erreurs)."
                                              if erreurs else "")
            st.rerun()


def appliquer(ctx, decisions: pd.DataFrame, art: pd.DataFrame) -> tuple[str, list[str]]:
    index = index_catalogue(art)
    codes = set(art["Code Article"].map(as_text))
    fiche = {as_text(c): (as_text(d), as_text(r)) for c, d, r in
             zip(art["Code Article"], art["Désignation"], art["Référence"])}
    p = ctx.load("ARTICLES_A_CATALOGUER")
    lignes_de = dict(zip(p["ID_Proposition"].map(as_text), p["ID_Ligne"].map(as_text)))
    user, auj = ctx.user.get("Nom", ""), date.today().isoformat()
    nouveaux, maj_prop, maj_lignes, erreurs = [], {}, {}, []
    n_int = n_rat = n_rej = 0
    for _, d in decisions.iterrows():
        ids = as_text(d["ids"]).split("|")
        dec = as_text(d["Décision"])
        des, ref = _norm(d["Désignation retenue"]), _norm(d["Référence retenue"])
        lib = f"« {as_text(d['Désignation saisie'])} »"
        if dec == "Rejeter":
            for i in ids:
                maj_prop[i] = {"Statut": "Rejeté", "Traité par": user, "Date traitement": auj}
            n_rej += 1
            continue
        if dec == "Rattacher":
            code = as_text(d["Code à rattacher"]).upper() or as_text(d["Article proche du catalogue"]).split(" · ")[0]
            if code not in codes:
                erreurs.append(f"{lib} : code « {code or '—'} » introuvable dans le catalogue.")
                continue
            statut = "Rattaché"
            n_rat += 1
        else:  # Intégrer
            if not des:
                erreurs.append(f"{lib} : désignation retenue vide.")
                continue
            existant = index.get(f"{des}|{ref}")
            if existant:  # déjà au catalogue sous ce libellé exact : simple rattachement
                code, statut = existant, "Rattaché"
                n_rat += 1
            else:
                fam = as_text(d["Famille"])
                if not fam:
                    erreurs.append(f"{lib} : choisissez la famille pour l'intégrer.")
                    continue
                code = nouveau_code(art, fam, {n["Code Article"] for n in nouveaux})
                nouveaux.append({"Code Article": code, "Désignation": des, "Référence": ref, "Famille": fam,
                                 "Colisage": as_text(d["Colisage"]), "Sous-famille": ""})
                index[f"{des}|{ref}"] = code
                fiche[code] = (des, ref)
                statut = "Intégré"
                n_int += 1
        des_c, ref_c = fiche.get(code, (des, ref))
        for i in ids:
            maj_prop[i] = {"Statut": statut, "Code attribué": code, "Désignation retenue": des_c,
                           "Référence retenue": ref_c, "Famille": as_text(d["Famille"]), "Traité par": user,
                           "Date traitement": auj}
            if lignes_de.get(i):
                maj_lignes[lignes_de[i]] = {"Code Article": code, "Désignation": des_c, "Référence": ref_c}
    store = ctx.get_store()
    if nouveaux:
        store.append("ARTICLES", nouveaux)
    maj_bc = {k[len(PREFIXE_BC):]: v for k, v in maj_lignes.items() if k.startswith(PREFIXE_BC)}
    maj_lignes = {k: v for k, v in maj_lignes.items() if not k.startswith(PREFIXE_BC)}
    if maj_bc:
        store.update_many("BC_LIGNES", "ID_LigneBC", maj_bc)
    if maj_lignes:
        store.update_many("HISTORIQUE_ARTICLES", "ID_Ligne", maj_lignes)
        bl = ctx.load("BC_LIGNES")
        bl = bl.loc[bl["ID_Ligne_DA"].map(as_text).isin(maj_lignes).astype(bool)]
        if len(bl):
            store.update_many("BC_LIGNES", "ID_LigneBC",
                              {as_text(r["ID_LigneBC"]): maj_lignes[as_text(r["ID_Ligne_DA"])]
                               for _, r in bl.iterrows()})
    if maj_prop:
        store.update_many("ARTICLES_A_CATALOGUER", "ID_Proposition", maj_prop)
        ctx.refresh()  # catalogue (statique) et lignes (dynamiques)
    msg = (f"{n_int} article(s) intégré(s) au catalogue, {n_rat} rattaché(s), {n_rej} rejeté(s)."
           if maj_prop else "")
    return msg, erreurs
