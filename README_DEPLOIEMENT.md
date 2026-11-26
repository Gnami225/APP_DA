# DA Chantier PWC — mise en ligne (environ 30 minutes)

Application web Python (Streamlit) : saisie des DA depuis le téléphone, fiche PDF au format
SC/ADM/LOG/QHSE/TECH-PWC-ExB-01, validation Direction / Achats, archivage dans le Google Sheet
`PWC_DA_Chantier_BASE`, export Excel pour les analyses.

## 1. Clé d'accès Google (10 min)

1. Ouvrir https://console.cloud.google.com avec pwcjacques@gmail.com, créer un projet `pwc-da`.
2. APIs et services → Bibliothèque → activer **Google Sheets API**.
3. APIs et services → Identifiants → Créer des identifiants → **Compte de service** (nom : `da-app`).
4. Ouvrir le compte de service → Clés → Ajouter une clé → JSON. Un fichier `.json` se télécharge.
5. Copier l'adresse du compte de service (`da-app@pwc-da.iam.gserviceaccount.com`) et **partager le
   Google Sheet `PWC_DA_Chantier_BASE` avec cette adresse en Éditeur**.

## 2. Code sur GitHub (5 min)

1. Sur https://github.com, créer un dépôt **privé** `pwc-da-chantier`.
2. « Add file → Upload files » → « choose your files » : sélectionner les 8 fichiers
   (app.py, logic.py, pdf.py, schema.py, storage.py, requirements.txt, logo.png,
   README_DEPLOIEMENT.md) → Commit changes. Ne jamais déposer le fichier JSON de la clé.

## 3. Mise en ligne sur Streamlit Community Cloud (10 min, gratuit)

1. https://share.streamlit.io → se connecter avec GitHub → **Create app** → dépôt `pwc-da-chantier`,
   branche `main`, fichier `app.py`.
2. Advanced settings → **Secrets** : coller le contenu de `secrets_exemple.toml`,
   remplacer le bloc `[gcp_service_account]` par les valeurs du fichier JSON, choisir un `ADMIN_PIN`.
3. Deploy. L'adresse obtenue (`https://pwc-da-chantier.streamlit.app` ou similaire) est celle à
   envoyer aux chantiers.

Au premier démarrage, l'app ajoute seule la colonne **PIN** dans l'onglet UTILISATEURS.

## 4. Utilisateurs

Dans le Google Sheet, onglet UTILISATEURS, une ligne par personne :

| Colonne | Exemple | Rôle |
|---|---|---|
| Nom | Yao K. | affiché à la connexion |
| Rôle | Demandeur / Admin | Admin voit en plus « Administration » (modification des DA, statuts Émise / Validée / Rejetée) et « Analyses » ; les autres saisissent et voient leurs propres DA |
| Chantier | TORTIYA | chantier proposé par défaut |
| Service | MAT | service proposé par défaut |
| Actif | TRUE | FALSE pour retirer l'accès |
| PIN | 4 à 6 chiffres | code de connexion personnel |
| Email | facultatif | pour recevoir les notifications |

Compléter aussi CHANTIERS (avec `Email validateur`) et SERVICES (avec `Responsable`).
Les listes ARTICLES et PARC alimentent les suggestions de saisie ; les désignations déjà saisies
sont aussi proposées automatiquement.

## 5. Sur le téléphone

Ouvrir l'adresse dans Chrome (Android) ou Safari (iPhone) → menu → **Ajouter à l'écran d'accueil**.
Après « Soumettre la DA », le bouton « Télécharger / imprimer la fiche PDF » ouvre la fiche :
Partager → Imprimer (imprimante Wi-Fi), ou l'envoyer par WhatsApp/email au bureau.

## Bon à savoir

- Numérotation : `DA-<CHANTIER>-<ANNÉE>-<NNNN>`, séquence par chantier et par année.
- Le PDF n'est pas stocké : il est régénéré à la demande à partir de la base, toujours à jour
  (version validée ou rejetée incluse). Aucune limite de stockage Drive à gérer.
