"""Assistant de saisie des DA (onglet « Nouvelle DA ») : accueil personnalisé, aide, conseils, et DA en
conversation (orale ou écrite) : l'IA pose les questions, le demandeur répond, la DA se remplit.

- Accueil : « Bonjour M. Koné, bienvenue » (administrateurs et chefs de service), « Bonjour Ibrahim » (demandeurs),
  avec la situation de ses DA calculée par l'application.
- Voix : synthèse et reconnaissance vocales du navigateur (composant voix/) ; Claude est le seul modèle d'IA appelé.
- Rien n'est soumis sans le clic « Soumettre la DA ».
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

import ia
from logic import as_text, to_date, to_num

FORMEL = {"Admin", "Chef de service"}
CLES_ENTETE = {"chantier": "da-ch", "priorite": "da-prio", "date_besoin": "da-besoin", "observations": "da-obs"}

try:
    import streamlit.components.v1 as _components
    _voix = _components.declare_component("voix", path=str(Path(__file__).parent / "voix"))
except Exception:  # dossier absent : conversation écrite seulement
    _voix = None


# =========================================================================== accueil
def _famille_prenom(nom: str) -> tuple[str, str]:
    """Nom de famille = le mot écrit en MAJUSCULES (« KONÉ Ibrahim ») ; à défaut, le dernier mot
    (« Ibrahim Koné »). Prénom = les autres mots."""
    p = as_text(nom).split()
    if not p:
        return "", ""
    maj = [i for i, x in enumerate(p) if x.isupper() and len(x) > 1]
    i = maj[0] if maj and len(maj) < len(p) else (0 if maj else len(p) - 1)
    fam = p[i].capitalize() if p[i].isupper() else p[i]
    pre = " ".join(x.capitalize() if x.isupper() else x for j, x in enumerate(p) if j != i) or fam
    return fam, pre


def salutation(user: dict) -> str:
    nom = as_text(user.get("Nom"))
    if not nom or nom == "Administrateur":
        return "Bonjour"
    fam, pre = _famille_prenom(nom)
    if user.get("Rôle") in FORMEL:
        return f"Bonjour {as_text(user.get('Civilité')) or 'M.'} {fam}"
    return f"Bonjour {pre}"


def _mes_da(ctx, user) -> pd.DataFrame:
    da = ctx.load("DA")
    return da.loc[(da["Demandeur"].map(as_text) == user["Nom"])
                  | ((da["Email Demandeur"].map(as_text) == as_text(user.get("Email"))) & (as_text(user.get("Email")) != ""))]


def situation(ctx, user, nb_a_valider: int = 0) -> list[str]:
    m = _mes_da(ctx, user)
    st_ = m["Statut"].map(as_text)
    out = []
    n = int((st_ == "Rejetée").sum())
    if n:
        out.append(f"🔴 {n} DA rejetée(s) à corriger ({', '.join(m.loc[st_ == 'Rejetée', 'N° DA'].map(as_text).head(4))})"
                   " — onglet « Mes DA ».")
    n = int(st_.isin(["Émise", "Validée Resp. service"]).sum())
    if n:
        out.append(f"🟠 {n} DA en attente de validation.")
    lg = ctx.load("HISTORIQUE_ARTICLES")
    lg = lg.loc[lg["ID_DA"].map(as_text).isin(set(m["ID_DA"].map(as_text))).astype(bool)]
    sl = lg["Statut ligne"].map(as_text)
    n = int(sl.isin(["Commandée", "Commandée partielle"]).sum())
    if n:
        out.append(f"🚚 {n} article(s) commandé(s), en attente de livraison.")
    recent = lg.loc[sl.isin(["Livrée", "Livrée partielle"]) &
                    lg["Date livraison"].map(lambda d: bool(to_date(d)) and (date.today() - to_date(d)).days <= 7)]
    if len(recent):
        out.append(f"✅ {len(recent)} article(s) livré(s) ces 7 derniers jours.")
    if nb_a_valider:
        out.append(f"🔔 {nb_a_valider} DA de votre service à valider — onglet « Validation DA ».")
    return out


GUIDE = """Règles de l'application DA Chantier PWC :
- Une DA = un chantier, un service demandeur, une priorité (Normale, Urgente, Critique), une date de besoin
  facultative, et des articles (désignation + référence du catalogue, colisage, quantité, code parc de l'engin
  facultatif, usage obligatoire).
- Choisir l'article dans la liste « Désignation » puis « Référence ». Un article absent peut être tapé librement :
  il part au catalogage (l'administrateur l'intègre ou le rattache à un code existant).
