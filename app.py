"""DA Chantier PWC — saisie, impression, validation et archivage des demandes d'achat.

Lancer en local :   streamlit run app.py
Données : Google Sheet (si les secrets sont configurés) sinon fichier Excel local data/PWC_DA_local.xlsx
"""
from __future__ import annotations

import io
import os
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

from acces import POSTES, Garde, creer_jeton, ligne_txt, lire_jeton, normalise, nouveau_code, trouve_poste
from logic import as_text, build_da, decide, fmt_date, is_true, send_email, to_date
from pdf import build_pdf, find_logo
from schema import ANCIENS_STATUTS, COLISAGES, PRIORITES, ROLES_ADMIN, STATUTS, STATUTS_LIGNE
from storage import ExcelStore, GoogleSheetStore

APP_DIR = Path(__file__).resolve().parent
LOGO = find_logo()
_u = st.session_state.get("user") or {}
st.set_page_config(page_title="DA Chantier PWC", page_icon=str(LOGO) if LOGO else "📋",
                   layout="wide" if _u.get("Rôle") == "Admin" else "centered")
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


def get_store():
    sa, sheet_id = _service_account(), _secret("SHEET_ID")
    if sa and sheet_id:
        try:
            return GoogleSheetStore(sa, sheet_id)
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
    return True


@st.cache_data(ttl=45, show_spinner=False)
def _load_all() -> dict:
    _services_pwc()
    return get_store().read_all()  # un seul appel Google pour tous les onglets


_COLONNES_REQUISES = {"UTILISATEURS": ["Email", "Nom", "Rôle", "Chantier", "Service", "Actif", "PIN", "Poste"]}


def load(table: str) -> pd.DataFrame:
    df = _load_all()[table]
    manquantes = [c for c in _COLONNES_REQUISES.get(table, []) if c not in df.columns]
    if manquantes:  # tolère un schema.py ou un Google Sheet en retard d'une version
        df = df.copy()
        for c in manquantes:
            df[c] = ""
    if table == "DA" and len(df):
        df = df.copy()
        df["Statut"] = df["Statut"].map(lambda s: ANCIENS_STATUTS.get(as_text(s), as_text(s)))
    return df


def refresh():
    _load_all.clear()


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
    suffix = {"Validée": "_VALIDEE", "Rejetée": "_REJETEE"}.get(as_text(da.get("Statut")), "")
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
        return {"Nom": "Administrateur", "Rôle": "Admin", "Email": "", "Chantier": "", "Service": ""}
    users = load("UTILISATEURS")
    r = users.loc[users["Nom"].map(as_text) == nom]
    if not len(r):
        return None
    r = r.iloc[0]
    if not (as_text(r["Actif"]) == "" or is_true(r["Actif"])):
        return None
    return {k: as_text(r[k]) for k in ["Nom", "Rôle", "Email", "Chantier", "Service"]}


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


# --------------------------------------------------------------------------- vues communes
def detail_da(da: dict, key: str):
    st.markdown(f"**{as_text(da['N° DA'])}** · {as_text(da['Statut'])} · "
                f"{refs()['chantiers'].get(as_text(da['Chantier']), as_text(da['Chantier']))}")
    st.caption(f"{fmt_date(da['Date DA'])} · {as_text(da['Demandeur'])} · priorité {as_text(da['Priorité'])}"
               + (f" · besoin le {fmt_date(da['Date besoin'])}" if as_text(da["Date besoin"]) else ""))
    lg = pd.DataFrame(lignes_de(da["ID_DA"]))
    if len(lg):
        lg = lg.sort_values("N° Ligne")[["N° Ligne", "Désignation", "Référence", "Code_Parc",
                                         "Colisage", "Quantité", "Usage"]]
        st.dataframe(lg, hide_index=True, width="stretch")
    if as_text(da.get("Motif rejet")):
        st.warning(f"Motif du rejet : {as_text(da['Motif rejet'])}")
    st.download_button("Télécharger / imprimer la fiche PDF", pdf_de(da), file_name=pdf_name(da),
                       mime="application/pdf", key=f"pdf-{key}", type="primary")


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


def _choisir_article(d: str):
    st.session_state["art-des"] = d


