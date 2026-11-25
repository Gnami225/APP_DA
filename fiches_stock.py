"""Magasin : import des fiches papier (tenue de stock, fiches de flux / bons de sortie, inventaires) lues par l'IA.

Chaque fiche (photo, PDF, Excel) est lue ligne par ligne ; l'application rapproche l'article du catalogue et l'engin
du parc, recalcule les soldes et les compare à ceux écrits sur la fiche, repère les doublons, puis propose des
mouvements que le magasinier ou l'administrateur vérifie avant d'enregistrer.

Mode de chaque ligne :
- « Courant » : le mouvement met à jour le stock de l'application ;
- « Historique » : il compte dans les statistiques (consommations par engin, par mois…) mais ne modifie pas le
  stock actuel (colonne Historique = Oui de STOCK_MOUVEMENTS, ignorée par magasin._stock).
Automatique : article jamais suivi dans ce magasin → le report de la fiche devient le stock initial et les lignes
sont courantes ; article déjà suivi → les lignes datées après le dernier inventaire sont courantes, les autres
historiques.
"""
from __future__ import annotations

import difflib
from datetime import date

import pandas as pd
import streamlit as st

import ia
import magasin
from logic import as_text, to_date, to_num

MODES = {"auto": "🤖 Automatique (recommandé)", "hist": "🗂️ Tout en historique (statistiques seulement)",
         "cour": "📦 Tout en courant (met à jour le stock)"}
MOUVEMENTS = ["Sortie", "Entrée", "Inventaire", "Stock initial"]
TYPES_FICHE = {"tenue_de_stock": "Fiche de tenue de stock", "flux": "Fiche de flux / bon", "inventaire": "Inventaire",
               "autre": "Autre document"}


# =========================================================================== rapprochements
def _article(ctx, des: str, ref: str, unite: str) -> dict:
    """Article du catalogue le plus proche (sinon l'article tel que lu, sans code)."""
    des, ref = " ".join(as_text(des).upper().split()), " ".join(as_text(ref).upper().split())
    if des:
        art = ctx.load("ARTICLES")
        for c in ia.candidats(art, f"{des} {ref}".strip(), n=5, version=ctx.version()):
            d, r = as_text(c["designation"]).upper(), as_text(c["reference"]).upper()
            sd = difflib.SequenceMatcher(None, ia._plat(des), ia._plat(d)).ratio()
            sr = 1.0 if not ref and not r else difflib.SequenceMatcher(None, ia._plat(ref), ia._plat(r)).ratio()
            if sd >= 0.85 and sr >= 0.8:
                return {"Code Article": as_text(c["code"]), "Désignation": d, "Référence": r,
                        "Unité": as_text(c["colisage"]) or as_text(unite)}
    return {"Code Article": "", "Désignation": des, "Référence": ref, "Unité": as_text(unite)}


def _parc(ctx, code: str, immat: str, engin: str) -> str:
    p = ctx.load("PARC")
    codes = {as_text(c).upper().replace(" ", "").replace("-", ""): as_text(c) for c in p["Code_Parc"]}
    for t in (code, immat):
        k = as_text(t).upper().replace(" ", "").replace("-", "")
        if k and k in codes:
            return codes[k]
    if as_text(code):
        return as_text(code).upper().replace(" ", "")
    for c, d, mm in zip(p["Code_Parc"].map(as_text), p["Désignation engin"].map(as_text),
                        p["Marque / Modèle"].map(as_text)):
        if as_text(immat) and as_text(immat).upper().replace(" ", "") in (d + mm).upper().replace(" ", ""):
            return c
    return ""


