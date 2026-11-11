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
    "ARTICLES": ["Code Article", "Désignation", "Référence", "Famille", "Colisage", "Sous-famille"],
    # ---- volet Achats
    "PARAMETRES": ["Paramètre", "Valeur"],
    "CONDITIONS_REGLEMENT": ["Condition"],
    "FOURNISSEURS": ["Code Fournisseur", "Nom", "Contact", "Téléphone", "Email", "NCC", "Adresse", "Actif",
                     "Spécialités"],
    "BC": ["ID_BC", "N° BC", "Date BC", "ID_DA", "N° DA", "Chantier", "Code Parc", "Magasin", "Fournisseur",
           "Condition règlement", "Total HT", "Taux TVA", "Montant TVA", "Total TTC", "Statut", "Acheteur",
           "Validé par", "Date validation", "Motif rejet", "ID_TCP", "N° TCP", "Observations", "Créé le"],
    "BC_LIGNES": ["ID_LigneBC", "ID_BC", "N° BC", "N° Ligne", "ID_Ligne_DA", "Code Article", "Désignation",
                  "Référence", "Code_Parc", "Unité", "Quantité", "Prix unitaire HT", "Remise %", "Montant HT",
                  "Fournisseur", "Date BC", "Chantier", "N° DA"],
    "TCP": ["ID_TCP", "N° TCP", "Date TCP", "ID_DA", "N° DA", "Chantier", "Fournisseurs consultés",
            "Fournisseur retenu", "Montant retenu HT", "Moins-disant", "Montant moins-disant HT",
            "Motif choix", "Conditions offres", "Acheteur", "Statut", "N° BC", "Observations", "Créé le"],
    "TCP_OFFRES": ["ID_Offre", "ID_TCP", "N° TCP", "N° Ligne", "ID_Ligne_DA", "Code Article", "Désignation",
                   "Référence", "Unité", "Quantité", "Fournisseur", "Prix unitaire HT", "Montant HT",
                   "Retenu", "Date TCP", "N° DA", "Chantier"],
}

PRIORITES = ["Normale", "Urgente", "Critique"]
COLISAGES = ["Unité", "Paire", "Paquet", "Carton", "Boîte", "Sac", "Rouleau", "Barre", "Botte", "Pot", "Bidon", "Fût",
             "ml", "m²", "m³", "kg", "Tonne", "L", "Voyage", "ha", "Jeu", "Lot", "Jour", "Forfait"]
HORS_CATALOGUE = "HORS CATALOGUE"
STATUTS = ["Émise", "Validée", "Rejetée"]
ANCIENS_STATUTS = {"Soumise": "Émise"}  # DA créées avant le renommage
STATUTS_LIGNE = ["À commander", "Commandée partielle", "Commandée", "Livrée partielle", "Livrée", "Annulée"]
ROLES_ADMIN = {"Admin"}
ROLES = ["Demandeur", "Acheteur", "Direction", "Admin"]
ROLES_ACHATS = {"Acheteur", "Admin"}          # voient l'onglet Achats (DA, TCP, BC, fournisseurs)
ROLES_VALID_BC = {"Direction", "Admin"}       # valident les bons de commande

STATUTS_BC = ["Émis", "Validé", "Rejeté", "Annulé"]
STATUTS_TCP = ["Établi", "BC partiel", "BC émis", "Abandonné"]

PARAMETRES_DEFAUT = {  # valeurs initiales, modifiables dans Administration > Paramètres
    "Nom Société": "PUBLIC WORK CONTRACTOR (PWC)",
    "Adresse": "2 Plateaux Vallon - Rue des Jardins, 26 BP 263 Abidjan 26",
    "Téléphone": "27 22 46 44 41 / 01 40 70 64 06",
    "Email": "info@pwcci.com",
    "Reg. Commerce": "CI-ABJ-2014-B-19296",
    "N° CC": "14 35 841 J",
    "Taux TVA (%)": "18",
    "Prochain N° BC": "5944",
    "Préfixe BC": "BC-",
    "Préfixe TCP": "TCP-",
}
CONDITIONS_DEFAUT = [
    "30 jours, date dépôt facture",
    "Comptant à la livraison",
    "50% à la commande, solde à la livraison",
    "30 jours fin de mois",
    "60 jours, date de facture",
    "3 semaines, date de facture",
]
