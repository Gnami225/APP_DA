"""Graphiques des analyses (Altair, livré avec Streamlit : aucune dépendance en plus).

Règles : une couleur par série dans un ordre fixe (jamais recyclée), au-delà de 7 séries regroupement en « Autres »,
barres fines aux bouts arrondis, grille discrète, info-bulle au survol, tableau des valeurs disponible sous chaque
graphique."""
from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

BLEU = "#2a78d6"
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6"]
AUTRES = "#a3a29c"
MAX_SERIES = 7
# axes compacts à la française : 680k, 1,2M, 3Md
_AXE = "replace(replace(format(datum.value, '~s'), 'G', 'Md'), '.', ',')"


def _fr(v, dec: int = 0) -> str:
    """Nombre au format français (espace pour les milliers)."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return ""
    if x != x:
        return ""
    t = f"{x:,.{dec}f}".replace(",", " ").replace(".", ",")
    return t.rstrip("0").rstrip(",") if dec else t


def _axe(entier: bool) -> alt.Axis:
    return alt.Axis(labelExpr=_AXE, tickCount=6, tickMinStep=1 if entier else alt.Undefined)


def _base(ch: alt.Chart) -> alt.Chart:
    return (ch.configure_view(strokeWidth=0)
            .configure_axis(grid=True, gridOpacity=0.35, domain=False, tickColor="#d6d5d0", labelFontSize=11,
                            titleFontSize=11, titleFontWeight="normal", labelLimit=220)
            .configure_legend(orient="bottom", title=None, labelFontSize=11, symbolType="square", labelLimit=200))


def _tableau(df: pd.DataFrame, cle: str):
    with st.expander("Voir le tableau"):
        st.dataframe(df, hide_index=True, width="stretch", key=f"tab-{cle}")


def barres(df: pd.DataFrame, cat: str, val: str, titre: str, cle: str, fmt: str = ",.0f", top: int = 15,
           unite: str = "", table: pd.DataFrame | None = None, infos: list[str] | None = None):
    """Barres horizontales triées (classement). Au-delà de `top` : regroupement « Autres »."""
    st.markdown(f"##### {titre}")
    if not len(df):
        st.caption("Pas de données.")
        return
    d = df[[cat, val] + (infos or [])].copy().sort_values(val, ascending=False)
    d[cat] = d[cat].astype(str)
    if len(d) > top:
        reste = d.iloc[top - 1:]
        d = pd.concat([d.iloc[:top - 1], pd.DataFrame({cat: [f"Autres ({len(reste)})"], val: [reste[val].sum()]})],
                      ignore_index=True)
    ordre = d[cat].tolist()
    dec = 0 if fmt.endswith("0f") else 2
    entier = bool((d[val].fillna(0) % 1 == 0).all())
    titre_val = f"{val}{f' ({unite})' if unite else ''}"
    d["_lab"] = d[val].map(lambda x: _fr(x, dec))
    tip = [alt.Tooltip(f"{cat}:N", title=cat), alt.Tooltip("_lab:N", title=titre_val)]
    tip += [alt.Tooltip(c) for c in (infos or []) if c in d.columns]
    b = alt.Chart(d).encode(y=alt.Y(f"{cat}:N", sort=ordre, title=None, scale=alt.Scale(paddingInner=0.35)),
                            x=alt.X(f"{val}:Q", title=titre_val, axis=_axe(entier)), tooltip=tip)
    barres_ = b.mark_bar(color=BLEU, cornerRadiusEnd=4)
    etiq = b.mark_text(align="left", dx=4, fontSize=11, color="#6b6a65").encode(text="_lab:N")
    st.altair_chart(_base((barres_ + etiq).properties(height=alt.Step(30))), width="stretch")
    _tableau(table if table is not None else df, cle)


def _plie(df: pd.DataFrame, cat: str, val: str) -> tuple[pd.DataFrame, list[str]]:
    """Garde les 6 plus grosses séries + « Autres » (couleur fixe par série, jamais recyclée)."""
    tot = df.groupby(cat)[val].sum().sort_values(ascending=False)
    if len(tot) <= MAX_SERIES:
        return df, tot.index.tolist()
    garde = tot.index[:MAX_SERIES - 1].tolist()
    d = df.copy()
    d[cat] = d[cat].where(d[cat].isin(garde), "Autres")
    return d.groupby([c for c in d.columns if c != val], as_index=False)[val].sum(), garde + ["Autres"]


def empile_mois(df: pd.DataFrame, mois: str, cat: str, val: str, titre: str, cle: str, fmt: str = ",.0f",
                unite: str = "", table: pd.DataFrame | None = None):
    """Barres verticales par mois, empilées par catégorie (chantier…)."""
    st.markdown(f"##### {titre}")
    if not len(df):
        st.caption("Pas de données.")
        return
    d, ordre = _plie(df[[mois, cat, val]].copy(), cat, val)
    d[cat] = d[cat].astype(str)
    couleurs = [AUTRES if c == "Autres" else SERIES[i] for i, c in enumerate(ordre)]
    titre_val = f"{val}{f' ({unite})' if unite else ''}"
    entier = bool((d[val].fillna(0) % 1 == 0).all())
    ch = alt.Chart(d).mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3, stroke="white", strokeWidth=1).encode(
        x=alt.X(f"{mois}:O", title=None, axis=alt.Axis(labelAngle=0), scale=alt.Scale(paddingInner=0.3)),
        y=alt.Y(f"sum({val}):Q", title=titre_val, axis=_axe(entier)),
        color=alt.Color(f"{cat}:N", scale=alt.Scale(domain=ordre, range=couleurs), sort=ordre,
                        legend=alt.Legend(columns=4) if len(ordre) > 1 else None),
        order=alt.Order("rang:Q"),
        tooltip=[alt.Tooltip(f"{mois}:O", title="Mois"), alt.Tooltip(f"{cat}:N"),
                 alt.Tooltip(f"sum({val}):Q", title=titre_val, format=fmt.replace(",", ""))],
    ).transform_calculate(rang=f"indexof({ordre!r}, datum['{cat}'])")
    n_mois = d[mois].nunique()
    if n_mois <= 12:  # peu de mois : barres de largeur fixe plutôt qu'un bloc pleine largeur
        st.altair_chart(_base(ch.properties(height=280, width=alt.Step(min(90, max(48, 600 // n_mois))))),
                        width="content")
    else:
        st.altair_chart(_base(ch.properties(height=280)), width="stretch")
    _tableau(table if table is not None else d.pivot_table(index=cat, columns=mois, values=val, aggfunc="sum",
                                                           fill_value=0).reset_index(), cle)


def courbe(df: pd.DataFrame, x: str, y: str, titre: str, cle: str, fmt: str = ",.0f", unite: str = "",
           infos: list[str] | None = None, table: pd.DataFrame | None = None):
    """Évolution dans le temps : ligne fine + points, info-bulle au survol."""
    st.markdown(f"##### {titre}")
    if not len(df):
        st.caption("Pas de données.")
        return
    titre_y = f"{y}{f' ({unite})' if unite else ''}"
    df = df.assign(_lab=df[y].map(_fr))
    tip = [alt.Tooltip(f"{x}:T", title="Date", format="%d/%m/%Y"), alt.Tooltip("_lab:N", title=titre_y)]
    tip += [alt.Tooltip(c) for c in (infos or [])]
    base = alt.Chart(df).encode(x=alt.X(f"{x}:T", title=None, axis=alt.Axis(format="%m/%Y", tickCount="month")),
                                y=alt.Y(f"{y}:Q", title=titre_y, axis=_axe(False), scale=alt.Scale(zero=False)),
                                tooltip=tip)
    ch = base.mark_line(color=BLEU, strokeWidth=2) + base.mark_circle(color=BLEU, size=70, opacity=1)
    st.altair_chart(_base(ch.properties(height=260)), width="stretch")
    if table is not None:
        _tableau(table, cle)


def jauge_barres(df: pd.DataFrame, cat: str, val: str, titre: str, cle: str, table: pd.DataFrame | None = None):
    """Pourcentages (0–100 %) par catégorie, ex. taux de service par fournisseur."""
    st.markdown(f"##### {titre}")
    if not len(df):
        st.caption("Pas de données.")
        return
    d = df[[cat, val]].copy().sort_values(val, ascending=False).head(15)
    ordre = d[cat].astype(str).tolist()
    b = alt.Chart(d).encode(y=alt.Y(f"{cat}:N", sort=ordre, title=None, scale=alt.Scale(paddingInner=0.35)),
                            x=alt.X(f"{val}:Q", title=val, scale=alt.Scale(domain=[0, 1]), axis=alt.Axis(format="%", tickCount=5)),
                            tooltip=[alt.Tooltip(f"{cat}:N"), alt.Tooltip(f"{val}:Q", format=".0%")])
    ch = (b.mark_bar(color=BLEU, cornerRadiusEnd=4)
          + b.mark_text(align="left", dx=4, fontSize=11, color="#6b6a65").encode(text=alt.Text(f"{val}:Q",
                                                                                               format=".0%")))
    st.altair_chart(_base(ch.properties(height=alt.Step(30))), width="stretch")
    if table is not None:
        _tableau(table, cle)


# --------------------------------------------------------------------------- statistiques avancées
CLASSES = {"A": "#2a78d6", "B": "#eda100", "C": "#a3a29c"}


def pareto(df: pd.DataFrame, titre: str, cle: str, entite: str):
    """Courbe de Pareto : % cumulé de la dépense selon le % des éléments classés, colorée par classe ABC.
    df : colonnes rang_pct, cum_pct, classe, nom, montant."""
    st.markdown(f"##### {titre}")
    if not len(df):
        st.caption("Pas de données.")
        return
    d = df.copy()
    d["_lab"] = d["montant"].map(_fr)
    dom = [k for k in CLASSES if k in set(d["classe"])]
    base = alt.Chart(d).encode(
        x=alt.X("rang_pct:Q", title=f"% des {entite} (classés par dépense décroissante)",
                scale=alt.Scale(domain=[0, 1]), axis=alt.Axis(format="%", tickCount=10)),
        y=alt.Y("cum_pct:Q", title="% cumulé de la dépense", scale=alt.Scale(domain=[0, 1]),
                axis=alt.Axis(format="%", tickCount=10)))
    aire = base.mark_area(opacity=0.18, interpolate="step-after").encode(
        color=alt.Color("classe:N", scale=alt.Scale(domain=dom, range=[CLASSES[k] for k in dom]),
                        legend=alt.Legend(title=None, labelExpr="'Classe ' + datum.label")))
    ligne = base.mark_line(color="#3d3d3a", strokeWidth=2, interpolate="step-after")
    pts = base.mark_circle(size=60, opacity=0).encode(
        tooltip=[alt.Tooltip("nom:N", title=entite.capitalize()), alt.Tooltip("_lab:N", title="Dépense HT (F)"),
                 alt.Tooltip("cum_pct:Q", title="% cumulé", format=".1%"), alt.Tooltip("classe:N", title="Classe")])
    seuils = alt.Chart(pd.DataFrame({"y": [0.8, 0.95], "t": ["80 % (A)", "95 % (B)"]})).encode(y="y:Q")
    regles = seuils.mark_rule(strokeDash=[4, 4], color="#8a8984") + seuils.mark_text(
        align="left", x=6, dy=-6, fontSize=11, color="#6b6a65").encode(text="t:N")
    st.altair_chart(_base((aire + ligne + pts + regles).properties(height=320)), width="stretch")


def carte_chaleur(df: pd.DataFrame, x: str, y: str, val: str, texte: str, titre: str, cle: str,
                  ordre_x: list | None = None, ordre_y: list | None = None, fmt: str = ".0%"):
    """Matrice (ex. ABC × XYZ) : intensité séquentielle d'un seul bleu, valeur écrite dans la case."""
    st.markdown(f"##### {titre}")
    if not len(df):
        st.caption("Pas de données.")
        return
    base = alt.Chart(df).encode(x=alt.X(f"{x}:N", sort=ordre_x, title=None, axis=alt.Axis(labelAngle=0, orient="top")),
                                y=alt.Y(f"{y}:N", sort=ordre_y, title=None))
    cases = base.mark_rect(cornerRadius=4, stroke="white", strokeWidth=3).encode(
        color=alt.Color(f"{val}:Q", scale=alt.Scale(range=["#eaf2fc", "#0f4a8f"]), legend=None),
        tooltip=[alt.Tooltip(f"{x}:N"), alt.Tooltip(f"{y}:N"), alt.Tooltip(f"{val}:Q", format=fmt),
                 alt.Tooltip(f"{texte}:N", title="Détail")])
    lab = base.mark_text(fontSize=12, lineBreak="\n").encode(
        text=f"{texte}:N",
        color=alt.condition(f"datum['{val}'] > 0.25", alt.value("white"), alt.value("#3d3d3a")))
    st.altair_chart(_base((cases + lab).properties(height=alt.Step(70), width=alt.Step(150))), width="content")