# =========================================================================== construction des lignes
def construire(ctx, mag: str, fiches: list[dict], mode: str) -> list[dict]:
    """Lignes proposées à partir des fiches lues (ordre : article, date)."""
    m = magasin.mouvements(ctx)
    m = m.loc[(m["Magasin"] == mag)] if len(m) else m
    if len(m) and "Historique" in m:
        m_cour = m.loc[m["Historique"].map(as_text) != "Oui"]
    else:
        m_cour = m
    suivis = set(m_cour["cle"]) if len(m_cour) else set()
    inv = {}
    if len(m_cour):
        x = m_cour.loc[m_cour["Type"].map(as_text).isin(["Stock initial", "Ajustement inventaire"])]
        inv = x.groupby("cle")["d"].max().to_dict()
    deja = set()
    if len(m):
        for d, k, q, b, cp in zip(m["d"], m["cle"], m["q"], m.get("N° bon", pd.Series([""] * len(m), index=m.index)),
                                  m["Code_Parc"]):
            if pd.notna(d):
                deja.add((d.date(), k, round(abs(to_num(q)), 3), as_text(b) or as_text(cp)))
    lignes, initial_fait = [], set()
    for f in sorted(fiches, key=lambda f: (as_text((f["data"].get("article") or {}).get("designation")),
                                           min([as_text(l.get("date")) for l in f["data"].get("lignes") or []] or [""]),
                                           as_text(f["data"].get("numero_fiche")))):
        d = f["data"]
        tf = as_text(d.get("type_fiche"))
        ent = d.get("article") or {}
        art_ent = _article(ctx, ent.get("designation"), ent.get("reference"), ent.get("unite")) \
            if tf == "tenue_de_stock" else None
        prec = as_text(ent.get("precision"))
        dates = [to_date(l.get("date")) for l in d.get("lignes") or []]
        d0 = next((x for x in dates if x), None)
        lib_fiche = f"{TYPES_FICHE.get(tf, tf)}" + (f" n° {as_text(d.get('numero_fiche'))}"
                                                    if as_text(d.get("numero_fiche")) else "") + f" ({f['nom']})"
        # report de la fiche -> stock initial si l'article n'est pas encore suivi dans ce magasin
        rep = d.get("report")
        if art_ent and rep is not None and mode != "hist":
            k = magasin._cle(art_ent["Code Article"], art_ent["Désignation"], art_ent["Référence"])
            if k not in suivis and k not in initial_fait:
                initial_fait.add(k)
                lignes.append({**art_ent, "ok": True, "fiche": f["id"], "Fiche": lib_fiche, "Date": d0 or date.today(),
                               "Mouvement": "Stock initial", "Quantité": float(to_num(rep)), "Code_Parc": "",
                               "Bénéficiaire": "", "N° bon": "", "Solde fiche": float(to_num(rep)),
                               "Statut": "Courant", "Remarque": "report de la fiche = stock de départ", "prec": prec})
        bon, dl = "", None
        for l, dt in zip(d.get("lignes") or [], dates):
            dl = dt or dl
            bon = as_text(l.get("numero_bon")) or bon
            a = art_ent or _article(ctx, l.get("designation"), l.get("reference"), l.get("unite"))
            sens = as_text(l.get("sens"))
            mv = {"sortie": "Sortie", "entree": "Entrée", "inventaire": "Inventaire"}.get(sens, "Sortie")
            q = abs(float(to_num(l.get("quantite"))))
            cp = _parc(ctx, l.get("code_parc"), l.get("immatriculation"), l.get("engin"))
            ben = " ".join(x for x in (as_text(l.get("engin")), f"({as_text(l.get('immatriculation'))})"
                                       if as_text(l.get("immatriculation")) else "",
                                       as_text(l.get("provenance"))) if x)
            k = magasin._cle(a["Code Article"], a["Désignation"], a["Référence"])
            if mode == "hist":
                statut = "Historique"
            elif mode == "cour" or k not in suivis or k in initial_fait:
                statut = "Courant"
            else:
                li = inv.get(k)
                statut = "Historique" if (li is not None and pd.notna(li) and dl and dl <= li.date()) else "Courant"
            rem = []
            if l.get("doute"):
                rem.append("⚠️ lecture douteuse")
            if not a["Désignation"]:
                rem.append("article non lu")
            elif not a["Code Article"]:
                rem.append("hors catalogue")
            if not dl:
                rem.append("date manquante")
            elif dl > date.today():
                rem.append("date future")
            dup = dl and (dl, k, round(q, 3), bon or cp) in deja
            if dup:
                rem.append("déjà enregistré")
            lignes.append({**a, "ok": not dup and q > 0 and bool(a["Désignation"]), "fiche": f["id"], "Fiche": lib_fiche,
                           "Date": dl or date.today(), "Mouvement": mv, "Quantité": q, "Code_Parc": cp,
                           "Bénéficiaire": ben, "N° bon": bon,
                           "Solde fiche": None if l.get("solde_ecrit") is None else float(to_num(l.get("solde_ecrit"))),
                           "Statut": statut, "Remarque": ", ".join(rem), "prec": prec,
                           "Observation": as_text(l.get("observation"))})
    return lignes


