"""DA Chantier PWC — saisie, impression, validation et archivage des demandes d'achat.

Lancer en local :   streamlit run app.py
Données : Google Sheet (si les secrets sont configurés) sinon fichier Excel local data/PWC_DA_local.xlsx
"""
from __future__ import annotations

import difflib
import hashlib
import io
import os
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

from acces import POSTES, Garde, creer_jeton, ligne_txt, lire_jeton, normalise, nouveau_code, trouve_poste
from logic import as_text, build_da, lignes_da, fmt_date, fmt_montant, is_true, maintenant, nouvel_id, send_email, to_date
from pdf import build_pdf, find_logo
import achats
import catalogage
import assistant
import assistant_da
import bl
import magasin
import ia
import graphs
import prix
import stats
from schema import (TABLES, ANCIENS_STATUTS, COLISAGES, DA_EN_ATTENTE, PRIORITES, ROLES, ROLES_ACHATS, ROLES_ADMIN, ROLES_BL, ROLES_DA, CIVILITES, ROLES_STOCK_VUE,
                    ROLES_DG, ROLES_VALID_BC, ROLES_VALID_DA, STATUTS, STATUTS_LIGNE, LIGNE_BC_RETIREE)
from storage import ExcelStore, GoogleSheetStore

APP_DIR = Path(__file__).resolve().parent
LOGO = find_logo()
_u = st.session_state.get("user") or {}
LARGE = {"Admin", "Acheteur", "Direction"}  # rôles qui travaillent sur des tableaux : page large
st.set_page_config(page_title="DA Chantier PWC", page_icon=str(LOGO) if LOGO else "📋",
                   layout="wide" if _u.get("Rôle") in LARGE else "centered")
ORANGE = "#F29200"
st.markdown(f"""<style>
div.block-container{{padding-top:3.2rem;padding-bottom:3rem}}
.stButton>button,.stDownloadButton>button{{width:100%;min-height:2.8rem;font-weight:600}}
.pwc-bar{{display:flex;align-items:center;justify-content:space-between;gap:.8rem;
  border-bottom:3px solid {ORANGE};padding:.2rem 0 .6rem;margin-bottom:.6rem}}
.pwc-bar .t{{font-size:1.15rem;font-weight:700;letter-spacing:.02em}}
.pwc-bar .t span{{color:{ORANGE}}}
.pwc-bar .u{{font-size:.85rem;opacity:.8;text-align:right}}
.pwc-badge{{display:inline-block;padding:.05rem .5rem;border-radius:1rem;font-size:.75rem;font-weight:700;
  background:{ORANGE};color:#141414;margin-left:.3rem}}
div[data-baseweb="tab-list"] button[aria-selected="true"] p{{color:{ORANGE};font-weight:700}}
div[data-testid="stMetricValue"]{{color:{ORANGE}}}
[data-testid="stMarkdownContainer"] h5{{color:{ORANGE}}}
</style>""", unsafe_allow_html=True)


# --------------------------------------------------------------------------- données
def _secret(key, default=None):
    try:
        return st.secrets[key]
    except Exception:
        return default


@st.cache_resource(show_spinner="Connexion à la base…")
def _service_account():
    """Clé Google : bloc [gcp_service_account] ou fichier JSON collé tel quel dans GCP_JSON."""
    raw = _secret("GCP_JSON")
    if raw:
        import json
        if isinstance(raw, dict):  # GCP_JSON saisi comme table TOML
            return dict(raw)
        txt = str(raw)
        for bad, good in {"\u201c": '"', "\u201d": '"', "\u201e": '"', "\u00ab": '"', "\u00bb": '"',
                          "\u2018": "'", "\u2019": "'", "\u00a0": " ", "\ufeff": ""}.items():
            txt = txt.replace(bad, good)  # guillemets typographiques, espaces insécables
        start, end = txt.find("{"), txt.rfind("}")
        try:
            if start < 0 or end < start:
                raise ValueError("aucune accolade { } trouvée")
            return json.loads(txt[start:end + 1], strict=False)
        except ValueError as exc:
            import re
            champs = re.findall(r'"([a-z_0-9]+)"\s*:', txt)
            attendus = ["type", "project_id", "private_key_id", "private_key", "client_email", "token_uri"]
            manquants = [c for c in attendus if c not in champs]
            st.error("Le secret GCP_JSON n'est pas lisible. Diagnostic (sans la clé) :")
            st.code(f"Erreur : {getattr(exc, 'msg', exc)}"
                    + (f" (ligne {exc.lineno}, colonne {exc.colno})" if hasattr(exc, "lineno") else "")
                    + f"\nLongueur : {len(txt)} caractères, {txt.count(chr(10)) + 1} lignes"
                    + f"\nCommence par : {txt.strip()[:1]!r}   Finit par : {txt.strip()[-1:]!r}"
                    + f"\nChamps trouvés : {', '.join(champs) or 'aucun'}"
                    + f"\nChamps manquants : {', '.join(manquants) or 'aucun'}")
            st.stop()
    sa = _secret("gcp_service_account")
    return dict(sa) if sa else None


@st.cache_resource(show_spinner="Connexion à la base…")
def _store_google(sheet_id: str, version_schema: str):
    """Connexion ouverte une seule fois par serveur (évite 3 appels Google à chaque enregistrement).
    `version_schema` change dès qu'un onglet ou une colonne est ajouté dans schema.py : la connexion est
    alors rouverte et les nouveaux onglets / colonnes sont créés dans le Google Sheet."""
    return GoogleSheetStore(_service_account(), sheet_id)


# change dès que schema.py ou storage.py change : la connexion en mémoire est alors recréée avec le nouveau code
VERSION_SCHEMA = hashlib.md5((repr(sorted(TABLES.items())) + (APP_DIR / "storage.py").read_text(encoding="utf-8"))
                             .encode()).hexdigest()[:10]


def get_store():
    sa, sheet_id = _service_account(), _secret("SHEET_ID")
    if sa and sheet_id:
        try:
            return _store_google(sheet_id, VERSION_SCHEMA)
        except Exception as exc:
            code = getattr(exc, "code", "")
            aide = {403: "accès refusé : partagez le Google Sheet avec l'adresse du compte de service (Éditeur) "
                         "et vérifiez que l'API Google Sheets est activée dans le projet.",
                    404: "Google Sheet introuvable : vérifiez SHEET_ID dans les secrets.",
                    429: "quota Google momentanément dépassé : patientez une minute puis rechargez la page."}
            st.error(f"Connexion au Google Sheet impossible (code {code}) — "
                     f"{aide.get(code, 'réessayez dans une minute ; si cela persiste, envoyez ce message.')}")
            st.stop()
    if sheet_id:  # en ligne mais clé illisible : ne jamais basculer en silence sur un fichier local
        st.error("Clé Google introuvable dans les secrets : ajoutez GCP_JSON (contenu du fichier .json) "
                 "ou le bloc [gcp_service_account], puis redémarrez l'app.")
        st.stop()
    path = os.environ.get("DA_EXCEL_PATH", str(APP_DIR / "data" / "PWC_DA_local.xlsx"))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    return ExcelStore(path)


@st.cache_resource(show_spinner=False)
def _services_pwc() -> bool:
    """Ajoute dans SERVICES les 5 services PWC s'ils manquent (une fois par démarrage)."""
    store = get_store()
    sv = store.read("SERVICES")
    presents = set(sv["Code Service"].map(as_text))
    manquants = [{"Code Service": code, "Libellé": nom} for nom, code in POSTES.items() if code not in presents]
    if manquants:
        store.append("SERVICES", manquants)
    achats.init_parametres(store)
    us = store.read("UTILISATEURS")  # civilité préremplie à « M. » (à corriger pour les dames)
    if "Civilité" in us.columns:
        vides = {as_text(n): {"Civilité": "M."} for n, c in zip(us["Nom"], us["Civilité"]) if as_text(n) and not as_text(c)}
        if vides:
            store.update_many("UTILISATEURS", "Nom", vides)
    return True


# Onglets qui changent peu (gardés 15 min) et onglets de saisie (gardés 3 min). Les données restent en
# mémoire du serveur sans copie (cache_resource) : chaque lecture est instantanée.
STATIQUES = ["ARTICLES", "PARC", "CHANTIERS", "SERVICES", "UTILISATEURS", "PARAMETRES", "CONDITIONS_REGLEMENT",
             "FOURNISSEURS", "PRIX_HISTORIQUE"]
TTL = {"statique": 900, "dynamique": 180}


@st.cache_resource(show_spinner=False)
def _memoire() -> dict:
    import threading
    return {"lock": threading.Lock(), "statique": None, "dynamique": None, "t_statique": 0.0, "t_dynamique": 0.0,
            "version": 0}


def _load_all() -> dict:
    """Base en mémoire : un appel Google par groupe d'onglets à recharger, puis lectures instantanées."""
    import time
    m = _memoire()
    with m["lock"]:
        maintenant = time.time()
        a_lire = [g for g in ("statique", "dynamique") if m[g] is None or maintenant - m["t_" + g] > TTL[g]]
        if a_lire:
            _services_pwc()
            tables = [t for t in TABLES if (t in STATIQUES) == ("statique" in a_lire)] if len(a_lire) == 1 else None
            store = get_store()
            try:
                data = store.read_all(tables)
            except TypeError:  # connexion créée par une version précédente du code
                data = store.read_all()
            for g in a_lire:
                m[g] = {t: df for t, df in data.items() if (t in STATIQUES) == (g == "statique")}
                m["t_" + g] = maintenant
            m["version"] += 1
        return {**m["statique"], **m["dynamique"]}


_COLONNES_REQUISES = {"UTILISATEURS": ["Email", "Nom", "Rôle", "Chantier", "Service", "Actif", "PIN", "Poste",
                                        "Civilité"]}


def load(table: str) -> pd.DataFrame:
    df = _load_all().get(table)
    if df is None:  # onglet pas encore créé dans la base : table vide
        df = pd.DataFrame(columns=TABLES.get(table, []))
    df = df.copy(deep=False)  # protège les données en mémoire (copie légère, instantanée)
    manquantes = [c for c in TABLES.get(table, []) + _COLONNES_REQUISES.get(table, []) if c not in df.columns]
    if manquantes:  # tolère un schema.py ou un Google Sheet en retard d'une version
        df = df.copy()
        for c in manquantes:
            df[c] = ""
    if table == "BC_LIGNES" and len(df):  # lignes retirées lors d'une modification de BC : gardées, non affichées
        df = df.loc[df["Statut livraison"].map(as_text) != LIGNE_BC_RETIREE]
    if table == "DA" and len(df):
        df = df.copy()
        df["Statut"] = df["Statut"].map(lambda s: ANCIENS_STATUTS.get(as_text(s), as_text(s)))
    return df


def refresh(statique: bool = True):
    """Après un enregistrement : relit la base au prochain affichage (seulement les onglets de saisie si
    statique=False)."""
    m = _memoire()
    with m["lock"]:
        m["dynamique"] = None
        if statique:
            m["statique"] = None


def version_donnees() -> int:
    return _memoire()["version"]


def refs() -> dict:
    ch, sv = load("CHANTIERS"), load("SERVICES")
    return {
        "chantiers": {as_text(r["Code Chantier"]): as_text(r["Nom Chantier"]) or as_text(r["Code Chantier"])
                      for _, r in ch.iterrows()},
        "services": {as_text(r["Code Service"]): as_text(r["Libellé"]) or as_text(r["Code Service"])
                     for _, r in sv.iterrows()},
    }


def lignes_de(id_da: str) -> list[dict]:
    lg = load("HISTORIQUE_ARTICLES")
    return lg[lg["ID_DA"].map(as_text) == as_text(id_da)].to_dict("records")


def pdf_de(da: dict) -> bytes:
    return build_pdf(da, lignes_de(da["ID_DA"]), refs())


def pdf_name(da: dict) -> str:
    suffix = {"Validée DG": "_VALIDEE", "Validée Resp. service": "_VISA_SERVICE",
              "Rejetée": "_REJETEE"}.get(as_text(da.get("Statut")), "")
    return f"{as_text(da.get('N° DA'))}{suffix}.pdf"


# --------------------------------------------------------------------------- connexion
@st.cache_resource
def garde() -> Garde:
    return Garde()


ADMIN_SECOURS = "__admin__"


def _secret_session() -> str:
    """Clé de signature des jetons : SESSION_SECRET si défini, sinon dérivée des secrets existants."""
    import hashlib

    explicite = as_text(_secret("SESSION_SECRET", ""))
    if explicite:
        return explicite
    base = "|".join([as_text(_secret("GCP_JSON", "")), str(_secret("gcp_service_account", "")),
                     as_text(_secret("ADMIN_PIN", "")), as_text(_secret("SHEET_ID", "")), "pwc-da"])
    return hashlib.sha256(base.encode()).hexdigest()