def histogramme(valeurs: pd.Series, titre: str, cle: str, unite: str = "", pas: float | None = None,
                repere: float | None = None, repere_txt: str = ""):
    """Distribution d'une mesure (délais, écarts…), avec un repère vertical optionnel (médiane, objectif)."""
    st.markdown(f"##### {titre}")
    v = pd.to_numeric(valeurs, errors="coerce").dropna()
    if not len(v):
        st.caption("Pas de données.")
        return
    d = pd.DataFrame({"v": v})
    binning = alt.Bin(step=pas) if pas else alt.Bin(maxbins=25)
    h = alt.Chart(d).mark_bar(color=BLEU, cornerRadiusTopLeft=3, cornerRadiusTopRight=3).encode(
        x=alt.X("v:Q", bin=binning, title=unite or None),
        y=alt.Y("count():Q", title="Nombre", axis=alt.Axis(tickMinStep=1)),
        tooltip=[alt.Tooltip("v:Q", bin=binning, title=unite or "Classe"), alt.Tooltip("count():Q", title="Nombre")])
    ch = h
    if repere is not None:
        r = alt.Chart(pd.DataFrame({"x": [repere], "t": [repere_txt]})).encode(x="x:Q")
        ch = h + r.mark_rule(color="#eb6834", strokeWidth=2) + r.mark_text(
            align="left", dx=5, y=8, color="#eb6834", fontSize=11).encode(text="t:N")
    st.altair_chart(_base(ch.properties(height=240)), width="stretch")


