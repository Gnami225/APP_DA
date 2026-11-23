"""Assistance par l'IA (Claude, API Anthropic) dans toute l'application.

- DA en langage naturel (texte ou dictée au micro du clavier) -> lignes du catalogue ;
- lecture de documents (photo ou PDF) : devis / proforma -> prix du TCP et du BC, bon de livraison -> réception ;
- aide au catalogage des articles saisis hors catalogue ;
- rédaction (motifs de rejet, relances fournisseurs, commentaire de la période) ;
- assistant d'analyse : questions en français, chiffres calculés par l'application (outils), jamais inventés.

Secrets Streamlit :
    [ia]
    api_key = "sk-ant-…"                 # console.anthropic.com > API keys
    model = "claude-sonnet-5-5"           # facultatif
    model_rapide = "claude-haiku-5-5"     # facultatif (extraction simple)
L'IA est appelée seulement quand l'utilisateur clique sur un bouton « IA » : aucun effet sur la vitesse du reste.
"""
from __future__ import annotations

import base64
import io
import json
import re
import unicodedata

import pandas as pd
import streamlit as st

from logic import as_text

MODELES = ["claude-sonnet-5-5", "claude-sonnet-5", "claude-sonnet-4-6"]
MODELES_RAPIDES = ["claude-haiku-5-5", "claude-haiku-4-5"]
CONTEXTE_PWC = ("Tu assistes PWC (Public Work Contractor), entreprise de BTP et de logistique en Côte d'Ivoire : "
                "chantiers, parc d'engins et de camions, achats (demandes d'achat DA, tableaux comparatifs TCP, bons "
                "de commande BC). Montants en francs CFA (F). Réponds en français, de façon précise et concise.")


class IAErreur(Exception):
    pass


# =========================================================================== appel de l'API
def _conf() -> dict:
    try:
        c = dict(st.secrets.get("ia", {}))
    except Exception:
        c = {}
    if not c.get("api_key"):
        try:
            c["api_key"] = st.secrets.get("ANTHROPIC_API_KEY", "")
        except Exception:
            pass
    return c


def disponible() -> bool:
    return bool(_conf().get("api_key"))


@st.cache_resource(show_spinner=False)
def _client(cle: str):
    import anthropic
    return anthropic.Anthropic(api_key=cle, max_retries=2, timeout=120)


def _modeles(rapide: bool) -> list[str]:
    c = _conf()
    prefere = as_text(c.get("model_rapide" if rapide else "model"))
    liste = (MODELES_RAPIDES + MODELES) if rapide else MODELES
    return ([prefere] if prefere else []) + [m for m in liste if m != prefere]


def _creer(**kw):
    """Appel Messages API, en essayant les modèles dans l'ordre (un modèle absent passe au suivant)."""
    import anthropic
    c = _conf()
    if not c.get("api_key"):
        raise IAErreur("IA non configurée : ajoutez la clé API dans les secrets (section [ia]).")
    derniere = None
    for m in _modeles(kw.pop("rapide", False)):
        try:
            return _client(c["api_key"]).messages.create(model=m, **kw)
        except anthropic.NotFoundError as e:
            derniere = e
            continue
        except anthropic.AuthenticationError:
            raise IAErreur("Clé API refusée : vérifiez la clé dans les secrets [ia].")
        except anthropic.RateLimitError:
            raise IAErreur("Limite d'utilisation de l'IA atteinte : réessayez dans une minute.")
        except anthropic.APIConnectionError:
            raise IAErreur("IA injoignable : vérifiez la connexion internet.")
        except anthropic.APIStatusError as e:
            raise IAErreur(f"Erreur du service IA ({e.status_code}) : réessayez.")
    raise IAErreur(f"Aucun modèle Claude disponible avec cette clé ({derniere}).")


def _texte(resp) -> str:
    return "\n".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()


def _json_du_texte(t: str):
    m = re.search(r"\{.*\}|\[.*\]", t, re.S)
    if not m:
        raise IAErreur("Réponse de l'IA illisible : réessayez.")
    return json.loads(m.group(0))


def structure(system: str, contenu, outil: dict, rapide: bool = False, max_tokens: int = 8000) -> dict:
    """Réponse structurée : l'IA doit appeler l'outil `outil` (schéma JSON) ; à défaut, JSON dans le texte."""
    resp = _creer(system=system + f"\n\nRéponds uniquement en appelant l'outil « {outil['name']} ».",
                  messages=[{"role": "user", "content": contenu}], tools=[outil], max_tokens=max_tokens,
                  rapide=rapide)
    for b in resp.content:
        if getattr(b, "type", "") == "tool_use" and b.name == outil["name"]:
            return b.input
    return _json_du_texte(_texte(resp))


