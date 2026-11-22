"""Stockage des documents fournisseurs (BL, devis, factures) dans le Google Drive de PWC.

Le compte de service Google n'a pas d'espace Drive : les fichiers passent par un petit script Google Apps Script
installé sur le compte Gmail de PWC (fichier apps_script_documents.gs), qui les range dans le dossier
« PWC – Documents achats / AAAA / MM » et renvoie leur identifiant. Le Sheet ne garde que la fiche (table DOCUMENTS).

Secrets :
    [drive]
    url = "https://script.google.com/macros/s/…/exec"
    token = "le jeton choisi dans le script"
Sans cette section, les documents sont lus et rapprochés mais le fichier n'est pas archivé.
"""
from __future__ import annotations

import base64
import hashlib
import io

import streamlit as st

from logic import as_text, maintenant, nouvel_id


class DocErreur(Exception):
    pass


def _conf() -> dict:
    try:
        return dict(st.secrets.get("drive", {}))
    except Exception:
        return {}


def configure() -> bool:
    c = _conf()
    return bool(c.get("url") and c.get("token"))


def empreinte(contenu: bytes) -> str:
    return hashlib.sha256(contenu).hexdigest()[:32]


def preparer(nom: str, contenu: bytes) -> tuple[str, bytes, str]:
    """Photo -> JPEG compressé (≈ 300 Ko, lisible) ; PDF inchangé. Renvoie (nom, octets, type MIME)."""
    if nom.lower().endswith(".pdf"):
        return nom, contenu, "application/pdf"
    from PIL import Image, ImageOps
    im = ImageOps.exif_transpose(Image.open(io.BytesIO(contenu))).convert("RGB")
    im.thumbnail((2000, 2000))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=80, optimize=True)
    base = nom.rsplit(".", 1)[0]
    return f"{base}.jpg", buf.getvalue(), "image/jpeg"


def envoyer(nom: str, contenu: bytes, mime: str, dossier: str, description: str = "") -> tuple[str, str]:
    """Envoie le fichier dans Drive. Renvoie (id Drive, lien)."""
    import requests
    c = _conf()
    if not configure():
        raise DocErreur("Archivage Drive non configuré (section [drive] des secrets).")
    try:
        r = requests.post(c["url"], json={"token": c["token"], "nom": nom, "mime": mime, "dossier": dossier,
                                          "description": description,
                                          "data": base64.b64encode(contenu).decode()}, timeout=90)
        d = r.json()
    except Exception as e:
        raise DocErreur(f"Envoi vers Google Drive impossible ({e}).")
    if not d.get("ok"):
        raise DocErreur(f"Google Drive a refusé le fichier : {d.get('erreur', 'erreur inconnue')}.")
    return as_text(d.get("id")), as_text(d.get("url"))


@st.cache_data(show_spinner=False, max_entries=30, ttl=3600)
def telecharger(id_drive: str) -> tuple[str, bytes, str]:
    """Récupère un fichier archivé (nom, octets, MIME) — sans compte Google côté utilisateur."""
    import requests
    c = _conf()
    if not configure() or not id_drive:
        raise DocErreur("Document non archivé dans Drive.")
    try:
        d = requests.get(c["url"], params={"token": c["token"], "id": id_drive}, timeout=90).json()
    except Exception as e:
        raise DocErreur(f"Lecture depuis Google Drive impossible ({e}).")
    if not d.get("ok"):
        raise DocErreur(f"Google Drive : {d.get('erreur', 'fichier introuvable')}.")
    return as_text(d.get("nom")), base64.b64decode(d.get("data", "")), as_text(d.get("mime"))


def fiche(ctx, champs: dict) -> dict:
    """Ligne de la table DOCUMENTS."""
    return {"ID_Document": nouvel_id(10), "Saisi par": ctx.user["Nom"], "Créé le": maintenant(), **champs}


def bouton_telecharger(ctx, d: dict, key: str):
    """Bouton « ouvrir » d'un document archivé (téléchargé à la demande)."""
    idd = as_text(d.get("ID Drive"))
    if not idd:
        st.caption("Fichier non archivé (Drive non configuré lors de l'import).")
        return
    if st.button("📄 Ouvrir le document", key=f"{key}-get"):
        try:
            st.session_state[f"{key}-f"] = telecharger(idd)
        except DocErreur as e:
            st.error(str(e))
    f = st.session_state.get(f"{key}-f")
    if f:
        st.download_button(f"Télécharger {f[0]}", f[1], file_name=f[0], mime=f[2], key=f"{key}-dl")