def onglet_nouvelle(user: dict):
    if "lignes" not in st.session_state:
        st.session_state.lignes = []
    last = st.session_state.get("last_submit")
    if last:
        st.success(f"DA **{last['numero']}** enregistrée et transmise pour validation.")
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

    st.markdown("##### En-tête")
    c1, c2 = st.columns(2)
    chantier = c1.selectbox("Chantier", codes_ch, format_func=lambda c: r["chantiers"].get(c, c),
                            index=codes_ch.index(user["Chantier"]) if user["Chantier"] in codes_ch else 0)
    date_da = c2.date_input("Date", value=date.today(), format="DD/MM/YYYY")
    c1, c2 = st.columns(2)
    service = c1.selectbox("Service demandeur", codes_sv, format_func=lambda c: r["services"].get(c, c),
                           index=codes_sv.index(user["Service"]) if user["Service"] in codes_sv else 0)
    resp_defaut = as_text(sv.loc[sv["Code Service"].map(as_text) == service].iloc[0]["Responsable"])
    responsable = c2.text_input("Responsable du service", value=resp_defaut, key=f"resp-{service}")
    c1, c2 = st.columns(2)
    demandeur = c1.text_input("Demandeur", value=user["Nom"])
    code_da = c2.text_input("Code DA (facultatif)")
    c1, c2 = st.columns(2)
    priorite = c1.selectbox("Priorité", PRIORITES)
    date_besoin = c2.date_input("Date de besoin", value=None, format="DD/MM/YYYY")
    observations = st.text_area("Observations", height=68)

    st.markdown("##### Articles")
    art = load("ARTICLES")
    art = art.loc[art["Code Article"].map(as_text) != ""]
    cat = {as_text(a["Code Article"]): a for _, a in art.iterrows()}
    hist = load("HISTORIQUE_ARTICLES")
    parcs = sorted((set(load("PARC")["Code_Parc"].map(as_text)) | set(hist["Code_Parc"].map(as_text))) - {""})
    # libellé affiché = désignation + référence (ex. « PNEU — 13R22.5 ») : plusieurs articles
    # peuvent avoir la même désignation, la référence les distingue
    par_des = {}
    for c, a in cat.items():
        d, rf = as_text(a["Désignation"]), as_text(a.get("Référence"))
        par_des.setdefault(f"{d} — {rf}" if rf else d, c)
    desigs = sorted(par_des)  # uniquement l'onglet ARTICLES de la base

    # choix libre : on prend dans la liste OU on tape sa propre désignation
    des = st.selectbox("Désignation", desigs, index=None, accept_new_options=True, key="art-des",
                       placeholder="Choisir dans la liste ou taper librement (désignation ou référence)")
    code = par_des.get(as_text(des)) if des else None
    if des and not code:
        code = par_des.get(" ".join(as_text(des).upper().split()))
    if code:  # article du catalogue : la désignation enregistrée est sans la référence
        des = as_text(cat[code]["Désignation"])
    if des and not code:  # texte libre : proposer les articles standard les plus proches
        props = articles_proches(des, list(par_des))
        if props:
            st.info("Articles de la liste standard proches de votre saisie — cliquez pour le reprendre :")
            for i, p in enumerate(props):
                st.button(p, key=f"prop-{i}-{p}", on_click=_choisir_article, args=(p,), width="stretch")
            st.caption("Sinon, continuez : votre désignation sera enregistrée telle quelle.")
    a = cat.get(code, {}) if code else {}
    ref_def, col_def = as_text(a.get("Référence")), as_text(a.get("Colisage"))
    with st.form(f"ajout-{code or des or ''}", clear_on_submit=True):
        a1, a2 = st.columns(2)
        refa = a1.text_input("Référence", value=ref_def)
        parc = a2.selectbox("Code parc (engin)", parcs, index=None, accept_new_options=True,
                            placeholder="Facultatif")
        colis = a1.selectbox("Colisage", COLISAGES, index=COLISAGES.index(col_def) if col_def in COLISAGES else 0)
        qte = a2.number_input("Quantité", min_value=0.0, step=1.0, value=1.0)
        usage = st.text_input("Usage")
        if st.form_submit_button("Ajouter l'article", type="primary"):
            if not des or qte <= 0 or not usage.strip():
                st.error("Désignation, quantité et usage sont obligatoires.")
            else:
                st.session_state.lignes.append({
                    "Code Article": code or "", "Désignation": " ".join(des.split()),
                    "Référence": refa.strip(), "Code_Parc": (parc or "").strip(), "Colisage": colis,
                    "Quantité": qte, "Usage": usage.strip()})
                st.session_state.pop("art-des", None)
                st.rerun()

    for i, l in enumerate(st.session_state.lignes):
        a, b = st.columns([6, 1])
        a.markdown(f"**{i + 1}. {l['Désignation']}** — {l['Quantité']:g} {l['Colisage']}"
                   f"{' · ' + l['Code_Parc'] if l['Code_Parc'] else ''}  \n{l['Usage']}")
        if b.button("✕", key=f"del-{i}", help="Retirer"):
            st.session_state.lignes.pop(i)
            st.rerun()

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
        with st.spinner("Enregistrement…"):
            da_row, rows = build_da(get_store(), entete, st.session_state.lignes, user)
            refresh()
            pdf = build_pdf(da_row, rows, r)
        mail = ""
        smtp = _secret("smtp")
        if smtp:
            dest = [as_text(ch[ch["Code Chantier"].map(as_text) == chantier].iloc[0]["Email validateur"]),
                    as_text(sv[sv["Code Service"].map(as_text) == service].iloc[0]["Email responsable"]),
                    user.get("Email", "")]
            try:
                if send_email(smtp, dest, f"DA {da_row['N° DA']} – {r['chantiers'].get(chantier, chantier)} – à valider",
                              f"Nouvelle demande d'achat {da_row['N° DA']} de {demandeur} ({n} articles, "
                              f"priorité {priorite}).\nFiche en pièce jointe.", pdf, f"{da_row['N° DA']}.pdf"):
                    mail = "Email envoyé au validateur."
            except Exception as exc:  # l'envoi ne doit jamais bloquer la DA
                mail = f"DA enregistrée, mais l'email n'a pas pu partir ({exc})."
        st.session_state.lignes = []
        st.session_state.last_submit = {"numero": da_row["N° DA"], "pdf": pdf, "mail": mail}
        st.rerun()