- Circuit : DA émise → visa du responsable de service → validation Direction générale → Achats (TCP, BC) →
  livraison. Une DA saisie par un chef de service ou un administrateur passe directement à la DG.
- Une DA reste modifiable par son auteur tant qu'elle n'est pas validée par la DG (onglet « Mes DA »). Une DA
  rejetée se corrige et se renvoie depuis « Mes DA » ; le motif du rejet y est affiché.
- La fiche PDF est téléchargeable après la soumission et dans « Mes DA » ; le « Dossier d'achat » montre le suivi
  complet (TCP, BC, livraisons).
- On peut décrire la demande en langage naturel, ou faire la DA en conversation (à l'oral ou à l'écrit).
- Priorité Urgente / Critique : à réserver aux arrêts d'engin ou de chantier ; indiquer la date de besoin.
- Pièces d'engins importées : prévoir plusieurs semaines de délai."""


def panneau(ctx, user: dict, nb_a_valider: int = 0):
    with st.container(border=True):
        st.markdown(f"**{salutation(user)}, bienvenue.** 👋")
        for l in situation(ctx, user, nb_a_valider):
            st.markdown(l)
        if not ia.disponible():
            st.caption("🤖 Assistant IA non activé : la clé API Claude n'est pas encore configurée "
                       "(section [ia] des secrets de l'application). La saisie classique ci-dessous fonctionne.")
            return
        mode = st.segmented_control("Assistant", ["🎙️ Faire ma DA en conversation", "❓ Poser une question"],
                                    key="ada-mode", label_visibility="collapsed")
        if mode == "❓ Poser une question":
            _aide(user)
        elif mode == "🎙️ Faire ma DA en conversation":
            conversation(ctx, user)


def _aide(user, guide: str = GUIDE, cle: str = "ada", role_ia: str = "l'assistant de saisie des demandes d'achat",
          exemple: str = "Ex. : comment corriger une DA rejetée ? que faire si l'article n'est pas dans la liste ?"):
    h = st.session_state.setdefault(f"{cle}-aide", [])
    for m in h[-6:]:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])
    v = st.session_state.get(f"{cle}-aide-v", 0)
    c1, c2 = st.columns([5, 1])
    q = c1.text_input("Question", key=f"{cle}-q-{v}", label_visibility="collapsed",
                      placeholder=exemple)
    if c2.button("Demander", key=f"{cle}-go-{v}", disabled=not q.strip()):
        h.append({"role": "user", "content": q.strip()})
        try:
            rep = ia.discuter(ia.CONTEXTE_PWC + " Tu es " + role_ia + ". Réponds en 2 à 5 "
                              f"phrases simples, pratiques. Tu t'adresses à {salutation(user).replace('Bonjour ', '')}."
                              f"\n\n{guide}", h[-8:])
        except ia.IAErreur as e:
            rep = f"⚠️ {e}"
        h.append({"role": "assistant", "content": rep})
        st.session_state[f"{cle}-aide-v"] = v + 1
        st.rerun()


# =========================================================================== conseils (sans IA)
def conseils(ctx, lignes: list[dict], priorite: str, date_besoin) -> list[str]:
    out = []
    if not lignes:
        return out
    art = ctx.load("ARTICLES")
    vus = {}
    for l in lignes:
        k = (as_text(l.get("Code Article")) or f"{l['Désignation']}|{l.get('Référence', '')}")
        vus[k] = vus.get(k, 0) + 1
        if not as_text(l.get("Code Article")):
            c = ia.candidats(art, f"{l['Désignation']} {l.get('Référence', '')}", 1, ctx.version())
            if c:
                out.append(f"« {l['Désignation']} {l.get('Référence', '')} » n'est pas au catalogue : vérifiez s'il "
                           f"s'agit de « {c[0]['designation']} · {c[0]['reference']} » ({c[0]['code']}).")
    out += [f"Article saisi deux fois : {k.split('|')[0]} — regroupez les quantités." for k, n in vus.items() if n > 1]
    h = ctx.load("HISTORIQUE_ARTICLES")
    q = h.assign(q=h["Quantité"].map(to_num)).groupby(h["Code Article"].map(as_text))["q"].agg(["median", "count"])
    for l in lignes:
        c = as_text(l.get("Code Article"))
        if c in q.index and q.at[c, "count"] >= 3 and q.at[c, "median"] > 0 and to_num(l["Quantité"]) > 3 * q.at[c, "median"]:
            out.append(f"Quantité inhabituelle pour {l['Désignation']} : {to_num(l['Quantité']):g} demandés, "
                       f"{q.at[c, 'median']:g} d'habitude. Vérifiez.")
    try:
        import magasin
        noms = ctx.refs()["chantiers"]
        for l in lignes:
            if as_text(l.get("Code Article")):
                d = magasin.disponible(ctx, l["Code Article"])
                if d:
                    out.append(f"{l['Désignation']} {l.get('Référence', '')} est en stock : "
                               + ", ".join(f"{q:g} au magasin {noms.get(m, m)}" for m, q in d)
                               + " — un transfert peut éviter un achat.")
    except Exception:
        pass
    if date_besoin:
        j = (date_besoin - date.today()).days
        if j <= 3 and priorite == "Normale":
            out.append(f"Besoin dans {max(j, 0)} jour(s) avec une priorité « Normale » : passez-la en « Urgente » si "
                       "c'est justifié.")
        if priorite in ("Urgente", "Critique") and j > 30:
            out.append(f"Priorité « {priorite} » mais besoin dans {j} jours : la priorité « Normale » suffit peut-être.")
    elif priorite in ("Urgente", "Critique"):
        out.append("DA urgente : indiquez la date de besoin pour aider les achats.")
    return out