def _user_dict(nom: str) -> dict | None:
    if nom == ADMIN_SECOURS:
        return {"Nom": "Administrateur", "Rôle": "Admin", "Email": "", "Chantier": "", "Service": "", "Poste": "",
                "Civilité": ""}
    users = load("UTILISATEURS")
    r = users.loc[users["Nom"].map(as_text) == nom]
    if not len(r):
        return None
    r = r.iloc[0]
    if not (as_text(r["Actif"]) == "" or is_true(r["Actif"])):
        return None
    u = {k: as_text(r.get(k)) for k in ["Nom", "Rôle", "Email", "Chantier", "Service", "Poste", "Civilité"]}
    u["Rôle"] = u["Rôle"] if u["Rôle"] in ROLES else "Demandeur"
    return u


def _code_actuel(nom: str) -> str | None:
    if nom == ADMIN_SECOURS:
        return as_text(_secret("ADMIN_PIN", "")) or None
    if not _user_dict(nom):
        return None
    users = load("UTILISATEURS")
    return as_text(users.loc[users["Nom"].map(as_text) == nom].iloc[0]["PIN"]) or None


def ouvrir_session(nom: str, code: str):
    """Connecte l'utilisateur et mémorise un jeton signé dans l'adresse (survit à l'actualisation)."""
    st.session_state.user = _user_dict(nom)
    st.query_params["s"] = creer_jeton(nom, code, _secret_session())
    st.rerun()


def restaurer_session():
    jeton = st.query_params.get("s")
    if not jeton or "user" in st.session_state:
        return
    nom = lire_jeton(jeton, _secret_session(), _code_actuel)
    user = _user_dict(nom) if nom else None
    if user:
        st.session_state.user = user
    else:
        del st.query_params["s"]  # jeton expiré, code changé ou accès coupé


def login():
    if LOGO:
        st.image(str(LOGO), width=150)
    st.subheader("Demandes d'achat chantier")
    users = load("UTILISATEURS")
    users = users.loc[users["Actif"].map(lambda v: as_text(v) == "" or is_true(v)).astype(bool)]
    noms = sorted(users["Nom"].map(as_text).loc[lambda s: s != ""].unique())
    admin_pin = as_text(_secret("ADMIN_PIN", ""))
    if not noms and not admin_pin:
        st.error("Aucun utilisateur actif. Connectez-vous en administrateur pour créer les accès.")
        st.stop()
    poste_de = {as_text(r["Nom"]): (as_text(r["Poste"]) if as_text(r["Poste"]) in POSTES else "Autres")
                for _, r in users.iterrows()}
    postes_presents = [p for p in POSTES if p in poste_de.values()]
    if "Autres" in poste_de.values() or admin_pin:
        postes_presents.append("Autres")
    with st.form("login"):
        poste = st.selectbox("Votre poste", postes_presents, index=None, placeholder="Choisir votre poste")
        pin = st.text_input("Code d'accès", type="password")
        ok = st.form_submit_button("Se connecter", type="primary")
    if ok:
        if not poste:
            st.error("Choisissez votre poste.")
            st.stop()
        cle = f"poste:{poste}"
        attente = garde().bloque(cle)
        if attente:
            st.error(f"Trop de codes incorrects pour ce poste. Réessayez dans {attente // 60 + 1} min.")
            st.stop()
        if poste == "Autres" and admin_pin and normalise(pin) == normalise(admin_pin):
            garde().succes(cle)
            ouvrir_session(ADMIN_SECOURS, admin_pin)
        code = normalise(pin)
        for _, r in users.iterrows():
            if code and poste_de.get(as_text(r["Nom"])) == poste and normalise(r["PIN"]) == code:
                garde().succes(cle)
                ouvrir_session(as_text(r["Nom"]), as_text(r["PIN"]))
        garde().echec(cle)
        st.error("Poste ou code incorrect.")
    st.stop()


# --------------------------------------------------------------------------- circuit de validation des DA
def _actif(v) -> bool:
    return as_text(v) == "" or is_true(v)


def chef_du_service(code_service: str) -> dict:
    """Responsable (rôle « Chef de service ») d'un service : une seule personne par service."""
    u = load("UTILISATEURS")
    m = u.loc[(u["Rôle"].map(as_text) == "Chef de service") & (u["Service"].map(as_text) == as_text(code_service))
              & u["Actif"].map(_actif).astype(bool)]
    return m.iloc[0].to_dict() if len(m) else {}


def niveau_depart(user: dict, service: str) -> dict:
    """Champs de circuit d'une DA à sa soumission. La DA d'un responsable pour son service, de la direction
    ou de l'admin passe directement au niveau DG."""
    if user["Rôle"] in ROLES_DG or (user["Rôle"] == "Chef de service" and as_text(user.get("Service")) == service):
        return {"Statut": "Validée Resp. service", "Visa Resp. service": user["Nom"],
                "Date visa Resp. service": date.today().isoformat()}
    return {"Statut": "Émise", "Visa Resp. service": "", "Date visa Resp. service": ""}


def journaliser(entrees: list[dict]):
    if entrees:
        get_store().append("VALIDATIONS_DA", [{"ID_Validation": nouvel_id(10), "Date": maintenant(), **e}
                                              for e in entrees])


def _entree(d: dict, niveau: str, decision: str, user: dict, commentaire: str = "") -> dict:
    return {"ID_DA": as_text(d["ID_DA"]), "N° DA": as_text(d["N° DA"]), "Niveau": niveau, "Décision": decision,
            "Par": user["Nom"], "Rôle": user["Rôle"], "Commentaire": commentaire}


def circuit(d: dict) -> str:
    """Étapes du circuit : demandeur → responsable du service → DG."""
    s, visa = as_text(d.get("Statut")), as_text(d.get("Visa Resp. service"))
    e1 = f"✅ Émise le {fmt_date(d.get('Date DA'))}"
    if visa:
        e2 = f"✅ Resp. service : {visa} ({fmt_date(d.get('Date visa Resp. service'))})"
    elif s == "Rejetée":
        e2 = "❌ Rejetée par le responsable de service"
    elif s == "Émise":
        chef = chef_du_service(as_text(d.get("Service Demandeur"))).get("Nom")
        e2 = f"⏳ Attente Resp. service{f' ({chef})' if chef else ''}"
    else:
        e2 = "✅ Resp. service"
    if s == "Validée DG":
        e3 = f"✅ DG : {as_text(d.get('Validé par'))} ({fmt_date(d.get('Date validation'))})"
    elif s == "Rejetée" and visa:
        e3 = "❌ Rejetée par la DG"
    elif s == "Validée Resp. service":
        e3 = "⏳ Attente DG"
    else:
        e3 = "DG"
    return f"{e1}  →  {e2}  →  {e3}"


def journal_de(id_da: str) -> pd.DataFrame:
    j = load("VALIDATIONS_DA")
    j = j.loc[j["ID_DA"].map(as_text) == as_text(id_da)].sort_values("Date")
    return j[["Date", "Niveau", "Décision", "Par", "Commentaire"]]


def emails_role(roles: set[str]) -> list[str]:
    u = load("UTILISATEURS")
    m = u.loc[u["Rôle"].map(as_text).isin(roles).astype(bool) & u["Actif"].map(_actif).astype(bool)]
    return [as_text(e) for e in m["Email"] if "@" in as_text(e)]


# --------------------------------------------------------------------------- vues communes
def detail_da(da: dict, key: str):
    st.markdown(f"**{as_text(da['N° DA'])}** · {as_text(da['Statut'])} · "
                f"{refs()['chantiers'].get(as_text(da['Chantier']), as_text(da['Chantier']))}")
    st.caption(f"{fmt_date(da['Date DA'])} · {as_text(da['Demandeur'])} · priorité {as_text(da['Priorité'])}"
               + (f" · besoin le {fmt_date(da['Date besoin'])}" if as_text(da["Date besoin"]) else ""))
    st.caption(circuit(da))
    lg = pd.DataFrame(lignes_de(da["ID_DA"]))
    if len(lg):
        lg = lg.sort_values("N° Ligne")[["N° Ligne", "Désignation", "Référence", "Code_Parc",
                                         "Colisage", "Quantité", "Usage", "Statut ligne", "N° BC",
                                         "Qté livrée", "Date livraison"]]
        lg["Qté livrée"] = lg["Qté livrée"].map(lambda x: f"{float(x):g}" if as_text(x) not in ("", "nan") else "")
        lg["Date livraison"] = lg["Date livraison"].map(fmt_date)
        st.dataframe(lg, hide_index=True, width="stretch")
    if as_text(da.get("Motif rejet")) and as_text(da.get("Statut")) == "Rejetée":
        st.warning(f"Motif du rejet : {as_text(da['Motif rejet'])}")
    j = journal_de(da["ID_DA"])
    if len(j):
        with st.expander(f"Historique des validations ({len(j)})"):
            st.dataframe(j.assign(Date=j["Date"].map(lambda v: as_text(v)[:16])), hide_index=True, width="stretch")
    st.download_button("Télécharger / imprimer la fiche PDF", pdf_de(da), file_name=pdf_name(da),
                       mime="application/pdf", key=f"pdf-{key}", type="primary")
    import dossier
    dossier.vue_dossier(contexte(st.session_state.user), as_text(da["ID_DA"]), f"dos-{key}")


def choisir_da(df: pd.DataFrame, key: str) -> dict | None:
    if not len(df):
        st.info("Aucune demande.")
        return None
    df = df.sort_values("Créé le", ascending=False)
    labels = {r["ID_DA"]: f"{as_text(r['N° DA'])} · {fmt_date(r['Date DA'])} · {as_text(r['Statut'])}"
              for _, r in df.iterrows()}
    id_da = st.selectbox("Demande", list(labels), format_func=labels.get, key=f"sel-{key}")
    return df[df["ID_DA"] == id_da].iloc[0].to_dict()


def tableau_da(df: pd.DataFrame):
    if not len(df):
        return
    lg = load("HISTORIQUE_ARTICLES")
    nb = lg.groupby(lg["ID_DA"].map(as_text)).size()
    v = df.sort_values("Créé le", ascending=False).copy()
    v["Date"] = v["Date DA"].map(fmt_date)
    v["Articles"] = v["ID_DA"].map(lambda i: int(nb.get(as_text(i), 0)))
    st.dataframe(v[["N° DA", "Date", "Chantier", "Demandeur", "Priorité", "Statut", "Articles"]],
                 hide_index=True, width="stretch")


# --------------------------------------------------------------------------- onglets
def _sans_accent(t: str) -> str:
    import unicodedata
    t = unicodedata.normalize("NFKD", str(t).upper())
    return "".join(c for c in t if not unicodedata.combining(c))


def articles_proches(saisie: str, desigs: list, n: int = 6) -> list:
    """Articles du catalogue les plus proches d'une saisie libre (mots communs + ressemblance)."""
    import difflib, re
    q = _sans_accent(saisie)
    mots = [m for m in re.findall(r"[A-Z0-9/.]+", q) if len(m) > 1 or m.isdigit()]
    if not mots:
        return []
    res = []
    for d in desigs:
        dn = _sans_accent(d)
        toks = re.findall(r"[A-Z0-9/.]+", dn)
        nums = set(re.findall(r"\d+(?:[.,]\d+)?", dn))

        def trouve(m):
            if m.isdigit():
                return m in nums
            return any(t.startswith(m) or (m.isalpha() and len(m) > 3 and t.startswith(m[:-1])) for t in toks)
        commun = sum(1 for m in mots if trouve(m))
        m0 = mots[0]  # le premier mot (l'objet) doit ouvrir la désignation
        if not commun or not (m0 in nums if m0.isdigit() else any(t.startswith(m0[:max(3, len(m0) - 1)]) for t in toks[:2])):
            continue
        score = commun / len(mots) + 0.3 * difflib.SequenceMatcher(None, q, dn).ratio()
        res.append((score, d))
    res.sort(key=lambda x: -x[0])
    return [d for _, d in res[:n]]


def _choisir(cle: str, valeur: str):
    st.session_state[cle] = valeur