def benford(df: pd.DataFrame, titre: str, cle: str):
    """Loi de Benford : fréquence observée du 1er chiffre (barres) et fréquence théorique (points)."""
    st.markdown(f"##### {titre}")
    d = df.copy()
    d["Chiffre"] = d["chiffre"].astype(str)
    base = alt.Chart(d).encode(x=alt.X("Chiffre:N", title="Premier chiffre du montant", axis=alt.Axis(labelAngle=0)))
    tip = [alt.Tooltip("Chiffre:N"), alt.Tooltip("observe:Q", title="Observé", format=".1%"),
           alt.Tooltip("attendu:Q", title="Benford", format=".1%"), alt.Tooltip("n:Q", title="Montants")]
    barres_ = base.mark_bar(color=BLEU, cornerRadiusTopLeft=3, cornerRadiusTopRight=3).encode(
        y=alt.Y("observe:Q", title="Fréquence", axis=alt.Axis(format="%")), tooltip=tip)
    theo = base.mark_line(color="#eb6834", strokeWidth=2, point=alt.OverlayMarkDef(color="#eb6834", size=60)).encode(
        y="attendu:Q", tooltip=tip)
    st.altair_chart(_base((barres_ + theo).properties(height=260)), width="stretch")
    st.caption("Barres bleues : fréquences observées · ligne orange : fréquences attendues selon la loi de Benford.")