def texte(system: str, contenu, rapide: bool = False, max_tokens: int = 3000) -> str:
    return _texte(_creer(system=system, messages=[{"role": "user", "content": contenu}], max_tokens=max_tokens,
                         rapide=rapide))


def agent(system: str, messages: list[dict], outils: dict, max_tours: int = 6) -> tuple[str, list[dict]]:
    """Boucle d'outils : l'IA appelle les fonctions de calcul de l'application, puis rédige la réponse.
    outils : nom -> (schéma, fonction(**args) -> (résultat texte/JSON, tableau DataFrame ou None))."""
    msgs = list(messages)
    traces = []
    for _ in range(max_tours):
        resp = _creer(system=system, messages=msgs, tools=[s for s, _f in outils.values()], max_tokens=8000)
        appels = [b for b in resp.content if getattr(b, "type", "") == "tool_use"]
        if not appels:
            return _texte(resp), traces
        msgs.append({"role": "assistant", "content": resp.content})
        res = []
        for b in appels:
            schema, f = outils.get(b.name, (None, None))
            try:
                sortie, table = f(**b.input) if f else (f"Outil inconnu : {b.name}", None)
            except Exception as e:  # paramètre invalide : l'IA corrige au tour suivant
                sortie, table = f"Erreur : {e}", None
            traces.append({"outil": b.name, "parametres": b.input, "table": table})
            res.append({"type": "tool_result", "tool_use_id": b.id, "content": as_text(sortie)[:60000]})
        msgs.append({"role": "user", "content": res})
    return "Analyse trop longue : reformulez la question plus simplement.", traces


# =========================================================================== fichiers (images, PDF)
def bloc_fichier(nom: str, contenu: bytes) -> dict:
    """Bloc image (réduite à 1 600 px) ou PDF pour l'API."""
    if nom.lower().endswith(".pdf"):
        return {"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                               "data": base64.b64encode(contenu).decode()}}
    from PIL import Image, ImageOps
    im = ImageOps.exif_transpose(Image.open(io.BytesIO(contenu))).convert("RGB")
    im.thumbnail((1600, 1600))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                        "data": base64.b64encode(buf.getvalue()).decode()}}


# =========================================================================== recherche locale dans le catalogue
def _plat(t) -> str:
    t = unicodedata.normalize("NFKD", as_text(t).upper())
    return " ".join("".join(c for c in t if not unicodedata.combining(c)).replace(",", ".").split())


def _jetons(t: str) -> list[str]:
    return [x for x in re.split(r"[^A-Z0-9./]+", _plat(t)) if x and x not in {"DE", "DU", "LA", "LE", "LES", "ET",
                                                                              "POUR", "EN", "A", "AU", "AVEC"}]


@st.cache_resource(show_spinner=False, max_entries=8)
def _index_catalogue(version, _art: pd.DataFrame) -> list[tuple]:
    out = []
    for c, d, r, f, u in zip(_art["Code Article"], _art["Désignation"], _art["Référence"], _art["Famille"],
                             _art["Colisage"]):
        if as_text(c):
            out.append((as_text(c), as_text(d), as_text(r), as_text(f), as_text(u), set(_jetons(d)),
                        set(_jetons(r)), _plat(d)))
    return out


def candidats(art: pd.DataFrame, requete: str, n: int = 10, version=0) -> list[dict]:
    """Articles du catalogue les plus proches d'une description (recherche locale, sans IA)."""
    import difflib
    q = _jetons(requete)
    if not q:
        return []
    qs, qp = set(q), _plat(requete)
    res = []
    for code, d, r, f, u, jd, jr, pd_ in _index_catalogue(version, art):
        s = 0.0
        for t in qs:
            if t in jd:
                s += 3
            elif t in jr:
                s += 2
            elif len(t) >= 3 and any(x.startswith(t) or t.startswith(x) for x in jd if len(x) >= 3):
                s += 1.5
            elif len(t) >= 2 and any(t in x for x in jr):
                s += 1
        if s <= 0:
            continue
        s += 2 * difflib.SequenceMatcher(None, qp[:40], pd_[:40]).ratio()
        res.append((s, {"code": code, "designation": d, "reference": r, "famille": f, "colisage": u}))
    res.sort(key=lambda x: -x[0])
    return [x[1] for x in res[:n]]