def onglet_mes_da(user: dict):
    da = load("DA")
    mine = da[(da["Email Demandeur"].map(as_text) == user["Email"]) & (user["Email"] != "")
              | (da["Demandeur"].map(as_text) == user["Nom"])]
    tableau_da(mine)
    d = choisir_da(mine, "mes")
    if d:
        detail_da(d, "mes")


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


def onglet_admin_da(user: dict):
    da = load("DA")
    if not len(da):
        st.info("Aucune DA pour le moment.")
        return
    k1, k2, k3 = st.columns(3)
    k1.metric("Émises", int((da["Statut"] == "Émise").sum()))
    k2.metric("Validées", int((da["Statut"] == "Validée").sum()))
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
    st.caption("Modifiez directement dans le tableau (statut, priorité…), puis enregistrez. "
               "Passer une DA en Validée ou Rejetée renseigne automatiquement le validateur et la date.")
    cols = ["ID_DA", "N° DA", "Date DA", "Chantier", "Demandeur", "Statut", "Priorité",
            "Responsable", "Code DA", "Motif rejet", "Validé par", "Date validation"]
    vue = f[cols].copy()
    vue["Date DA"] = vue["Date DA"].map(fmt_date)
    vue["Date validation"] = vue["Date validation"].map(fmt_date)
    for c in cols:
        if c not in ("Date DA", "Date validation"):
            vue[c] = vue[c].map(as_text)
    version = st.session_state.get("adm_v", 0)
    ed = st.data_editor(
        vue, hide_index=True, width="stretch", key=f"adm-da-{version}",
        disabled=["N° DA", "Date DA", "Chantier", "Demandeur", "Validé par", "Date validation"],
        column_config={
            "ID_DA": None,
            "Statut": st.column_config.SelectboxColumn("Statut", options=STATUTS, required=True),
            "Priorité": st.column_config.SelectboxColumn("Priorité", options=PRIORITES, required=True),
            "Motif rejet": st.column_config.TextColumn("Motif rejet", width="medium"),
        })
    if st.button("Enregistrer les modifications", type="primary", key="adm-save"):
        avant, apres = vue.set_index("ID_DA"), ed.set_index("ID_DA")
        changes, notifs, sans_motif = {}, [], []
        for id_da in apres.index:
            diff = {c: as_text(apres.at[id_da, c]) for c in ["Statut", "Priorité", "Responsable", "Code DA", "Motif rejet"]
                    if as_text(apres.at[id_da, c]) != as_text(avant.at[id_da, c])}
            if not diff:
                continue
            if "Statut" in diff:
                if diff["Statut"] in ("Validée", "Rejetée"):
                    diff["Validé par"] = user["Nom"]
                    diff["Date validation"] = date.today().isoformat()
                    notifs.append((id_da, diff["Statut"]))
                else:
                    diff["Validé par"] = ""
                    diff["Date validation"] = ""
            if as_text(apres.at[id_da, "Statut"]) == "Rejetée" and not as_text(apres.at[id_da, "Motif rejet"]):
                sans_motif.append(as_text(apres.at[id_da, "N° DA"]))
            changes[id_da] = diff
        if not changes:
            st.info("Aucune modification à enregistrer.")
        else:
            get_store().update_many("DA", "ID_DA", changes)
            refresh()
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
    lg = pd.DataFrame(lignes_de(d["ID_DA"]))
    if not len(lg):
        return
    lcols = ["ID_Ligne", "N° Ligne", "Désignation", "Référence", "Code_Parc", "Colisage", "Quantité", "Usage",
             "Fournisseur", "N° BC", "Prix unitaire HT", "Qté livrée", "Date livraison", "Statut ligne"]
    lv = lg[lcols].sort_values("N° Ligne").copy()
    for c in lcols:
        lv[c] = lv[c].map(as_text)
    with st.expander("Modifier les lignes et le suivi achats"):
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
                refresh()
                st.session_state.adm_v = version + 1
                st.session_state.adm_msg = f"{len(changes)} ligne(s) mise(s) à jour."
                st.rerun()
            else:
                st.info("Aucune modification à enregistrer.")


