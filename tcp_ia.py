"""TCP intelligent : devis lus par l'IA (plusieurs photos / PDF), complément en langage naturel, critères pondérés
(prix, délai, conformité, qualité, fiabilité, paiement, regroupement), recommandation IA tenant compte de la
situation terrain, et choix final de l'acheteur, article par article, avec motif pour l'audit.

L'état du TCP en cours est gardé en session (clé « tcpx-<ID_DA> ») ; les tableaux éditables en sont le reflet. Toute
modification faite par l'IA ou par un bouton incrémente la version « v », ce qui recharge les tableaux.
Enregistrement : achats._enregistre_tcp (tables TCP / TCP_OFFRES existantes, PDF inchangé).
"""
from __future__ import annotations

import hashlib
import json
from datetime import date

import pandas as pd
import streamlit as st

import achats
import ia
import prix
from logic import as_text, fmt_montant, to_date, to_num

CONFORMITES = ["Conforme", "Équivalent", "À vérifier", "Non conforme"]
QUALITES = ["Haute", "Standard", "Entrée de gamme", "Inconnue"]
S_CONF = {"Conforme": 1.0, "Équivalent": 0.65, "À vérifier": 0.6, "Non conforme": 0.0, "": 0.75}
S_QUAL = {"Haute": 1.0, "Standard": 0.7, "Entrée de gamme": 0.4, "Inconnue": 0.65, "": 0.65}

CRITERES = {  # clé -> libellé
    "prix": "Prix", "delai": "Délai de livraison", "conformite": "Conformité à la demande", "qualite": "Qualité / marque",
    "fiabilite": "Fiabilité fournisseur (note)", "paiement": "Conditions de paiement", "regroupement": "Moins de BC"}
PROFILS = {
    "⚖️ Équilibré": {"prix": 35, "delai": 15, "conformite": 20, "qualite": 10, "fiabilite": 10, "paiement": 5,
                    "regroupement": 5},
    "💰 Moins cher": {"prix": 70, "delai": 5, "conformite": 15, "qualite": 5, "fiabilite": 5, "paiement": 0,
                     "regroupement": 0},
    "🚨 Urgence chantier": {"prix": 20, "delai": 45, "conformite": 15, "qualite": 5, "fiabilite": 15, "paiement": 0,
                           "regroupement": 0},
    "🏅 Qualité / conformité": {"prix": 20, "delai": 10, "conformite": 30, "qualite": 25, "fiabilite": 15,
                               "paiement": 0, "regroupement": 0},
    "📦 Moins de fournisseurs": {"prix": 30, "delai": 10, "conformite": 15, "qualite": 5, "fiabilite": 5, "paiement": 5,
                                "regroupement": 30},
}
CHAMPS_FOUR = ["Nom", "Délai", "Délai (j)", "Conditions", "Crédit (j)", "Remise %", "Transport", "Validité",
               "Observation", "Devis"]


# =========================================================================== état
def _cle(id_da: str) -> str:
    return f"tcpx-{id_da}"


def _etat(id_da: str) -> dict:
    cle = _cle(id_da)
    if cle not in st.session_state:
        st.session_state[cle] = {"fours": {}, "ordre": [], "prix": {}, "choix": {}, "incl": {}, "qte": {},
                                 "docs": {}, "lus": [], "journal": [], "reco_ia": None, "v": 0, "n": 0,
                                 "sit_ajout": ""}
    return st.session_state[cle]


def _bump(S: dict):
    S["v"] += 1


def _nom(S: dict, k: str) -> str:
    return as_text(S["fours"][k].get("Nom"))


def _k_de(S: dict, nom: str) -> str | None:
    import sourcing
    n = " ".join(as_text(nom).upper().split())
    if not n:
        return None
    for k in S["ordre"]:
        if _nom(S, k) == n:
            return k
    for k in S["ordre"]:
        if sourcing.meme_fournisseur(_nom(S, k), n):
            return k
    return None


def _nom_base(nom: str, base: list[str]) -> str:
    """Nom rapproché de la base fournisseurs (« Sococe sarl » -> SOCOCE) ; sinon en majuscules."""
    import sourcing
    n = " ".join(as_text(nom).upper().split())
    if n in base:
        return n
    for b in base:
        if sourcing.meme_fournisseur(b, n):
            return b
    return n


def _ajouter(S: dict, nom: str, **champs) -> str:
    k = _k_de(S, nom)
    if k is None:
        S["n"] += 1
        k = f"f{S['n']}"
        S["fours"][k] = {c: "" for c in CHAMPS_FOUR}
        S["fours"][k]["Nom"] = " ".join(as_text(nom).upper().split())
        S["ordre"].append(k)
        S["prix"][k] = {}
    for c, v in champs.items():
        if v not in (None, ""):
            S["fours"][k][c] = v
    return k


def _retirer(S: dict, k: str):
    S["ordre"] = [x for x in S["ordre"] if x != k]
    S["fours"].pop(k, None)
    S["prix"].pop(k, None)
    S["choix"] = {l: x for l, x in S["choix"].items() if x != k}


def _cell(S: dict, k: str, lid: str) -> dict:
    return S["prix"].setdefault(k, {}).setdefault(lid, {})


def pu_net(S: dict, k: str, lid: str) -> float:
    """Prix unitaire comparé : prix du devis (remise de ligne déduite) moins la remise globale du fournisseur."""
    pu = to_num(S["prix"].get(k, {}).get(lid, {}).get("pu"))
    if pu <= 0:
        return 0.0
    return round(pu * (1 - (to_num(S["fours"][k].get("Remise %")) or 0) / 100), 2)


# =========================================================================== évaluation (sans IA)
def _jours(v) -> float | None:
    if as_text(v) == "":
        return None
    x = to_num(v)
    return float(x) if x == x else None


