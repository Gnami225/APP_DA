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

NOIR = colors.black  # fiche 100 % noir sur blanc : aucun aplat, économie d'encre
GRID = colors.black
HERE = Path(__file__).resolve().parent


def find_logo() -> Path | None:
    """logo.png, logo.jpg ou logo.jpeg (majuscules acceptées) à côté du code."""
    for f in sorted(HERE.iterdir()):
        if f.is_file() and f.stem.lower() == "logo" and f.suffix.lower() in (".png", ".jpg", ".jpeg"):
            return f
    return None


def _logo_flowable(max_w=38 * mm, max_h=19 * mm):
    path = find_logo()
    if not path:
        return ""
    from PIL import Image as PILImage

    with PILImage.open(path) as im:
        w, h = im.size
    scale = min(max_w / w, max_h / h)  # proportions du logo respectées
    return Image(str(path), width=w * scale, height=h * scale)
NB_LIGNES_MIN = 15  # le formulaire papier comporte 15 lignes

_s = {
    "cell": ParagraphStyle("cell", fontName="Helvetica", fontSize=8.5, leading=10),
    "cellc": ParagraphStyle("cellc", fontName="Helvetica", fontSize=8.5, leading=10, alignment=TA_CENTER),
    "lab": ParagraphStyle("lab", fontName="Helvetica-Bold", fontSize=8.5, leading=10),
    "th": ParagraphStyle("th", fontName="Helvetica-Bold", fontSize=7.5, leading=9,
                         alignment=TA_CENTER, textColor=NOIR),
    "sec": ParagraphStyle("sec", fontName="Helvetica-Bold", fontSize=10, leading=12,
                          textColor=NOIR, spaceBefore=6, spaceAfter=3),
    "t1": ParagraphStyle("t1", fontName="Helvetica-Bold", fontSize=12, leading=14, alignment=TA_CENTER),
    "t2": ParagraphStyle("t2", fontName="Helvetica-Bold", fontSize=10, leading=12, alignment=TA_CENTER),
    "ref": ParagraphStyle("ref", fontName="Helvetica", fontSize=7, leading=9),
    "small": ParagraphStyle("small", fontName="Helvetica", fontSize=7.5, leading=9, textColor=NOIR),
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
        canvas.setFillColor(NOIR)
        canvas.drawString(15 * mm, 9 * mm, f"{numero}  —  Statut : {statut}")
        canvas.drawRightString(195 * mm, 9 * mm, f"Page {doc.page}")
        if statut in ("Validée", "Rejetée"):
            canvas.setFont("Helvetica-Bold", 11)
            canvas.setFillColor(NOIR)
            canvas.drawRightString(195 * mm, 287 * mm, statut.upper())
        canvas.restoreState()

    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=12 * mm, bottomMargin=15 * mm,
                            title=numero or "Demande d'achat", author="PWC")
    story = []

    # ---- en-tête documentaire
    logo = _logo_flowable()
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


# =========================================================================== BON DE COMMANDE
from datetime import datetime

from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import landscape

from logic import fmt_montant, montant_ligne, to_num

_s.update({
    "bc_t": ParagraphStyle("bc_t", fontName="Helvetica-Bold", fontSize=13, leading=16, alignment=TA_CENTER),
    "bc_c": ParagraphStyle("bc_c", fontName="Helvetica", fontSize=7, leading=8.5, alignment=TA_CENTER),
    "bc_l": ParagraphStyle("bc_l", fontName="Helvetica", fontSize=8, leading=9.5),
    "bc_lb": ParagraphStyle("bc_lb", fontName="Helvetica-Bold", fontSize=8, leading=9.5),
    "bc_cb": ParagraphStyle("bc_cb", fontName="Helvetica-Bold", fontSize=8.5, leading=10, alignment=TA_CENTER),
    "bc_num": ParagraphStyle("bc_num", fontName="Helvetica-Bold", fontSize=11, leading=13, alignment=TA_CENTER),
    "bc_r": ParagraphStyle("bc_r", fontName="Helvetica", fontSize=8, leading=9.5, alignment=TA_RIGHT),
    "bc_rb": ParagraphStyle("bc_rb", fontName="Helvetica-Bold", fontSize=8.5, leading=10, alignment=TA_RIGHT),
    "bc_i": ParagraphStyle("bc_i", fontName="Helvetica-BoldOblique", fontSize=7.5, leading=9, alignment=TA_CENTER),
    "bc_act": ParagraphStyle("bc_act", fontName="Helvetica", fontSize=6, leading=7),
})
NB_LIGNES_BC = 30