- Il faut du réseau pour soumettre (pas de mode hors ligne, contrairement à AppSheet).
- Sur l'offre gratuite de Streamlit Cloud, l'app se met en veille après 12 heures sans visite
  (donc souvent la nuit) : le premier utilisateur du matin clique sur « Yes, get this app back up »
  et attend environ une minute. Les données ne sont pas touchées : elles sont dans le Google Sheet.
  L'offre gratuite autorise une seule app privée, ce qui suffit ici.
- Emails : facultatifs, à activer avec le bloc `[smtp]` des secrets (mot de passe d'application Gmail).
- Secours sans internet au bureau : `pip install -r requirements.txt` puis `streamlit run app.py`
  sur un PC ; sans secrets, l'app utilise le fichier local `data/PWC_DA_local.xlsx`, accessible
  aux téléphones du même Wi-Fi via l'adresse affichée « Network URL ».

## Volet Achats (BC, TCP)

Fichiers à mettre sur GitHub : `app.py`, `achats.py` (nouveau), `schema.py`, `logic.py`, `pdf.py`,
`storage.py`, `acces.py`. Redémarrer l'app (Manage app → Reboot) : les onglets PARAMETRES,
CONDITIONS_REGLEMENT, FOURNISSEURS, BC, BC_LIGNES, TCP et TCP_OFFRES sont créés automatiquement
dans le Google Sheet, avec les paramètres société par défaut.

Rôles (Administration → Utilisateurs & accès, colonne Rôle) :
- Demandeur : saisit ses DA.
- Acheteur : + onglet Achats (DA à traiter, TCP, bons de commande, fournisseurs).
- Direction : + onglet Validation BC et Analyses.
- Admin : tout, dont Paramètres (société, TVA, numérotation BC, conditions de règlement) et Archivage.

Circuit : DA validée → (TCP facultatif) → BC émis par l'acheteur → validé ou rejeté par la direction.
Un BC rejeté ou annulé libère les quantités de la DA.

Archivage annuel (Administration → Archivage) : télécharger l'archive Excel de l'année, puis
la retirer de la base. Les DA validées encore à commander sont conservées.

## Suivi des livraisons
Achats → Suivi livraisons : cocher des BC puis « Marquer livrés en totalité » à une date, ou ouvrir un BC
pour saisir une réception partielle (quantités reçues, date, N° de BL). Chaque réception est tracée dans
l'onglet LIVRAISONS ; les colonnes Qté livrée / Date livraison / Statut livraison de BC_LIGNES et BC, et le
statut des lignes de DA (Livrée, Livrée partielle) sont recalculés automatiquement.

## Circuit de validation des DA et base de prix (version novembre 2026)

1. Remplacer sur GitHub : `app.py`, `achats.py`, `schema.py`, `logic.py`, `pdf.py` et ajouter `prix.py`.
   Les nouveaux onglets (`VALIDATIONS_DA`, `PRIX_HISTORIQUE`) et colonnes sont créés automatiquement.
2. Désigner un responsable par service : Administration > Utilisateurs & accès, rôle « Chef de service »
   (le service suit le poste ; un seul chef par service).
3. Importer l'historique des prix : Administration > Paramètres > Base de prix, fichier `PRIX_HISTORIQUE_PWC.xlsx`.
4. Paramètres ajoutés : « Seuil alerte prix (%) » (15) et « Familles sans alerte prix ».

Circuit : Émise → visa du responsable de service → validation DG (Direction ou Admin) → achats.
Une DA saisie par un chef pour son service, par la Direction ou par l'Admin passe directement au niveau DG.
Les anciennes DA « Validée » sont lues comme « Validée DG ».

## Articles à cataloguer et modification des DA / BC
- Remplacer `app.py`, `achats.py`, `schema.py`, `logic.py`, `prix.py` et ajouter `catalogage.py`.
- Onglet créé automatiquement : `ARTICLES_A_CATALOGUER`. Traitement : Administration > Articles à cataloguer.
- DA modifiable par son demandeur tant qu'elle n'est pas « Validée DG » (Mes DA > Modifier la DA).
- BC modifiable par les acheteurs tant qu'il est « Émis » ou « Rejeté » (Bons de commande émis > Modifier ce BC).

## Modifications admin et graphiques (20/11/2026)
- Ajouter `graphs.py` (graphiques Altair, déjà inclus dans Streamlit : rien à installer).
- Admin : modifie toute DA (statut inchangé), tout TCP non abandonné, et les BC validés (restent validés).

## Reprise des BC antérieurs (20/11/2026)
- Remplacer `achats.py` et `catalogage.py`.
- Administration > Paramètres > « Reprise de BC antérieurs à l'application » : importer `REPRISE_BC_AVANT_APP.xlsx`.

## Statistiques avancées (20/11/2026)
- Ajouter `stats.py` ; remplacer `app.py` et `graphs.py`. Onglet « Statistiques » visible par le rôle Admin uniquement.


## Assistance IA — Claude (21/11/2026)
Activation : Streamlit Cloud > app > Settings > Secrets, ajouter la section `[ia]` avec `api_key = "sk-ant-…"`
(clé créée sur console.anthropic.com, avec un plafond de dépense mensuel). Ne jamais mettre la clé sur GitHub.
Sans clé, l'application fonctionne comme avant (les boutons IA n'apparaissent pas).

