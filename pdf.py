"""Génération de la fiche DA au format du modèle PWC (SC/ADM/LOG/QHSE/TECH-PWC-ExB-01)."""
from __future__ import annotations

import io
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (Image, Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

from logic import as_text, fmt_date

ORANGE = colors.HexColor("#F29200")
DARK = colors.HexColor("#3A3A3A")
LIGHT = colors.HexColor("#F2F2F2")
GRID = colors.black
LOGO = Path(__file__).resolve().parent / "logo.png"
NB_LIGNES_MIN = 15  # le formulaire papier comporte 15 lignes

_s = {
    "cell": ParagraphStyle("cell", fontName="Helvetica", fontSize=8.5, leading=10),
    "cellc": ParagraphStyle("cellc", fontName="Helvetica", fontSize=8.5, leading=10, alignment=TA_CENTER),
    "lab": ParagraphStyle("lab", fontName="Helvetica-Bold", fontSize=8.5, leading=10),
    "th": ParagraphStyle("th", fontName="Helvetica-Bold", fontSize=7.5, leading=9,
                         alignment=TA_CENTER, textColor=colors.white),
    "sec": ParagraphStyle("sec", fontName="Helvetica-Bold", fontSize=10, leading=12,
                          textColor=ORANGE, spaceBefore=6, spaceAfter=3),
    "t1": ParagraphStyle("t1", fontName="Helvetica-Bold", fontSize=12, leading=14, alignment=TA_CENTER),
    "t2": ParagraphStyle("t2", fontName="Helvetica-Bold", fontSize=10, leading=12, alignment=TA_CENTER),
    "ref": ParagraphStyle("ref", fontName="Helvetica", fontSize=7, leading=9),
    "small": ParagraphStyle("small", fontName="Helvetica", fontSize=7.5, leading=9, textColor=colors.grey),
}


def _p(text, style="cell"):
    t = as_text(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br/>")
    return Paragraph(t, _s[style])


def _qte(v) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return as_text(v)
    return (f"{f:,.3f}".rstrip("0").rstrip(".")).replace(",", " ").replace(".", ",")


def build_pdf(da: dict, lignes: list[dict], refs: dict) -> bytes:
    """da : ligne de la table DA ; lignes : ses articles ; refs : libellés chantiers / services."""
    buf = io.BytesIO()
    W = 180 * mm
    statut = as_text(da.get("Statut"))
    numero = as_text(da.get("N° DA"))

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.grey)
        canvas.drawString(15 * mm, 9 * mm, f"{numero}  —  Statut : {statut}")
        canvas.drawRightString(195 * mm, 9 * mm, f"Page {doc.page}")
        if statut in ("Validée", "Rejetée"):
            canvas.setFont("Helvetica-Bold", 11)
            canvas.setFillColor(colors.HexColor("#1E7B34") if statut == "Validée" else colors.HexColor("#B3261E"))
            canvas.drawRightString(195 * mm, 287 * mm, statut.upper())
        canvas.restoreState()

    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=12 * mm, bottomMargin=15 * mm,
                            title=numero or "Demande d'achat", author="PWC")
    story = []

    # ---- en-tête documentaire
    logo = Image(str(LOGO), width=38 * mm, height=38 * mm * 60 / 130) if LOGO.exists() else ""
    ref = _p("Référence : SC/ADM/LOG/QHSE/TECH-PWC-ExB-01\nCréation : 05/11/2025\nRévision :\n"
             "Accessibilité : Tous les services\nVersion : 01", "ref")
    head = Table([[logo, _p("ASSURER LES COMMANDES", "t1"), ref],
                  ["", _p("FICHE D’EXPRESSION DE BESOIN OU DE DEMANDE D’ACHAT", "t2"), ""]],
                 colWidths=[42 * mm, 88 * mm, 50 * mm], rowHeights=[11 * mm, 11 * mm])
    head.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.8, GRID),
        ("SPAN", (0, 0), (0, 1)), ("SPAN", (2, 0), (2, 1)),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("ALIGN", (0, 0), (0, 1), "CENTER"),
    ]))
    story += [head, Paragraph("INFORMATIONS DE LA DEMANDE", _s["sec"])]

    chantier = as_text(da.get("Chantier"))
    service = as_text(da.get("Service Demandeur"))
    info = Table([
        [_p("DEMANDE D'ACHAT N°", "lab"), _p(numero, "lab"),
         _p("CHANTIER", "lab"), _p(refs.get("chantiers", {}).get(chantier, chantier))],
        [_p("DATE", "lab"), _p(fmt_date(da.get("Date DA"))), _p("CODE DA", "lab"), _p(da.get("Code DA"))],
        [_p("SERVICE DEMANDEUR", "lab"), _p(refs.get("services", {}).get(service, service)),
         _p("NOM DU RESPONSABLE DU SERVICE DEMANDEUR", "lab"), _p(da.get("Responsable"))],
        [_p("PRIORITÉ", "lab"), _p(da.get("Priorité")), _p("DATE DE BESOIN", "lab"), _p(fmt_date(da.get("Date besoin")))],
    ], colWidths=[38 * mm, 52 * mm, 38 * mm, 52 * mm])
    info.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.8, GRID),
        ("BACKGROUND", (0, 0), (0, -1), LIGHT), ("BACKGROUND", (2, 0), (2, -1), LIGHT),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story += [info, Paragraph("DÉTAIL DES BESOINS", _s["sec"])]

    # ---- lignes d'articles
    th = ["N°", "CODE PARC", "DÉSIGNATION", "RÉFÉRENCE", "COLISAGE (unité / paquet / ml / kg)",
          "QUANTITÉ", "USAGE"]
    data = [[_p(h, "th") for h in th]]
    lignes = sorted(lignes, key=lambda l: float(l.get("N° Ligne") or 0))
    for l in lignes:
        data.append([_p(as_text(l.get("N° Ligne")), "cellc"), _p(l.get("Code_Parc"), "cellc"),
                     _p(l.get("Désignation")), _p(l.get("Référence"), "cellc"),
                     _p(l.get("Colisage"), "cellc"), _p(_qte(l.get("Quantité")), "cellc"),
                     _p(l.get("Usage"))])
    for n in range(len(lignes) + 1, max(NB_LIGNES_MIN, len(lignes)) + 1):
        data.append([_p(str(n), "cellc"), "", "", "", "", "", ""])
    det = Table(data, colWidths=[9 * mm, 20 * mm, 52 * mm, 25 * mm, 22 * mm, 18 * mm, 34 * mm],
                repeatRows=1)
    det.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.8, GRID),
        ("BACKGROUND", (0, 0), (-1, 0), DARK),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 1), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 4),
    ]))
    story += [det]

    # ---- visas
    story.append(Paragraph("VISAS", _s["sec"]))
    v_dem = _p(f"Nom : {as_text(da.get('Demandeur'))}\nDate : {fmt_date(da.get('Date DA'))}\nSignature :")
    v_resp = _p(f"Nom : {as_text(da.get('Responsable'))}\nDate :\nSignature :")
    v_val = _p(f"Nom : {as_text(da.get('Validé par'))}\nDate : {fmt_date(da.get('Date validation'))}\nSignature :")
    visas = Table([[_p(h, "th") for h in ["LE DEMANDEUR", "RESPONSABLE DU SERVICE DEMANDEUR",
                                           "VALIDATION (DIRECTION / ACHATS)"]],
                   [v_dem, v_resp, v_val]],
                  colWidths=[60 * mm] * 3, rowHeights=[None, 30 * mm])
    visas.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.8, GRID),
        ("BACKGROUND", (0, 0), (-1, 0), DARK),
        ("VALIGN", (0, 0), (-1, 0), "MIDDLE"), ("VALIGN", (0, 1), (-1, 1), "TOP"),
    ]))
    story.append(visas)

    obs = as_text(da.get("Observations"))
    if obs:
        story += [Spacer(1, 3 * mm), _p(f"Observations : {obs}")]
    if statut == "Rejetée" and as_text(da.get("Motif rejet")):
        story += [Spacer(1, 2 * mm), _p(f"Motif du rejet : {as_text(da.get('Motif rejet'))}")]

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()