def evaluer(S: dict, ids: list[str], notes: dict, poids: dict) -> dict:
    """Pour chaque article : moins-disant, score pondéré de chaque offre, recommandation et sa raison."""
    tot = sum(poids.values()) or 1
    w = {c: poids.get(c, 0) / tot for c in CRITERES}
    n = len(ids) or 1
    part = {k: sum(1 for lid in ids if pu_net(S, k, lid) > 0) / n for k in S["ordre"]}
    out = {}
    for lid in ids:
        offres = {k: pu_net(S, k, lid) for k in S["ordre"] if pu_net(S, k, lid) > 0}
        if not offres:
            out[lid] = {"best": "", "reco": "", "raison": "Non chiffré", "scores": {}}
            continue
        best = min(offres, key=offres.get)
        pmin = offres[best]
        delais = {k: _jours(S["fours"][k].get("Délai (j)")) for k in offres}
        connus = [d for d in delais.values() if d is not None]
        dmin = min(connus) if len(connus) >= 2 else None  # un seul délai connu : critère neutre
        credits = {k: _jours(S["fours"][k].get("Crédit (j)")) for k in offres}
        cr_neutre = sum(1 for c in credits.values() if c is not None) < 2
        conf = {k: as_text(_cell(S, k, lid).get("conformite")) for k in offres}
        conforme_existe = any(c != "Non conforme" for c in conf.values())
        scores = {}
        for k, p in offres.items():
            d, cr = delais[k], credits[k]
            nt = notes.get(k)
            s = {"prix": pmin / p,
                 "delai": 1.0 if dmin is None else (0.6 if d is None else 1 / (1 + max(0.0, d - dmin) / 7)),
                 "conformite": S_CONF.get(conf[k], 0.75),
                 "qualite": S_QUAL.get(as_text(_cell(S, k, lid).get("qualite")), 0.65),
                 "fiabilite": 0.5 if nt is None else nt / 100,
                 "paiement": 0.5 if cr_neutre else (0.2 if cr is None else min(cr, 90) / 90),
                 "regroupement": part[k]}
            total = sum(w[c] * s[c] for c in CRITERES)
            if conf[k] == "Non conforme" and conforme_existe:
                total *= 0.3
            scores[k] = (round(100 * total, 1), s)
        reco = max(scores, key=lambda k: (scores[k][0], -offres[k]))
        out[lid] = {"best": best, "reco": reco, "scores": scores, "raison": _raison(S, lid, reco, best, offres, delais,
                                                                                   conf, notes)}
    return out


def _raison(S, lid, reco, best, offres, delais, conf, notes) -> str:
    if reco == best:
        r = "Moins-disant"
        if conf.get(reco) in ("Non conforme", "À vérifier"):
            r += f" ({conf[reco].lower()})"
        return r
    ecart = (offres[reco] / offres[best] - 1) * 100
    plus = []
    if delais.get(reco) is not None and delais.get(best) is not None and delais[reco] < delais[best]:
        plus.append(f"livré en {delais[reco]:g} j au lieu de {delais[best]:g} j")
    if S_CONF.get(conf.get(reco, ""), 0.75) > S_CONF.get(conf.get(best, ""), 0.75):
        plus.append(f"{conf.get(reco) or 'conforme'} (moins-disant : {(conf.get(best) or 'non vérifié').lower()})")
    qr, qb = as_text(_cell(S, reco, lid).get("qualite")), as_text(_cell(S, best, lid).get("qualite"))
    if S_QUAL.get(qr, 0.65) > S_QUAL.get(qb, 0.65):
        plus.append(f"qualité {qr.lower()}")
    if (notes.get(reco) or 0) > (notes.get(best) or 0) + 10:
        plus.append(f"fournisseur mieux noté ({notes[reco]:.0f}/100)")
    cr, cb = _jours(S["fours"][reco].get("Crédit (j)")), _jours(S["fours"][best].get("Crédit (j)"))
    if cr and cb is not None and cr > cb:
        plus.append(f"crédit {cr:g} j")
    if not plus:
        plus.append("regroupement des commandes")
    return f"+{ecart:.0f} % vs {_nom(S, best)}, mais " + ", ".join(plus)


def signature(S: dict, ids: list[str], poids: dict, situation: str) -> str:
    d = {"f": [[_nom(S, k), *(as_text(S["fours"][k].get(c)) for c in ("Délai (j)", "Crédit (j)", "Remise %"))]
               for k in S["ordre"]],
         "p": [[k, lid, pu_net(S, k, lid), as_text(_cell(S, k, lid).get("conformite")),
                as_text(_cell(S, k, lid).get("qualite"))] for k in S["ordre"] for lid in ids],
         "w": poids, "s": situation.strip(), "ids": ids, "q": [S["qte"].get(l) for l in ids]}
    return hashlib.md5(json.dumps(d, default=str, sort_keys=True).encode()).hexdigest()[:12]


