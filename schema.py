"""Structure des tables : identique au Google Sheet PWC_DA_Chantier_BASE."""

DA_COLS = [
    "ID_DA", "N° DA", "Date DA", "Chantier", "Code DA", "Service Demandeur",
    "Demandeur", "Email Demandeur", "Responsable", "Priorité", "Date besoin",
    "Observations", "Signature Demandeur", "Statut", "Validé par",
    "Date validation", "Motif rejet", "Créé le", "Visa Resp. service", "Date visa Resp. service",
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
    "UTILISATEURS": ["Email", "Nom", "Rôle", "Chantier", "Service", "Actif", "PIN", "Poste", "Civilité"],
    "PARC": ["Code_Parc", "Désignation engin", "Type", "Marque / Modèle", "Chantier"],
    "ARTICLES": ["Code Article", "Désignation", "Référence", "Famille", "Colisage", "Sous-famille"],
    # ---- volet Achats
    "PARAMETRES": ["Paramètre", "Valeur"],
    "CONDITIONS_REGLEMENT": ["Condition"],
    "FOURNISSEURS": ["Code Fournisseur", "Nom", "Contact", "Téléphone", "Email", "NCC", "Adresse", "Actif",
                     "Spécialités", "Statut", "Pays", "Ville", "Site web", "Source", "Ajouté le"],
    "BC": ["ID_BC", "N° BC", "Date BC", "ID_DA", "N° DA", "Chantier", "Code Parc", "Magasin", "Fournisseur",
           "Condition règlement", "Total HT", "Taux TVA", "Montant TVA", "Total TTC", "Statut", "Acheteur",
           "Validé par", "Date validation", "Motif rejet", "ID_TCP", "N° TCP", "Observations", "Créé le",
           "Statut livraison", "Date livraison", "Modifié le", "Modifié par", "Total HT brut", "Remise globale %",
           "Remise globale (F)"],
    "BC_LIGNES": ["ID_LigneBC", "ID_BC", "N° BC", "N° Ligne", "ID_Ligne_DA", "Code Article", "Désignation",
                  "Référence", "Code_Parc", "Unité", "Quantité", "Prix unitaire HT", "Remise %", "Montant HT",
                  "Fournisseur", "Date BC", "Chantier", "N° DA", "TVA %", "Montant TVA",
                  "Qté livrée", "Date livraison", "Statut livraison", "Prix réf. HT", "Écart prix %"],
    "LIVRAISONS": ["ID_Livraison", "ID_BC", "N° BC", "ID_LigneBC", "ID_Ligne_DA", "Désignation", "Référence",
                   "Unité", "Qté livrée", "Date livraison", "Observation", "Fournisseur", "Chantier", "Saisi par",
                   "Créé le", "N° BL", "ID_Document", "Écart"],
    # documents fournisseurs importés (BL, devis, factures) : le fichier est dans Google Drive, ici sa fiche
    "DOCUMENTS": ["ID_Document", "Type", "N° document", "Date document", "Fournisseur", "ID_BC", "N° BC", "ID_DA",
                  "N° DA", "Chantier", "Nom fichier", "Lien", "ID Drive", "Empreinte", "Lu par IA", "Écarts",
                  "Saisi par", "Créé le", "ID_TCP", "N° TCP"],
    # stock : chaque entrée / sortie / transfert / inventaire ; le stock = somme des mouvements par magasin
    "STOCK_MOUVEMENTS": ["ID_Mouvement", "Date", "Magasin", "Type", "Code Article", "Désignation", "Référence",
                         "Unité", "Quantité", "Code_Parc", "Bénéficiaire", "Motif", "ID_BC", "N° BC", "N° BL",
                         "ID_Livraison", "ID_Transfert", "Magasin lié", "Statut", "Valeur unitaire", "Saisi par",
                         "Créé le"],
    # consommation de l'API Claude (estimation par appel)
    "CONSO_IA": ["Date", "Utilisateur", "Fonction", "Modèle", "Jetons entrée", "Jetons sortie", "Recherches web",
                 "Coût estimé $"],
    # actions automatiques de l'IA sur les référentiels (catalogue, fournisseurs) : annulables
    "JOURNAL_IA": ["ID_Action", "Date", "Objet", "Action", "Détail", "Confiance", "Annulation", "Statut", "Par"],
    # résultats du sourcing (base interne + recherche web), gardés pour ne pas relancer la même recherche
    "SOURCING": ["ID_Sourcing", "Clé article", "Désignation", "Référence", "Zones", "Date", "Résultats",
                 "Nb recherches", "Par"],
    # articles saisis hors catalogue : à analyser puis intégrer (ou rattacher à un article existant)
    "ARTICLES_A_CATALOGUER": ["ID_Proposition", "Date", "Désignation", "Référence", "Colisage", "Saisi par",
                              "Service", "Chantier", "N° DA", "ID_DA", "ID_Ligne", "Statut", "Désignation retenue",
                              "Référence retenue", "Famille", "Code attribué", "Traité par", "Date traitement",
                              "Commentaire"],
    "VALIDATIONS_DA": ["ID_Validation", "ID_DA", "N° DA", "Niveau", "Décision", "Par", "Rôle", "Date", "Commentaire"],
    "PRIX_HISTORIQUE": ["Date", "N° BC", "Fournisseur", "Chantier", "Code Article", "Désignation", "Référence",
                        "Famille", "Unité", "Quantité", "PU HT", "Remise %", "PU net HT", "Libellé d'origine",
                        "Source"],
    "TCP": ["ID_TCP", "N° TCP", "Date TCP", "ID_DA", "N° DA", "Chantier", "Fournisseurs consultés",
            "Fournisseur retenu", "Montant retenu HT", "Moins-disant", "Montant moins-disant HT",
            "Motif choix", "Conditions offres", "Acheteur", "Statut", "N° BC", "Observations", "Créé le",
            "Profil", "Situation terrain", "Recommandation IA", "Recommandation suivie"],
    "TCP_OFFRES": ["ID_Offre", "ID_TCP", "N° TCP", "N° Ligne", "ID_Ligne_DA", "Code Article", "Désignation",
                   "Référence", "Unité", "Quantité", "Fournisseur", "Prix unitaire HT", "Montant HT",
                   "Retenu", "Date TCP", "N° DA", "Chantier", "Marque", "Conformité", "Qualité",
                   "Libellé devis", "Recommandé"],
}