def controler(lignes: list[dict], fiches: list[dict]) -> tuple[list, dict]:
    """Solde recalculé ligne par ligne sur les fiches de tenue de stock (report + entrées - sorties) et comparé au
    solde écrit. Renvoie (soldes calculés par ligne, bilan par fiche)."""
    par_id = {f["id"]: f for f in fiches}
    calc = [None] * len(lignes)
    bilan = {}
    for fid, f in par_id.items():
        if as_text(f["data"].get("type_fiche")) != "tenue_de_stock":
            continue
        idx = [i for i, l in enumerate(lignes) if l["fiche"] == fid]
        rep = f["data"].get("report")
        run = None if rep is None else float(to_num(rep))
        ecarts = 0
        for i in idx:
            l = lignes[i]
            if l["Mouvement"] == "Stock initial":
                calc[i] = run
                continue
            if run is None:  # pas de report : on part du premier solde écrit
                s0 = l.get("Solde fiche")
                if s0 is None:
                    continue
                run = s0 + (l["Quantité"] if l["Mouvement"] == "Sortie" else -l["Quantité"])
            if l["Mouvement"] == "Sortie":
                run -= l["Quantité"]
            elif l["Mouvement"] == "Entrée":
                run += l["Quantité"]
            elif l["Mouvement"] == "Inventaire":
                run = l["Quantité"]
            calc[i] = round(run, 3)
            if l.get("Solde fiche") is not None and abs(l["Solde fiche"] - run) > 0.5:
                ecarts += 1
        bilan[fid] = {"ecarts": ecarts, "fin": run}
    return calc, bilan