def _pied_pwc(nom_societe: str, titre: str, statut_txt: str = ""):
    imprime = datetime.now().strftime("%d/%m/%Y %H:%M")

    def footer(canvas, doc):
        canvas.saveState()
        w, h = doc.pagesize
        canvas.setFont("Helvetica", 7)
        canvas.drawRightString(w - 12 * mm, h - 7 * mm, f"Imprimé le {imprime}")
        canvas.drawString(12 * mm, 7 * mm, nom_societe)
        canvas.drawRightString(w - 12 * mm, 7 * mm, f"{titre} — Page {doc.page}")
        if statut_txt:
            canvas.setFont("Helvetica-Bold", 9)
            canvas.drawString(12 * mm, h - 7 * mm, statut_txt)
        canvas.restoreState()
    return footer


def _bloc_logo(param: dict, max_w=52 * mm):
    return _logo_flowable(max_w=max_w, max_h=18 * mm)  # le logo PWC contient déjà les activités


def build_bc_pdf(bc: dict, lignes: list[dict], param: dict, chantier_nom: str = "") -> bytes:
    """Bon de commande au format PWC : noir sur blanc, 1 page (lignes vides jusqu'à 26)."""
    buf = io.BytesIO()
    numero = as_text(bc.get("N° BC"))
    statut = as_text(bc.get("Statut"))
    societe = as_text(param.get("Nom Société")) or "PUBLIC WORK CONTRACTOR (PWC)"
    avert = "" if statut == "Validé" else (f"BC {statut.upper()} — NE PAS TRANSMETTRE AU FOURNISSEUR"
                                           if statut in ("Rejeté", "Annulé") else "BC NON VALIDÉ — EN ATTENTE DE VALIDATION")
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=12 * mm, rightMargin=12 * mm,
                            topMargin=11 * mm, bottomMargin=12 * mm, title=numero or "Bon de commande", author=societe)
    cw = [64 * mm, 34 * mm, 13 * mm, 15 * mm, 22 * mm, 14 * mm, 24 * mm]   # 186 mm
    W1, W2, W3 = cw[0], sum(cw[1:5]), sum(cw[5:])

    # ---- en-tête
    soc = _p(f"REG. COMMERCE {as_text(param.get('Reg. Commerce'))}\nNCC : {as_text(param.get('N° CC'))}\n"
             f"{as_text(param.get('Adresse'))}\nEmail : {as_text(param.get('Email'))}", "bc_c")
    num = Table([[_p("N°", "bc_cb")], [_p(numero, "bc_num")]], colWidths=[W3], rowHeights=[11 * mm, 9 * mm])
    num.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, 0), 0.8, GRID), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    dat = Table([[_p("DATE", "bc_cb")], [_p(fmt_date(bc.get("Date BC")), "bc_cb")]], colWidths=[W3],
                rowHeights=[9 * mm, 8 * mm])
    dat.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, 0), 0.8, GRID), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    ch = as_text(bc.get("Chantier"))
    head = Table([
        [_bloc_logo(param), _p("BON DE COMMANDE", "bc_t"), num],
        [soc, _p(f"<b>CHANTIER :</b>   {chantier_nom or ch}" + (f" ({ch})" if chantier_nom and chantier_nom != ch else ""),
                 "bc_l"), dat],
        [_p(f"Tél : {as_text(param.get('Téléphone'))}", "bc_l"),
         _p(f"<b>CODE PARC :</b>   {as_text(bc.get('Code Parc'))}", "bc_l"),
         _p(f"<b>Magasin :</b> {as_text(bc.get('Magasin'))}", "bc_l")],
        [_p(f"<b>Fournisseur :</b>   {as_text(bc.get('Fournisseur'))}", "bc_l"),
         _p(f"<b>DA N° :</b>   {as_text(bc.get('N° DA'))}", "bc_l"), ""],
    ], colWidths=[W1, W2, W3], rowHeights=[21 * mm, 17 * mm, 8 * mm, 8 * mm])
    head.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.8, GRID), ("INNERGRID", (0, 0), (-1, -1), 0.8, GRID),
        ("SPAN", (1, 3), (2, 3)), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (2, 0), (2, 1), 0), ("RIGHTPADDING", (2, 0), (2, 1), 0),
        ("TOPPADDING", (2, 0), (2, 1), 0), ("BOTTOMPADDING", (2, 0), (2, 1), 0),
    ]))
    # « &nbsp; » pour les balises <b> : _p échappe le texte, on passe donc par Paragraph direct
    for (r, c) in [(1, 1), (2, 1), (2, 2), (3, 0), (3, 1)]:
        cell = head._cellvalues[r][c]
        cell.__init__(cell.text.replace("&lt;b&gt;", "<b>").replace("&lt;/b&gt;", "</b>"), cell.style)
    story = [head, Spacer(1, 3 * mm)]

    # ---- lignes
    th = ["DÉSIGNATION", "RÉFÉRENCE", "QTÉ", "UNITÉ", "PRIX UNIT.\n(FCFA)", "REM. %", "MONTANT\n(FCFA)"]
    data = [[_p(h, "th") for h in th]]
    lignes = sorted(lignes, key=lambda l: to_num(l.get("N° Ligne")))
    for l in lignes:
        rem = to_num(l.get("Remise %"))
        data.append([_p(l.get("Désignation"), "bc_l"), _p(l.get("Référence"), "bc_c"),
                     _p(_qte(l.get("Quantité")), "bc_c"), _p(l.get("Unité"), "bc_c"),
                     _p(fmt_montant(l.get("Prix unitaire HT")), "bc_r"), _p(_qte(rem) if rem else "", "bc_c"),
                     _p(fmt_montant(montant_ligne(l.get("Quantité"), l.get("Prix unitaire HT"), rem)), "bc_r")])
    for _ in range(len(lignes), NB_LIGNES_BC):
        data.append([""] * 7)
    det = Table(data, colWidths=cw, repeatRows=1)
    det.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.6, GRID), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 1), (-1, -1), 1.5), ("BOTTOMPADDING", (0, 1), (-1, -1), 1.5),
        ("ROWHEIGHTS", (0, 1), (-1, -1), 5 * mm),
    ]))
    for i in range(1, len(data)):
        det._argH[i] = det._argH[i] or 5.2 * mm
    story.append(det)

    # ---- pied : conditions, mentions, totaux, signature
    taux = to_num(bc.get("Taux TVA"))
    lib_tva = f"MASSE TVA ({_qte(taux)} %)" if taux else "MASSE TVA"
    visa = "Signature"
    if statut == "Validé":
        visa = f"Signature\n\nValidé par {as_text(bc.get('Validé par'))}\nle {fmt_date(bc.get('Date validation'))}"
    cond = _p(f"<b>Conditions de règlement :</b> {as_text(bc.get('Condition règlement'))}", "bc_l")
    cond.__init__(cond.text.replace("&lt;b&gt;", "<b>").replace("&lt;/b&gt;", "</b>"), cond.style)
    obs = as_text(bc.get("Observations"))
    pied = Table([
        [cond, "", _p("TOTAL HT", "bc_rb"), _p(fmt_montant(bc.get("Total HT")), "bc_rb")],
        [_p(f"Observations : {obs}" if obs else "", "bc_l"), "", _p(lib_tva, "bc_rb"),
         _p(fmt_montant(bc.get("Montant TVA")), "bc_rb")],
        ["", "", _p("TOTAL TTC", "bc_rb"), _p(fmt_montant(bc.get("Total TTC")), "bc_rb")],
        [_p("Mentionner T.V.A sur facture\nExemplaire à joindre à la facture", "bc_i"), "", _p(visa, "bc_i"), ""],
    ], colWidths=[sum(cw[:3]), cw[3], cw[4] + cw[5], cw[6]],
       rowHeights=[6 * mm, 6 * mm, 6 * mm, 20 * mm])
    pied.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.8, GRID),
        ("GRID", (2, 0), (3, 2), 0.6, GRID),
        ("LINEABOVE", (0, 3), (-1, 3), 0.8, GRID), ("LINEBEFORE", (2, 3), (2, 3), 0.8, GRID),
        ("SPAN", (0, 0), (1, 0)), ("SPAN", (0, 1), (1, 2)), ("SPAN", (0, 3), (1, 3)), ("SPAN", (2, 3), (3, 3)),
        ("VALIGN", (0, 0), (-1, 2), "MIDDLE"), ("VALIGN", (0, 3), (-1, 3), "TOP"), ("VALIGN", (0, 1), (1, 2), "TOP"),
    ]))
    story.append(pied)
    pied_page = _pied_pwc(societe, numero, avert)
    doc.build(story, onFirstPage=pied_page, onLaterPages=pied_page)
    return buf.getvalue()


