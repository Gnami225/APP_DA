"""Structure des tables : identique au Google Sheet PWC_DA_Chantier_BASE."""

DA_COLS = [
    "ID_DA", "N° DA", "Date DA", "Chantier", "Code DA", "Service Demandeur",
    "Demandeur", "Email Demandeur", "Responsable", "Priorité", "Date besoin",
    "Observations", "Signature Demandeur", "Statut", "Validé par",
    "Date validation", "Motif rejet", "Créé le",
]

LIGNE_COLS = [
    "ID_Ligne", "ID_DA", "N° Ligne", "Code_Parc", "Code Article", "Désignation",
    "Référence", "Colisage", "Quantité", "Usage", "DA_Numero", "DA_Date",
    "DA_Chantier", "DA_Service", "Fournisseur", "N° BC", "Prix unitaire HT",
    "Qté livrée", "Date livraison", "Statut ligne",
]

TABLES = {
    "DA": DA_COLS,
    "HISTORIQUE_ARTICLES": LIGNE_COLS,
    "CHANTIERS": ["Code Chantier", "Nom Chantier", "Localisation", "Chef de chantier",
                  "Email validateur", "Actif"],
    "SERVICES": ["Code Service", "Libellé", "Responsable", "Email responsable"],
    "UTILISATEURS": ["Email", "Nom", "Rôle", "Chantier", "Service", "Actif", "PIN", "Poste"],
    "PARC": ["Code_Parc", "Désignation engin", "Type", "Marque / Modèle", "Chantier"],
    "ARTICLES": ["Code Article", "Désignation", "Référence", "Famille", "Colisage"],
}

PRIORITES = ["Normale", "Urgente", "Critique"]
COLISAGES = ["Unité", "Paquet", "Carton", "ml", "m²", "m³", "kg", "L", "Jeu", "Lot"]
STATUTS = ["Émise", "Validée", "Rejetée"]
ANCIENS_STATUTS = {"Soumise": "Émise"}  # DA créées avant le renommage
STATUTS_LIGNE = ["À commander", "Commandée", "Livrée partielle", "Livrée", "Annulée"]
ROLES_ADMIN = {"Admin"}
