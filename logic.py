"""Règles métier : numérotation, création et validation des DA."""
from __future__ import annotations

import re
import uuid
from datetime import date, datetime

import pandas as pd


def as_text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def is_true(v) -> bool:
    return as_text(v).upper() in {"TRUE", "VRAI", "1", "OUI", "YES"}


def to_date(v):
    """Accepte date, datetime, texte ISO ou JJ/MM/AAAA, ou numéro de série Sheets."""
    if v in ("", None):
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, (int, float)):  # numéro de série Google Sheets / Excel
        return (pd.Timestamp("1899-12-30") + pd.Timedelta(days=float(v))).date()
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%d/%m/%Y", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s[:19] if "T" in s or " " in s else s, fmt).date()
        except ValueError:
            continue
    return None


def fmt_date(v) -> str:
    d = to_date(v)
    return d.strftime("%d/%m/%Y") if d else ""


def next_numero(da: pd.DataFrame, chantier: str, annee: int) -> str:
    """DA-<CHANTIER>-<AAAA>-<NNNN>, séquence par chantier et par année."""
    prefix = f"DA-{chantier}-{annee}-"
    seqs = [0]
    for n in da.get("N° DA", pd.Series(dtype=str)).map(as_text):
        if n.startswith(prefix):
            m = re.search(r"(\d+)$", n)
            if m:
                seqs.append(int(m.group(1)))
    return f"{prefix}{max(seqs) + 1:04d}"


def build_da(store, entete: dict, lignes: list[dict], user: dict) -> tuple[dict, list[dict]]:
    """Écrit une DA et ses lignes. Retourne (ligne DA, lignes articles) telles qu'archivées."""
    da_df = store.read("DA")
    d_da = entete["Date DA"]
    numero = next_numero(da_df, entete["Chantier"], d_da.year)
    id_da = uuid.uuid4().hex[:8]
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    da_row = {
        "ID_DA": id_da,
        "N° DA": numero,
        "Date DA": d_da.isoformat(),
        "Chantier": entete["Chantier"],
        "Code DA": entete.get("Code DA", ""),
        "Service Demandeur": entete["Service Demandeur"],
        "Demandeur": entete["Demandeur"],
        "Email Demandeur": user.get("Email", ""),
        "Responsable": entete["Responsable"],
        "Priorité": entete.get("Priorité", "Normale"),
        "Date besoin": entete["Date besoin"].isoformat() if entete.get("Date besoin") else "",
        "Observations": entete.get("Observations", ""),
        "Signature Demandeur": "",
        "Statut": "Émise",
        "Validé par": "",
        "Date validation": "",
        "Motif rejet": "",
        "Créé le": now,
    }
    rows = []
    for i, l in enumerate(lignes, start=1):
        rows.append({
            "ID_Ligne": uuid.uuid4().hex[:10],
            "ID_DA": id_da,
            "N° Ligne": i,
            "Code_Parc": l.get("Code_Parc", ""),
            "Code Article": l.get("Code Article", ""),
            "Désignation": l["Désignation"],
            "Référence": l.get("Référence", ""),
            "Colisage": l.get("Colisage", ""),
            "Quantité": float(l["Quantité"]),
            "Usage": l.get("Usage", ""),
            "DA_Numero": numero,
            "DA_Date": d_da.isoformat(),
            "DA_Chantier": entete["Chantier"],
            "DA_Service": entete["Service Demandeur"],
            "Statut ligne": "À commander",
        })
    # lignes d'abord : l'en-tête n'apparaît qu'une fois ses articles écrits
    store.append("HISTORIQUE_ARTICLES", rows)
    store.append("DA", [da_row])
    return da_row, rows


def decide(store, id_da: str, statut: str, valideur: str, motif: str = ""):
    values = {"Statut": statut, "Validé par": valideur,
              "Date validation": date.today().isoformat()}
    if statut == "Rejetée":
        values["Motif rejet"] = motif
    store.update("DA", "ID_DA", id_da, values)


def send_email(cfg: dict, to: list[str], subject: str, body: str,
               pdf_bytes: bytes | None = None, pdf_name: str = "DA.pdf") -> bool:
    """Envoi SMTP facultatif (ex. Gmail + mot de passe d'application). False si non configuré."""
    to = [t for t in to if t and "@" in t]
    if not cfg or not cfg.get("host") or not to:
        return False
    import smtplib
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["From"] = cfg.get("sender", cfg.get("user"))
    msg["To"] = ", ".join(dict.fromkeys(to))
    msg["Subject"] = subject
    msg.set_content(body)
    if pdf_bytes:
        msg.add_attachment(pdf_bytes, maintype="application", subtype="pdf", filename=pdf_name)
    with smtplib.SMTP(cfg["host"], int(cfg.get("port", 587)), timeout=20) as s:
        s.starttls()
        s.login(cfg["user"], cfg["password"])
        s.send_message(msg)
    return True