| Où | Ce que fait l'IA |
|---|---|
| Nouvelle DA | « 🎙️ Décrire la demande » : texte libre ou dicté (micro du clavier du téléphone) → articles du catalogue, quantités, engins, usages, à vérifier puis ajouter |
| TCP | « 📷 Lire le devis » par fournisseur (photo ou PDF) → prix unitaires nets pré-remplis |
| Bon de commande | « 📷 Lire le devis / la proforma » → P.U. HT, remise, TVA, quantités pré-remplis |
| Suivi livraisons | « 📷 Lire le bon de livraison » → quantités reçues ; « ✉️ Relance fournisseur » pour le reste à livrer |
| Validations DA / BC | « ✨ Reformuler » le motif de rejet |
| Articles à cataloguer | « ✨ Proposer les décisions » (Rattacher à un code existant / Intégrer avec désignation, référence, famille) |
| Statistiques (admin) | section « 🤖 Assistant IA » (questions en français) + « ✨ Commentaire de la période » |
| Analyses (Direction) | volet « 🤖 Assistant IA » |

Les chiffres de l'assistant sont calculés par l'application (outils pandas) et les tableaux utilisés sont affichés
sous chaque réponse. L'IA propose, l'utilisateur valide : rien n'est enregistré sans clic.
Données envoyées à Anthropic : seulement le texte, le document ou les agrégats concernés par le clic ;
pas d'entraînement sur les données transmises par l'API.

## Sourcing, réception des BL, dossier d'achat (22/11/2026)
**Nouveaux onglets / volets**
- Achats > **📋 À faire** : DA validées en attente (au-delà de « Délai traitement DA (jours) », ou urgentes),
  BC à faire valider, livraisons en retard (au-delà de « Délai livraison attendu (jours) ») ou partielles avec
  relance IA, prospects à qualifier.
- Achats > **🔎 Sourcing** (acheteurs, admin) : pour chaque article de la DA, fournisseurs de la base (prix passés,
  note) puis recherche Internet par Claude (Côte d'Ivoire, Afrique de l'Ouest, international ; lien source pour
  chaque fournisseur, « ⚠ source à vérifier » si la page n'a pas été réellement consultée). Résultats gardés
  30 jours (table SOURCING). Ensuite : ajout des prospects (FOURNISSEURS, statut « Prospect »), lancement du TCP
  pré-rempli, demandes de cotation (FR/EN).
- Achats > **⭐ Notes fournisseurs** : note /100 (service 40 %, délai 30 %, prix 30 %), classes A/B/C.
- **Réception BL** (demandeurs et chefs : BC de leur chantier ou de leurs DA ; acheteurs et admin : tous) : import
  de BL en photo ou PDF, lecture IA, BC le plus probable (score), rapprochement ligne à ligne, écarts (partiel,
  surplus, article hors BC), enregistrement dans LIVRAISONS (N° BL, écart) ; doublons bloqués.
- **📁 Dossier d'achat** (détail d'une DA dans Mes DA / Validation / Administration / Achats) : chronologie
  DA → validations → TCP → BC → livraisons et **liasse PDF** complète.