# =========================================================================== lecture des devis
def _importer(S: dict, lignes: pd.DataFrame, fichiers, base: list[str]):
    import documents
    attendues = achats._attendues(lignes, "_id", "Reste à commander", "Colisage")
    qte = dict(zip(lignes["_id"].map(as_text), lignes["Reste à commander"].map(to_num)))
    unites = dict(zip(lignes["_id"].map(as_text), lignes["Colisage"].map(as_text)))
    barre = st.progress(0.0, "Lecture des devis…")
    for i, f in enumerate(fichiers):
        octets = f.getvalue()
        emp = documents.empreinte(octets)
        if emp in S["docs"]:
            S["lus"].append({"fichier": f.name, "fournisseur": S["docs"][emp]["fournisseur"], "alertes": [],
                             "info": "déjà lu (ignoré)"})
            continue
        barre.progress(i / len(fichiers), f"Lecture de {f.name}…")
        try:
            d = ia.lire_devis(f.name, octets, attendues)
        except ia.IAErreur as e:
            S["lus"].append({"fichier": f.name, "fournisseur": "", "alertes": [str(e)], "info": "échec"})
            continue
        except Exception as e:
            S["lus"].append({"fichier": f.name, "fournisseur": "", "alertes": [f"Lecture impossible : {e}"],
                             "info": "échec"})
            continue
        nom = _nom_base(d.get("fournisseur") or f.name.rsplit(".", 1)[0], base)
        deja = _k_de(S, nom)
        rg = to_num(d.get("remise_globale_pct"))
        k = _ajouter(S, nom, **{
            "Délai": as_text(d.get("delai_texte")), "Délai (j)": d.get("delai_jours"),
            "Conditions": as_text(d.get("conditions")), "Crédit (j)": d.get("credit_jours"),
            "Remise %": rg if rg else None, "Transport": d.get("transport"), "Validité": as_text(d.get("validite")),
            "Devis": f"{as_text(d.get('numero')) or f.name}", "Observation": as_text(d.get("remarques"))[:150]})
        alertes, n_ok = [], 0
        if deja and S["docs"]:
            alertes.append("fournisseur déjà présent : ses prix sont remplacés par ce devis")
        for l in d.get("lignes") or []:
            lid = as_text(l.get("id_attendu"))
            if lid not in qte:
                continue
            pu = to_num(l.get("prix_unitaire_ht"))
            if pu <= 0:
                continue
            pu = round(pu * (1 - (to_num(l.get("remise_pct")) or 0) / 100), 2)
            c = _cell(S, k, lid)
            c.update({"pu": pu, "libelle": as_text(l.get("libelle")), "marque": as_text(l.get("marque")),
                      "conformite": as_text(l.get("conformite")) if as_text(l.get("conformite")) in CONFORMITES else "",
                      "qualite": as_text(l.get("qualite")) if as_text(l.get("qualite")) in QUALITES else "",
                      "remarque": as_text(l.get("remarque"))})
            n_ok += 1
            ql = to_num(l.get("quantite"))
            des = as_text(lignes.loc[lignes["_id"].map(as_text) == lid, "Désignation"].iloc[0])
            if ql and abs(ql - qte[lid]) > 1e-6:
                alertes.append(f"{des} : quantité devis {ql:g} ≠ demandée {qte[lid]:g}")
            u = as_text(l.get("unite"))
            if u and unites[lid] and u.lower().rstrip("s.") != unites[lid].lower().rstrip("s."):
                alertes.append(f"{des} : unité devis « {u} » / DA « {unites[lid]} » — vérifiez le prix unitaire")
            if c["conformite"] in ("Non conforme", "À vérifier"):
                alertes.append(f"{des} : {c['conformite'].lower()}" + (f" — {c['remarque']}" if c["remarque"] else ""))
        val = to_date(d.get("validite"))
        if val and val < date.today():
            alertes.append(f"devis expiré le {val:%d/%m/%Y}")
        hors = [as_text(l.get("libelle")) for l in d.get("lignes") or [] if as_text(l.get("id_attendu")) not in qte]
        if hors:
            alertes.append("lignes hors demande ignorées : " + ", ".join(h for h in hors[:4] if h))
        S["docs"][emp] = {"nom": f.name, "octets": octets, "k": k, "fournisseur": nom,
                          "numero": as_text(d.get("numero")), "date": as_text(d.get("date"))}
        S["lus"].append({"fichier": f.name, "fournisseur": nom, "alertes": alertes,
                         "info": f"{n_ok}/{len(qte)} article(s) chiffré(s)"})
    barre.empty()
    _bump(S)


def _bloc_import(S: dict, da: dict, lignes: pd.DataFrame, base: list[str]):
    with st.expander("📥 Importer les devis (photos ou PDF, plusieurs à la fois) — l'IA remplit le tableau",
                     expanded=not S["ordre"]):
        fichiers = st.file_uploader("Devis / proformas", type=["jpg", "jpeg", "png", "webp", "pdf"],
                                    accept_multiple_files=True, key=f"tcpx-up-{da['_id']}-{S['v']}",
                                    label_visibility="collapsed")
        st.caption("Un fichier par devis (une photo par page si besoin : même fournisseur = mêmes colonnes). L'IA "
                   "reconnaît le fournisseur, le délai, les conditions, la remise, les prix, la conformité et la marque.")
        if fichiers and st.button(f"🤖 Lire {len(fichiers)} devis", type="primary", key=f"tcpx-lire-{da['_id']}"):
            S["lus"] = []
            _importer(S, lignes, fichiers, base)
            st.rerun()
        for r in S["lus"]:
            tete = f"**{r['fichier']}** → {r['fournisseur'] or '?'} · {r['info']}"
            if r["alertes"]:
                st.warning(tete + "\n\n" + "\n".join(f"- {a}" for a in r["alertes"]))
            else:
                st.success(tete)


# =========================================================================== langage naturel
def _etat_ia(S: dict, ids: list[str], lignes: pd.DataFrame) -> dict:
    des = dict(zip(lignes["_id"].map(as_text), lignes["Désignation"].map(as_text)))
    ref = dict(zip(lignes["_id"].map(as_text), lignes["Référence"].map(as_text)))
    return {"fournisseurs": [{"nom": _nom(S, k), **{c: S["fours"][k].get(c) for c in
                                                    ("Délai", "Délai (j)", "Conditions", "Crédit (j)", "Remise %")}}
                             for k in S["ordre"]],
            "articles": [{"id": lid, "designation": des.get(lid), "reference": ref.get(lid),
                          "quantite": S["qte"].get(lid),
                          "prix": {_nom(S, k): to_num(_cell(S, k, lid).get("pu")) or None for k in S["ordre"]}}
                         for lid in ids]}