# =========================================================================== DA en conversation
def appliquer_entete():
    """À appeler en haut de l'onglet, avant les champs : reporte les valeurs dictées dans l'en-tête."""
    for k, v in (st.session_state.pop("da_pending", None) or {}).items():
        st.session_state[k] = v


def _etat(ctx) -> dict:
    r = ctx.refs()
    return {"aujourdhui": date.today().isoformat(),
            "entete": {"chantier": st.session_state.get("da-ch"), "priorite": st.session_state.get("da-prio"),
                       "date_besoin": str(st.session_state.get("da-besoin") or ""),
                       "observations": st.session_state.get("da-obs", "")},
            "articles": [{"numero": i + 1, **{k: l.get(k) for k in ("Code Article", "Désignation", "Référence",
                                                                  "Colisage", "Quantité", "Code_Parc", "Usage")}}
                         for i, l in enumerate(st.session_state.get("lignes", []))],
            "chantiers": [f"{c} = {n}" for c, n in list(r["chantiers"].items())[:60]]}


def _outils(ctx) -> dict:
    from schema import COLISAGES, PRIORITES
    art = ctx.load("ARTICLES")
    fiche = {as_text(c): (as_text(d), as_text(r), as_text(u)) for c, d, r, u in
             zip(art["Code Article"], art["Désignation"], art["Référence"], art["Colisage"])}
    lignes = st.session_state.setdefault("lignes", [])

    def chercher_articles(description):
        c = ia.candidats(art, description, 8, ctx.version())
        return json.dumps(c or "aucun article proche au catalogue", ensure_ascii=False), None

    def chercher_engin(texte):
        p = ctx.load("PARC")
        t = as_text(texte).upper()
        m = p.loc[p["Code_Parc"].map(as_text).str.upper().str.contains(t, regex=False)
                  | p["Désignation engin"].map(as_text).str.upper().str.contains(t, regex=False)
                  | p["Marque / Modèle"].map(as_text).str.upper().str.contains(t, regex=False)].head(10)
        return json.dumps([{"code_parc": as_text(c), "engin": as_text(d), "modele": as_text(mo)} for c, d, mo in
                           zip(m["Code_Parc"], m["Désignation engin"], m["Marque / Modèle"])] or "aucun engin trouvé",
                          ensure_ascii=False), None

    def ajouter_article(quantite, usage, code=None, designation="", reference="", colisage="", code_parc=""):
        if not as_text(usage):
            return "Refusé : l'usage est obligatoire, demande-le.", None
        if to_num(quantite) <= 0:
            return "Refusé : quantité manquante, demande-la.", None
        code = as_text(code).upper()
        if code and code in fiche:
            des, ref, col = fiche[code]
        elif code:
            return f"Refusé : le code {code} n'existe pas ; utilise chercher_articles.", None
        else:
            des, ref, col = (" ".join(as_text(designation).upper().split()), " ".join(as_text(reference).upper().split()),
                             "")
            if not des:
                return "Refusé : désignation manquante.", None
        col = as_text(colisage) if as_text(colisage) in COLISAGES else (col if col in COLISAGES else "Unité")
        lignes.append({"Code Article": code if code in fiche else "", "Désignation": des, "Référence": ref,
                       "Code_Parc": as_text(code_parc).upper(), "Colisage": col, "Quantité": float(to_num(quantite)),
                       "Usage": as_text(usage)})
        return f"Ajouté en ligne {len(lignes)} : {des} {ref} — {to_num(quantite):g} {col}" + \
               ("" if code in fiche else " (hors catalogue)"), None

    def modifier_article(numero, quantite=None, colisage=None, code_parc=None, usage=None):
        i = int(numero) - 1
        if not 0 <= i < len(lignes):
            return "Numéro de ligne inconnu.", None
        if quantite is not None and to_num(quantite) > 0:
            lignes[i]["Quantité"] = float(to_num(quantite))
        if colisage in COLISAGES:
            lignes[i]["Colisage"] = colisage
        if code_parc is not None:
            lignes[i]["Code_Parc"] = as_text(code_parc).upper()
        if as_text(usage):
            lignes[i]["Usage"] = as_text(usage)
        return f"Ligne {numero} modifiée.", None

    def retirer_article(numero):
        i = int(numero) - 1
        if not 0 <= i < len(lignes):
            return "Numéro de ligne inconnu.", None
        l = lignes.pop(i)
        return f"Ligne {numero} retirée ({l['Désignation']}).", None

    def definir_entete(priorite=None, date_besoin=None, observations=None, chantier=None):
        p = st.session_state.setdefault("da_pending", {})
        msg = []
        if priorite in PRIORITES:
            p["da-prio"] = priorite
            msg.append(f"priorité {priorite}")
        if as_text(date_besoin):
            try:
                d = date.fromisoformat(as_text(date_besoin)[:10])
                p["da-besoin"] = d
                msg.append(f"besoin le {d:%d/%m/%Y}")
            except ValueError:
                return "Date invalide : format AAAA-MM-JJ.", None
        if observations is not None:
            p["da-obs"] = as_text(observations)
            msg.append("observations notées")
        if as_text(chantier):
            if as_text(chantier) in ctx.refs()["chantiers"]:
                p["da-ch"] = as_text(chantier)
                msg.append(f"chantier {chantier}")
            else:
                return "Code chantier inconnu : choisis dans la liste des chantiers.", None
        return "En-tête mis à jour : " + ", ".join(msg) if msg else "Rien à changer.", None

    def terminer(recapitulatif=""):
        st.session_state["conv_fini"] = True
        return "Conversation terminée : invite à vérifier puis cliquer sur « Soumettre la DA ».", None

    S = lambda props, req=(): {"type": "object", "properties": props, "required": list(req)}
    s, n = {"type": "string"}, {"type": "number"}
    return {
        "chercher_articles": ({"name": "chercher_articles", "description": "Cherche dans le catalogue PWC les "
                               "articles proches d'une description (type + caractéristiques).",
                               "input_schema": S({"description": s}, ["description"])}, chercher_articles),
        "chercher_engin": ({"name": "chercher_engin", "description": "Cherche le code parc d'un engin/véhicule "
                            "(numéro, type ou marque).", "input_schema": S({"texte": s}, ["texte"])}, chercher_engin),
        "ajouter_article": ({"name": "ajouter_article", "description": "Ajoute une ligne à la DA. code = code "
                             "catalogue trouvé par chercher_articles, ou vide pour un article hors catalogue (alors "
                             "designation et reference standardisées en majuscules).",
                             "input_schema": S({"code": s, "designation": s, "reference": s, "quantite": n,
                                                "colisage": {"type": "string", "enum": COLISAGES}, "code_parc": s,
                                                "usage": s}, ["quantite", "usage"])}, ajouter_article),
        "modifier_article": ({"name": "modifier_article", "description": "Modifie une ligne existante (numéro).",
                              "input_schema": S({"numero": {"type": "integer"}, "quantite": n,
                                                 "colisage": {"type": "string", "enum": COLISAGES}, "code_parc": s,
                                                 "usage": s}, ["numero"])}, modifier_article),
        "retirer_article": ({"name": "retirer_article", "description": "Retire une ligne de la DA.",
                             "input_schema": S({"numero": {"type": "integer"}}, ["numero"])}, retirer_article),
        "definir_entete": ({"name": "definir_entete", "description": "Renseigne l'en-tête : priorité, date de "
                            "besoin (AAAA-MM-JJ), observations, code chantier.",
                            "input_schema": S({"priorite": {"type": "string", "enum": PRIORITES}, "date_besoin": s,
                                               "observations": s, "chantier": s})}, definir_entete),
        "terminer": ({"name": "terminer", "description": "Quand tout est saisi et récapitulé.",
                      "input_schema": S({"recapitulatif": s})}, terminer),
    }