def choisir_article(prefix: str, version: int = 0):
    """Désignation puis référence (liste filtrée) : choisir dans le catalogue ou saisir librement.
    `version` renouvelle le champ Référence (après un ajout, la désignation reste choisie : on peut enchaîner
    plusieurs références de la même désignation).
    Retourne (désignation, référence, code article, colisage par défaut, clés des widgets à vider)."""
    load("ARTICLES")
    cat, refs_de = _catalogue(version_donnees())  # désignation -> {référence -> code article}
    desigs = sorted(refs_de)  # uniquement l'onglet ARTICLES de la base

    # 1) Désignation : choisir dans la liste OU saisir librement
    des = st.selectbox("Désignation", desigs, index=None, accept_new_options=True, key=f"{prefix}-des",
                       placeholder="Choisir dans la liste ou taper librement")
    if des:
        des = " ".join(as_text(des).upper().split())
    connue = bool(des) and des in refs_de
    if des and not connue:  # texte libre : proposer les désignations standard les plus proches
        props = articles_proches(des, desigs)
        if props:
            st.info("Désignations de la liste standard proches de votre saisie — cliquez pour la reprendre :")
            for i, p in enumerate(props):
                st.button(p, key=f"{prefix}-prop-{i}-{p}", on_click=_choisir, args=(f"{prefix}-des", p), width="stretch")
            st.caption("Sinon, continuez : votre désignation sera enregistrée telle quelle.")

    # 2) Référence : liste filtrée sur la désignation choisie, ou saisie libre
    refs_dispo = refs_de.get(des, {}) if connue else {}
    opts = sorted(r for r in refs_dispo if r)
    cle_ref = f"{prefix}-ref-{des or ''}-{version}"
    ref = st.selectbox("Référence", opts, index=None, accept_new_options=True, key=cle_ref, disabled=not des,
                       placeholder=(f"{len(opts)} référence(s) pour cette désignation : choisir ou taper"
                                    if opts else "Taper la référence (facultatif)"))
    ref = " ".join(as_text(ref).upper().split()) if ref else ""
    if ref and opts and ref not in refs_dispo:
        proches = difflib.get_close_matches(ref, opts, n=4, cutoff=0.5)
        if proches:
            st.caption("Références proches dans la liste :")
            cols = st.columns(len(proches))
            for i, p in enumerate(proches):
                cols[i].button(p, key=f"{prefix}-pref-{i}-{p}", on_click=_choisir, args=(cle_ref, p), width="stretch")

    code = refs_dispo.get(ref) if connue else None
    if connue and not code and not ref and "" in refs_dispo:
        code = refs_dispo[""]
    a = cat.get(code, {}) if code else {}
    col_def = as_text(a.get("Colisage"))
    if not col_def and connue:  # même colisage pour toutes les références de la désignation
        cols_des = {as_text(cat[c].get("Colisage")) for c in refs_dispo.values()} - {""}
        col_def = cols_des.pop() if len(cols_des) == 1 else ""
    if des:
        st.caption(f"✓ Article du catalogue {code}" if code else "Article hors catalogue (sans code)")

    return des, ref, code, col_def, [f"{prefix}-des", cle_ref]


def _catalogage_auto(user: dict, id_da: str):
    """Articles hors catalogue de la DA : catalogués tout de suite par l'IA si elle est certaine (annulable)."""
    ctx = contexte(user)
    if not ia.disponible() or as_text(achats.params(ctx).get("Catalogage automatique IA")).lower() == "non":
        return
    ctx.refresh(False)  # les propositions viennent d'être écrites
    p = ctx.load("ARTICLES_A_CATALOGUER")
    if not ((p["ID_DA"].map(as_text) == id_da) & (p["Statut"].map(as_text) == "À analyser")).any():
        return
    try:
        with st.spinner("Catalogage des nouveaux articles par l'IA…"):
            import auto_ia
            auto_ia.cataloguer_auto(ctx, id_da)
    except Exception:  # ne bloque jamais la soumission : l'administrateur traitera
        pass


def _colisage_doc(unite_doc, defaut) -> str:
    """Unité écrite sur le document (paquets, rouleaux, m²…) -> colisage de la liste, sinon celui proposé."""
    u = as_text(unite_doc).lower().replace("²", "2").replace("³", "3").strip().rstrip("s").rstrip("x")
    if not u:
        return as_text(defaut) or "Unité"
    norm = {c: c.lower().replace("²", "2").replace("³", "3") for c in COLISAGES}
    for c, cc in norm.items():
        if u == cc:
            return c
    for c, cc in norm.items():  # « paquet » / « pq », « rouleau », « unite »…
        if len(cc) >= 3 and (u.startswith(cc[:4]) or cc.startswith(u)):
            return c
    return as_text(defaut) or "Unité"


def _raz_ia_da():
    for k in ("ia-da-lignes", "ia-da-err", "ia-da-doc"):
        st.session_state.pop(k, None)
    st.session_state["ia-da-v"] = st.session_state.get("ia-da-v", 0) + 1


def da_par_ia(parcs: list[str]):
    """Saisie des articles en langage naturel (écrit ou dicté au micro du clavier du téléphone)."""
    if not ia.disponible():
        return
    v = st.session_state.get("ia-da-v", 0)
    with st.expander("✨ Remplir avec l'IA — texte, dictée, photo ou PDF",
                     expanded=not st.session_state.lignes):
        st.caption("Écrivez ou dictez (touche 🎤 du clavier du téléphone), par exemple : « 4 pneus 315/80 R22.5 "
                   "pour le camion 12, 2 filtres à gasoil de la niveleuse et 20 sacs de ciment pour le dallage ».")
        source = st.segmented_control("Source", ["✍️ Texte ou dictée", "📷 Photo ou PDF"], default="✍️ Texte ou dictée",
                                      key="ia-da-src", label_visibility="collapsed") or "✍️ Texte ou dictée"
        if source == "✍️ Texte ou dictée":
            txt = st.text_area("Besoin", key=f"ia-da-txt-{v}", height=100, label_visibility="collapsed",
                               placeholder="Décrivez les articles, quantités, engins et usages…")
            if st.button("✨ Analyser avec l'IA", key=f"ia-da-go-{v}", type="primary", disabled=not txt.strip()):
                with st.spinner("L'IA analyse la demande et cherche les articles dans le catalogue…"):
                    try:
                        st.session_state["ia-da-lignes"] = ia.analyser_besoin(
                            txt, load("ARTICLES"), load("PARC"), COLISAGES, version_donnees())
                        st.session_state["ia-da-doc"] = False
                        if not st.session_state["ia-da-lignes"]:
                            st.warning("Aucun article reconnu : précisez la demande.")
                    except ia.IAErreur as e:
                        st.error(str(e))
                    except Exception as e:
                        st.error(f"Analyse impossible : {e}")
        else:
            st.caption("Photo d'une DA papier, d'une liste écrite à la main, d'un devis, ou un PDF : l'IA lit les "
                       "articles et les rattache au catalogue (et reprend priorité / date de besoin si indiquées).")
            f = st.file_uploader("Document", type=["jpg", "jpeg", "png", "webp", "pdf"], key=f"ia-da-f-{v}",
                                 label_visibility="collapsed")
            consigne = st.text_input("Précision (facultatif)", key=f"ia-da-cons-{v}",
                                     placeholder="Ex. : c'est pour le chantier de Bonoua, usage : entretien des camions")
            if f is not None and st.button("📷 Lire le document", key=f"ia-da-fgo-{v}", type="primary"):
                with st.spinner("Lecture du document et recherche des articles dans le catalogue…"):
                    try:
                        lignes, ent = ia.analyser_document_da(f.name, f.getvalue(), load("ARTICLES"), load("PARC"),
                                                              COLISAGES, version_donnees(), consigne.strip())
                        st.session_state["ia-da-lignes"] = lignes
                        st.session_state["ia-da-doc"] = True
                        pend = {}
                        if as_text(ent.get("priorite")) in PRIORITES:
                            pend["da-prio"] = ent["priorite"]
                        try:
                            if as_text(ent.get("date_besoin")):
                                pend["da-besoin"] = date.fromisoformat(as_text(ent["date_besoin"])[:10])
                        except ValueError:
                            pass
                        if as_text(ent.get("observations")):
                            pend["da-obs"] = as_text(ent["observations"])
                        if pend:
                            st.session_state["da_pending"] = pend
                        if not lignes:
                            st.warning("Aucun article lu : vérifiez la photo (nette, bien éclairée, à plat).")
                        else:
                            st.rerun()
                    except ia.IAErreur as e:
                        st.error(str(e))
                    except Exception as e:
                        st.error(f"Lecture impossible : {e}")
        props = st.session_state.get("ia-da-lignes")
        if not props:
            return
        cat, refs_de = _catalogue(version_donnees())
        t = pd.DataFrame(props)
        for c in ("Texte", "Code Article"):
            if c not in t:
                t[c] = ""
        t["Texte"] = [as_text(x) or " ".join(y for y in (as_text(d), as_text(r)) if y)
                      for x, d, r in zip(t["Texte"], t["Désignation"], t["Référence"])]
        fidele = st.toggle("📄 Reprendre exactement les libellés du document", key=f"ia-da-fid-{v}",
                           value=bool(st.session_state.get("ia-da-doc")),
                           help="Activé : chaque ligne garde le libellé tel qu'il est écrit sur le devis / la liste. "
                                "Cochez « Catalogue » sur une ligne pour utiliser plutôt l'article du catalogue proposé.")
        t["Proposition catalogue"] = [f"{as_text(d)} · {as_text(r)} ({as_text(c)})" if as_text(c) else "— aucun —"
                                      for d, r, c in zip(t["Désignation"], t["Référence"], t["Code Article"])]
        t.insert(0, "Inclure", True)
        if fidele:
            t["Catalogue"] = False
            for c in ("Désignation doc", "Référence doc", "Unité doc"):
                if c not in t:
                    t[c] = ""
            # nature propre (DISJONCTEUR) + reste du libellé mot pour mot (20 Ampères LE Grand) ; à défaut, le texte
            t["Référence doc"] = [as_text(r) if as_text(d) else "" for d, r in zip(t["Désignation doc"], t["Référence doc"])]
            t["Désignation doc"] = [as_text(d) or as_text(x) for d, x in zip(t["Désignation doc"], t["Texte"])]
            t["Colisage"] = [_colisage_doc(u, c) for u, c in zip(t["Unité doc"], t["Colisage"])]
            cols = ["Inclure", "Texte", "Désignation doc", "Référence doc", "Colisage", "Quantité", "Code_Parc",
                    "Usage", "Catalogue", "Proposition catalogue"]
            st.caption("Fidèle au document : la désignation donne la nature de l'article, la référence reprend le reste "
                       "du libellé mot pour mot (colonne « Sur le document » pour comparer). Corrigez si besoin. Cochez "
                       "« Catalogue » pour utiliser plutôt l'article proposé ; sinon la ligne partira au catalogage.")
        else:
            cols = ["Inclure", "Texte", "Statut", "Désignation", "Référence", "Colisage", "Quantité", "Code_Parc",
                    "Usage", "À vérifier"]
            st.caption("Articles rattachés au catalogue ; la colonne « Sur le document » montre le libellé d'origine. "
                       "🆕 = article absent du catalogue : il partira au catalogage.")
        ed = st.data_editor(t[cols], hide_index=True, width="stretch", key=f"ia-da-ed-{v}-{int(fidele)}",
                            disabled=["Statut", "À vérifier", "Texte", "Proposition catalogue"],
                            column_config={
                                "Inclure": st.column_config.CheckboxColumn("✓", width="small"),
                                "Texte": st.column_config.TextColumn("Sur le document", width="medium"),
                                "Désignation doc": st.column_config.TextColumn("Désignation", width="medium"),
                                "Référence doc": st.column_config.TextColumn("Référence", width="medium"),
                                "Catalogue": st.column_config.CheckboxColumn("Catalogue", width="small"),
                                "Colisage": st.column_config.SelectboxColumn("Colisage", options=COLISAGES),
                                "Quantité": st.column_config.NumberColumn("Quantité", min_value=0.0, format="%g"),
                                "Code_Parc": st.column_config.TextColumn("Code parc"),
                                "Usage": st.column_config.TextColumn("Usage (obligatoire)")})
        garder = ed.loc[ed["Inclure"].astype(bool)]
        a, b = st.columns(2)
        if a.button(f"➕ Ajouter {len(garder)} article(s) à la DA", key=f"ia-da-add-{v}", type="primary",
                    disabled=garder.empty):
            manque = garder.loc[(garder["Usage"].map(as_text) == "") | (garder["Quantité"].fillna(0) <= 0)]
            if len(manque):
                st.error("Quantité et usage obligatoires pour chaque article coché.")
                return
            for i, l in garder.iterrows():
                if fidele and not l["Catalogue"]:  # libellé exact du document
                    des = " ".join(as_text(l["Désignation doc"]).split())
                    ref = " ".join(as_text(l["Référence doc"]).split())
                    code = refs_de.get(des.upper(), {}).get(ref.upper(), "")
                    if code:
                        des, ref = des.upper(), ref.upper()
                else:
                    des = " ".join(as_text(t.at[i, "Désignation"] if fidele else l["Désignation"]).upper().split())
                    ref = " ".join(as_text(t.at[i, "Référence"] if fidele else l["Référence"]).upper().split())
                    code = refs_de.get(des, {}).get(ref, "")  # recalcul : l'utilisateur a pu corriger
                st.session_state.lignes.append({
                    "Code Article": code, "Désignation": des, "Référence": ref,
                    "Code_Parc": as_text(l["Code_Parc"]).upper(), "Colisage": as_text(l["Colisage"]) or "Unité",
                    "Quantité": float(l["Quantité"]), "Usage": as_text(l["Usage"])})
            _raz_ia_da()
            st.rerun()
        b.button("Effacer", key=f"ia-da-raz-{v}", on_click=_raz_ia_da)


@st.cache_resource(show_spinner=False, max_entries=2)
def _catalogue(version: int) -> tuple[dict, dict]:
    """Catalogue calculé une fois par version des données (et non à chaque clic)."""
    art = load("ARTICLES")
    recs = [r for r in art.to_dict("records") if as_text(r.get("Code Article"))]
    cat = {as_text(r["Code Article"]): r for r in recs}
    refs_de = {}
    for c, x in cat.items():
        refs_de.setdefault(as_text(x["Désignation"]), {}).setdefault(as_text(x.get("Référence")), c)
    return cat, refs_de