def appliquer(S: dict, ops: list[dict], ids: list[str], base: list[str]) -> list[str]:
    """Applique les opérations décidées par l'IA. Renvoie les faits de terrain à ajouter à la situation."""
    terrain = []
    for o in ops:
        a, nom, aid = as_text(o.get("action")), as_text(o.get("fournisseur")), as_text(o.get("article_id"))
        nb, tx = o.get("nombre"), as_text(o.get("texte"))
        if a == "situation":
            if tx:
                terrain.append(tx)
            continue
        if not nom:
            continue
        k = _k_de(S, nom)
        if a == "ajouter_fournisseur" or (k is None and a == "prix"):
            k = _ajouter(S, _nom_base(nom, base))
        if k is None:
            continue
        cibles = [aid] if aid in ids else [l for l in ids if a != "prix"]
        f = S["fours"][k]
        if a == "retirer_fournisseur":
            _retirer(S, k)
        elif a == "prix" and aid in ids and nb is not None:
            _cell(S, k, aid)["pu"] = float(nb)
        elif a == "retirer_prix":
            for l in cibles:
                _cell(S, k, l)["pu"] = None
        elif a == "remise_globale" and nb is not None:
            f["Remise %"] = float(nb)
        elif a == "delai":
            f["Délai"] = tx or (f"{nb:g} jours" if nb is not None else f["Délai"])
            if nb is not None:
                f["Délai (j)"] = float(nb)
        elif a == "conditions":
            f["Conditions"] = tx or f["Conditions"]
            if nb is not None:
                f["Crédit (j)"] = float(nb)
        elif a == "transport" and nb is not None:
            f["Transport"] = float(nb)
        elif a == "observation" and tx:
            f["Observation"] = " ; ".join(x for x in (as_text(f["Observation"]), tx) if x)
        elif a in ("conformite", "qualite", "marque") and tx:
            ok = {"conformite": CONFORMITES, "qualite": QUALITES}.get(a)
            if ok and tx not in ok:
                continue
            for l in cibles:
                if a == "marque" or to_num(_cell(S, k, l).get("pu")) > 0:
                    _cell(S, k, l)[a] = tx
        elif a == "choisir":
            for l in cibles:
                if pu_net(S, k, l) > 0:
                    S["choix"][l] = k
    return terrain


def _bloc_phrase(S: dict, da: dict, ids: list[str], lignes: pd.DataFrame, base: list[str], cle_sit: str):
    with st.expander("💬 Compléter ou corriger en langage naturel", expanded=bool(S["journal"])):
        st.caption("Ex. : « SOCOCE a appelé : délai 2 jours et 5 % de remise » · « KANIS ne fait pas le câble » · "
                   "« Ajoute BERNABE : disjoncteur 20A à 8 500 » · « le chantier est arrêté, urgence ». "
                   "Le micro du clavier du téléphone permet de dicter.")
        cp = f"tcpx-phrase-{da['_id']}-{len(S['journal'])}"
        phrase = st.text_area("Votre phrase", key=cp, height=80, label_visibility="collapsed",
                              placeholder="Dites ce qui change…")
        if st.button("Appliquer", key=f"tcpx-phr-go-{da['_id']}", disabled=not phrase.strip()):
            with st.spinner("L'IA met à jour le tableau…"):
                try:
                    r = ia.modifier_offres(phrase.strip(), _etat_ia(S, ids, lignes))
                    terrain = appliquer(S, r.get("operations") or [], ids, base)
                    if terrain:
                        S["sit_ajout"] = " ".join(terrain)
                    S["journal"].append((phrase.strip(), as_text(r.get("reponse")) or "Tableau mis à jour."))
                    _bump(S)
                    st.rerun()
                except ia.IAErreur as e:
                    st.error(str(e))
        for q, r in reversed(S["journal"][-4:]):
            st.markdown(f"🗣️ *{q}*  \n🤖 {r}")