def _systeme(ctx, user) -> str:
    return (ia.CONTEXTE_PWC + f" Tu remplis une demande d'achat (DA) en conversation avec "
            f"{salutation(user).replace('Bonjour ', '')}, souvent à l'oral sur un chantier. Tes réponses sont LUES À "
            "VOIX HAUTE : une ou deux phrases courtes, une seule question à la fois, pas de liste ni de markdown, "
            "pas de codes articles à prononcer (dis la désignation et la référence).\n"
            "Déroulé : pour chaque article demandé → chercher_articles → si un seul article correspond, confirme-le "
            "brièvement ; s'il y en a plusieurs, propose au plus 3 références et demande laquelle ; si aucun ne "
            "correspond, enregistre-le hors catalogue. Demande la quantité (et l'unité si elle n'est pas évidente), "
            "l'engin concerné s'il y en a un (chercher_engin pour le code parc ; « aucun » est possible), et l'usage "
            "(obligatoire). Puis ajouter_article et demande s'il y a un autre article. Quand il n'y en a plus : "
            "demande la priorité et la date de besoin (definir_entete ; convertis « mardi prochain », « dans 10 "
            "jours » en date), fais un récapitulatif oral très court (nombre d'articles, principaux articles), puis "
            "appelle terminer et dis de vérifier l'écran et de cliquer sur « Soumettre la DA ». Corrige si on te le "
            "demande (modifier_article / retirer_article). N'invente jamais un code.\n\nÉtat actuel de la DA : "
            + json.dumps(_etat(ctx), ensure_ascii=False, default=str))