def catalogue() -> dict:
    load("ARTICLES")  # s'assure que les données sont chargées
    return _catalogue(version_donnees())[0]


def onglet_nouvelle(user: dict):
    if "lignes" not in st.session_state:
        st.session_state.lignes = []
    last = st.session_state.get("last_submit")
    if last:
        st.success(f"DA **{last['numero']}** enregistrée et {last.get('suite', 'transmise pour validation')}.")
        st.download_button("Télécharger / imprimer la fiche PDF", last["pdf"], file_name=f"{last['numero']}.pdf",
                           mime="application/pdf", type="primary", key="pdf-last")
        if last.get("mail"):
            st.caption(last["mail"])
        if st.button("Saisir une nouvelle DA"):
            del st.session_state["last_submit"]
            st.rerun()
        return

    ch = load("CHANTIERS")
    ch = ch.loc[ch["Actif"].map(lambda v: as_text(v) == "" or is_true(v)).astype(bool)]
    codes_ch = ch["Code Chantier"].map(as_text).tolist()
    sv = load("SERVICES")
    codes_sv = sv["Code Service"].map(as_text).tolist()
    r = refs()
    if not codes_ch or not codes_sv:
        st.error("Renseignez les onglets CHANTIERS et SERVICES de la base.")
        return

    ctx = contexte(user)
    assistant_da.appliquer_entete()  # valeurs dictées à l'assistant (avant la création des champs)
    assistant_da.panneau(ctx, user, nb_a_valider(user) if user["Rôle"] in ROLES_VALID_DA else 0)
    for k, v in (("da-ch", user["Chantier"] if user["Chantier"] in codes_ch else codes_ch[0]),
                 ("da-prio", PRIORITES[0]), ("da-besoin", None), ("da-obs", "")):
        if k not in st.session_state or (k == "da-ch" and st.session_state[k] not in codes_ch):
            st.session_state[k] = v

    st.markdown("##### En-tête")
    c1, c2 = st.columns(2)
    chantier = c1.selectbox("Chantier", codes_ch, format_func=lambda c: r["chantiers"].get(c, c), key="da-ch")
    date_da = c2.date_input("Date", value=date.today(), format="DD/MM/YYYY")
    c1, c2 = st.columns(2)
    service = c1.selectbox("Service demandeur", codes_sv, format_func=lambda c: r["services"].get(c, c),
                           index=codes_sv.index(user["Service"]) if user["Service"] in codes_sv else 0)
    resp_defaut = (as_text(chef_du_service(service).get("Nom"))
                   or as_text(sv.loc[sv["Code Service"].map(as_text) == service].iloc[0]["Responsable"]))
    responsable = c2.text_input("Responsable du service", value=resp_defaut, key=f"resp-{service}")
    c1, c2 = st.columns(2)
    demandeur = c1.text_input("Demandeur", value=user["Nom"])
    code_da = c2.text_input("Code DA (facultatif)")
    c1, c2 = st.columns(2)
    priorite = c1.selectbox("Priorité", PRIORITES, key="da-prio")
    date_besoin = c2.date_input("Date de besoin", format="DD/MM/YYYY", key="da-besoin")
    observations = st.text_area("Observations", height=68, key="da-obs")

    st.markdown("##### Articles")
    hist = load("HISTORIQUE_ARTICLES")
    parcs = sorted((set(load("PARC")["Code_Parc"].map(as_text)) | set(hist["Code_Parc"].map(as_text))) - {""})
    da_par_ia(parcs)
    nb = len(st.session_state.lignes)
    des, ref, code, col_def, (_, cle_ref) = choisir_article("art", nb)
    if nb and des:
        st.caption("Astuce : la désignation reste choisie après un ajout — choisissez une autre référence pour "
                   "ajouter un second article de la même désignation, ou changez de désignation.")

    with st.form(f"ajout-{des or ''}-{ref}-{nb}", clear_on_submit=True):
        a1, a2 = st.columns(2)
        colis = a1.selectbox("Colisage", COLISAGES, index=COLISAGES.index(col_def) if col_def in COLISAGES else 0)
        qte = a2.number_input("Quantité", min_value=0.0, step=1.0, value=1.0)
        parc = a1.selectbox("Code parc (engin)", parcs, index=None, accept_new_options=True,
                            placeholder="Facultatif")
        usage = a2.text_input("Usage")
        if st.form_submit_button("Ajouter l'article", type="primary"):
            if not des or qte <= 0 or not usage.strip():
                st.error("Désignation, quantité et usage sont obligatoires.")
            else:
                st.session_state.lignes.append({
                    "Code Article": code or "", "Désignation": des, "Référence": ref,
                    "Code_Parc": (parc or "").strip(), "Colisage": colis,
                    "Quantité": qte, "Usage": usage.strip()})
                st.session_state.pop(cle_ref, None)  # la désignation reste : enchaîner une autre référence
                st.rerun()

    for i, l in enumerate(st.session_state.lignes):
        a, b = st.columns([6, 1])
        rf = f" · {l['Référence']}" if l.get("Référence") else ""
        a.markdown(f"**{i + 1}. {l['Désignation']}{rf}** — {l['Quantité']:g} {l['Colisage']}"
                   f"{' · ' + l['Code_Parc'] if l['Code_Parc'] else ''}  \n{l['Usage']}")
        if b.button("✕", key=f"del-{i}", help="Retirer"):
            st.session_state.lignes.pop(i)
            st.rerun()

    for c in assistant_da.conseils(ctx, st.session_state.lignes, priorite, date_besoin):
        st.info(f"💡 {c}")
    n = len(st.session_state.lignes)
    if st.button(f"Soumettre la DA ({n} article{'s' if n > 1 else ''})", type="primary", disabled=n == 0):
        if not demandeur.strip() or not responsable.strip():
            st.error("Demandeur et responsable sont obligatoires.")
            return
        if date_besoin and date_besoin < date_da:
            st.error("La date de besoin est antérieure à la date de la DA.")
            return
        entete = {"Date DA": date_da, "Chantier": chantier, "Code DA": code_da.strip(),
                  "Service Demandeur": service, "Demandeur": demandeur.strip(),
                  "Responsable": responsable.strip(), "Priorité": priorite,
                  "Date besoin": date_besoin, "Observations": observations.strip()}
        depart = niveau_depart(user, service)
        with st.spinner("Enregistrement…"):
            da_row, rows = build_da(get_store(), entete, st.session_state.lignes, user, depart)
            j = [_entree(da_row, "Demandeur", "Émise", user)]
            if depart["Statut"] != "Émise":
                j.append(_entree(da_row, "Resp. service", "Visa automatique (DA du responsable / de la direction)",
                                 user))
            journaliser(j)
            catalogage.enregistrer(contexte(user), da_row, rows, user)
            _catalogage_auto(user, as_text(da_row.get("ID_DA")))
            refresh(statique=False)
            pdf = build_pdf(da_row, rows, r)
        chef = chef_du_service(service)
        suite = ("transmise à la Direction générale pour validation" if depart["Statut"] != "Émise" else
                 f"transmise à {chef['Nom']} (responsable du service) pour validation" if chef else
                 "transmise pour validation (aucun responsable de service désigné : la DG valide directement)")
        mail = ""
        smtp = _secret("smtp")
        if smtp:
            dest = ([as_text(chef.get("Email"))] if depart["Statut"] == "Émise" and chef else
                    emails_role(ROLES_DG) if depart["Statut"] != "Émise" else
                    [as_text(sv[sv["Code Service"].map(as_text) == service].iloc[0]["Email responsable"])])
            dest += [user.get("Email", "")]
            try:
                if send_email(smtp, dest, f"DA {da_row['N° DA']} – {r['chantiers'].get(chantier, chantier)} – à valider",
                              f"Nouvelle demande d'achat {da_row['N° DA']} de {demandeur} ({n} articles, "
                              f"priorité {priorite}).\nFiche en pièce jointe.", pdf, f"{da_row['N° DA']}.pdf"):
                    mail = "Email envoyé au validateur."
            except Exception as exc:  # l'envoi ne doit jamais bloquer la DA
                mail = f"DA enregistrée, mais l'email n'a pas pu partir ({exc})."
        st.session_state.lignes = []
        for k in ("da-prio", "da-besoin", "da-obs", "conv", "conv_fini", "conv-vu", "ada-mode"):
            st.session_state.pop(k, None)
        st.session_state.last_submit = {"numero": da_row["N° DA"], "pdf": pdf, "mail": mail, "suite": suite}
        st.rerun()


def onglet_mes_da(user: dict):
    if st.session_state.get("mes_msg"):
        st.success(st.session_state.pop("mes_msg"))
    da = load("DA")
    mine = da[(da["Email Demandeur"].map(as_text) == user["Email"]) & (user["Email"] != "")
              | (da["Demandeur"].map(as_text) == user["Nom"])]
    rejetees = mine.loc[mine["Statut"] == "Rejetée"]
    if len(rejetees):
        st.warning(f"{len(rejetees)} DA rejetée(s) à corriger : {', '.join(rejetees['N° DA'].map(as_text))}. "
                   "Choisissez-la ci-dessous, corrigez puis renvoyez-la.")
    tableau_da(mine)
    st.caption("Une DA reste modifiable tant qu'elle n'est pas validée par la Direction générale.")
    d = choisir_da(mine, "mes")
    if d:
        detail_da(d, "mes")
        if as_text(d["Statut"]) in MODIFIABLES:
            modifier_da(d, user)


MODIFIABLES = ("Émise", "Validée Resp. service", "Rejetée")  # la DA n'est pas encore validée par la DG