def onglet_admin(user: dict):
    t1, t2 = st.tabs(["Demandes d'achat", "Utilisateurs & accès"])
    with t1:
        onglet_admin_da(user)
    with t2:
        onglet_acces(user)


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
        if nom.lower() in noms:
            erreurs.append(f"« {nom} » existe déjà (un nom doit être unique)")
            continue
        code = nouveau_code(p["Poste"].strip(), codes)
        codes.add(code)
        noms.add(nom.lower())
        rows.append({"Email": p.get("Email", ""), "Nom": nom, "Rôle": p.get("Rôle") or "Demandeur",
                     "Chantier": p.get("Chantier", ""), "Service": p.get("Service", ""),
                     "Actif": "TRUE", "PIN": code, "Poste": p["Poste"].strip()})
        lignes.append(ligne_txt(nom, p["Poste"].strip(), code))
    if rows:
        get_store().append("UTILISATEURS", rows)
        refresh()
    return lignes, erreurs


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
        role = c2.selectbox("Rôle", ["Demandeur", "Admin"])
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
    cols = ["Nom", "Poste", "Rôle", "Chantier", "Service", "Actif", "Email", "PIN"]
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
            "Poste": st.column_config.SelectboxColumn(options=postes),
            "Rôle": st.column_config.SelectboxColumn(options=["Demandeur", "Admin"], required=True),
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
            for c in ["Poste", "Rôle", "Chantier", "Email"]:
                if as_text(apres.at[n, c]) != as_text(avant.at[n, c]):
                    diff[c] = as_text(apres.at[n, c])
            if diff.get("Poste") in POSTES:
                diff["Service"] = POSTES[diff["Poste"]]
            if bool(apres.at[n, "Actif"]) != bool(avant.at[n, "Actif"]):
                diff["Actif"] = "TRUE" if bool(apres.at[n, "Actif"]) else "FALSE"
            if diff:
                changes[n] = diff
        if changes:
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


