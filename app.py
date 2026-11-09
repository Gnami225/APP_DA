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


@st.cache_data(ttl=45, show_spinner=False)
def _load_all() -> dict:
    return get_store().read_all()  # un seul appel Google pour tous les onglets


def load(table: str) -> pd.DataFrame:
    df = _load_all()[table]
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
def _pin(v) -> str:
    """PIN normalisé : sans espaces ; 0123 et 123 équivalents (Sheets supprime les zéros)."""
    s = as_text(v).replace(" ", "")
    return s.lstrip("0") or s if s.isdigit() else s


def login():
    if LOGO:
        st.image(str(LOGO), width=150)
    st.subheader("Demandes d'achat chantier")
    users = load("UTILISATEURS")
    users = users.loc[users["Actif"].map(lambda v: as_text(v) == "" or is_true(v)).astype(bool)]
    noms = sorted(users["Nom"].map(as_text).loc[lambda s: s != ""].unique())
    admin_pin = as_text(_secret("ADMIN_PIN", ""))
    choix = noms + (["Administrateur (secours)"] if admin_pin else [])
    if not choix:
        st.error("Aucun utilisateur actif. Renseignez l'onglet UTILISATEURS (Nom, Rôle, PIN).")
        st.stop()
    with st.form("login"):
        nom = st.selectbox("Votre nom", choix)
        pin = st.text_input("Code PIN", type="password")
        ok = st.form_submit_button("Se connecter", type="primary")
    if ok:
        if nom == "Administrateur (secours)" and _pin(pin) == _pin(admin_pin):
            st.session_state.user = {"Nom": "Administrateur", "Rôle": "Admin", "Email": "",
                                     "Chantier": "", "Service": ""}
            st.rerun()
        row = users[users["Nom"].map(as_text) == nom]
        if len(row) and _pin(row.iloc[0]["PIN"]) and _pin(pin) == _pin(row.iloc[0]["PIN"]):
            st.session_state.user = {k: as_text(row.iloc[0][k]) for k in
                                     ["Nom", "Rôle", "Email", "Chantier", "Service"]}
            st.rerun()
        st.error("Nom ou code PIN incorrect.")
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
    hist = load("HISTORIQUE_ARTICLES")
    desigs = sorted((set(art["Désignation"].map(as_text)) | set(hist["Désignation"].map(as_text))) - {""})
    parcs = sorted((set(load("PARC")["Code_Parc"].map(as_text)) | set(hist["Code_Parc"].map(as_text))) - {""})
    with st.form("ajout", clear_on_submit=True):
        des = st.selectbox("Désignation", desigs, index=None, accept_new_options=True,
                           placeholder="Choisir ou taper une désignation")
        a1, a2 = st.columns(2)
        refa = a1.text_input("Référence")
        parc = a2.selectbox("Code parc (engin)", parcs, index=None, accept_new_options=True,
                            placeholder="Facultatif")
        colis = a1.selectbox("Colisage", COLISAGES)
        qte = a2.number_input("Quantité", min_value=0.0, step=1.0, value=1.0)
        usage = st.text_input("Usage")
        if st.form_submit_button("Ajouter l'article"):
            if not des or qte <= 0 or not usage.strip():
                st.error("Désignation, quantité et usage sont obligatoires.")
            else:
                st.session_state.lignes.append({"Désignation": des.strip(), "Référence": refa.strip(),
                                                "Code_Parc": (parc or "").strip(), "Colisage": colis,
                                                "Quantité": qte, "Usage": usage.strip()})

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


def onglet_admin(user: dict):
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
    st.markdown("##### Articles les plus demandés")
    lg2 = lg.copy()
    lg2["Quantité"] = pd.to_numeric(lg2["Quantité"], errors="coerce")
    top = (lg2.groupby(["Désignation", "Colisage"])
           .agg(Quantité=("Quantité", "sum"), DA=("ID_DA", "nunique"))
           .sort_values("DA", ascending=False).head(20).reset_index())
    st.dataframe(top, hide_index=True, width="stretch")

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