def modifier_da(d: dict, user: dict, admin: bool = False, suffixe: str = ""):
    """Le demandeur modifie sa DA tant que la DG ne l'a pas validée : quantités, colisages, codes parc, usages,
    articles retirés ou ajoutés, en-tête. Une DA déjà visée repart chez le responsable de service.
    admin=True : l'administrateur modifie n'importe quelle DA, son statut ne change pas."""
    id_da, statut = as_text(d["ID_DA"]), as_text(d["Statut"])
    cle = id_da + suffixe  # clés des widgets : la même DA peut être ouverte dans deux onglets
    rejet = statut == "Rejetée" and not admin
    titre = ("✏️ Modifier la DA (admin)" if admin else
             "✏️ Corriger et renvoyer la DA" if rejet else "✏️ Modifier la DA")
    with st.expander(titre, expanded=rejet):
        if admin:
            st.caption("Modification administrateur : tout est modifiable (en-tête, désignations, références, "
                       "quantités…) ; le statut, les visas et le N° DA ne changent pas. Les articles déjà commandés "
                       "(BC) ne peuvent pas être retirés, et leur BC garde son propre libellé.")
        elif statut == "Validée Resp. service" and not (user["Rôle"] in ROLES_DG or (
                user["Rôle"] == "Chef de service" and as_text(user.get("Service")) == as_text(d["Service Demandeur"]))):
            st.warning("Cette DA a déjà le visa du responsable de service : après modification, elle repart chez lui "
                       "pour un nouveau visa.")
        entete = {}
        if admin:
            rf = refs()
            a1, a2, a3 = st.columns(3)
            chs = list(rf["chantiers"]) or [as_text(d["Chantier"])]
            if as_text(d["Chantier"]) not in chs:
                chs.append(as_text(d["Chantier"]))
            entete["Chantier"] = a1.selectbox("Chantier", chs, index=chs.index(as_text(d["Chantier"])),
                                              format_func=lambda c: rf["chantiers"].get(c, c), key=f"mod-ch-{cle}")
            svs = list(rf["services"])
            if as_text(d["Service Demandeur"]) not in svs:
                svs.append(as_text(d["Service Demandeur"]))
            entete["Service Demandeur"] = a2.selectbox("Service demandeur", svs,
                                                       index=svs.index(as_text(d["Service Demandeur"])),
                                                       format_func=lambda c: rf["services"].get(c, c),
                                                       key=f"mod-sv-{cle}")
            dda = a3.date_input("Date de la DA", value=to_date(d.get("Date DA")), format="DD/MM/YYYY",
                                key=f"mod-dda-{cle}")
            entete["Date DA"] = dda.isoformat() if dda else as_text(d.get("Date DA"))
            b1, b2, b3 = st.columns(3)
            entete["Demandeur"] = b1.text_input("Demandeur", value=as_text(d.get("Demandeur")), key=f"mod-dem-{cle}")
            entete["Responsable"] = b2.text_input("Responsable", value=as_text(d.get("Responsable")),
                                                  key=f"mod-resp-{cle}")
            entete["Code DA"] = b3.text_input("Code DA", value=as_text(d.get("Code DA")), key=f"mod-cda-{cle}")
        c1, c2 = st.columns(2)
        prio = c1.selectbox("Priorité", PRIORITES, key=f"mod-prio-{cle}",
                            index=PRIORITES.index(as_text(d["Priorité"])) if as_text(d["Priorité"]) in PRIORITES else 0)
        besoin = c2.date_input("Date de besoin", value=to_date(d.get("Date besoin")), format="DD/MM/YYYY",
                               key=f"mod-bes-{cle}")
        lg = pd.DataFrame(lignes_de(id_da))
        lg = lg.loc[lg["Statut ligne"].map(as_text) != "Annulée"].sort_values("N° Ligne")
        cols = ["ID_Ligne", "N° Ligne", "Désignation", "Référence", "Code_Parc", "Colisage", "Quantité", "Usage"]
        v = lg[cols].copy()
        for c in cols:
            v[c] = v[c].map(as_text) if c != "Quantité" else v[c].map(lambda x: float(_num(x) or 0))
        st.caption("Modifiez directement le tableau. Pour retirer un article : cochez la ligne puis 🗑 (ou quantité 0).")
        ed = st.data_editor(v, hide_index=True, width="stretch", key=f"mod-ed-{cle}", num_rows="delete",
                            disabled=["N° Ligne"] if admin else ["N° Ligne", "Désignation", "Référence"],
                            column_config={"ID_Ligne": None,
                                           "Colisage": st.column_config.SelectboxColumn(
                                               options=sorted(set(COLISAGES) | set(v["Colisage"]) - {""})),
                                           "Quantité": st.column_config.NumberColumn(min_value=0.0, format="%.2f")})

        # ---- articles ajoutés (catalogue ou saisie libre)
        cle_aj = f"mod-aj-{cle}"
        st.session_state.setdefault(cle_aj, [])
        st.markdown("###### Ajouter un article")
        nb_aj = len(st.session_state[cle_aj])
        des, ref, code, col_def, cles = choisir_article(f"modart-{cle}", nb_aj)
        with st.form(f"mod-form-{cle}-{des or ''}-{ref}-{nb_aj}", clear_on_submit=True):
            a1, a2, a3, a4 = st.columns(4)
            colis = a1.selectbox("Colisage", COLISAGES, index=COLISAGES.index(col_def) if col_def in COLISAGES else 0)
            qte = a2.number_input("Quantité", min_value=0.0, step=1.0, value=1.0)
            parc = a3.text_input("Code parc", placeholder="Facultatif")
            usage = a4.text_input("Usage")
            if st.form_submit_button("Ajouter à la DA"):
                if not des or qte <= 0 or not usage.strip():
                    st.error("Désignation, quantité et usage sont obligatoires.")
                else:
                    st.session_state[cle_aj].append({"Code Article": code or "", "Désignation": des, "Référence": ref,
                                                     "Code_Parc": parc.strip().upper(), "Colisage": colis,
                                                     "Quantité": qte, "Usage": usage.strip()})
                    st.session_state.pop(cles[1], None)  # la désignation reste choisie
                    st.rerun()
        for i, l in enumerate(st.session_state[cle_aj]):
            a, b = st.columns([6, 1])
            a.write(f"➕ {l['Désignation']} {l['Référence']} — {l['Quantité']:g} {l['Colisage']} · {l['Usage']}")
            if b.button("✕", key=f"mod-del-{cle}-{i}"):
                st.session_state[cle_aj].pop(i)
                st.rerun()

        obs = st.text_area("Observations" + (" (réponse au motif du rejet)" if rejet else ""),
                           value=as_text(d.get("Observations")), key=f"mod-obs-{cle}", height=68)
        libelle = "Renvoyer la DA corrigée" if rejet else "Enregistrer les modifications de la DA"
        if not st.button(libelle, type="primary", key=f"mod-go-{cle}"):
            return
        ajouts = st.session_state[cle_aj]
        gardees = ed.loc[ed["Quantité"].map(lambda x: float(x or 0)) > 0]
        if not len(gardees) and not ajouts:
            st.error("Il faut au moins un article avec une quantité.")
            return
        store = get_store()
        frais = store.read("DA")  # lecture directe : la DG a peut-être validé entre-temps
        frais = frais.loc[frais["ID_DA"].map(as_text) == id_da]
        if not admin and len(frais) and ANCIENS_STATUTS.get(as_text(frais.iloc[0]["Statut"]),
                                              as_text(frais.iloc[0]["Statut"])) not in MODIFIABLES:
            st.error("Cette DA vient d'être validée par la Direction générale : elle n'est plus modifiable.")
            refresh(statique=False)
            return
        avant = v.set_index("ID_Ligne")
        apres = ed.dropna(subset=["ID_Ligne"]).set_index("ID_Ligne")
        bc_de = dict(zip(lg["ID_Ligne"].map(as_text), lg["N° BC"].map(as_text)))
        retire_cmd = [as_text(avant.at[i, "Désignation"]) for i in avant.index if bc_de.get(i) and (
            i not in apres.index or float(apres.at[i, "Quantité"] or 0) <= 0)]
        if retire_cmd:
            st.error("Article déjà commandé, impossible de le retirer (annulez d'abord le BC) : "
                     + ", ".join(retire_cmd))
            return
        changes, relibelle = {}, False
        for idl in avant.index:
            if idl not in apres.index:  # ligne supprimée
                changes[idl] = {"Statut ligne": "Annulée"}
                continue
            diff = {c: as_text(apres.at[idl, c]) for c in ("Code_Parc", "Colisage", "Usage")
                    if as_text(apres.at[idl, c]) != as_text(avant.at[idl, c])}
            if admin:  # l'administrateur corrige aussi les libellés ; l'article est recatalogué
                for c in ("Désignation", "Référence"):
                    nv = " ".join(as_text(apres.at[idl, c]).split())
                    nv = nv.upper() if c == "Désignation" else nv
                    if nv != as_text(avant.at[idl, c]):
                        diff[c] = nv
                if "Désignation" in diff or "Référence" in diff:
                    diff["Code Article"] = ""
                    relibelle = True
            q = float(apres.at[idl, "Quantité"] or 0)
            if q != float(avant.at[idl, "Quantité"] or 0):
                diff["Quantité"] = q
            if q <= 0:
                diff["Statut ligne"] = "Annulée"
            if diff:
                changes[idl] = diff
        depart = {} if admin else niveau_depart(user, as_text(d["Service Demandeur"]))
        if changes:
            store.update_many("HISTORIQUE_ARTICLES", "ID_Ligne", changes)
        nouvelles = []
        if ajouts:
            premier = int(max([_num(x) or 0 for x in lg["N° Ligne"]] or [0])) + 1
            nouvelles = lignes_da(d, ajouts, premier)
            store.append("HISTORIQUE_ARTICLES", nouvelles)
            catalogage.enregistrer(contexte(user), d, nouvelles, user)
            _catalogage_auto(user, as_text(d.get("ID_DA")))
        circuit_maj = {} if admin else {**depart, "Motif rejet": "", "Validé par": "", "Date validation": ""}
        store.update("DA", "ID_DA", id_da, {**circuit_maj, **{k: v.strip() for k, v in entete.items()},
                                            "Observations": obs.strip(), "Priorité": prio,
                                            "Date besoin": besoin.isoformat() if besoin else ""})
        if admin:  # copies de l'en-tête sur les lignes de la DA
            maj_l = {col: entete[k] for k, col in (("Chantier", "DA_Chantier"), ("Service Demandeur", "DA_Service"),
                                                    ("Date DA", "DA_Date"))
                     if entete.get(k) and entete[k] != as_text(d.get(k))}
            if maj_l:
                store.update_many("HISTORIQUE_ARTICLES", "ID_Ligne", {as_text(i): maj_l for i in lg["ID_Ligne"]})
        if relibelle:
            try:
                _catalogage_auto(user, id_da)
            except Exception:
                pass
        journaliser([_entree(d, "Admin" if admin else "Demandeur",
                             "Renvoyée après correction" if rejet else "Modifiée", user,
                             f"{len(changes)} ligne(s) modifiée(s), {len(nouvelles)} ajoutée(s)")])
        st.session_state.pop(cle_aj, None)
        refresh(statique=False)
        st.session_state["adm_msg" if admin else "mes_msg"] = (
            f"DA {as_text(d['N° DA'])} " + ("corrigée et renvoyée pour validation." if rejet else "modifiée.")
            + (" Elle repart chez le responsable de service pour visa."
               if not admin and statut != "Émise" and depart["Statut"] == "Émise" else ""))
        st.rerun()


def _notifier(d: dict, statut: str):
    smtp = _secret("smtp")
    if not smtp or not as_text(d.get("Email Demandeur")):
        return
    da = load("DA")
    d2 = da[da["ID_DA"] == d["ID_DA"]].iloc[0].to_dict()
    try:
        send_email(smtp, [as_text(d2["Email Demandeur"])], f"DA {as_text(d2['N° DA'])} – {statut}",
                   f"Votre demande {as_text(d2['N° DA'])} est {statut.lower()}."
                   + (f"\nMotif : {as_text(d2['Motif rejet'])}" if statut == "Rejetée" else ""),
                   pdf_de(d2), pdf_name(d2))
    except Exception:
        pass


def _num(v):
    t = as_text(v).replace(" ", "").replace(",", ".")
    try:
        return float(t) if t else ""
    except ValueError:
        return as_text(v)


# --------------------------------------------------------------------------- validation hiérarchique des DA
def da_a_valider(user: dict, avec_emises: bool = False) -> pd.DataFrame:
    """Chef de service : DA émises de son service. DG / admin : DA visées par le responsable de service
    (et, sur demande, celles encore en attente du responsable, pour le suppléer)."""
    da = load("DA")
    if user["Rôle"] in ROLES_DG:
        return da.loc[da["Statut"].isin(DA_EN_ATTENTE if avec_emises else ["Validée Resp. service"])]
    if user["Rôle"] == "Chef de service":
        return da.loc[(da["Statut"] == "Émise") & (da["Service Demandeur"].map(as_text) == as_text(user.get("Service")))]
    return da.iloc[0:0]


def decider_da(user: dict, ids: list[str], valider: bool, motif: str = "") -> tuple[list[str], list[str]]:
    """Valide (niveau suivant) ou rejette des DA. Retourne (N° traités, N° ignorés)."""
    da = load("DA")
    da = da.set_index(da["ID_DA"].map(as_text))
    dg, auj = user["Rôle"] in ROLES_DG, date.today().isoformat()
    changes, entrees, faits, ignores, vers_dg, finales = {}, [], [], [], [], []
    for i in ids:
        if i not in da.index:
            continue
        d = da.loc[i].to_dict()
        s, num = as_text(d["Statut"]), as_text(d["N° DA"])
        ok = (s == "Validée Resp. service" and dg) or (s == "Émise" and (dg or (
            user["Rôle"] == "Chef de service" and as_text(d["Service Demandeur"]) == as_text(user.get("Service")))))
        if not ok:
            ignores.append(num)
            continue
        niveau = "DG" if dg else "Resp. service"
        if not valider:
            changes[i] = {"Statut": "Rejetée", "Motif rejet": f"{motif} — {user['Nom']} ({niveau})"}
            entrees.append(_entree(d, niveau, "Rejetée", user, motif))
            finales.append((i, "Rejetée"))
        elif dg:
            ch = {"Statut": "Validée DG", "Validé par": user["Nom"], "Date validation": auj, "Motif rejet": "",
                  **_trace_validation(user)}
            if s == "Émise":  # la DG supplée le responsable de service
                ch.update({"Visa Resp. service": f"{user['Nom']} (suppléance)", "Date visa Resp. service": auj})
            changes[i] = ch
            entrees.append(_entree(d, "DG", "Validée" + (" (niveau service suppléé)" if s == "Émise" else ""), user))
            finales.append((i, "Validée DG"))
        else:
            changes[i] = {"Statut": "Validée Resp. service", "Visa Resp. service": user["Nom"],
                          "Date visa Resp. service": auj, "Motif rejet": ""}
            entrees.append(_entree(d, "Resp. service", "Validée", user))
            vers_dg.append(num)
        faits.append(num)
    if changes:
        get_store().update_many("DA", "ID_DA", changes)
        journaliser(entrees)
        refresh(statique=False)
        for i, statut in finales:
            _notifier({"ID_DA": i, "Email Demandeur": "x"}, statut)
        smtp = _secret("smtp")
        if smtp and vers_dg:
            try:
                send_email(smtp, emails_role(ROLES_DG), f"{len(vers_dg)} DA à valider (DG)",
                           f"Visées par {user['Nom']} (responsable de service) : {', '.join(vers_dg)}.")
            except Exception:
                pass
    return faits, ignores


def _lignes_prix(d: dict) -> tuple[pd.DataFrame, float, int]:
    ctx = contexte({})
    lg = [l for l in lignes_de(d["ID_DA"]) if as_text(l.get("Statut ligne")) != "Annulée"]
    rows, tot, n = [], 0.0, 0
    for l in sorted(lg, key=lambda x: _num(x.get("N° Ligne")) or 0):
        s = prix.ref_article(ctx, l.get("Code Article"), l.get("Désignation"), l.get("Référence"), l.get("Colisage"))
        q = float(_num(l.get("Quantité")) or 0)
        est = q * s["moyenne"] if s and not s.get("autre_unite") else None
        if est is not None:
            tot, n = tot + est, n + 1
        rows.append({"Désignation": as_text(l["Désignation"]), "Référence": as_text(l.get("Référence")),
                     "Qté": q, "Colisage": as_text(l.get("Colisage")), "Usage": as_text(l.get("Usage")),
                     **prix.colonnes_ref(s), "Montant estimé": fmt_montant(est) if est is not None else ""})
    return pd.DataFrame(rows), tot, n