def nuage(df: pd.DataFrame, x: str, y: str, nom: str, titre: str, cle: str, titre_x: str, titre_y: str,
          log_x: bool = True, fmt_y: str = ".0%", repere_y: float | None = None, infos: list[str] | None = None):
    """Nuage de points (ex. dépense × volatilité des prix) avec un seuil horizontal optionnel."""
    st.markdown(f"##### {titre}")
    if not len(df):
        st.caption("Pas de données.")
        return
    d = df.copy()
    d["_x"] = d[x].map(_fr)
    tip = [alt.Tooltip(f"{nom}:N"), alt.Tooltip("_x:N", title=titre_x), alt.Tooltip(f"{y}:Q", title=titre_y,
                                                                                     format=fmt_y)]
    tip += [alt.Tooltip(c) for c in (infos or [])]
    pts = alt.Chart(d).mark_circle(size=70, color=BLEU, opacity=0.7, stroke="white", strokeWidth=1).encode(
        x=alt.X(f"{x}:Q", title=titre_x, scale=alt.Scale(type="log") if log_x else alt.Undefined,
                axis=alt.Axis(labelExpr=_AXE, tickCount=6)),
        y=alt.Y(f"{y}:Q", title=titre_y, axis=alt.Axis(format=fmt_y)), tooltip=tip)
    ch = pts
    if repere_y is not None:
        ch = pts + alt.Chart(pd.DataFrame({"y": [repere_y]})).mark_rule(strokeDash=[4, 4], color="#8a8984").encode(
            y="y:Q")
    st.altair_chart(_base(ch.properties(height=320)), width="stretch")


def indice(df: pd.DataFrame, x: str, y: str, titre: str, cle: str, titre_y: str = "Indice (base 100)"):
    """Indice de prix mensuel (base 100) avec la ligne de référence 100."""
    st.markdown(f"##### {titre}")
    if not len(df):
        st.caption("Pas de données.")
        return
    d = df.assign(_lab=df[y].map(lambda v: _fr(v, 1)))
    base = alt.Chart(d).encode(x=alt.X(f"{x}:T", title=None, axis=alt.Axis(format="%m/%Y", tickCount="month")),
                               y=alt.Y(f"{y}:Q", title=titre_y, scale=alt.Scale(zero=False),
                                       axis=alt.Axis(labelExpr=_AXE, tickCount=6)),
                               tooltip=[alt.Tooltip(f"{x}:T", title="Mois", format="%m/%Y"),
                                        alt.Tooltip("_lab:N", title=titre_y)])
    ref = alt.Chart(pd.DataFrame({"y": [100]})).mark_rule(color="#8a8984", strokeDash=[4, 4]).encode(y="y:Q")
    ch = ref + base.mark_line(color=BLEU, strokeWidth=2) + base.mark_circle(color=BLEU, size=60)
    st.altair_chart(_base(ch.properties(height=260)), width="stretch")