PRIORITES = ["Normale", "Urgente", "Critique"]
COLISAGES = ["Unité", "Paire", "Paquet", "Carton", "Boîte", "Sac", "Rouleau", "Barre", "Botte", "Pot", "Bidon", "Fût",
             "m", "ml", "m²", "m³", "kg", "Tonne", "L", "Voyage", "ha", "Jeu", "Lot", "Jour", "Forfait", "F CFA",
             "Feuille"]
HORS_CATALOGUE = "HORS CATALOGUE"
# circuit DA : Émise -> Validée Resp. service -> Validée DG (part aux achats) ; Rejetée à tout niveau
STATUTS = ["Émise", "Validée Resp. service", "Validée DG", "Rejetée"]
DA_EN_ATTENTE = ["Émise", "Validée Resp. service"]
ANCIENS_STATUTS = {"Soumise": "Émise", "Validée": "Validée DG"}  # DA créées avant le circuit à deux niveaux
STATUTS_LIGNE = ["À commander", "Commandée partielle", "Commandée", "Livrée partielle", "Livrée", "Annulée"]
ROLES_ADMIN = {"Admin"}
ROLES = ["Demandeur", "Chef de service", "Acheteur", "Direction", "Admin", "Magasinier"]
ROLES_VALID_DA = {"Chef de service", "Direction", "Admin"}  # onglet « Validation DA »
ROLES_DG = {"Direction", "Admin"}                            # valident au niveau DG (et peuvent suppléer le chef)
ROLES_ACHATS = {"Acheteur", "Admin"}          # voient l'onglet Achats (DA, TCP, BC, fournisseurs)
ROLES_VALID_BC = {"Direction", "Admin"}       # valident les bons de commande
ROLES_BL = {"Acheteur", "Admin"}              # importent les bons de livraison
ROLES_DA = {"Demandeur", "Chef de service", "Admin"}  # saisissent des DA (assistant IA, DA à l'oral)
CIVILITES = ["M.", "Mme"]
ROLES_STOCK_VUE = {"Admin", "Direction", "Acheteur"}  # consultent les stocks de tous les magasins
# magasin = chantier (un magasin par chantier) ; le magasinier ne voit que le magasin de son chantier
TYPES_MOUVEMENT = {"Entrée réception": 1, "Entrée transfert": 1, "Stock initial": 1, "Sortie": -1,
                   "Sortie transfert": -1, "Ajustement inventaire": 0}  # 0 : signe porté par la quantité
ZONES_SOURCING = ["Côte d'Ivoire", "Afrique de l'Ouest", "International"]
TYPES_DOCUMENT = ["Bon de livraison", "Devis / proforma", "Facture", "Autre"]

STATUTS_BC = ["Émis", "Validé", "Rejeté", "Annulé"]
STATUTS_CATALOGAGE = ["À analyser", "Intégré", "Rattaché", "Rejeté"]
LIGNE_BC_RETIREE = "Retirée"  # ligne supprimée lors d'une modification du BC (gardée pour la trace)
STATUTS_LIVRAISON = ["Non livré", "Partiel", "Livré"]
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
    "Seuil alerte prix (%)": "15",
    "Catalogage automatique IA": "Oui",
    "Délai traitement DA (jours)": "3",
    "Délai livraison attendu (jours)": "15",
    "Sourcing : recherches web max par article": "3",
    "Sourcing : validité des résultats (jours)": "60",
    "Sourcing : articles max par lancement": "10",
    "Familles sans alerte prix": "TRANSPORT & LOGISTIQUE; FRAIS ADMIN. VÉHICULES; RÉPARATIONS & ENTRETIEN ENGINS; "
                                 "LOCATION ENGINS & MATÉRIEL",
}
CONDITIONS_DEFAUT = [
    "30 jours, date dépôt facture",
    "Comptant à la livraison",
    "50% à la commande, solde à la livraison",
    "30 jours fin de mois",
    "60 jours, date de facture",
    "3 semaines, date de facture",
]