# =========================================================================== 1. DA en langage naturel
_OUTIL_EXTRAIRE = {
    "name": "extraire_besoins",
    "description": "Liste des articles demandés dans le texte.",
    "input_schema": {"type": "object", "properties": {"articles": {"type": "array", "items": {
        "type": "object", "properties": {
            "texte": {"type": "string", "description": "passage d'origine"},
            "designation": {"type": "string", "description": "type d'article, ex. PNEU, FILTRE A GASOIL, CIMENT"},
            "caracteristiques": {"type": "string", "description": "dimensions, référence, marque, norme…"},
            "quantite": {"type": "number"},
            "unite": {"type": "string", "description": "unité dite (sac, tonne, litre, unité…) ou vide"},
            "engin": {"type": "string", "description": "engin ou véhicule concerné, tel que dit (ex. camion 12)"},
            "usage": {"type": "string", "description": "usage ou destination, déduit du texte si possible"}},
        "required": ["designation", "quantite"]}},
        "entete": {"type": "object", "description": "si indiqué (formulaire de DA, liste écrite…)", "properties": {
            "priorite": {"type": "string", "enum": ["Normale", "Urgente", "Critique"]},
            "date_besoin": {"type": "string", "description": "AAAA-MM-JJ"},
            "observations": {"type": "string"}}}},
        "required": ["articles"]}}

_OUTIL_CHOISIR = {
    "name": "choisir_articles",
    "description": "Pour chaque besoin, article du catalogue retenu (ou proposition hors catalogue).",
    "input_schema": {"type": "object", "properties": {"choix": {"type": "array", "items": {
        "type": "object", "properties": {
            "index": {"type": "integer"},
            "code": {"type": ["string", "null"], "description": "code d'un des candidats, ou null si aucun ne "
                                                                 "correspond vraiment"},
            "colisage": {"type": "string", "description": "unité de commande à utiliser"},
            "quantite": {"type": "number", "description": "quantité convertie dans ce colisage si besoin"},
            "code_parc": {"type": "string", "description": "code parc de l'engin s'il est identifiable dans la "
                                                           "liste fournie, sinon vide"},
            "designation_proposee": {"type": "string", "description": "si code null : type d'article standardisé"},
            "reference_proposee": {"type": "string", "description": "si code null : référence standardisée"},
            "confiance": {"type": "string", "enum": ["haute", "moyenne", "faible"]},
            "remarque": {"type": "string", "description": "point à vérifier par le demandeur, sinon vide"}},
        "required": ["index", "code", "colisage", "quantite", "confiance"]}}}, "required": ["choix"]}}


def analyser_besoin(texte_libre: str, art: pd.DataFrame, parc: pd.DataFrame, colisages: list[str],
                    version=0) -> list[dict]:
    """Texte libre -> lignes de DA rattachées au catalogue (2 appels : extraction, puis choix parmi des
    candidats trouvés localement dans le catalogue)."""
    return _analyser(texte_libre, art, parc, colisages, version, rapide=True)[0]


def analyser_document_da(nom: str, contenu: bytes, art: pd.DataFrame, parc: pd.DataFrame, colisages: list[str],
                         version=0, consigne: str = "") -> tuple[list[dict], dict]:
    """Photo / PDF (DA papier, liste manuscrite, devis, capture…) -> (lignes de DA, en-tête lu)."""
    return _analyser([bloc_fichier(nom, contenu),
                      {"type": "text", "text": "Document : demande ou liste de besoins d'un chantier. Lis toutes les "
                                               "lignes (même manuscrites)." + (f" Précision : {consigne}" if consigne
                                                                                else "")}],
                     art, parc, colisages, version, rapide=False)