def estimation_da(id_da: str) -> str:
    _, tot, n = _lignes_prix({"ID_DA": id_da})
    return fmt_montant(tot) if n else ""


def onglet_validation_da(user: dict):
    dg = user["Rôle"] in ROLES_DG
    if st.session_state.get("vda_msg"):
        st.success(st.session_state.pop("vda_msg"))
    if user["Rôle"] == "Chef de service" and not as_text(user.get("Service")):
        st.warning("Votre service n'est pas renseigné : demandez à l'administrateur de compléter votre poste.")
        return
    avec_emises = False
    if dg:
        st.caption("DA visées par le responsable de leur service, à valider par la Direction générale. "
                   "Une DA validée part aux achats.")
        avec_emises = st.toggle("Afficher aussi les DA en attente du responsable de service (pour le suppléer)",
                                key="vda-emises")
    else:
        st.caption(f"DA du service {refs()['services'].get(user.get('Service'), user.get('Service'))} à viser. "
                   "Une fois visée, la DA passe à la Direction générale.")
    f = da_a_valider(user, avec_emises).sort_values("Créé le")
    k1, k2 = st.columns(2)
    k1.metric("DA à valider", len(f))
    k2.metric("Dont urgentes / critiques", int(f["Priorité"].isin(["Urgente", "Critique"]).sum()))
    if not len(f):
        st.info("Aucune DA en attente de votre validation.")
        return
    r = refs()
    lg = load("HISTORIQUE_ARTICLES")
    nb = lg.loc[lg["Statut ligne"].map(as_text) != "Annulée"].groupby(lg["ID_DA"].map(as_text)).size()
    v = pd.DataFrame({
        "ID_DA": f["ID_DA"].map(as_text), "N° DA": f["N° DA"].map(as_text), "Date": f["Date DA"].map(fmt_date),
        "Chantier": f["Chantier"].map(lambda c: r["chantiers"].get(as_text(c), as_text(c))),
        "Service": f["Service Demandeur"].map(lambda c: r["services"].get(as_text(c), as_text(c))),
        "Demandeur": f["Demandeur"].map(as_text), "Priorité": f["Priorité"].map(as_text),
        "Besoin le": f["Date besoin"].map(fmt_date),
        "Articles": f["ID_DA"].map(lambda i: int(nb.get(as_text(i), 0))),
        "Montant estimé (F)": f["ID_DA"].map(estimation_da),
        "Étape": f["Statut"].map(lambda s: "Attente DG" if s == "Validée Resp. service" else "Attente Resp. service"),
        "Visa service": f["Visa Resp. service"].map(as_text)})
    version = st.session_state.get("vda_v", 0)
    tout = st.checkbox("Tout cocher", key=f"vda-tout-{version}")
    v.insert(0, "✓", tout)
    ed = st.data_editor(v, hide_index=True, width="stretch", key=f"vda-ed-{version}-{tout}",
                        disabled=[c for c in v.columns if c != "✓"],
                        column_config={"ID_DA": None, "✓": st.column_config.CheckboxColumn("✓", width="small")})
    coches = ed.loc[ed["✓"].astype(bool)]
    n = len(coches)
    b1, b2, b3 = st.columns([1, 1, 2])
    motif = b3.text_input("Motif", key=f"vda-motif-{version}", label_visibility="collapsed",
                          placeholder="Motif du rejet (obligatoire pour rejeter)")
    with b3:
        ia.bouton_reformuler(f"vda-motif-{version}", "Rejet de demandes d'achat par le validateur", "vda-ref")
    go = None
    if b1.button(f"✅ Valider ({n})", disabled=n == 0, type="primary", width="stretch", key="vda-ok"):
        go = True
    if b2.button(f"❌ Rejeter ({n})", disabled=n == 0, width="stretch", key="vda-ko"):
        go = False
    if go is False and not motif.strip():
        st.error("Indiquez le motif du rejet avant de cliquer sur Rejeter.")
        go = None
    if go is not None:
        faits, ignores = decider_da(user, coches["ID_DA"].tolist(), go, motif.strip())
        st.session_state.vda_v = version + 1
        st.session_state.vda_msg = (f"{len(faits)} DA {'validée(s)' if go else 'rejetée(s)'} : {', '.join(faits)}."
                                    + (f" Ignorées (déjà traitées) : {', '.join(ignores)}." if ignores else ""))
        st.rerun()

    st.markdown("##### Examiner une DA")
    lab = dict(zip(v["ID_DA"], v["N° DA"] + " · " + v["Demandeur"] + " · " + v["Étape"]))
    id_da = st.selectbox("DA", list(lab), format_func=lab.get, index=None, key=f"vda-sel-{version}",
                         placeholder="Choisir une DA pour voir ses articles et leurs prix passés")
    if not id_da:
        return
    d = f.loc[f["ID_DA"].map(as_text) == id_da].iloc[0].to_dict()
    st.caption(circuit(d))
    if as_text(d.get("Observations")):
        st.caption(f"Observations : {as_text(d['Observations'])}")
    t, tot, nl = _lignes_prix(d)
    st.dataframe(t, hide_index=True, width="stretch")
    st.metric("Montant estimé de la DA (prix moyens)", f"{fmt_montant(tot)} F" if nl else "–",
              help=f"{nl}/{len(t)} article(s) avec un prix de référence ; les autres ne sont pas estimés.")
    c1, c2, c3 = st.columns([1, 1, 2])
    m1 = c3.text_input("Motif", key=f"vda-m1-{id_da}", label_visibility="collapsed",
                       placeholder="Motif du rejet (obligatoire pour rejeter)")
    with c3:
        ia.bouton_reformuler(f"vda-m1-{id_da}", f"Rejet de la DA {as_text(d.get('N° DA'))} ; articles : "
                             + ", ".join(t["Désignation"].head(15).map(as_text)) if len(t) else "", f"vda-ref1-{id_da}")
    un = None
    if c1.button("✅ Valider cette DA", type="primary", width="stretch", key=f"vda-ok1-{id_da}"):
        un = True
    if c2.button("❌ Rejeter cette DA", width="stretch", key=f"vda-ko1-{id_da}"):
        un = False
    if un is False and not m1.strip():
        st.error("Indiquez le motif du rejet.")
        un = None
    if un is not None:
        faits, _ = decider_da(user, [id_da], un, m1.strip())
        st.session_state.vda_v = version + 1
        st.session_state.vda_msg = f"DA {', '.join(faits) or '—'} {'validée' if un else 'rejetée'}."
        st.rerun()
    st.download_button("Télécharger la fiche PDF", pdf_de(d), file_name=pdf_name(d), mime="application/pdf",
                       key=f"vda-pdf-{id_da}")


def nb_a_valider(user: dict) -> int:
    return len(da_a_valider(user))


def _trace_validation(user: dict) -> dict:
    from logic import trace_validation
    return trace_validation(user)


def _champs_statut(da: pd.DataFrame, id_da: str, statut: str, user: dict, motif: str = "") -> dict:
    """Champs à écrire quand l'admin force un statut (tableau de l'Administration)."""
    d = da.loc[da["ID_DA"] == id_da].iloc[0]
    auj = date.today().isoformat()
    visa = as_text(d.get("Visa Resp. service"))
    if statut == "Émise":
        return {"Statut": "Émise", "Visa Resp. service": "", "Date visa Resp. service": "", "Validé par": "",
                "Date validation": "", "Motif rejet": "", "Validé le": "", "Validé en tant que": ""}
    if statut == "Validée Resp. service":
        return {"Statut": statut, "Visa Resp. service": visa or f"{user['Nom']} (suppléance)",
                "Date visa Resp. service": as_text(d.get("Date visa Resp. service")) or auj,
                "Validé par": "", "Date validation": "", "Motif rejet": "", "Validé le": "", "Validé en tant que": ""}
    if statut == "Validée DG":
        return {"Statut": statut, "Visa Resp. service": visa or f"{user['Nom']} (suppléance)",
                "Date visa Resp. service": as_text(d.get("Date visa Resp. service")) or auj,
                "Validé par": user["Nom"], "Date validation": auj, "Motif rejet": "", **_trace_validation(user)}
    return {"Statut": "Rejetée", "Motif rejet": f"{motif} — {user['Nom']} (admin)" if motif else ""}