# =========================================================================== écran
def ecran(ctx, da: dict, lignes: pd.DataFrame):
    import sourcing
    S = _etat(da["_id"])
    ids = [as_text(x) for x in lignes["_id"]]
    base = achats.fournisseurs(ctx)
    for lid, q in zip(ids, lignes["Reste à commander"]):
        S["qte"].setdefault(lid, float(to_num(q)))
        S["incl"].setdefault(lid, True)
    cle_sit = f"tcpx-sit-{da['_id']}"
    if S["sit_ajout"]:  # fait de terrain dicté dans la phrase : ajouté avant d'afficher la zone
        st.session_state[cle_sit] = " ".join(x for x in (st.session_state.get(cle_sit, ""), S["sit_ajout"]) if x)
        S["sit_ajout"] = ""
    prio, besoin = as_text(da.get("Priorité")), to_date(da.get("Date besoin"))
    st.caption(f"DA **{as_text(da['N° DA'])}** · priorité **{prio or 'Normale'}**"
               + (f" · besoin le **{besoin:%d/%m/%Y}**" + (f" (dans {(besoin - date.today()).days} j)"
                                                          if besoin >= date.today() else " (dépassé)")
                  if besoin else ""))

    if ia.disponible():
        _bloc_import(S, da, lignes, base)
        if S["ordre"]:
            _bloc_phrase(S, da, ids, lignes, base, cle_sit)

    # ---- fournisseurs consultés
    st.markdown("##### Fournisseurs consultés")
    a, b = st.columns([3, 1])
    nouveau = a.selectbox("Ajouter un fournisseur", base, index=None, accept_new_options=True,
                          key=f"tcpx-add-{da['_id']}-{S['v']}", placeholder="Ajouter un fournisseur (choisir ou saisir)",
                          label_visibility="collapsed")
    if b.button("➕ Ajouter", key=f"tcpx-addb-{da['_id']}", disabled=not nouveau):
        _ajouter(S, _nom_base(nouveau, base))
        _bump(S)
        st.rerun()
    if not S["ordre"]:
        st.info("Importez les devis ci-dessus, ou ajoutez les fournisseurs consultés un par un.")
        return
    tf = pd.DataFrame([{**{c: S["fours"][k].get(c) for c in CHAMPS_FOUR}, "Retirer": False} for k in S["ordre"]],
                      index=S["ordre"])
    for c in ("Délai (j)", "Crédit (j)", "Remise %", "Transport"):
        tf[c] = pd.to_numeric(tf[c].map(lambda x: None if as_text(x) == "" else to_num(x)), errors="coerce")
    for c in ("Nom", "Délai", "Conditions", "Validité", "Observation", "Devis"):
        tf[c] = tf[c].map(as_text)
    edf = st.data_editor(tf, hide_index=True, width="stretch", key=f"tcpx-fo-{da['_id']}-{S['v']}",
                         disabled=["Devis"], column_config={
                             "Nom": st.column_config.TextColumn("Fournisseur", required=True),
                             "Délai": st.column_config.TextColumn("Délai (texte)"),
                             "Délai (j)": st.column_config.NumberColumn("Délai (j)", min_value=0, format="%g",
                                                                        help="en jours ; disponible = 0"),
                             "Crédit (j)": st.column_config.NumberColumn("Crédit (j)", min_value=0, format="%g",
                                                                         help="jours de crédit ; comptant = 0"),
                             "Remise %": st.column_config.NumberColumn("Remise globale %", min_value=0, max_value=100,
                                                                       format="%g"),
                             "Transport": st.column_config.NumberColumn("Transport (F)", min_value=0, format="%.0f"),
                             "Retirer": st.column_config.CheckboxColumn("🗑", width="small")})
    for k, r in edf.iterrows():
        if bool(r["Retirer"]):
            _retirer(S, k)
            _bump(S)
            st.rerun()
        for c in CHAMPS_FOUR:
            if c == "Nom":
                S["fours"][k]["Nom"] = " ".join(as_text(r["Nom"]).upper().split()) or S["fours"][k]["Nom"]
            elif c != "Devis":
                v = r[c]
                S["fours"][k][c] = "" if v is None or (isinstance(v, float) and v != v) else v
    noms = [_nom(S, k) for k in S["ordre"]]
    if len(set(noms)) < len(noms):
        st.error("Un même fournisseur figure deux fois : renommez ou retirez la ligne en double.")
        return
    nouveaux = [n for n in noms if n not in base]
    if nouveaux:
        st.caption("Nouveau(x) fournisseur(s), ajouté(s) à la base à l'enregistrement : " + ", ".join(nouveaux))

    # ---- 1) prix
    st.markdown("##### 1. Prix unitaires HT (FCFA)")
    st.caption("Prix du devis, remise de ligne déduite ; la remise globale du fournisseur est appliquée dans la "
               "comparaison. Vide = non chiffré. Décochez ✓ pour sortir un article du TCP.")
    refs_l = {lid: prix.ref_article(ctx, l["Code Article"], l["Désignation"], l["Référence"], l["Colisage"])
              for lid, (_, l) in zip(ids, lignes.iterrows())}
    cref = {lid: prix.colonnes_ref(refs_l[lid]) for lid in ids}
    g = pd.DataFrame({"Inclure": [bool(S["incl"][l]) for l in ids],
                      "Désignation": lignes["Désignation"].map(as_text).values,
                      "Référence": lignes["Référence"].map(as_text).values,
                      "Unité": lignes["Colisage"].map(as_text).values,
                      "Quantité": [S["qte"][l] for l in ids],
                      "Dernier prix": [cref[l]["Dernier prix"] for l in ids],
                      "Meilleur prix passé": [cref[l]["Meilleur prix"] for l in ids],
                      "Prix moyen passé": [cref[l]["Prix moyen"] for l in ids]}, index=ids)
    cfg = {"Inclure": st.column_config.CheckboxColumn("✓", width="small"),
           "Quantité": st.column_config.NumberColumn(min_value=0.0, format="%.2f")}
    for k in S["ordre"]:
        g[f"PU·{k}"] = pd.to_numeric(pd.Series([(to_num(_cell(S, k, l).get("pu")) or None) for l in ids], index=ids,
                                               dtype="object"), errors="coerce")
        cfg[f"PU·{k}"] = st.column_config.NumberColumn(_nom(S, k), min_value=0.0, step=1.0, format="%.0f")
    eg = st.data_editor(g, hide_index=True, width="stretch", column_config=cfg,
                        disabled=["Désignation", "Référence", "Unité", "Dernier prix", "Meilleur prix passé",
                                  "Prix moyen passé"],
                        key=f"tcpx-g-{da['_id']}-{S['v']}-{'|'.join(S['ordre'])}")
    for lid, r in eg.iterrows():
        S["incl"][lid] = bool(r["Inclure"])
        S["qte"][lid] = float(to_num(r["Quantité"]))
        for k in S["ordre"]:
            v = to_num(r[f"PU·{k}"])
            c = _cell(S, k, lid)
            c["pu"] = float(v) if v > 0 else None
    act = [l for l in ids if S["incl"][l] and S["qte"][l] > 0]
    if not act:
        st.warning("Aucun article inclus.")
        return
    if not any(pu_net(S, k, l) > 0 for k in S["ordre"] for l in act):
        st.caption("Saisissez ou importez les prix pour voir la comparaison et la recommandation.")
        return
    des = dict(zip(ids, lignes["Désignation"].map(as_text)))
    ref = dict(zip(ids, lignes["Référence"].map(as_text)))
    uni = dict(zip(ids, lignes["Colisage"].map(as_text)))

    # ---- conformité et qualité
    paires = [(l, k) for l in act for k in S["ordre"] if pu_net(S, k, l) > 0]
    a_voir = sum(1 for l, k in paires if as_text(_cell(S, k, l).get("conformite")) in ("", "À vérifier", "Non conforme"))
    with st.expander(f"🔍 Conformité et qualité des offres ({len(paires)} offre(s)"
                     + (f", {a_voir} à vérifier" if a_voir else "") + ")",
                     expanded=any(as_text(_cell(S, k, l).get("conformite")) == "Non conforme" for l, k in paires)):
        st.caption("Rempli par l'IA à la lecture des devis ; corrigez d'après la fiche technique ou le terrain. "
                   "Une offre non conforme n'est pas recommandée s'il existe une offre conforme.")
        tc = pd.DataFrame([{"Article": f"{des[l]} {ref[l]}".strip(), "Fournisseur": _nom(S, k),
                            "PU net": pu_net(S, k, l), "Libellé du devis": as_text(_cell(S, k, l).get("libelle")),
                            "Marque": as_text(_cell(S, k, l).get("marque")),
                            "Conformité": as_text(_cell(S, k, l).get("conformite")) or None,
                            "Qualité": as_text(_cell(S, k, l).get("qualite")) or None,
                            "Remarque": as_text(_cell(S, k, l).get("remarque"))} for l, k in paires],
                          index=[f"{l}|{k}" for l, k in paires])
        etc = st.data_editor(tc, hide_index=True, width="stretch", disabled=["Article", "Fournisseur", "PU net",
                                                                            "Libellé du devis"],
                             column_config={"PU net": st.column_config.NumberColumn(format="%.0f"),
                                            "Conformité": st.column_config.SelectboxColumn(options=CONFORMITES),
                                            "Qualité": st.column_config.SelectboxColumn(options=QUALITES)},
                             key=f"tcpx-cq-{da['_id']}-{S['v']}-{abs(hash(tuple(tc.index))) % 10 ** 8}")
        for ix, r in etc.iterrows():
            l, k = ix.split("|", 1)
            c = _cell(S, k, l)
            for col, champ in (("Marque", "marque"), ("Conformité", "conformite"), ("Qualité", "qualite"),
                               ("Remarque", "remarque")):
                c[champ] = as_text(r[col])

    # ---- 2) critères, situation terrain, recommandation
    st.markdown("##### 2. Recommandation et choix final")
    cp = f"tcpx-profil-{da['_id']}"
    if cp not in st.session_state:
        st.session_state[cp] = "🚨 Urgence chantier" if prio in ("Urgente", "Critique") else "⚖️ Équilibré"

    def _profil():
        for c, v in PROFILS.get(st.session_state.get(cp), {}).items():
            st.session_state[f"tcpx-w-{c}-{da['_id']}"] = v
    for c, v in PROFILS[st.session_state[cp] if st.session_state[cp] in PROFILS else "⚖️ Équilibré"].items():
        st.session_state.setdefault(f"tcpx-w-{c}-{da['_id']}", v)
    st.segmented_control("Priorité de l'achat", list(PROFILS), key=cp, on_change=_profil)
    with st.expander("🎚️ Ajuster les critères (poids)"):
        cols = st.columns(4)
        poids = {}
        for i, (c, lib) in enumerate(CRITERES.items()):
            poids[c] = cols[i % 4].slider(lib, 0, 100, step=5, key=f"tcpx-w-{c}-{da['_id']}")
    poids = {c: st.session_state[f"tcpx-w-{c}-{da['_id']}"] for c in CRITERES}
    situation = st.text_area("📍 Situation terrain (facultatif) — l'IA en tient compte", key=cle_sit, height=70,
                             placeholder="Ex. : chantier arrêté faute de disjoncteurs ; SOCOCE a livré en retard le mois "
                                         "dernier ; paiement comptant difficile ce mois…")
    notes_df = sourcing.notes_fournisseurs(ctx)
    notes = {}
    for k in S["ordre"]:
        n = sourcing.note_de(notes_df, _nom(S, k))
        notes[k] = to_num(n.get("Note /100")) if n and as_text(n.get("Note /100")) != "" else None
    ev = evaluer(S, act, notes, poids)
    sig = signature(S, act, poids, situation)
    ria = S["reco_ia"]
    if ia.disponible():
        a, b = st.columns([1, 2])
        if a.button("🤖 Recommandation IA", key=f"tcpx-reco-{da['_id']}", type="primary" if not ria else "secondary"):
            with st.spinner("L'IA analyse les offres…"):
                try:
                    r = ia.recommander_tcp(_donnees_reco(S, da, act, ev, notes, poids, situation, des, ref, cref))
                    arts = {}
                    for x in r.get("articles") or []:
                        k = _k_de(S, as_text(x.get("fournisseur")))
                        lid = as_text(x.get("id"))
                        if lid in act and k and pu_net(S, k, lid) > 0:
                            arts[lid] = (k, as_text(x.get("raison")))
                    S["reco_ia"] = ria = {"sig": sig, "articles": arts, "synthese": as_text(r.get("synthese")),
                                          "alertes": [as_text(x) for x in r.get("alertes") or [] if as_text(x)]}
                    _bump(S)
                except ia.IAErreur as e:
                    st.error(str(e))
        if ria and ria["sig"] != sig:
            b.caption("⚠️ Offres, critères ou situation modifiés depuis la recommandation IA : relancez-la. En "
                      "attendant, la recommandation affichée est celle du calcul des critères.")
        elif not ria:
            b.caption("Recommandation actuelle : calcul des critères pondérés. L'IA ajoute la situation terrain, "
                      "l'urgence et le bon sens acheteur.")
    frais = bool(ria and ria["sig"] == sig)
    if frais:
        st.info("🤖 " + ria["synthese"] + ("\n\n" + "\n".join(f"- ⚠️ {x}" for x in ria["alertes"])
                                         if ria["alertes"] else ""))
    reco, raison = {}, {}
    for l in act:
        if frais and l in ria["articles"]:
            reco[l], raison[l] = ria["articles"][l]
            raison[l] = "🤖 " + raison[l]
        else:
            reco[l], raison[l] = ev[l]["reco"], ev[l]["raison"]
    retenu = {l: (S["choix"][l] if l in S["choix"] and (S["choix"][l] == "" or pu_net(S, S["choix"][l], l) > 0)
                  else reco[l]) for l in act}

    # actions rapides
    q1, q2, q3, q4 = st.columns([1.2, 1, 1.3, 0.7])
    if q1.button("✨ Suivre la recommandation", key=f"tcpx-q1-{da['_id']}"):
        S["choix"] = {}
        _bump(S)
        st.rerun()
    if q2.button("💰 Tout au moins-disant", key=f"tcpx-q2-{da['_id']}"):
        S["choix"] = {l: ev[l]["best"] for l in act}
        _bump(S)
        st.rerun()
    tout = q3.selectbox("Tout chez", noms, index=None, key=f"tcpx-q3-{da['_id']}-{S['v']}",
                        placeholder="Tout chez… (ce qu'il chiffre)", label_visibility="collapsed")
    if q4.button("Appliquer", key=f"tcpx-q4-{da['_id']}", disabled=not tout):
        k = _k_de(S, tout)
        for l in act:
            if pu_net(S, k, l) > 0:
                S["choix"][l] = k
        _bump(S)
        st.rerun()

    nom_de = {k: _nom(S, k) for k in S["ordre"]}

    def lib(k, l):
        return f"{nom_de[k]} ({fmt_montant(pu_net(S, k, l))})" if k else "—"
    tch = pd.DataFrame({
        "Article": [f"{des[l]} {ref[l]}".strip() for l in act], "Qté": [S["qte"][l] for l in act],
        "Moins-disant": [lib(ev[l]["best"], l) if ev[l]["best"] else "non chiffré" for l in act],
        "Recommandé": [lib(reco[l], l) for l in act], "Pourquoi": [raison[l] for l in act],
        "Retenu": [nom_de.get(retenu[l]) if retenu[l] else "—" for l in act],
        "Écart vs moins-disant": [(S["qte"][l] * (pu_net(S, retenu[l], l) - pu_net(S, ev[l]["best"], l)))
                                  if retenu[l] and ev[l]["best"] else 0 for l in act],
        "Votre choix": ["✋ modifié" if retenu[l] != reco[l] else "" for l in act]}, index=act)
    ech = st.data_editor(tch, hide_index=True, width="stretch",
                         disabled=["Article", "Qté", "Moins-disant", "Recommandé", "Pourquoi", "Écart vs moins-disant",
                                   "Votre choix"],
                         column_config={"Retenu": st.column_config.SelectboxColumn("Fournisseur retenu ✏️",
                                                                                   options=["—"] + noms, required=True),
                                        "Qté": st.column_config.NumberColumn(format="%g"),
                                        "Écart vs moins-disant": st.column_config.NumberColumn(format="%.0f"),
                                        "Pourquoi": st.column_config.TextColumn(width="large")},
                         key=f"tcpx-ch-{da['_id']}-{S['v']}-{sig}-{int(frais)}")
    change = False
    for l, r in ech.iterrows():
        v = as_text(r["Retenu"])
        k = "" if v in ("", "—") else _k_de(S, v)
        if k is not None and k != retenu[l]:
            if k and pu_net(S, k, l) <= 0:
                st.error(f"{des[l]} : {v} n'a pas chiffré cet article.")
                continue
            S["choix"][l] = k
            change = True
    if change:
        st.rerun()

    # ---- 3) synthèse
    ret = [retenu[l] for l in act]
    best = [ev[l]["best"] for l in act]
    q = [S["qte"][l] for l in act]
    total_ret = sum(qq * pu_net(S, k, l) for qq, k, l in zip(q, ret, act) if k)
    total_best = sum(qq * pu_net(S, k, l) for qq, k, l in zip(q, best, act) if k)
    synth = []
    for k in S["ordre"]:
        chiffres = [l for l in act if pu_net(S, k, l) > 0]
        synth.append({"Fournisseur": nom_de[k], "Articles chiffrés": f"{len(chiffres)}/{len(act)}",
                      "Total offre HT": sum(S["qte"][l] * pu_net(S, k, l) for l in chiffres),
                      "Articles retenus": sum(1 for x in ret if x == k),
                      "Montant retenu HT": sum(qq * pu_net(S, k, l) for qq, x, l in zip(q, ret, act) if x == k),
                      "Délai": as_text(S["fours"][k].get("Délai")) or (f"{to_num(S['fours'][k].get('Délai (j)')):g} j"
                                                                      if as_text(S["fours"][k].get("Délai (j)")) else ""),
                      "Note": f"{notes[k]:.0f}/100" if notes.get(k) is not None else "—"})
    sy = pd.DataFrame(synth)
    st.markdown("##### 3. Synthèse")
    st.dataframe(sy.assign(**{c: sy[c].map(fmt_montant) for c in ("Total offre HT", "Montant retenu HT")}),
                 hide_index=True, width="stretch")
    nb_bc = len({k for k in ret if k})
    delais_ret = [_jours(S["fours"][k].get("Délai (j)")) for k in set(ret) if k]
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total retenu HT", f"{fmt_montant(total_ret)} F")
    m2.metric("Moins-disant (par article)", f"{fmt_montant(total_best)} F")
    ecart = total_ret - total_best
    m3.metric("Écart", f"{'+' if ecart > 0 else ''}{fmt_montant(ecart)} F",
              f"{ecart / total_best * 100:+.1f} %" if total_best and abs(ecart) >= 1 else None, delta_color="inverse")
    m4.metric("Bons de commande", nb_bc, help="Un BC par fournisseur retenu"
              + (f" · délai le plus long : {max(d for d in delais_ret if d is not None):g} j"
                 if any(d is not None for d in delais_ret) else ""))
    transp = [(nom_de[k], to_num(S["fours"][k].get("Transport"))) for k in set(ret) if k
              and to_num(S["fours"][k].get("Transport")) > 0]
    if transp:
        st.caption("Transport facturé en plus : " + ", ".join(f"{n} {fmt_montant(v)} F" for n, v in transp))
    sans = [des[l] for l, k in zip(act, ret) if not k]
    if sans:
        st.warning("Sans fournisseur retenu (non commandés via ce TCP) : " + ", ".join(sans))
    nc = [f"{des[l]} ({nom_de[k]})" for l, k in zip(act, ret) if k
          and as_text(_cell(S, k, l).get("conformite")) == "Non conforme"]
    if nc:
        st.error("⚠️ Offre retenue non conforme : " + ", ".join(nc) + ". Justifiez-le dans le motif.")
    achats._alerte_prix(ctx, [(des[l], pu_net(S, k, l), refs_l.get(l)) for l, k in zip(act, ret) if k], "retenu")

    # ---- motif et enregistrement
    ecarts = [l for l, k, b in zip(act, ret, best) if k and b and k != b]
    cm = f"tcpx-motif-{da['_id']}"
    if ecarts and ia.disponible():
        def _rediger():
            try:
                st.session_state[cm] = ia.motif_tcp({
                    "situation_terrain": st.session_state.get(cle_sit, ""), "priorite_da": prio,
                    "profil": st.session_state.get(cp), "ecart_total_F": round(ecart),
                    "articles": [{"article": des[l], "retenu": nom_de[retenu[l]], "pu_retenu": pu_net(S, retenu[l], l),
                                  "moins_disant": nom_de[ev[l]["best"]], "pu_moins_disant": pu_net(S, ev[l]["best"], l),
                                  "raison": raison[l] if retenu[l] == reco[l] else "choix de l'acheteur",
                                  "conformite_retenu": as_text(_cell(S, retenu[l], l).get("conformite")),
                                  "conformite_moins_disant": as_text(_cell(S, ev[l]["best"], l).get("conformite"))}
                                 for l in ecarts]})
            except ia.IAErreur as e:
                st.session_state[f"{cm}-err"] = str(e)
        st.button("🤖 Rédiger le motif", key=f"tcpx-motif-ia-{da['_id']}", on_click=_rediger)
        if st.session_state.get(f"{cm}-err"):
            st.error(st.session_state.pop(f"{cm}-err"))
    motif = st.text_area("Motif des choix" + (" (obligatoire : des articles ne vont pas au moins-disant)" if ecarts
                                              else " (facultatif)"), key=cm, height=80,
                         placeholder="Ex. : délai, conformité, qualité, urgence chantier…")
    obs = st.text_input("Observations du TCP (facultatif)", key=f"tcpx-obs-{da['_id']}")
    manque = bool(ecarts) and not motif.strip()
    if manque:
        st.caption(f"{len(ecarts)} article(s) hors moins-disant : saisissez ou faites rédiger le motif pour enregistrer.")
    if st.button("Enregistrer le TCP", type="primary", key=f"tcpx-save-{da['_id']}",
                 disabled=not any(ret) or manque):
        _enregistrer(ctx, da, S, act, des, ref, uni, ret, best, reco, raison, sy, motif, obs, situation, frais)