**Base** : nouvelles tables DOCUMENTS et SOURCING ; colonnes ajoutées à FOURNISSEURS (Statut, Pays, Ville, Site web,
Source, Ajouté le) et LIVRAISONS (N° BL, ID_Document, Écart) — créées automatiquement, rien n'est supprimé.

**Archivage des fichiers (Google Drive, gratuit)** : installer `apps_script_documents.gs` (instructions en tête du
fichier) puis ajouter la section `[drive]` aux secrets. Sans elle, tout fonctionne mais le fichier du BL n'est pas
conservé (seules les données lues sont enregistrées).

**Recherche web** : vérifier dans les paramètres de la console Anthropic que la recherche web est autorisée pour
l'organisation. Coût : 10 $ / 1 000 recherches + tokens ; plafond réglable « Sourcing : recherches web max par
article » (5 par défaut).

## Accès, assistant DA et DA à l'oral (22/11/2026)
- **Saisie des DA** (Nouvelle DA, Mes DA) : administrateurs, chefs de service et demandeurs (N-1). Acheteurs et
  Direction n'en saisissent plus. **Import des BL** : acheteurs et administrateurs uniquement.
- **Civilité** (M. / Mme) : nouvelle colonne de UTILISATEURS, préremplie à « M. » — corriger pour les dames dans
  Administration > Utilisateurs & accès. Écrire le NOM DE FAMILLE EN MAJUSCULES (« KONÉ Ibrahim ») ; sinon le
  dernier mot est pris comme nom de famille.
- **Accueil** en haut de « Nouvelle DA » : « Bonjour M. Koné, bienvenue » (administrateurs, chefs de service),
  « Bonjour Ibrahim » (demandeurs), avec la situation de ses DA ; **❓ Poser une question** (aide) ;
  **💡 conseils** pendant la saisie (article proche du catalogue, doublon, quantité inhabituelle, priorité / date).
- **🎙️ Faire ma DA en conversation** : l'IA pose les questions à voix haute, le demandeur répond (bouton 🎤 ou
  écoute automatique) ou écrit ; la DA se remplit à l'écran ; il vérifie puis clique « Soumettre la DA ».
  Voix = fonctions vocales du navigateur (Chrome Android, Safari iPhone récent ; autoriser le micro) ; Claude est le
  seul modèle d'IA appelé. **Le dossier `voix/` (index.html) doit être envoyé sur GitHub avec les fichiers .py.**
- **Prix passés** : dernier prix, meilleur prix et prix moyen pondéré sur **tout l'historique**, plus le meilleur
  prix des 12 derniers mois et le nombre d'achats depuis la première date.

## Magasin (gestion de stock) — 23/11/2026
- **Rôle Magasinier**, poste « Magasin » (codes MAG-…), **chantier = son magasin** (un magasin par chantier). Il ne
  voit que l'onglet **Magasin** de son chantier. Création : Administration > Utilisateurs & accès, poste « Magasin »,
  code chantier obligatoire.
- **📥 Réception** : photo / PDF du BL → lecture IA → BC du chantier → confirmation → livraison sur le BC et la DA
  + **entrée en stock**. Toute réception saisie ailleurs (acheteur, admin) alimente aussi le stock du chantier.
- **📤 Sortie** : texte libre ou dicté → l'IA retrouve l'article dans le stock et l'engin → vérification →
  enregistrement (sorties libres, tracées ; impossible de sortir plus que le stock).
