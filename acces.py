"""Gestion des accès : postes, génération des codes, contrôle des tentatives."""
from __future__ import annotations

import secrets
import time
import unicodedata

POSTES = {  # poste = service PWC ; la valeur est le code service (et le préfixe des codes d'accès)
    "Logistique et Supply": "LS",
    "Direction technique": "DT",
    "Parc matériel": "PM",
    "Moyens généraux": "MG",
    "QHSE": "QHSE",
    "Comptabilité": "CPT",
    "Ressources humaines": "RH",
}


def _plat(txt: str) -> str:
    sans = unicodedata.normalize("NFKD", str(txt)).encode("ascii", "ignore").decode()
    return " ".join(sans.lower().replace("&", " et ").split())


def trouve_poste(saisie: str) -> str | None:
    """Reconnaît un poste par son nom, son code ou un début de nom : « parc », « PM », « logistique »…"""
    s = _plat(saisie)
    if not s:
        return None
    for nom, code in POSTES.items():
        if s in (_plat(nom), code.lower()):
            return nom
    candidats = [nom for nom in POSTES if _plat(nom).startswith(s) or s in _plat(nom)]
    return candidats[0] if len(candidats) == 1 else None


def prefixe(poste: str) -> str:
    """Préfixe du code : celui du poste connu, sinon les 3 premières lettres sans accent."""
    if poste in POSTES:
        return POSTES[poste]
    sans = unicodedata.normalize("NFKD", poste).encode("ascii", "ignore").decode()
    lettres = "".join(c for c in sans.upper() if c.isalpha())
    return (lettres[:3] or "USR").ljust(3, "X")


def nouveau_code(poste: str, existants: set[str]) -> str:
    """PRÉFIXE-NNNNN : 5 chiffres tirés au hasard (module secrets), unique dans la base."""
    while True:
        code = f"{prefixe(poste)}-{secrets.randbelow(90000) + 10000}"
        if code not in existants:
            return code


def normalise(code) -> str:
    """Comparaison tolérante : majuscules, sans espaces ; 0123 et 123 équivalents."""
    s = str(code if code is not None else "").strip().upper().replace(" ", "")
    if s.endswith(".0") and s[:-2].isdigit():
        s = s[:-2]
    return (s.lstrip("0") or s) if s.isdigit() else s


class Garde:
    """Bloque un poste 5 minutes après 10 codes faux (partagé entre toutes les sessions)."""

    MAX, DUREE = 10, 300

    def __init__(self):
        self.echecs: dict[str, list[float]] = {}

    def bloque(self, nom: str) -> int:
        now = time.time()
        recents = [t for t in self.echecs.get(nom, []) if now - t < self.DUREE]
        self.echecs[nom] = recents
        if len(recents) >= self.MAX:
            return int(self.DUREE - (now - recents[0])) + 1
        return 0

    def echec(self, nom: str):
        self.echecs.setdefault(nom, []).append(time.time())

    def succes(self, nom: str):
        self.echecs.pop(nom, None)


def ligne_txt(nom: str, poste: str, code: str) -> str:
    return f"{nom} ; {poste} : {code}"