def onglet_admin_da(user: dict):
    da = load("DA")
    if not len(da):
        st.info("Aucune DA pour le moment.")
        return
    k0, k1, k2, k3 = st.columns(4)
    k0.metric("Attente Resp. service", int((da["Statut"] == "Émise").sum()))
    k1.metric("Attente DG", int((da["Statut"] == "Validée Resp. service").sum()))
    k2.metric("Validées DG", int((da["Statut"] == "Validée DG").sum()))
    k3.metric("Rejetées", int((da["Statut"] == "Rejetée").sum()))
    r = refs()
    c1, c2, c3 = st.columns([2, 2, 2])
    chs = c1.multiselect("Chantier", sorted(da["Chantier"].map(as_text).unique()),
                         format_func=lambda c: r["chantiers"].get(c, c), placeholder="Tous")
    sts = c2.multiselect("Statut", STATUTS, placeholder="Tous")
    cherche = c3.text_input("N° DA ou demandeur")
    f = da
    if chs:
        f = f.loc[f["Chantier"].map(as_text).isin(chs)]
    if sts:
        f = f.loc[f["Statut"].isin(sts)]
    if cherche.strip():
        q = cherche.strip().lower()
        f = f.loc[(f["N° DA"].map(as_text).str.lower().str.contains(q, regex=False))
                  | (f["Demandeur"].map(as_text).str.lower().str.contains(q, regex=False))]
    f = f.sort_values("Créé le", ascending=False)

    st.markdown("##### Demandes d'achat")
    st.caption("Cochez une ou plusieurs DA puis cliquez sur Valider ou Rejeter. "
               "Vous pouvez aussi modifier directement le tableau (statut, priorité…) puis enregistrer.")
    cols = ["ID_DA", "N° DA", "Date DA", "Chantier", "Demandeur", "Statut", "Priorité",
            "Responsable", "Code DA", "Motif rejet", "Visa Resp. service", "Validé par", "Date validation"]
    vue = f[cols].copy()
    vue["Date DA"] = vue["Date DA"].map(fmt_date)
    vue["Date validation"] = vue["Date validation"].map(fmt_date)
    for c in cols:
        if c not in ("Date DA", "Date validation"):
            vue[c] = vue[c].map(as_text)
    version = st.session_state.get("adm_v", 0)
    t1, t2 = st.columns([1, 3])
    tout = t1.checkbox("Tout cocher", key=f"adm-tout-{version}")
    if t2.toggle("Seulement les DA en attente de validation", key="adm-emises"):
        vue = vue.loc[vue["Statut"].isin(DA_EN_ATTENTE)]
    vue.insert(0, "✓", tout)
    ed = st.data_editor(
        vue, hide_index=True, width="stretch", key=f"adm-da-{version}-{tout}",
        disabled=["N° DA", "Date DA", "Chantier", "Demandeur", "Visa Resp. service", "Validé par", "Date validation"],
        column_config={
            "✓": st.column_config.CheckboxColumn("✓", width="small", help="Cocher pour valider / rejeter en un clic"),
            "ID_DA": None,
            "Statut": st.column_config.SelectboxColumn("Statut", options=STATUTS, required=True),
            "Priorité": st.column_config.SelectboxColumn("Priorité", options=PRIORITES, required=True),
            "Motif rejet": st.column_config.TextColumn("Motif rejet", width="medium"),
        })
    coches = ed.loc[ed["✓"].astype(bool)]
    n = len(coches)
    b1, b2, b3, b4 = st.columns([1, 1, 1, 2])
    motif = b4.text_input("Motif (obligatoire pour rejeter)", key=f"adm-motif-{version}",
                          label_visibility="collapsed", placeholder="Motif du rejet (obligatoire pour rejeter)")
    with b4:
        ia.bouton_reformuler(f"adm-motif-{version}", "Rejet de demandes d'achat par l'administration", "adm-ref")
    action = None
    if b1.button(f"✅ Valider DG ({n})", disabled=n == 0, type="primary", width="stretch", key="adm-ok"):
        action = "Validée DG"
    if b2.button(f"❌ Rejeter ({n})", disabled=n == 0, width="stretch", key="adm-ko"):
        action = "Rejetée"
    if b3.button(f"↩ Remettre en Émise ({n})", disabled=n == 0, width="stretch", key="adm-re"):
        action = "Émise"
    if action == "Rejetée" and not motif.strip():
        st.error("Indiquez le motif du rejet avant de cliquer sur Rejeter.")
        action = None
    if action:
        changes = {}
        for id_da in coches["ID_DA"]:
            changes[id_da] = _champs_statut(da, id_da, action, user, motif.strip())
        get_store().update_many("DA", "ID_DA", changes)
        journaliser([_entree(da.loc[da["ID_DA"] == i].iloc[0].to_dict(), "Admin", action, user, motif.strip())
                     for i in changes])
        refresh(statique=False)
        if action != "Émise":
            for id_da in changes:
                _notifier({"ID_DA": id_da, "Email Demandeur": "x"}, action)
        st.session_state.adm_v = version + 1
        st.session_state.adm_msg = f"{n} DA passée(s) en « {action} » : {', '.join(coches['N° DA'])}."
        st.rerun()

    if st.button("Enregistrer les modifications du tableau", key="adm-save"):
        avant, apres = vue.drop(columns="✓").set_index("ID_DA"), ed.drop(columns="✓").set_index("ID_DA")
        changes, notifs, sans_motif = {}, [], []
        for id_da in apres.index:
            diff = {c: as_text(apres.at[id_da, c]) for c in ["Statut", "Priorité", "Responsable", "Code DA", "Motif rejet"]
                    if as_text(apres.at[id_da, c]) != as_text(avant.at[id_da, c])}
            if not diff:
                continue
            if "Statut" in diff:
                diff.update({k: v for k, v in _champs_statut(da, id_da, diff["Statut"], user,
                                                             diff.get("Motif rejet", "")).items()
                             if k != "Motif rejet" or diff["Statut"] != "Rejetée"})
                if diff["Statut"] in ("Validée DG", "Rejetée"):
                    notifs.append((id_da, diff["Statut"]))
            if as_text(apres.at[id_da, "Statut"]) == "Rejetée" and not as_text(apres.at[id_da, "Motif rejet"]):
                sans_motif.append(as_text(apres.at[id_da, "N° DA"]))
            changes[id_da] = diff
        if not changes:
            st.info("Aucune modification à enregistrer.")
        else:
            get_store().update_many("DA", "ID_DA", changes)
            journaliser([_entree(da.loc[da["ID_DA"] == i].iloc[0].to_dict(), "Admin", c["Statut"], user)
                         for i, c in changes.items() if "Statut" in c])
            refresh(statique=False)
            for id_da, statut in notifs:
                _notifier({"ID_DA": id_da, "Email Demandeur": "x"}, statut)
            st.session_state.adm_v = version + 1
            st.session_state.adm_msg = (f"{len(changes)} DA mise(s) à jour."
                                        + (f" Motif de rejet manquant : {', '.join(sans_motif)}." if sans_motif else ""))
            st.rerun()
    if st.session_state.get("adm_msg"):
        st.success(st.session_state.pop("adm_msg"))

    st.markdown("##### Détail et lignes d'une DA")
    d = choisir_da(f, "adm")
    if not d:
        return
    detail_da(d, "adm")
    modifier_da(d, user, admin=True)
    lg = pd.DataFrame(lignes_de(d["ID_DA"]))
    if not len(lg):
        return
    lcols = ["ID_Ligne", "N° Ligne", "Désignation", "Référence", "Code_Parc", "Colisage", "Quantité", "Usage",
             "Fournisseur", "N° BC", "Prix unitaire HT", "Qté livrée", "Date livraison", "Statut ligne"]
    lv = lg[lcols].sort_values("N° Ligne").copy()
    for c in lcols:
        lv[c] = lv[c].map(as_text)
    with st.expander("Suivi achats des lignes (avancé : fournisseur, BC, livraison)"):
        led = st.data_editor(
            lv, hide_index=True, width="stretch", key=f"adm-lg-{d['ID_DA']}-{version}",
            disabled=["N° Ligne"],
            column_config={"ID_Ligne": None,
                           "Colisage": st.column_config.SelectboxColumn(options=COLISAGES),
                           "Statut ligne": st.column_config.SelectboxColumn(options=STATUTS_LIGNE),
                           "Date livraison": st.column_config.TextColumn(help="AAAA-MM-JJ")})
        if st.button("Enregistrer les lignes", key="adm-lg-save"):
            avant, apres = lv.set_index("ID_Ligne"), led.set_index("ID_Ligne")
            changes = {}
            for idl in apres.index:
                diff = {c: as_text(apres.at[idl, c]) for c in lcols[2:]
                        if as_text(apres.at[idl, c]) != as_text(avant.at[idl, c])}
                for c in ("Quantité", "Prix unitaire HT", "Qté livrée"):
                    if c in diff:
                        diff[c] = _num(diff[c])
                if diff:
                    changes[idl] = diff
            if changes:
                get_store().update_many("HISTORIQUE_ARTICLES", "ID_Ligne", changes)
                refresh(statique=False)
                st.session_state.adm_v = version + 1
                st.session_state.adm_msg = f"{len(changes)} ligne(s) mise(s) à jour."
                st.rerun()
            else:
                st.info("Aucune modification à enregistrer.")


def onglet_admin(user: dict):
    t1, t5, t6, t2, t3, t4 = st.tabs(["Demandes d'achat", "Articles à cataloguer", "🤖 IA référentiels",
                                      "Utilisateurs & accès", "Paramètres", "Archivage"])
    with t6:
        import auto_ia
        auto_ia.onglet_ia_referentiels(contexte(user))
    with t1:
        onglet_admin_da(user)
    with t5:
        catalogage.onglet_a_cataloguer(contexte(user))
    with t2:
        onglet_acces(user)
    with t3:
        achats.onglet_parametres(contexte(user))
    with t4:
        achats.onglet_archivage(contexte(user))


def _creer_acces(personnes: list[dict]) -> tuple[list[str], list[str]]:
    """Crée les accès ; retourne (lignes txt créées, erreurs)."""
    users = load("UTILISATEURS")
    noms = {as_text(n).lower() for n in users["Nom"]}
    codes = {normalise(c) for c in users["PIN"] if as_text(c)}
    rows, lignes, erreurs = [], [], []
    for p in personnes:
        nom = p["Nom"].strip()
        poste = trouve_poste(p["Poste"])
        if not nom or not poste:
            erreurs.append(f"« {nom or '?'} » : poste non reconnu « {p['Poste']} » "
                           f"(attendu : {', '.join(POSTES)})")
            continue
        p = {**p, "Poste": poste, "Service": POSTES[poste]}
        if p.get("Rôle") == "Chef de service" and (chef_du_service(p["Service"]) or any(
                r["Rôle"] == "Chef de service" and r["Service"] == p["Service"] for r in rows)):
            erreurs.append(f"« {nom} » : le service {poste} a déjà un responsable (un seul chef par service)")
            continue
        if nom.lower() in noms:
            erreurs.append(f"« {nom} » existe déjà (un nom doit être unique)")
            continue
        code = nouveau_code(p["Poste"].strip(), codes)
        codes.add(code)
        noms.add(nom.lower())
        role = p.get("Rôle") if p.get("Rôle") in ROLES else (
            "Acheteur" if poste == "Achats" else "Magasinier" if poste == "Magasin" else
            "Direction" if poste in ("Direction générale", "Direction d'exploitation") else "Demandeur")
        rows.append({"Email": p.get("Email", ""), "Nom": nom, "Rôle": role,
                     "Chantier": p.get("Chantier", ""), "Service": p.get("Service", ""),
                     "Actif": "TRUE", "PIN": code, "Poste": p["Poste"].strip(), "Civilité": "M."})
        lignes.append(ligne_txt(nom, p["Poste"].strip(), code))
    if rows:
        get_store().append("UTILISATEURS", rows)
        refresh()
    return lignes, erreurs


def _chefs_en_double(users: pd.DataFrame) -> list[str]:
    """Services ayant plus d'un « Chef de service » actif (users : colonnes Nom, Poste, Rôle, Actif)."""
    u = users.copy()
    u["_svc"] = u["Poste"].map(lambda p: POSTES.get(as_text(p), as_text(p)))
    u = u.loc[(u["Rôle"].map(as_text) == "Chef de service") & u["Actif"].map(lambda v: v is True or _actif(v)).astype(bool)]
    return [f"{svc} : {', '.join(g['Nom'].map(as_text))}" for svc, g in u.groupby("_svc") if len(g) > 1]


def onglet_acces(user: dict):
    ch_codes = [""] + load("CHANTIERS")["Code Chantier"].map(as_text).loc[lambda s: s != ""].tolist()
    postes = list(POSTES)
    st.session_state.setdefault("acces_crees", [])
    for e in st.session_state.pop("acces_err", []):
        st.error(e)

    # --- accès créés dans cette session : à transmettre
    if st.session_state.acces_crees:
        st.markdown("##### Accès à transmettre")
        txt = "\n".join(st.session_state.acces_crees)
        st.code(txt, language=None)
        c1, c2 = st.columns(2)
        c1.download_button("Télécharger (.txt)", txt.encode("utf-8"), file_name=f"Acces_DA_{date.today():%Y%m%d}.txt",
                           mime="text/plain", type="primary")
        if c2.button("Vider la liste"):
            st.session_state.acces_crees = []
            st.rerun()
        st.caption("Envoyez à chaque personne sa ligne en message privé, puis supprimez ce fichier.")

    # --- création unitaire
    st.markdown("##### Créer un accès")
    with st.form("acces1", clear_on_submit=True):
        c1, c2 = st.columns(2)
        nom = c1.text_input("Nom et prénom")
        poste = c2.selectbox("Poste", postes, index=None, placeholder="Choisir")
        c1, c2 = st.columns(2)
        chantier = c1.selectbox("Chantier", ch_codes, format_func=lambda c: c or "— aucun —")
        role = c2.selectbox("Rôle", ["Selon le poste"] + ROLES,
                            help="Selon le poste : Achats → Acheteur ; Direction générale / d'exploitation → "
                                 "Direction ; autres → Demandeur")
        email = st.text_input("Email (facultatif, pour les notifications)")
        if st.form_submit_button("Créer l'accès", type="primary"):
            lignes, erreurs = _creer_acces([{"Nom": nom, "Poste": poste or "", "Chantier": chantier,
                                             "Rôle": role, "Email": email.strip()}])
            st.session_state.acces_err = erreurs
            if lignes:
                st.session_state.acces_crees += lignes
                st.rerun()

    # --- création en masse
    with st.expander("Créer plusieurs accès d'un coup"):
        st.caption("Une personne par ligne : Nom ; Poste ; Code chantier (facultatif). "
                   f"Postes : {', '.join(f'{n} ({c})' for n, c in POSTES.items())}. "
                   "Exemple : Koné Ibrahim ; Parc matériel ; TORTIYA")
        bloc = st.text_area("Liste", height=160, key="acces_bloc")
        if st.button("Créer tous les accès", type="primary"):
            personnes = []
            for ln in bloc.splitlines():
                if not ln.strip():
                    continue
                parts = [x.strip() for x in ln.split(";")] + ["", "", ""]
                personnes.append({"Nom": parts[0], "Poste": parts[1], "Chantier": parts[2].upper()})
            lignes, erreurs = _creer_acces(personnes)
            st.session_state.acces_err = erreurs
            if lignes:
                st.session_state.acces_crees += lignes
                st.rerun()

    # --- liste et modification
    st.markdown("##### Utilisateurs")
    users = load("UTILISATEURS")
    if not len(users):
        st.info("Aucun utilisateur.")
        return
    cols = ["Civilité", "Nom", "Poste", "Rôle", "Chantier", "Service", "Actif", "Email", "PIN"]
    vue = users[cols].copy()
    for c in cols:
        vue[c] = vue[c].map(as_text)
    vue["Actif"] = vue["Actif"].map(lambda v: v == "" or is_true(v))
    vue = vue.sort_values(["Actif", "Nom"], ascending=[False, True])
    version = st.session_state.get("acc_v", 0)
    ed = st.data_editor(
        vue, hide_index=True, width="stretch", key=f"acc-ed-{version}",
        disabled=["Nom", "PIN", "Service"],
        column_config={
            "Civilité": st.column_config.SelectboxColumn(options=CIVILITES, width="small",
                                                         help="Pour l'accueil : « Bonjour M. Koné »"),
            "Poste": st.column_config.SelectboxColumn(options=postes),
            "Rôle": st.column_config.SelectboxColumn(options=ROLES, required=True,
                                                     help="Chef de service : vise les DA de son service (un seul par "
                                                          "service) · Acheteur : onglet Achats · Direction : valide "
                                                          "les DA (niveau DG) et les BC"),
            "Chantier": st.column_config.SelectboxColumn(options=ch_codes),
            "Service": st.column_config.TextColumn(help="Suit automatiquement le poste"),
            "Actif": st.column_config.CheckboxColumn(help="Décocher pour couper l'accès"),
            "PIN": st.column_config.TextColumn("Code d'accès"),
        })
    c1, c2 = st.columns(2)
    if c1.button("Enregistrer les modifications", key="acc-save", type="primary"):
        avant, apres = vue.set_index("Nom"), ed.set_index("Nom")
        changes = {}
        for n in apres.index:
            diff = {}
            for c in ["Civilité", "Poste", "Rôle", "Chantier", "Email"]:
                if as_text(apres.at[n, c]) != as_text(avant.at[n, c]):
                    diff[c] = as_text(apres.at[n, c])
            if diff.get("Poste") in POSTES:
                diff["Service"] = POSTES[diff["Poste"]]
            if bool(apres.at[n, "Actif"]) != bool(avant.at[n, "Actif"]):
                diff["Actif"] = "TRUE" if bool(apres.at[n, "Actif"]) else "FALSE"
            if diff:
                changes[n] = diff
        doublons = _chefs_en_double(ed)
        if doublons:
            st.error("Un seul responsable (rôle « Chef de service ») par service : " + " ; ".join(doublons))
        elif changes:
            get_store().update_many("UTILISATEURS", "Nom", changes)
            refresh()
            st.session_state.acc_v = version + 1
            st.rerun()
        else:
            st.info("Aucune modification à enregistrer.")
    actifs = ed.loc[ed["Actif"].astype(bool)]
    tout = "\n".join(ligne_txt(r["Nom"], r["Poste"], r["PIN"]) for _, r in actifs.iterrows() if r["PIN"])
    c2.download_button("Exporter tous les accès actifs (.txt)", tout.encode("utf-8"),
                       file_name=f"Acces_DA_tous_{date.today():%Y%m%d}.txt", mime="text/plain")

    # --- nouveau code
    with st.expander("Générer un nouveau code (code perdu ou divulgué)"):
        qui = st.selectbox("Utilisateur", vue["Nom"].tolist(), index=None, placeholder="Choisir")
        if st.button("Générer un nouveau code", disabled=not qui):
            r = vue.loc[vue["Nom"] == qui].iloc[0]
            code = nouveau_code(r["Poste"] or "Utilisateur", {normalise(c) for c in vue["PIN"] if c})
            get_store().update_many("UTILISATEURS", "Nom", {qui: {"PIN": code}})
            refresh()
            st.session_state.acces_crees += [ligne_txt(qui, r["Poste"], code)]
            st.session_state.acc_v = version + 1
            st.rerun()
        st.caption("L'ancien code ne fonctionne plus dès que le nouveau est généré.")