def _analyser(contenu, art, parc, colisages, version, rapide) -> tuple[list[dict], dict]:
    ex = structure(CONTEXTE_PWC + " Extrais chaque article demandé, avec sa quantité ; et l'en-tête s'il est "
                   "indiqué (priorité, date de besoin, observations).", contenu, _OUTIL_EXTRAIRE, rapide=rapide)
    besoins = ex.get("articles") or []
    entete = ex.get("entete") or {}
    if not besoins:
        return [], entete
    cand = [candidats(art, f"{b.get('designation', '')} {b.get('caracteristiques', '')}", 12, version)
            for b in besoins]
    parcs = [f"{as_text(c)} = {as_text(d)}" for c, d in zip(parc["Code_Parc"], parc["Désignation engin"])
             if as_text(c)][:400]
    demande = {"besoins": [{"index": i, **b, "candidats": cand[i]} for i, b in enumerate(besoins)],
               "codes_parc": parcs, "colisages_autorises": colisages}
    ch = structure(CONTEXTE_PWC + " Pour chaque besoin, choisis l'article du catalogue qui correspond (même type "
                   "ET mêmes caractéristiques : dimension, référence). Si aucun candidat ne correspond vraiment, "
                   "mets code à null et propose une désignation (type d'article, sans dimension) et une référence "
                   "(dimensions, norme) standardisées en MAJUSCULES, comme au catalogue. Choisis le colisage dans "
                   "la liste autorisée ; garde l'unité du catalogue si elle convient.",
                   json.dumps(demande, ensure_ascii=False), _OUTIL_CHOISIR)
    par_i = {int(c["index"]): c for c in ch.get("choix", []) if "index" in c}
    fiche = {as_text(c): (as_text(d), as_text(r), as_text(u)) for c, d, r, u in
             zip(art["Code Article"], art["Désignation"], art["Référence"], art["Colisage"])}
    lignes = []
    for i, b in enumerate(besoins):
        c = par_i.get(i, {})
        code = as_text(c.get("code"))
        if code and code in fiche:
            des, ref, col = fiche[code]
            statut = "✅ Catalogue"
        else:
            code, des = "", as_text(c.get("designation_proposee") or b.get("designation")).upper()
            ref, col = as_text(c.get("reference_proposee") or b.get("caracteristiques")).upper(), ""
            statut = "🆕 Hors catalogue"
        col = as_text(c.get("colisage")) or col or "Unité"
        lignes.append({"Code Article": code, "Désignation": des, "Référence": ref,
                       "Colisage": col if col in colisages else (col or "Unité"),
                       "Quantité": float(c.get("quantite") or b.get("quantite") or 1),
                       "Code_Parc": as_text(c.get("code_parc")) or as_text(b.get("engin")).upper(),
                       "Usage": as_text(b.get("usage")), "Statut": statut,
                       "Confiance": as_text(c.get("confiance")), "À vérifier": as_text(c.get("remarque")),
                       "Texte": as_text(b.get("texte"))})
    return lignes, entete


# =========================================================================== 2. lecture de documents
_OUTIL_DOC = {
    "name": "lire_document",
    "description": "Contenu structuré du document (devis, proforma, facture, bon de livraison).",
    "input_schema": {"type": "object", "properties": {
        "type_document": {"type": "string", "enum": ["devis", "proforma", "facture", "bon de livraison", "autre"]},
        "fournisseur": {"type": "string"},
        "numero": {"type": "string", "description": "numéro du document lui-même (n° BL, n° devis, n° facture)"},
        "references_commande": {"type": "array", "items": {"type": "string"},
                                "description": "numéros de bon de commande / commande client / DA cités"},
        "chantier": {"type": "string", "description": "chantier ou lieu de livraison s'il est indiqué"},
        "date": {"type": "string", "description": "AAAA-MM-JJ si lisible"},
        "lignes": {"type": "array", "items": {"type": "object", "properties": {
            "designation": {"type": "string"}, "reference": {"type": "string"},
            "quantite": {"type": "number"}, "unite": {"type": "string"},
            "prix_unitaire_ht": {"type": "number"}, "remise_pct": {"type": "number"},
            "tva_pct": {"type": "number"}, "montant_ht": {"type": "number"}},
            "required": ["designation"]}},
        "total_ht": {"type": "number"}, "total_ttc": {"type": "number"},
        "conditions": {"type": "string", "description": "conditions de paiement / délai si indiqués"},
        "remarques": {"type": "string", "description": "éléments illisibles ou douteux"}},
        "required": ["lignes"]}}

_OUTIL_RAPPROCHER = {
    "name": "rapprocher",
    "description": "Correspondance entre les lignes du document et les lignes attendues.",
    "input_schema": {"type": "object", "properties": {"correspondances": {"type": "array", "items": {
        "type": "object", "properties": {
            "id_attendu": {"type": "string"}, "index_document": {"type": ["integer", "null"]},
            "remarque": {"type": "string"}}, "required": ["id_attendu", "index_document"]}}},
        "required": ["correspondances"]}}