def _donnees_reco(S, da, act, ev, notes, poids, situation, des, ref, cref) -> dict:
    return {
        "da": {"numero": as_text(da.get("N° DA")), "chantier": as_text(da.get("Chantier")),
               "priorite": as_text(da.get("Priorité")), "date_besoin": as_text(da.get("Date besoin")),
               "aujourd_hui": date.today().isoformat()},
        "poids_criteres_pct": poids, "situation_terrain": situation.strip(),
        "fournisseurs": [{"nom": _nom(S, k), "delai": as_text(S["fours"][k].get("Délai")),
                          "delai_jours": _jours(S["fours"][k].get("Délai (j)")),
                          "conditions": as_text(S["fours"][k].get("Conditions")),
                          "credit_jours": _jours(S["fours"][k].get("Crédit (j)")),
                          "remise_globale_pct": _jours(S["fours"][k].get("Remise %")),
                          "transport_F": _jours(S["fours"][k].get("Transport")),
                          "note_fiabilite_sur_100": notes.get(k), "observation": as_text(S["fours"][k].get("Observation"))}
                         for k in S["ordre"]],
        "articles": [{"id": l, "designation": des[l], "reference": ref[l], "quantite": S["qte"][l],
                      "prix_passes": {"meilleur": cref[l].get("Meilleur prix"), "moyen": cref[l].get("Prix moyen")},
                      "moins_disant": _nom(S, ev[l]["best"]) if ev[l]["best"] else "",
                      "recommandation_calculee": _nom(S, ev[l]["reco"]) if ev[l]["reco"] else "",
                      "offres": [{"fournisseur": _nom(S, k), "pu_net": pu_net(S, k, l),
                                  "conformite": as_text(_cell(S, k, l).get("conformite")),
                                  "qualite": as_text(_cell(S, k, l).get("qualite")),
                                  "marque": as_text(_cell(S, k, l).get("marque")),
                                  "remarque": as_text(_cell(S, k, l).get("remarque")),
                                  "score": ev[l]["scores"][k][0]} for k in ev[l]["scores"]]} for l in act]}


