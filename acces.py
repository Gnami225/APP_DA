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
    "Direction générale": "DG",
    "Direction d'exploitation": "DE",
}


def _plat(txt: str) -> str:
    txt = str(txt)
    for c in "'’‘`´-–_/":
        txt = txt.replace(c, " ")
    sans = unicodedata.normalize("NFKD", txt).encode("ascii", "ignore").decode()
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


# --------------------------------------------------------------------------- session persistante
import base64
import hashlib
import hmac

DUREE_SESSION_JOURS = 30


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def creer_jeton(nom: str, code: str, secret: str, jours: int = DUREE_SESSION_JOURS) -> str:
    """Jeton signé : nom + échéance ; la signature dépend aussi du code d'accès actuel."""
    exp = int(time.time()) + jours * 86400
    charge = f"{nom}|{exp}"
    sig = hmac.new(secret.encode(), f"{charge}|{normalise(code)}".encode(), hashlib.sha256).digest()[:18]
    return f"{_b64(charge.encode())}.{_b64(sig)}"


def lire_jeton(jeton: str, secret: str, code_de) -> str | None:
    """Retourne le nom si le jeton est valide, non expiré et que le code n'a pas changé ; sinon None.
    code_de(nom) doit renvoyer le code actuel de la personne (ou None si inconnue / inactive)."""
    try:
        p_charge, p_sig = jeton.split(".", 1)
        charge = _unb64(p_charge).decode()
        nom, exp = charge.rsplit("|", 1)
        if int(exp) < time.time():
            return None
        code = code_de(nom)
        if not code:
            return None
        attendu = hmac.new(secret.encode(), f"{charge}|{normalise(code)}".encode(), hashlib.sha256).digest()[:18]
        return nom if hmac.compare_digest(attendu, _unb64(p_sig)) else None
    except Exception:
        return None