def lire_document(nom: str, contenu: bytes) -> dict:
    return structure(CONTEXTE_PWC + " Lis ce document fournisseur et recopie fidèlement chaque ligne. Les montants "
                     "sont en F CFA : n'invente aucune valeur, laisse vide ce qui est illisible.",
                     [bloc_fichier(nom, contenu), {"type": "text", "text": "Extrais le contenu du document."}],
                     _OUTIL_DOC)


def rapprocher(doc: dict, attendues: list[dict]) -> dict[str, dict]:
    """Associe les lignes lues sur le document aux lignes attendues (DA / BC). Renvoie id -> ligne du document."""
    lignes = doc.get("lignes") or []
    if not lignes or not attendues:
        return {}
    r = structure(CONTEXTE_PWC + " Associe chaque ligne attendue à la ligne du document qui désigne le même "
                  "article (même type et mêmes caractéristiques). index_document = null si absente.",
                  json.dumps({"attendues": attendues, "document": [{"index": i, **l} for i, l in enumerate(lignes)]},
                             ensure_ascii=False), _OUTIL_RAPPROCHER, rapide=True)
    out = {}
    for c in r.get("correspondances", []):
        i = c.get("index_document")
        if isinstance(i, int) and 0 <= i < len(lignes):
            out[as_text(c.get("id_attendu"))] = {**lignes[i], "remarque": as_text(c.get("remarque"))}
    return out


def ui_lecture(cle: str, attendues: list[dict], quoi: str = "le devis ou la proforma") -> dict[str, dict] | None:
    """Bloc d'interface : photo / PDF -> lecture -> rapprochement. Le résultat reste en session sous `cle`."""
    if not disponible():
        return st.session_state.get(cle)
    with st.expander(f"📷 Lire {quoi} avec l'IA (photo ou PDF)"):
        f = st.file_uploader("Document", type=["jpg", "jpeg", "png", "webp", "pdf"], key=f"{cle}-f",
                             label_visibility="collapsed")
        if f is not None and st.button("Lire le document", key=f"{cle}-go", type="primary"):
            with st.spinner("Lecture du document par l'IA…"):
                try:
                    doc = lire_document(f.name, f.getvalue())
                    st.session_state[cle] = rapprocher(doc, attendues)
                    st.session_state[f"{cle}-doc"] = doc
                    st.session_state[f"{cle}-v"] = st.session_state.get(f"{cle}-v", 0) + 1
                except IAErreur as e:
                    st.error(str(e))
                except Exception as e:
                    st.error(f"Lecture impossible : {e}")
        doc = st.session_state.get(f"{cle}-doc")
        if doc:
            m = st.session_state.get(cle) or {}
            ent = " · ".join(x for x in (f"{as_text(doc.get('type_document'))} {as_text(doc.get('numero'))}".strip(),
                                         as_text(doc.get("fournisseur"))) if x)
            st.caption(f"Lu : {ent + ' · ' if ent else ''}{len(doc.get('lignes') or [])} ligne(s), "
                       f"{len(m)}/{len(attendues)} rapprochée(s) avec les articles attendus."
                       + (f" Total HT lu : {doc['total_ht']:,.0f} F".replace(",", " ") if doc.get("total_ht") else "")
                       + (f" ⚠ {doc['remarques']}" if doc.get("remarques") else ""))
            if st.button("Effacer la lecture", key=f"{cle}-raz"):
                for k in (cle, f"{cle}-doc"):
                    st.session_state.pop(k, None)
                st.session_state[f"{cle}-v"] = st.session_state.get(f"{cle}-v", 0) + 1
                st.rerun()
    return st.session_state.get(cle)


# =========================================================================== 3. catalogage
_OUTIL_CATALOGUE = {
    "name": "decider",
    "description": "Décision de catalogage pour chaque article saisi hors catalogue.",
    "input_schema": {"type": "object", "properties": {"decisions": {"type": "array", "items": {
        "type": "object", "properties": {
            "index": {"type": "integer"},
            "decision": {"type": "string", "enum": ["Rattacher", "Intégrer"]},
            "code": {"type": "string", "description": "si Rattacher : code du candidat identique"},
            "designation": {"type": "string"}, "reference": {"type": "string"},
            "famille": {"type": "string", "description": "si Intégrer : famille parmi la liste"},
            "justification": {"type": "string"}},
        "required": ["index", "decision"]}}}, "required": ["decisions"]}}