def _enregistrer(ctx, da, S, act, des, ref, uni, ret, best, reco, raison, sy, motif, obs, situation, frais):
    nom_de = {k: _nom(S, k) for k in S["ordre"]}
    offres = []
    for k in S["ordre"]:
        f = S["fours"][k]
        extra = "; ".join(x for x in (
            f"remise globale {to_num(f['Remise %']):g} %" if to_num(f.get("Remise %")) else "",
            f"transport {fmt_montant(f['Transport'])} F" if to_num(f.get("Transport")) else "",
            f"validité {f['Validité']}" if as_text(f.get("Validité")) else "",
            f"devis {f['Devis']}" if as_text(f.get("Devis")) else "") if x)
        offres.append({"Nom": nom_de[k], "Délai": as_text(f.get("Délai")) or (
            f"{to_num(f['Délai (j)']):g} jours" if as_text(f.get("Délai (j)")) else ""),
            "Condition": as_text(f.get("Conditions")),
            "Observation": " ; ".join(x for x in (as_text(f.get("Observation")), extra) if x),
            "Délai (j)": _jours(f.get("Délai (j)")), "Crédit (j)": _jours(f.get("Crédit (j)")),
            "Remise %": _jours(f.get("Remise %")), "Transport": _jours(f.get("Transport"))})
    ed = pd.DataFrame({"ID": act, "Désignation": [des[l] for l in act], "Référence": [ref[l] for l in act],
                       "Unité": [uni[l] for l in act], "Quantité": [S["qte"][l] for l in act]})
    pu = pd.DataFrame({nom_de[k]: [pu_net(S, k, l) for l in act] for k in S["ordre"]})
    details = {(l, nom_de[k]): {"Marque": as_text(_cell(S, k, l).get("marque")),
                                "Conformité": as_text(_cell(S, k, l).get("conformite")),
                                "Qualité": as_text(_cell(S, k, l).get("qualite")),
                                "Libellé devis": as_text(_cell(S, k, l).get("libelle")),
                                "Recommandé": "TRUE" if reco[l] == k else "FALSE"}
               for l in act for k in S["ordre"]}
    suivis = sum(1 for l, k in zip(act, ret) if k == reco[l])
    extras = {"Recommandation IA": (" ; ".join(f"{des[l]} → {nom_de.get(reco[l], '—')} ({raison[l].lstrip('🤖 ')})"
                                               for l in act))[:4000],
              "Profil": as_text(st.session_state.get(f"tcpx-profil-{da['_id']}")).split(" ", 1)[-1],
              "Situation terrain": situation.strip()[:1000],
              "Recommandation suivie": f"{suivis}/{len(act)}" + (" (IA)" if frais else " (critères)"),
              "details": details, "docs": list(S["docs"].values())}
    achats._enregistre_tcp(ctx, da, offres, ed, pu, [nom_de.get(k, "") for k in ret],
                           [nom_de.get(k, "") for k in best], sy, motif, obs, extras)