# =========================================================================== écran
def vue_import(ctx, mag: str):
    noms = ctx.refs()["chantiers"]
    cle = f"fs-{mag}"
    S = st.session_state.setdefault(cle, {"fiches": [], "lignes": [], "v": 0, "mode": "auto"})
    st.caption("Prenez en photo (ou importez en PDF / Excel) les **fiches de tenue de stock**, **fiches de flux / bons "
               "de sortie** et **fiches d'inventaire**. L'IA lit chaque ligne ; l'application recalcule les soldes, "
               "repère les doublons et vous montre tout avant d'enregistrer.")
    if not ia.disponible():
        st.info("Lecture automatique indisponible (assistant IA non configuré). Utilisez « Sortie » ou « Inventaire ».")
        return
    fichiers = st.file_uploader("Fiches", type=["jpg", "jpeg", "png", "webp", "pdf", "xlsx", "xls", "csv"],
                                accept_multiple_files=True, key=f"{cle}-up-{S['v']}", label_visibility="collapsed")
    mode = st.radio("Effet sur le stock", list(MODES), format_func=MODES.get, horizontal=True, key=f"{cle}-mode",
                    help="Automatique : un article jamais suivi dans ce magasin démarre au report de la fiche ; pour un "
                         "article déjà suivi, ce qui est daté après le dernier inventaire met le stock à jour, le "
                         "reste va en historique. Historique : compte dans les statistiques sans toucher au stock.")
    if fichiers and st.button(f"🤖 Lire {len(fichiers)} fiche(s)", type="primary", key=f"{cle}-lire"):
        import documents
        connus = {f["emp"] for f in S["fiches"]}
        docs = ctx.load("DOCUMENTS")
        archives = set(docs.loc[docs["Type"].map(as_text) == "Fiche de stock", "Empreinte"].map(as_text)) \
            if len(docs) else set()
        barre = st.progress(0.0)
        for i, fu in enumerate(fichiers):
            octets = fu.getvalue()
            emp = documents.empreinte(octets)
            if emp in connus:
                continue
            if emp in archives:
                st.warning(f"{fu.name} : fiche déjà importée précédemment — ignorée.")
                continue
            barre.progress(i / len(fichiers), f"Lecture de {fu.name}… (10 à 30 s par page)")
            try:
                d = ia.lire_fiche_stock(fu.name, octets, noms.get(mag, mag))
                S["fiches"].append({"id": f"F{len(S['fiches']) + 1}", "nom": fu.name, "emp": emp, "octets": octets,
                                    "data": d})
            except ia.IAErreur as e:
                st.error(f"{fu.name} : {e}")
            except Exception as e:
                st.error(f"{fu.name} : lecture impossible ({e}).")
        barre.empty()
        S["lignes"] = construire(ctx, mag, S["fiches"], mode)
        S["mode"] = mode
        S["v"] += 1
        st.rerun()
    if not S["fiches"]:
        return
    if mode != S["mode"]:
        if st.button("Appliquer le nouveau mode aux lignes lues", key=f"{cle}-remode"):
            S["lignes"] = construire(ctx, mag, S["fiches"], mode)
            S["mode"] = mode
            S["v"] += 1
            st.rerun()
    lignes = S["lignes"]
    calc, bilan = controler(lignes, S["fiches"])

    # ---- bilan par fiche
    for f in S["fiches"]:
        d = f["data"]
        n = sum(1 for l in lignes if l["fiche"] == f["id"])
        ent = d.get("article") or {}
        tete = (f"**{f['nom']}** · {TYPES_FICHE.get(as_text(d.get('type_fiche')), '?')}"
                + (f" n° {as_text(d.get('numero_fiche'))}" if as_text(d.get("numero_fiche")) else "")
                + (f" · {as_text(ent.get('designation'))}" + (f" ({as_text(ent.get('precision'))})"
                                                              if as_text(ent.get("precision")) else "")
                   if as_text(ent.get("designation")) else "")
                + f" · {n} ligne(s)" + (f" · report {to_num(d.get('report')):g}" if d.get("report") is not None else ""))
        b = bilan.get(f["id"])
        if b and b["ecarts"]:
            st.warning(f"{tete}\n\n⚠️ {b['ecarts']} solde(s) écrit(s) ne correspondent pas au calcul : vérifiez les "
                       "quantités surlignées (erreur de lecture ou de calcul sur la fiche).")
        elif b:
            st.success(tete + f" · ✅ soldes justes jusqu'à {b['fin']:g}" if b["fin"] is not None else tete)
        else:
            st.info(tete + (f"\n\n{as_text(d.get('remarques'))}" if as_text(d.get("remarques")) else ""))

    # ---- tableau de contrôle
    t = pd.DataFrame([{"✓": l["ok"], "Fiche": l["Fiche"], "Date": l["Date"], "Mouvement": l["Mouvement"],
                       "Désignation": l["Désignation"], "Référence": l["Référence"], "Unité": l["Unité"],
                       "Quantité": l["Quantité"], "Code_Parc": l["Code_Parc"], "Bénéficiaire": l["Bénéficiaire"],
                       "N° bon": l["N° bon"], "Solde fiche": l["Solde fiche"], "Solde calculé": c,
                       "Contrôle": ("❌" if l["Solde fiche"] is not None and c is not None
                                    and abs(l["Solde fiche"] - c) > 0.5 else ("✅" if c is not None else "")),
                       "Statut": l["Statut"], "Remarque": l["Remarque"],
                       "Catalogue": l["Code Article"] or "—"} for l, c in zip(lignes, calc)])
    if not len(t):
        st.warning("Aucune ligne lue.")
        return
    t["Solde fiche"] = pd.to_numeric(t["Solde fiche"], errors="coerce")
    t["Solde calculé"] = pd.to_numeric(t["Solde calculé"], errors="coerce")
    ed = st.data_editor(
        t, hide_index=True, width="stretch", key=f"{cle}-ed-{S['v']}",
        disabled=["Fiche", "Solde calculé", "Contrôle", "Remarque", "Catalogue"],
        column_config={"✓": st.column_config.CheckboxColumn(width="small"),
                       "Date": st.column_config.DateColumn(format="DD/MM/YYYY"),
                       "Mouvement": st.column_config.SelectboxColumn(options=MOUVEMENTS, required=True),
                       "Quantité": st.column_config.NumberColumn(min_value=0.0, format="%g"),
                       "Solde fiche": st.column_config.NumberColumn(format="%g"),
                       "Solde calculé": st.column_config.NumberColumn(format="%g"),
                       "Statut": st.column_config.SelectboxColumn(options=["Courant", "Historique"], required=True,
                                                                  help="Courant : met à jour le stock ; Historique : "
                                                                       "statistiques seulement"),
                       "Fiche": st.column_config.TextColumn(width="small")})
    change = False
    for i, r in ed.iterrows():
        l = lignes[i]
        nv = {"ok": bool(r["✓"]), "Date": to_date(r["Date"]) or l["Date"], "Mouvement": as_text(r["Mouvement"]),
              "Désignation": " ".join(as_text(r["Désignation"]).upper().split()),
              "Référence": " ".join(as_text(r["Référence"]).upper().split()), "Unité": as_text(r["Unité"]),
              "Quantité": float(to_num(r["Quantité"])), "Code_Parc": as_text(r["Code_Parc"]).upper(),
              "Bénéficiaire": as_text(r["Bénéficiaire"]), "N° bon": as_text(r["N° bon"]),
              "Solde fiche": None if pd.isna(r["Solde fiche"]) else float(r["Solde fiche"]),
              "Statut": as_text(r["Statut"]) or "Courant"}
        if any(nv[k] != l.get(k) for k in nv):
            if (nv["Désignation"], nv["Référence"]) != (l["Désignation"], l["Référence"]):
                nv.update(_article(ctx, nv["Désignation"], nv["Référence"], nv["Unité"]))
            l.update(nv)
            change = True
    if change:
        st.rerun()

    sel = [l for l in lignes if l["ok"]]
    nb = {s: sum(1 for l in sel if l["Statut"] == s) for s in ("Courant", "Historique")}
    hors = sorted({l["Désignation"] for l in sel if not l["Code Article"]})
    st.caption(f"{len(sel)} ligne(s) cochée(s) : **{nb['Courant']}** mettent à jour le stock, **{nb['Historique']}** "
               "en historique (statistiques)." + (f" Hors catalogue (suivis sous leur libellé) : {', '.join(hors)}."
                                                 if hors else ""))
    # stock négatif après import ?
    st_act = magasin.stock(ctx, mag)
    dispo = dict(zip(st_act["cle"], st_act["Stock"])) if len(st_act) else {}
    proj = dict(dispo)
    for l in sorted([l for l in sel if l["Statut"] == "Courant"], key=lambda l: l["Date"]):
        k = magasin._cle(l["Code Article"], l["Désignation"], l["Référence"])
        if l["Mouvement"] == "Inventaire":
            proj[k] = l["Quantité"]
        else:
            proj[k] = proj.get(k, 0.0) + (l["Quantité"] if l["Mouvement"] in ("Entrée", "Stock initial") else
                                          -l["Quantité"])
    neg = [f"{k.split('|')[0].replace('T:', '')} ({v:g})" for k, v in proj.items() if v < -1e-6]
    if neg:
        st.warning("Stock négatif après import (sorties sans stock de départ ?) : " + ", ".join(neg[:8])
                   + ". Ajoutez la fiche précédente, un inventaire, ou passez ces lignes en Historique.")
    a, b = st.columns([1, 1])
    if a.button(f"✅ Enregistrer {len(sel)} mouvement(s)", type="primary", disabled=not sel, key=f"{cle}-go"):
        n = enregistrer(ctx, mag, S["fiches"], sel)
        st.session_state.pop(cle, None)
        st.session_state["mag_msg"] = f"{n} mouvement(s) enregistré(s) depuis les fiches."
        st.rerun()
    if b.button("Tout effacer", key=f"{cle}-raz"):
        st.session_state.pop(cle, None)
        st.rerun()