def onglet_analyses():
    da, lg = load("DA"), load("HISTORIQUE_ARTICLES")
    if not len(da):
        st.info("Pas encore de données.")
        return
    da = da.copy()
    da["_date"] = pd.to_datetime(da["Date DA"].map(to_date))
    da["_val"] = pd.to_datetime(da["Date validation"].map(to_date))
    delai = (da["_val"] - da["_date"]).dt.days.dropna()
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("DA", len(da))
    k2.metric("Articles", len(lg))
    k3.metric("Urgentes / critiques", f"{(da['Priorité'].isin(['Urgente', 'Critique'])).mean():.0%}")
    k4.metric("Délai de validation", f"{delai.mean():.1f} j" if len(delai) else "–")

    st.markdown("##### DA par chantier et par mois")
    da["Mois"] = da["_date"].dt.strftime("%Y-%m")
    st.dataframe(pd.crosstab(da["Chantier"], da["Mois"]), width="stretch")
    st.markdown("##### DA par statut")
    st.dataframe(da["Statut"].value_counts().rename("DA"), width="stretch")
    lg2 = lg.copy()
    lg2["Quantité"] = pd.to_numeric(lg2["Quantité"], errors="coerce")
    art = load("ARTICLES")
    fam = dict(zip(art["Code Article"].map(as_text), art["Famille"].map(as_text)))
    lg2["Famille"] = lg2["Code Article"].map(lambda c: fam.get(as_text(c), "Hors catalogue"))
    lg2["Référence"] = lg2["Référence"].map(as_text).replace("", "(sans référence)")

    st.markdown("##### Commandes par famille")
    st.dataframe(lg2.groupby("Famille").agg(Lignes=("ID_Ligne", "size"), DA=("ID_DA", "nunique"))
                 .sort_values("DA", ascending=False), width="stretch")
    st.markdown("##### Commandes par type d'article (désignation)")
    top = (lg2.groupby(["Désignation", "Colisage"])
           .agg(Quantité=("Quantité", "sum"), DA=("ID_DA", "nunique"), Références=("Référence", "nunique"))
           .sort_values("DA", ascending=False).reset_index())
    st.dataframe(top.head(30), hide_index=True, width="stretch")
    choix = st.selectbox("Détail par référence pour", top["Désignation"].unique(), index=None,
                         placeholder="Choisir un type d'article (ex. BETON PRET A L'EMPLOI)")
    if choix:
        det = (lg2.loc[lg2["Désignation"] == choix].groupby(["Référence", "Colisage"])
               .agg(Quantité=("Quantité", "sum"), DA=("ID_DA", "nunique"))
               .sort_values("Quantité", ascending=False).reset_index())
        st.dataframe(det, hide_index=True, width="stretch")

    flat = lg.merge(da[["ID_DA", "Statut", "Priorité", "Demandeur", "Responsable", "Date besoin",
                        "Validé par", "Date validation", "Motif rejet"]], on="ID_DA", how="left")
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as xw:
        flat.to_excel(xw, sheet_name="Lignes_DA", index=False)
        da.drop(columns=["_date", "_val", "Mois"]).to_excel(xw, sheet_name="DA", index=False)
    st.download_button("Exporter la base pour analyse (Excel)", out.getvalue(),
                       file_name=f"Export_DA_{date.today():%Y%m%d}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# --------------------------------------------------------------------------- main
def main():
    restaurer_session()
    if (st.session_state.get("user") or {}).get("Rôle") == "Admin" and _u.get("Rôle") != "Admin":
        st.set_page_config(layout="wide")  # session restaurée après actualisation
    if "user" not in st.session_state:
        login()
    user = st.session_state.user
    admin = user["Rôle"] in ROLES_ADMIN
    badge = '<span class="pwc-badge">ADMIN</span>' if admin else ""
    h1, h2 = st.columns([5, 1], vertical_alignment="center")
    h1.markdown(f'<div class="pwc-bar"><div class="t">DA <span>Chantier</span> PWC</div>'
                f'<div class="u">{user["Nom"]}{badge}</div></div>', unsafe_allow_html=True)
    if h2.button("Quitter"):
        st.session_state.clear()
        st.query_params.clear()
        st.rerun()

    noms = ["Nouvelle DA", "Mes DA"] + (["Administration", "Analyses"] if admin else [])
    tabs = st.tabs(noms)
    with tabs[0]:
        onglet_nouvelle(user)
    with tabs[1]:
        onglet_mes_da(user)
    if admin:
        with tabs[2]:
            onglet_admin(user)
        with tabs[3]:
            onglet_analyses()


main()