def proposer_catalogage(items: list[dict], art: pd.DataFrame, familles: list[str], version=0) -> list[dict]:
    """items : [{désignation, référence}] -> décisions (Rattacher à un code existant ou Intégrer)."""
    demande = [{"index": i, "designation_saisie": it["designation"], "reference_saisie": it["reference"],
                "candidats": candidats(art, f"{it['designation']} {it['reference']}", 8, version)}
               for i, it in enumerate(items)]
    r = structure(CONTEXTE_PWC + " Tu standardises le catalogue articles : DÉSIGNATION = type d'article sans "
                  "dimension (ex. PNEU, FILTRE A GASOIL, BETON PRET A L'EMPLOI) ; RÉFÉRENCE = variante (dimension, "
                  "norme, référence constructeur). Rattacher seulement si un candidat est exactement le même article ; "
                  "sinon Intégrer avec désignation et référence standardisées en MAJUSCULES et une famille de la liste.",
                  json.dumps({"articles": demande, "familles": familles}, ensure_ascii=False), _OUTIL_CATALOGUE)
    return r.get("decisions", [])


# =========================================================================== 4. rédaction
def reformuler_motif(brouillon: str, contexte: str) -> str:
    return texte(CONTEXTE_PWC + " Reformule ce motif de rejet en une ou deux phrases professionnelles, claires et "
                 "courtoises, qui disent précisément quoi corriger. Réponds avec le motif seul.",
                 f"Contexte : {contexte}\nBrouillon : {brouillon}", rapide=True, max_tokens=400)


def relance_fournisseur(bc: dict, reste: list[dict], expediteur: str) -> str:
    return texte(CONTEXTE_PWC + " Rédige un email de relance de livraison, ferme et courtois, prêt à envoyer : "
                 "objet sur la première ligne (« Objet : … »), puis le corps. Pas de crochets à compléter.",
                 json.dumps({"bc": bc, "articles_restant_a_livrer": reste, "signataire": expediteur},
                            ensure_ascii=False, default=str), max_tokens=1200)


def commentaire_periode(indicateurs: dict) -> str:
    return texte(CONTEXTE_PWC + " Rédige le commentaire de la direction achats sur la période : 5 à 8 puces "
                 "(faits marquants, tendances, risques, actions recommandées), en t'appuyant uniquement sur les "
                 "chiffres fournis.", json.dumps(indicateurs, ensure_ascii=False, default=str), max_tokens=1500)


def bouton_reformuler(cle_texte: str, contexte: str, cle: str):
    """Bouton « ✨ Reformuler » sous un champ texte : remplace le brouillon par une formulation propre."""
    if not disponible():
        return

    def _cb():
        b = as_text(st.session_state.get(cle_texte))
        if not b:
            st.session_state[f"{cle}-err"] = "Écrivez d'abord quelques mots."
            return
        try:
            st.session_state[cle_texte] = reformuler_motif(b, contexte)
        except IAErreur as e:
            st.session_state[f"{cle}-err"] = str(e)

    st.button("✨ Reformuler avec l'IA", key=cle, on_click=_cb, help="Améliore la rédaction du motif")
    if st.session_state.get(f"{cle}-err"):
        st.caption(st.session_state.pop(f"{cle}-err"))


# =========================================================================== 5. recherche web (sourcing)
LIEU_PWC = {"type": "approximate", "city": "Abidjan", "country": "CI", "timezone": "Africa/Abidjan"}


def recherche_web(system: str, consigne: str, outil: dict, max_recherches: int = 5,
                  max_tokens: int = 12000) -> tuple[dict, list[str], int]:
    """Recherche web côté Anthropic puis réponse structurée via `outil`.
    Renvoie (résultat, URLs réellement consultées, nombre de recherches)."""
    tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": int(max_recherches),
              "user_location": LIEU_PWC}, outil]
    msgs = [{"role": "user", "content": consigne}]
    urls, nb = [], 0
    for _ in range(4):  # « pause_turn » : l'API rend la main pendant une longue recherche, on relance
        resp = _creer(system=system + f"\n\nCherche sur le web, puis réponds en appelant l'outil « {outil['name']} ».",
                      messages=msgs, tools=tools, max_tokens=max_tokens)
        for b in resp.content:
            t = getattr(b, "type", "")
            if t == "server_tool_use":
                nb += 1
            elif t == "web_search_tool_result":
                c = getattr(b, "content", None)
                if isinstance(c, list):
                    urls += [getattr(r, "url", "") for r in c if getattr(r, "url", "")]
            elif t == "tool_use" and b.name == outil["name"]:
                return b.input, urls, nb
        if getattr(resp, "stop_reason", "") != "pause_turn":
            break
        msgs = msgs + [{"role": "assistant", "content": resp.content}]
    try:
        return _json_du_texte(_texte(resp)), urls, nb
    except IAErreur:
        raise IAErreur("La recherche web n'a pas abouti : réessayez ou précisez l'article.")