def _tour(ctx, user, texte: str):
    c = st.session_state["conv"]
    c["hist"].append({"role": "user", "content": texte})
    with st.spinner("…"):
        try:
            msgs = [m for m in c["hist"][-16:]]
            while msgs and msgs[0]["role"] != "user":
                msgs.pop(0)
            rep, _ = ia.agent(_systeme(ctx, user), msgs, _outils(ctx), max_tours=8)
        except ia.IAErreur as e:
            rep = f"Désolé, {e}"
    c["hist"].append({"role": "assistant", "content": rep or "Pouvez-vous répéter ?"})
    c["id"] += 1
    st.rerun()


def conversation(ctx, user):
    c = st.session_state.get("conv")
    if not c:
        st.caption("L'assistant vous pose les questions et remplit la DA. Répondez à l'oral (bouton 🎤) ou à l'écrit. "
                   "Autorisez le micro au premier usage. Vous vérifiez à la fin, puis cliquez sur « Soumettre la DA ».")
        if st.button("▶️ Commencer", type="primary", key="conv-go"):
            st.session_state["conv"] = {"hist": [{"role": "assistant", "content":
                                                  f"{salutation(user)}. Je remplis la demande avec vous. "
                                                  "Quel est le premier article dont vous avez besoin ?"}], "id": 1}
            st.session_state.pop("conv_fini", None)
            st.rerun()
        return
    for m in c["hist"][-6:]:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])
    fini = bool(st.session_state.get("conv_fini"))
    dernier = c["hist"][-1]["content"] if c["hist"][-1]["role"] == "assistant" else ""
    a, b = st.columns([3, 1])
    auto = b.toggle("Écoute auto", value=True, key="conv-auto", help="Le micro s'ouvre après chaque question")
    if _voix is not None:
        with a:
            v = _voix(dire=dernier, id=c["id"], auto=auto, fini=fini, key="conv-voix", default=None)
        if v and v.get("id") != st.session_state.get("conv-vu"):
            st.session_state["conv-vu"] = v.get("id")
            _tour(ctx, user, as_text(v.get("texte")))
    k = st.session_state.get("conv-txt-v", 0)
    c1, c2, c3 = st.columns([4, 1, 1])
    t = c1.text_input("Répondre par écrit", key=f"conv-txt-{k}", label_visibility="collapsed",
                      placeholder="Ou répondez ici par écrit…")
    if c2.button("Envoyer", key=f"conv-send-{k}", disabled=not t.strip()):
        st.session_state["conv-txt-v"] = k + 1
        _tour(ctx, user, t.strip())
    if c3.button("Arrêter", key="conv-stop"):
        for x in ("conv", "conv_fini", "conv-vu"):
            st.session_state.pop(x, None)
        st.rerun()
    if fini:
        st.success("DA remplie : vérifiez l'en-tête et les articles ci-dessous, puis cliquez sur « Soumettre la DA ».")