def enregistrer(ctx, mag: str, fiches: list[dict], sel: list[dict]) -> int:
    import documents
    store = ctx.get_store()
    # justificatifs
    id_doc, fiches_doc = {}, []
    for f in fiches:
        if not any(l["fiche"] == f["id"] for l in sel):
            continue
        d = f["data"]
        dates = [to_date(l.get("date")) for l in d.get("lignes") or [] if to_date(l.get("date"))]
        fd = documents.fiche(ctx, {"Type": "Fiche de stock", "N° document": as_text(d.get("numero_fiche")),
                                   "Date document": min(dates).isoformat() if dates else "", "Chantier": mag,
                                   "Nom fichier": f["nom"], "Empreinte": f["emp"], "Lu par IA": "Oui",
                                   "Écarts": as_text(d.get("remarques"))[:300]})
        if documents.configure() and not f["nom"].lower().endswith((".csv",)):
            try:
                nom, octets, mime = documents.preparer(f["nom"], f["octets"])
                nom = f"Fiche stock {mag} {as_text(d.get('numero_fiche'))} - {nom}".strip()
                fd["ID Drive"], fd["Lien"] = documents.envoyer(nom, octets, mime, f"{date.today():%Y}/{date.today():%m}",
                                                               f"Fiche de stock {mag}")
                fd["Nom fichier"] = nom
            except documents.DocErreur as e:
                st.warning(f"{e} Fiche {f['nom']} non archivée dans Drive.")
        id_doc[f["id"]] = fd["ID_Document"]
        fiches_doc.append(fd)
    # mouvements
    st_act = magasin.stock(ctx, mag)
    theo = dict(zip(st_act["cle"], st_act["Stock"])) if len(st_act) else {}
    vu = {}
    rows = []
    for l in sorted(sel, key=lambda l: (l["Date"], 0 if l["Mouvement"] == "Stock initial" else 1)):
        k = magasin._cle(l["Code Article"], l["Désignation"], l["Référence"])
        if k not in vu:
            vu[k] = magasin._valeur_unitaire(ctx, l["Code Article"], l["Désignation"], l["Référence"], l["Unité"])
        hist = l["Statut"] == "Historique"
        commun = {"date": l["Date"], "Code_Parc": l["Code_Parc"], "Bénéficiaire": l["Bénéficiaire"],
                  "N° bon": l["N° bon"], "Historique": "Oui" if hist else "", "Source": "Fiche lue par IA",
                  "ID_Document": id_doc.get(l["fiche"], ""), "Valeur unitaire": vu[k],
                  "Solde fiche": "" if l.get("Solde fiche") is None else l["Solde fiche"],
                  "Motif": " · ".join(x for x in (l["Fiche"].split(" (")[0], l.get("prec", ""),
                                                  l.get("Observation", "")) if x)}
        if l["Mouvement"] == "Inventaire":
            if hist:
                continue
            ecart = l["Quantité"] - theo.get(k, 0.0)
            if k not in theo:
                rows.append(magasin._mvt(ctx, mag, "Stock initial", l, l["Quantité"], **commun))
            elif abs(ecart) > 1e-9:
                commun["Motif"] = f"Inventaire (fiche) : compté {l['Quantité']:g}, app {theo.get(k, 0.0):g}"
                rows.append(magasin._mvt(ctx, mag, "Ajustement inventaire", l, ecart, **commun))
            theo[k] = l["Quantité"]
            continue
        rows.append(magasin._mvt(ctx, mag, l["Mouvement"], l, l["Quantité"], **commun))
        if not hist:
            theo[k] = theo.get(k, 0.0) + (l["Quantité"] if l["Mouvement"] in ("Entrée", "Stock initial")
                                          else -l["Quantité"])
    if fiches_doc:
        store.append("DOCUMENTS", fiches_doc)
    for i in range(0, len(rows), 500):
        store.append("STOCK_MOUVEMENTS", rows[i:i + 500])
    ctx.refresh(False)
    return len(rows)