# =========================================================================== TABLEAU COMPARATIF DES PRIX
def build_tcp_pdf(tcp: dict, fournisseurs: list[dict], lignes: list[dict], param: dict,
                  chantier_nom: str = "") -> bytes:
    """fournisseurs : [{Nom, Délai, Condition, Observation}] ; lignes : [{Désignation, Référence, Unité,
    Quantité, prix: {nom fournisseur: PU}}]. A4 paysage, noir sur blanc."""
    buf = io.BytesIO()
    societe = as_text(param.get("Nom Société")) or "PUBLIC WORK CONTRACTOR (PWC)"
    numero = as_text(tcp.get("N° TCP"))
    taux = to_num(tcp.get("Taux TVA", param.get("Taux TVA (%)")))
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=10 * mm, rightMargin=10 * mm,
                            topMargin=11 * mm, bottomMargin=12 * mm, title=numero or "TCP", author=societe)
    W = 277 * mm
    noms = [as_text(f["Nom"]) for f in fournisseurs]
    nf = max(len(noms), 1)
    fixe = [8 * mm, 62 * mm, 32 * mm, 13 * mm, 13 * mm]
    reste = W - sum(fixe)
    cw = fixe + [reste / nf * 0.42, reste / nf * 0.58] * nf

    num = Table([[_p("N°", "bc_cb")], [_p(numero, "bc_num")]], colWidths=[60 * mm], rowHeights=[8 * mm, 10 * mm])
    num.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, 0), 0.8, GRID), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    head = Table([[_bloc_logo(param), _p("TABLEAU COMPARATIF DES PRIX (TCP)", "bc_t"), num]],
                 colWidths=[60 * mm, W - 120 * mm, 60 * mm], rowHeights=[20 * mm])
    head.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.8, GRID), ("INNERGRID", (0, 0), (-1, -1), 0.8, GRID),
                              ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                              ("LEFTPADDING", (2, 0), (2, 0), 0), ("RIGHTPADDING", (2, 0), (2, 0), 0),
                              ("TOPPADDING", (2, 0), (2, 0), 0), ("BOTTOMPADDING", (2, 0), (2, 0), 0)]))
    ch = as_text(tcp.get("Chantier"))
    lab = lambda t: _p(t, "bc_lb")
    info = Table([[lab("DA N°"), _p(tcp.get("N° DA"), "bc_l"), lab("CHANTIER"), _p(chantier_nom or ch, "bc_l"),
                   lab("DATE"), _p(fmt_date(tcp.get("Date TCP")), "bc_l"), lab("ACHETEUR"), _p(tcp.get("Acheteur"), "bc_l")]],
                 colWidths=[18 * mm, 45 * mm, 22 * mm, 70 * mm, 15 * mm, 25 * mm, 24 * mm, 58 * mm])
    info.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.8, GRID), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    story = [head, Spacer(1, 2 * mm), info, Spacer(1, 3 * mm)]

    # ---- tableau des offres
    h1 = [_p(t, "th") for t in ["N°", "DÉSIGNATION", "RÉFÉRENCE", "UNITÉ", "QTÉ"]]
    h2 = ["", "", "", "", ""]
    for n in noms:
        h1 += [_p(n, "th"), ""]
        h2 += [_p("P.U. HT", "th"), _p("MONTANT HT", "th")]
    data = [h1, h2]
    tot = {n: 0.0 for n in noms}
    gras = []
    for i, l in enumerate(lignes, start=1):
        prix = {n: to_num(l["prix"].get(n)) for n in noms}
        valides = {n: v for n, v in prix.items() if v > 0}
        mini = min(valides.values()) if valides else None
        row = [_p(str(i), "bc_c"), _p(l.get("Désignation"), "bc_l"), _p(l.get("Référence"), "bc_c"),
               _p(l.get("Unité"), "bc_c"), _p(_qte(l.get("Quantité")), "bc_c")]
        for j, n in enumerate(noms):
            pu = prix[n]
            mt = montant_ligne(l.get("Quantité"), pu)
            tot[n] += mt
            row += [_p(fmt_montant(pu) if pu else "—", "bc_r"), _p(fmt_montant(mt) if pu else "—", "bc_r")]
            if mini is not None and pu == mini:
                gras.append((5 + 2 * j, len(data)))
        data.append(row)
    n_l = len(data)
    def ligne_tot(label, valeurs, style="bc_rb"):
        r = ["", _p(label, "bc_rb"), "", "", ""]
        for v in valeurs:
            r += ["", _p(v, style)]
        return r
    tva = {n: round(tot[n] * taux / 100) for n in noms}
    data.append(ligne_tot("TOTAL HT", [fmt_montant(tot[n]) for n in noms]))
    data.append(ligne_tot(f"TVA ({_qte(taux)} %)", [fmt_montant(tva[n]) for n in noms], "bc_r"))
    data.append(ligne_tot("TOTAL TTC", [fmt_montant(tot[n] + tva[n]) for n in noms]))
    for lib, cle in [("Délai de livraison", "Délai"), ("Conditions de règlement", "Condition"), ("Observations", "Observation")]:
        r = ["", _p(lib, "bc_lb"), "", "", ""]
        for f in fournisseurs:
            r += [_p(f.get(cle), "bc_c"), ""]
        data.append(r)
    t = Table(data, colWidths=cw, repeatRows=2)
    st_ = [("GRID", (0, 0), (-1, -1), 0.6, GRID), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
           ("SPAN", (0, 0), (0, 1)), ("SPAN", (1, 0), (1, 1)), ("SPAN", (2, 0), (2, 1)),
           ("SPAN", (3, 0), (3, 1)), ("SPAN", (4, 0), (4, 1))]
    for j in range(nf):
        c = 5 + 2 * j
        st_.append(("SPAN", (c, 0), (c + 1, 0)))
        for r in range(n_l + 3, n_l + 6):
            st_.append(("SPAN", (c, r), (c + 1, r)))
    for r in range(n_l, n_l + 6):
        st_.append(("SPAN", (1, r), (4, r)))
    st_.append(("LINEABOVE", (0, n_l), (-1, n_l), 1.2, GRID))
    for c, r in gras:  # meilleur prix de la ligne : cadre épais
        st_.append(("BOX", (c, r), (c + 1, r), 1.6, GRID))
    t.setStyle(TableStyle(st_))
    story.append(t)

    # ---- synthèse et visas
    retenu = as_text(tcp.get("Fournisseur retenu"))
    moins = as_text(tcp.get("Moins-disant"))
    synth = (f"<b>Moins-disant :</b> {moins} ({fmt_montant(tcp.get('Montant moins-disant HT'))} FCFA HT)"
             f"&nbsp;&nbsp;&nbsp;&nbsp;<b>Fournisseur retenu :</b> {retenu} "
             f"({fmt_montant(tcp.get('Montant retenu HT'))} FCFA HT)")
    motif = as_text(tcp.get("Motif choix"))
    story += [Spacer(1, 3 * mm), Paragraph(synth, _s["bc_l"])]
    if motif:
        story.append(Paragraph(f"<b>Motif du choix :</b> {_p(motif).text}", _s["bc_l"]))
    story.append(Paragraph("Le cadre épais signale le meilleur prix de chaque ligne.", _s["small"]))
    visas = Table([[_p("ÉTABLI PAR (ACHATS)", "th"), _p("CONTRÔLE", "th"), _p("VALIDATION (DIRECTION)", "th")],
                   [_p(f"Nom : {as_text(tcp.get('Acheteur'))}\nDate : {fmt_date(tcp.get('Date TCP'))}\nSignature :"),
                    _p("Nom :\nDate :\nSignature :"), _p("Nom :\nDate :\nSignature :")]],
                  colWidths=[W / 3] * 3, rowHeights=[None, 20 * mm])
    visas.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.8, GRID), ("VALIGN", (0, 1), (-1, 1), "TOP")]))
    story += [Spacer(1, 3 * mm), visas]
    pied = _pied_pwc(societe, numero)
    doc.build(story, onFirstPage=pied, onLaterPages=pied)
    return buf.getvalue()