_OUTIL_COTATION = {
    "name": "rediger_cotations",
    "description": "Un email de demande de prix par fournisseur.",
    "input_schema": {"type": "object", "properties": {"emails": {"type": "array", "items": {
        "type": "object", "properties": {"fournisseur": {"type": "string"},
                                         "langue": {"type": "string", "enum": ["fr", "en"]},
                                         "objet": {"type": "string"}, "corps": {"type": "string"}},
        "required": ["fournisseur", "objet", "corps"]}}}, "required": ["emails"]}}


def demandes_cotation(fournisseurs: list[dict], articles: list[dict], infos: dict) -> list[dict]:
    """fournisseurs : [{nom, pays}] ; articles : [{designation, reference, quantite, unite}]."""
    r = structure(CONTEXTE_PWC + " Rédige pour chaque fournisseur une demande de cotation (demande de prix) "
                  "professionnelle, prête à envoyer : liste des articles avec quantités, demande de prix unitaire HT, "
                  "délai de livraison, conditions de paiement, validité de l'offre, et pour un fournisseur hors Côte "
                  "d'Ivoire : incoterm (CIF/CFR Abidjan ou FOB), devise et délai de transport. En français, en "
                  "anglais si le fournisseur est dans un pays non francophone. Pas de crochets à compléter.",
                  json.dumps({"fournisseurs": fournisseurs, "articles": articles, "acheteur": infos},
                             ensure_ascii=False, default=str), _OUTIL_COTATION, max_tokens=8000)
    return r.get("emails", [])


def discuter(system: str, messages: list[dict], rapide: bool = True, max_tokens: int = 1500) -> str:
    """Conversation simple (sans outil) : messages = [{"role": "user"|"assistant", "content": texte}]."""
    msgs = [m for m in messages if as_text(m.get("content"))]
    while msgs and msgs[0]["role"] != "user":
        msgs.pop(0)
    return _texte(_creer(system=system, messages=msgs, max_tokens=max_tokens, rapide=rapide))


_OUTIL_BC = {
    "name": "remplir_bc",
    "description": "Valeurs du bon de commande d'après la demande de l'acheteur.",
    "input_schema": {"type": "object", "properties": {
        "fournisseur": {"type": "string", "description": "nom du fournisseur (de préférence tel que dans la liste)"},
        "condition_reglement": {"type": "string"}, "magasin": {"type": "string"}, "observations": {"type": "string"},
        "lignes": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string", "description": "id de la ligne de la DA concernée"},
            "inclure": {"type": "boolean", "description": "false si l'acheteur dit de ne pas commander la ligne"},
            "quantite": {"type": "number"}, "prix_unitaire_ht": {"type": "number"},
            "remise_pct": {"type": "number"}, "tva_pct": {"type": "number"}}, "required": ["id"]}}},
        "required": ["lignes"]}}


def remplir_bc(consigne: str, lignes: list[dict], fournisseurs: list[str], conditions: list[str],
               actuel: dict) -> dict:
    """Consigne de l'acheteur (écrite ou dictée) -> valeurs du BC (en-tête + lignes par id)."""
    return structure(CONTEXTE_PWC + " Tu prépares un bon de commande pour un acheteur. Applique sa consigne aux "
                     "lignes de la DA (id fournis) : prix unitaires HT en F CFA (« 185 mille » = 185000), remises, "
                     "TVA (0 si exonéré), quantités, lignes à exclure ; fournisseur et conditions de règlement : "
                     "reprends l'orthographe de la liste si possible. Ne renvoie que ce que la consigne indique.",
                     json.dumps({"consigne": consigne, "lignes_da": lignes, "fournisseurs_connus": fournisseurs[:400],
                                 "conditions_connues": conditions, "valeurs_actuelles": actuel},
                                ensure_ascii=False, default=str), _OUTIL_BC)
