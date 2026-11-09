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
2. « Add file → Upload files » : déposer tout le contenu de ce dossier (app.py, requirements.txt,
   da_core/, assets/, .streamlit/config.toml). Ne pas déposer de fichier `secrets.toml`.

## 3. Mise en ligne sur Streamlit Community Cloud (10 min, gratuit)

1. https://share.streamlit.io → se connecter avec GitHub → **Create app** → dépôt `pwc-da-chantier`,
   branche `main`, fichier `app.py`.
2. Advanced settings → **Secrets** : coller le contenu de `.streamlit/secrets.toml.example`,
   remplacer le bloc `[gcp_service_account]` par les valeurs du fichier JSON, choisir un `ADMIN_PIN`.
3. Deploy. L'adresse obtenue (`https://pwc-da-chantier.streamlit.app` ou similaire) est celle à
   envoyer aux chantiers.

Au premier démarrage, l'app ajoute seule la colonne **PIN** dans l'onglet UTILISATEURS.

## 4. Utilisateurs

Dans le Google Sheet, onglet UTILISATEURS, une ligne par personne :

| Colonne | Exemple | Rôle |
|---|---|---|
| Nom | Yao K. | affiché à la connexion |
| Rôle | Demandeur / Validateur / Admin | Validateur et Admin voient « À valider », « Toutes les DA », « Analyses » |
| Chantier | TORTIYA | chantier proposé par défaut ; un Validateur avec un chantier ne valide que celui-là |
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
