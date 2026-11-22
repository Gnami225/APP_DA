"""Dossier d'achat d'une DA : chronologie DA → validations → TCP → BC → livraisons / BL, et liasse PDF complète
(DA, TCP, BC générés à la demande + documents fournisseurs archivés dans Drive)."""
from __future__ import annotations

import io

import pandas as pd
import streamlit as st

import documents
from logic import as_text, fmt_date, fmt_montant, to_num


def _chronologie(ctx, id_da: str) -> tuple[pd.DataFrame, dict]:
    da = ctx.load("DA")
    d = da.loc[da["ID_DA"].map(as_text) == id_da].iloc[0].to_dict()
    ev = [(as_text(d["Créé le"]), "DA", f"Émise par {as_text(d['Demandeur'])}", as_text(d["N° DA"]))]
    j = ctx.load("VALIDATIONS_DA")
    for _, r in j.loc[j["ID_DA"].map(as_text) == id_da].iterrows():
        ev.append((as_text(r["Date"]), "Validation", f"{as_text(r['Niveau'])} : {as_text(r['Décision'])} "
                   f"({as_text(r['Par'])})" + (f" — {as_text(r['Commentaire'])}" if as_text(r["Commentaire"]) else ""),
                   ""))
    tcp = ctx.load("TCP")
    tcp = tcp.loc[tcp["ID_DA"].map(as_text) == id_da]
    for _, r in tcp.iterrows():
        ev.append((as_text(r["Créé le"]) or as_text(r["Date TCP"]), "TCP",
                   f"{as_text(r['Fournisseurs consultés'])} → retenu(s) : {as_text(r['Fournisseur retenu'])}",
                   as_text(r["N° TCP"])))
    bc = ctx.load("BC")
    bc = bc.loc[bc["ID_DA"].map(as_text) == id_da]
    for _, r in bc.iterrows():
        ev.append((as_text(r["Créé le"]) or as_text(r["Date BC"]), "BC",
                   f"{as_text(r['Fournisseur'])} · {fmt_montant(to_num(r['Total TTC']))} F TTC · {as_text(r['Statut'])}",
                   as_text(r["N° BC"])))
        if as_text(r["Date validation"]):
            ev.append((as_text(r["Date validation"]), "Validation BC",
                       f"{as_text(r['Statut'])} par {as_text(r['Validé par'])}", as_text(r["N° BC"])))
    lv = ctx.load("LIVRAISONS")
    lv = lv.loc[lv["ID_BC"].map(as_text).isin(set(bc["ID_BC"].map(as_text))).astype(bool)]
    for (dt, bl, nbc), g in lv.groupby([lv["Date livraison"].map(as_text), lv["N° BL"].map(as_text),
                                        lv["N° BC"].map(as_text)]):
        ev.append((dt, "Livraison", f"{len(g)} ligne(s) reçue(s)" + (f" — BL {bl}" if bl else "")
                   + (" — " + "; ".join(sorted(set(x for x in g["Écart"].map(as_text) if x and not x.startswith("Conforme"))))
                      if g["Écart"].map(as_text).str.startswith(("Partiel", "Surplus")).any() else ""), nbc))
    t = pd.DataFrame(ev, columns=["Date", "Étape", "Détail", "Pièce"])
    t["_tri"] = t["Date"].str[:19]
    t = t.sort_values("_tri").drop(columns="_tri")
    t["Date"] = t["Date"].map(lambda x: fmt_date(x[:10]) + (f" {x[11:16]}" if len(x) >= 16 else ""))
    docs = ctx.load("DOCUMENTS")
    docs = docs.loc[docs["ID_DA"].map(as_text) == id_da]
    return t, {"da": d, "tcp": tcp, "bc": bc, "docs": docs}


def _image_en_pdf(octets: bytes) -> bytes:
    from PIL import Image
    im = Image.open(io.BytesIO(octets)).convert("RGB")
    buf = io.BytesIO()
    im.save(buf, "PDF", resolution=150)
    return buf.getvalue()


def liasse(ctx, p: dict) -> tuple[bytes, list[str]]:
    """Fusionne DA, TCP, BC et documents archivés en un seul PDF. Renvoie (pdf, avertissements)."""
    from pypdf import PdfWriter
    from achats import pdf_bc, pdf_tcp
    from pdf import build_pdf
    lg = ctx.load("HISTORIQUE_ARTICLES")
    w, alertes = PdfWriter(), []
    pieces = [("DA", build_pdf(p["da"], lg.loc[lg["ID_DA"].map(as_text) == as_text(p["da"]["ID_DA"])]
                               .to_dict("records"), ctx.refs()))]
    pieces += [(f"TCP {as_text(r['N° TCP'])}", pdf_tcp(ctx, r.to_dict())) for _, r in p["tcp"].iterrows()]
    pieces += [(f"BC {as_text(r['N° BC'])}", pdf_bc(ctx, r.to_dict())) for _, r in p["bc"].iterrows()
               if as_text(r["Statut"]) != "Annulé"]
    for _, r in p["docs"].sort_values("Créé le").iterrows():
        nom = f"{as_text(r['Type'])} {as_text(r['N° document'])}"
        if not as_text(r["ID Drive"]):
            alertes.append(f"{nom} : fichier non archivé, absent de la liasse.")
            continue
        try:
            _n, octets, mime = documents.telecharger(as_text(r["ID Drive"]))
            pieces.append((nom, octets if "pdf" in mime else _image_en_pdf(octets)))
        except documents.DocErreur as e:
            alertes.append(f"{nom} : {e}")
    for nom, b in pieces:
        try:
            w.append(io.BytesIO(b), outline_item=nom)
        except Exception as e:
            alertes.append(f"{nom} : non fusionné ({e}).")
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue(), alertes


def vue_dossier(ctx, id_da: str, key: str):
    key = f"{key}-{id_da}"
    with st.expander("📁 Dossier d'achat (DA → TCP → BC → livraisons) et liasse PDF"):
        t, p = _chronologie(ctx, id_da)
        st.dataframe(t, hide_index=True, width="stretch")
        if len(p["docs"]):
            st.markdown("###### Documents fournisseurs")
            lab = dict(zip(p["docs"]["ID_Document"].map(as_text),
                           p["docs"]["Type"].map(as_text) + " " + p["docs"]["N° document"].map(as_text) + " · "
                           + p["docs"]["N° BC"].map(as_text) + " · " + p["docs"]["Date document"].map(fmt_date)))
            sel = st.selectbox("Document", list(lab), format_func=lab.get, index=None, key=f"{key}-dossier-doc")
            if sel:
                documents.bouton_telecharger(ctx, p["docs"].loc[p["docs"]["ID_Document"].map(as_text) == sel]
                                             .iloc[0].to_dict(), f"{key}-dd-{sel}")
        n = 1 + len(p["tcp"]) + len(p["bc"]) + len(p["docs"])
        if st.button(f"📚 Préparer la liasse complète ({n} pièce(s))", key=f"{key}-liasse"):
            with st.spinner("Assemblage de la liasse…"):
                st.session_state[f"{key}-liasse-pdf"] = liasse(ctx, p)
        res = st.session_state.get(f"{key}-liasse-pdf")
        if res:
            for a in res[1]:
                st.caption(f"⚠ {a}")
            st.download_button("Télécharger la liasse (PDF)", res[0], type="primary", mime="application/pdf",
                               file_name=f"Liasse_{as_text(p['da']['N° DA'])}.pdf", key=f"{key}-liasse-dl")