def onglet_analyses(ctx=None):
    # un seul volet calculé à la fois (des onglets imbriqués calculeraient les deux à chaque affichage)
    vues = ["Demandes d'achat", "Achats", "Prix"] + (["🤖 Assistant IA"] if ia.disponible() else [])
    vue = st.segmented_control("Analyse", vues, default="Demandes d'achat",
                               key="an-vue", label_visibility="collapsed") or "Demandes d'achat"
    if vue == "🤖 Assistant IA":
        df = stats.base(ctx)
        assistant.ui(ctx, df.loc[~df["aberrant"]] if len(df) else None, "an-chat", "tous les achats et toutes les DA")
    elif vue == "Achats":
        achats.analyses_achats(ctx)
    elif vue == "Prix":
        prix.vue_evolution(ctx)
    else:
        _analyses_da()


def _analyses_da():
    da, lg = load("DA"), load("HISTORIQUE_ARTICLES")
    if not len(da):
        st.info("Pas encore de données.")
        return
    da = da.copy()
    da["_date"] = pd.to_datetime(da["Date DA"].map(to_date))
    da["_val"] = pd.to_datetime(da["Date validation"].map(to_date))
    delai = (da["_val"] - da["_date"]).dt.days.dropna()
    da["_visa"] = pd.to_datetime(da["Date visa Resp. service"].map(to_date))
    d1 = (da["_visa"] - da["_date"]).dt.days.dropna()
    d2 = (da["_val"] - da["_visa"]).dt.days.dropna()
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("DA", len(da))
    k2.metric("Articles", len(lg))
    k3.metric("Urgentes / critiques", f"{(da['Priorité'].isin(['Urgente', 'Critique'])).mean():.0%}")
    k4.metric("Délai total de validation", f"{delai.mean():.1f} j" if len(delai) else "–")
    j1, j2, j3 = st.columns(3)
    j1.metric("Délai visa Resp. service", f"{d1.mean():.1f} j" if len(d1) else "–")
    j2.metric("Délai validation DG", f"{d2.mean():.1f} j" if len(d2) else "–")
    j3.metric("En attente de validation", int(da["Statut"].isin(DA_EN_ATTENTE).sum()))

    da["Mois"] = da["_date"].dt.strftime("%Y-%m")
    noms_ch = refs()["chantiers"]
    par_mois = (da.assign(Chantier=da["Chantier"].map(lambda c: noms_ch.get(as_text(c), as_text(c))), DA=1)
                .groupby(["Mois", "Chantier"], as_index=False)["DA"].sum())
    graphs.empile_mois(par_mois, "Mois", "Chantier", "DA", "DA par mois et par chantier", "da-mois")
    c1, c2 = st.columns(2)
    with c1:
        stt = da["Statut"].value_counts().rename_axis("Statut").reset_index(name="DA")
        graphs.barres(stt, "Statut", "DA", "DA par statut", "da-statut")
    with c2:
        svc = refs()["services"]
        sv = (da["Service Demandeur"].map(lambda c: svc.get(as_text(c), as_text(c)) or "—").value_counts()
              .rename_axis("Service").reset_index(name="DA"))
        graphs.barres(sv, "Service", "DA", "DA par service", "da-service", top=10)
    lg2 = lg.copy()
    lg2["Quantité"] = pd.to_numeric(lg2["Quantité"], errors="coerce")
    art = load("ARTICLES")
    fam = dict(zip(art["Code Article"].map(as_text), art["Famille"].map(as_text)))
    lg2["Famille"] = lg2["Code Article"].map(lambda c: fam.get(as_text(c), "Hors catalogue"))
    lg2["Référence"] = lg2["Référence"].map(as_text).replace("", "(sans référence)")

    fam_t = (lg2.groupby("Famille").agg(DA=("ID_DA", "nunique"), Lignes=("ID_Ligne", "size"))
             .sort_values("DA", ascending=False).reset_index())
    graphs.barres(fam_t, "Famille", "DA", "Familles d'articles les plus demandées (nombre de DA)", "da-fam",
                  infos=["Lignes"])
    top = (lg2.groupby(["Désignation", "Colisage"])
           .agg(Quantité=("Quantité", "sum"), DA=("ID_DA", "nunique"), Références=("Référence", "nunique"))
           .sort_values("DA", ascending=False).reset_index())
    graphs.barres(top, "Désignation", "DA", "Articles les plus demandés (nombre de DA)", "da-top",
                  infos=["Quantité", "Colisage", "Références"], table=top.head(100))
    choix = st.selectbox("Détail par référence pour", top["Désignation"].unique(), index=None,
                         placeholder="Choisir un type d'article (ex. BETON PRET A L'EMPLOI)")
    if choix:
        det = (lg2.loc[lg2["Désignation"] == choix].groupby(["Référence", "Colisage"])
               .agg(Quantité=("Quantité", "sum"), DA=("ID_DA", "nunique"))
               .sort_values("Quantité", ascending=False).reset_index())
        graphs.barres(det, "Référence", "Quantité", f"{choix} : quantités demandées par référence", "da-det",
                      fmt=",.2~f", infos=["Colisage", "DA"])

    flat = lg.merge(da[["ID_DA", "Statut", "Priorité", "Demandeur", "Responsable", "Date besoin",
                        "Validé par", "Date validation", "Motif rejet"]], on="ID_DA", how="left")
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as xw:
        flat.to_excel(xw, sheet_name="Lignes_DA", index=False)
        da.drop(columns=["_date", "_val", "_visa", "Mois"]).to_excel(xw, sheet_name="DA", index=False)
        for t in ("VALIDATIONS_DA", "BC", "BC_LIGNES", "LIVRAISONS", "TCP", "TCP_OFFRES"):
            load(t).to_excel(xw, sheet_name=t, index=False)
    st.download_button("Exporter la base pour analyse (Excel)", out.getvalue(),
                       file_name=f"Export_DA_{date.today():%Y%m%d}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# --------------------------------------------------------------------------- main
def _fragment(fn, nom: str):
    """Exécute fn dans un fragment Streamlit (rafraîchissement local), sinon normalement."""
    frag = None if os.environ.get("DA_SANS_FRAGMENT") else getattr(st, "fragment", None)
    if frag is None:
        return fn()

    def corps():
        fn()
    corps.__name__ = corps.__qualname__ = nom.replace("-", "_")
    return frag(corps)()


def contexte(user: dict):
    from types import SimpleNamespace
    return SimpleNamespace(load=load, refresh=refresh, get_store=get_store, refs=refs, picker=choisir_article,
                           user=user, fragment=_fragment, version=version_donnees, secret=_secret,
                           modifier_da=lambda d: modifier_da(d, user, admin=user.get("Rôle") in ROLES_ADMIN, suffixe="-ach"))


def bloquer_traduction():
    """La traduction automatique du navigateur (Chrome « Traduire cette page ») modifie la page
    et provoque l'erreur « removeChild … n'est pas un enfant de ce nœud ». On déclare la page
    en français et non traduisible."""
    js = """<script>
    try {
      const d = (window.parent && window.parent.document) || document, h = d.documentElement;
      h.lang = "fr"; h.setAttribute("translate", "no"); h.classList.add("notranslate");
      if (!d.querySelector('meta[name="google"]')) {
        const m = d.createElement("meta"); m.name = "google"; m.content = "notranslate"; d.head.appendChild(m);
      }
    } catch (e) {}
    </script>"""
    try:
        st.html(js, unsafe_allow_javascript=True)
    except TypeError:  # Streamlit plus ancien
        import streamlit.components.v1 as components
        components.html(js, height=0)


def main():
    ia.brancher_conso(get_store)  # compteur de consommation de l'API Claude (table CONSO_IA)
    bloquer_traduction()
    restaurer_session()
    if (st.session_state.get("user") or {}).get("Rôle") in LARGE and _u.get("Rôle") not in LARGE:
        st.set_page_config(layout="wide")  # session restaurée après actualisation
    if "user" not in st.session_state:
        login()
    user = st.session_state.user
    admin = user["Rôle"] in ROLES_ADMIN
    badge = f'<span class="pwc-badge">{user["Rôle"].upper()}</span>' if user["Rôle"] != "Demandeur" else ""
    if user["Rôle"] in ROLES_VALID_DA:
        n_val = nb_a_valider(user)
        if n_val:
            badge += f'<br><span class="pwc-badge">🔔 {n_val} DA à valider</span>'
    if user["Rôle"] == "Admin":
        n_cat = catalogage.nb_a_analyser(contexte(user))
        if n_cat:
            badge += f'<br><span class="pwc-badge">📦 {n_cat} article(s) à cataloguer</span>'
    h1, h0, h2 = st.columns([5, 1, 1], vertical_alignment="center")
    h1.markdown(f'<div class="pwc-bar"><div class="t">DA <span>Chantier</span> PWC</div>'
                f'<div class="u">{user["Nom"]}{badge}</div></div>', unsafe_allow_html=True)
    if h0.button("🔄", help="Actualiser les données (DA et BC saisis par les autres)"):
        refresh()
        st.rerun()
    if h2.button("Quitter"):
        st.session_state.clear()
        st.query_params.clear()
        st.rerun()

    role = user["Rôle"]
    ctx = contexte(user)
    # l'acheteur ne saisit pas de DA : il consulte les DA et fait les TCP et les BC (onglet Achats)
    # saisie des DA : administrateurs, chefs de service et demandeurs (N-1) ; l'acheteur et la direction n'en
    # saisissent pas (achats, validations, analyses)
    onglets = {"Nouvelle DA": lambda: onglet_nouvelle(user),
               "Mes DA": lambda: onglet_mes_da(user)} if role in ROLES_DA else {}
    if role in ROLES_VALID_DA:  # libellé fixe : un libellé qui change ferait revenir sur le premier onglet
        onglets["Validation DA"] = lambda: onglet_validation_da(user)
    if role in ROLES_ACHATS:
        onglets["Achats"] = lambda: achats.onglet_achats(ctx)
    if role in ROLES_BL:
        onglets["Réception BL"] = lambda: bl.onglet_bl(ctx)
    if role in ROLES_VALID_BC:
        onglets["Validation BC"] = lambda: achats.onglet_validation_bc(ctx)
    if role == "Magasinier":  # le magasinier ne voit que son magasin
        onglets["Magasin"] = lambda: magasin.onglet_magasin(ctx)
    if role in ROLES_STOCK_VUE:
        onglets["Stocks"] = lambda: magasin.vue_stocks(ctx)
    if admin:
        onglets["Administration"] = lambda: onglet_admin(user)
        onglets["Statistiques"] = lambda: stats.onglet_statistiques(ctx)  # administrateurs uniquement
    if role in ROLES_ADMIN | ROLES_VALID_BC:
        onglets["Analyses"] = lambda: onglet_analyses(ctx)
    # chaque onglet est un « fragment » : un clic dans un onglet ne recalcule que cet onglet
    for i, (tab, fn) in enumerate(zip(st.tabs(list(onglets)), onglets.values())):
        with tab:
            _fragment(fn, f"onglet-{i}")

main()