- **📦 Mon stock**, **🔁 Transfert** (confirmé par le magasin destinataire), **📋 Inventaire** (saisi, dicté ou
  importé d'Excel ; le premier = stock de départ), **❓ Aide**.
- **Onglet Stocks** (admin, direction, acheteur) : stocks de tous les magasins, valeur au prix moyen pondéré des
  achats, consommations 90 jours par engin et article, mouvements ; l'admin peut agir sur n'importe quel magasin.
- Liens : conseil « en stock au magasin X » lors de la saisie d'une DA ; assistant d'analyse (outil stocks).
- Table **STOCK_MOUVEMENTS** (créée automatiquement). Un ancien onglet MOUVEMENTS_STOCK (essai Kobo) n'est pas utilisé.

## Saisie DA et BC par l'IA : conversation, photo, PDF (23/11/2026)
- **DA** (Nouvelle DA) : « ✨ Remplir avec l'IA » → ✍️ texte ou dictée, ou 📷 photo / PDF (DA papier, liste
  manuscrite, devis…) : articles rattachés au catalogue, priorité / date de besoin / observations reprises si
  indiquées ; et « 🎙️ Faire ma DA en conversation » (questions de l'IA, réponses à l'oral ou à l'écrit).
- **BC** (Achats > Bons de commande, après le choix de la DA) : « ✨ Remplir le BC avec l'IA » → consigne écrite ou
  dictée (« chez SOCOCE, pneus à 185 mille, 5 % de remise, pas les bâches, paiement 30 jours fin de mois ») et / ou
  proforma en photo / PDF : fournisseur, conditions, magasin, observations (n° de proforma), prix, remises, TVA,
  quantités et lignes à exclure sont préremplis ; consignes successives possibles ; vérification puis « Émettre ».

## IA de fond sur les référentiels (24/11/2026)
- **Catalogage automatique** à chaque soumission / modification de DA : les articles hors catalogue sont rattachés
  ou intégrés par l'IA **seulement si elle est certaine** (confiance haute) ; sinon ils restent dans « Articles à
  cataloguer » avec la colonne « Proposition IA (auto) ». Paramètre « Catalogage automatique IA » (Oui / Non).
- **Administration > 🤖 IA référentiels** : cataloguer tout ce qui est en attente ; fournisseurs (spécialités déduites
  des achats, doublons détectés puis **fusion validée par l'admin**, fiches complétées par recherche web — champs vides
  seulement) ; **Journal IA** avec **annulation** de chaque action.
- Fusion de fournisseurs : fiches doublons « Fusionné » (inactives), BC renommés, historique regroupé dans les analyses.
- Table **JOURNAL_IA** (créée automatiquement). Annuler un « Intégrer » rétablit les lignes et la proposition ; la
  fiche article créée reste au catalogue (rien n'est supprimé).

## Coûts IA, sourcing réservé à l'admin, remise globale BC (24/11/2026)
- **🔎 Sourcing** : administrateurs uniquement. Réglages économes : 3 recherches web par article, résultats gardés
  60 jours, 10 articles max par lancement, estimation du coût affichée avant de lancer (Paramètres « Sourcing : … »).
- **💰 Consommation IA** (Administration > 🤖 IA référentiels) : coût estimé par mois, par fonction et par utilisateur
  (table CONSO_IA, écrite par lots). La facture exacte reste dans la Claude Console (Usage).
- **Remise globale sur le BC** : en % ou en montant, sur le total HT (après remises des lignes) ; TVA sur le HT net ;
  affichée à l'émission, en modification, sur le PDF (Total HT brut / Remise globale / Total HT net / TVA / TTC) ;
  lue sur la proforma ou dictée à l'IA ; prix nets et statistiques tiennent compte de la remise.
  Colonnes BC ajoutées : Total HT brut, Remise globale %, Remise globale (F).

## TCP intelligent (25/11/2026)
Nouveau fichier `tcp_ia.py` (écran « Nouveau TCP ») ; `achats.py`, `ia.py`, `schema.py`, `sourcing.py`, `pdf.py` modifiés.
- **Import de plusieurs devis** (photos / PDF) : une lecture IA par devis → fournisseur reconnu (rapproché de la base),
  délai, conditions, crédit, remise globale, transport, validité, prix nets, marque, **conformité** et **qualité** par
  article. Contrôles : quantité / unité différentes, devis expiré, lignes hors demande, même devis importé deux fois.
- **Langage naturel** : « SOCOCE : délai 2 jours et 5 % de remise », « KANIS ne fait pas le câble »… ; un fait de
  terrain dicté va dans la zone « Situation terrain ».
- **Recommandation dynamique** : profils (Équilibré, Moins cher, Urgence chantier — par défaut si DA urgente —,
  Qualité / conformité, Moins de fournisseurs) et poids ajustables sur 7 critères : prix, délai, conformité, qualité,
  fiabilité (note fournisseur), paiement, regroupement. Une offre non conforme n'est pas recommandée s'il existe une
  offre conforme. Bouton « 🤖 Recommandation IA » : tient compte de la situation terrain et de l'urgence.
- **Choix final par l'acheteur**, article par article (✋ modifié) ou d'un clic (suivre la recommandation, tout au
  moins-disant, tout chez X). Motif **obligatoire** si des articles ne vont pas au moins-disant (bouton « 🤖 Rédiger
  le motif »).
- Enregistré : TCP (+ Profil, Situation terrain, Recommandation IA, Recommandation suivie), TCP_OFFRES (+ Marque,
  Conformité, Qualité, Libellé devis, Recommandé), DOCUMENTS (devis, + ID_TCP / N° TCP, fichier dans Drive si
  configuré). Colonnes ajoutées automatiquement. Le PDF du TCP affiche le profil et la recommandation suivie.
- Sans clé IA : saisie manuelle + recommandation calculée sur les critères (tout fonctionne, sans import ni phrase).

## Fiches de stock lues par l'IA + statistiques stocks et couverture (25/11/2026)
Nouveaux fichiers `fiches_stock.py`, `stats_stock.py` ; `magasin.py`, `stats.py`, `ia.py`, `schema.py` modifiés.
- **Magasin › 📄 Fiches (IA)** (magasinier ; admin via Stocks › Agir sur un magasin) : photos / PDF / Excel des
  fiches de tenue de stock, fiches de flux / bons, fiches d'inventaire, plusieurs à la fois. L'IA lit chaque ligne
  (date, n° de bon, engin, code parc, immatriculation, quantité, solde) ; l'application rapproche l'article du
  catalogue et l'engin du parc, **recalcule les soldes** et les compare à ceux de la fiche (❌ à vérifier), repère les
  mouvements déjà enregistrés et les fiches déjà importées. Tableau modifiable, rien n'est écrit avant validation.
- Mode : **Courant** (met le stock à jour) ou **Historique** (statistiques seulement, colonne Historique = Oui).
  Automatique : article jamais suivi → le report de la fiche devient le stock initial ; article suivi → après le
  dernier inventaire = courant, avant = historique. Fiche d'inventaire → ajustements.
- STOCK_MOUVEMENTS : colonnes N° bon, Historique, Source, ID_Document, Solde fiche (ajoutées automatiquement) ;
  fiches archivées dans DOCUMENTS (type « Fiche de stock », Drive si configuré).
- **Statistiques** : panneau « 🧾 Couverture des données » (période, BC, lignes, montant par source — historique
  importé, reprise de BC, application —, mois sans achat, trous entre sources, lignes non comptées) ; filtre Source
  avec « Reprise BC antérieurs » ; nouvelle section **📦 Stocks & consommations** (consommation valorisée par mois /
  magasin / famille / engin / article, carburant par engin et par mois, achats vs consommation par chantier, articles
  dormants, écarts d'inventaire), historique des fiches compris.

## Reprise complète de l'historique des BC + droits BC (26/11/2026)
- `REPRISE_HISTORIQUE_COMPLETE.xlsx` : tous les BC du suivi commandes / livraisons (janv. 2025 → oct. 2026) + BC de
  novembre hors application, au format de l'application (BC, BC_LIGNES, LIVRAISONS) ; onglet CONTRÔLES (lignes
  exclues, ajoutées, corrigées, à vérifier). Import : Administration › Paramètres › Reprise de BC antérieurs ;
  réimport sans doublon. Les livraisons reprises ne créent pas d'entrée en stock.
- Statistiques et prix : un BC présent dans l'application (créé ou repris) remplace ses lignes de PRIX_HISTORIQUE
  (numéros comparés quel que soit le format : « BC 5357 » = « BC-005357 ») → aucun double compte.
- Le prochain N° de BC n'est relevé que d'après les BC repris de moins de 120 jours (un numéro hors série ne décale
  pas la numérotation).
- « Livraisons à relancer » : BC de moins de 120 jours (paramètre « Relances livraison : BC de moins de (jours) ») ;
  les reliquats plus anciens restent visibles dans Suivi livraisons.
- Seul l'administrateur modifie ou annule un BC (tous statuts) ; l'acheteur voit et retélécharge.
- Filtre Chantier dans Validation BC et Liste des BC.
