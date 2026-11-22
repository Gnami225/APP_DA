/**
 * PWC – Archivage des documents achats (BL, devis, factures) dans Google Drive.
 * Installation (5 minutes, avec le compte Gmail de PWC) :
 *  1. https://script.google.com > Nouveau projet > coller ce code à la place du contenu.
 *  2. Remplacer CHANGER-CE-JETON par un mot de passe long (ex. 30 caractères au hasard). Enregistrer.
 *  3. Déployer > Nouveau déploiement > type « Application Web » :
 *       Exécuter en tant que : Moi        Qui a accès : Tout le monde
 *     > Déployer > Autoriser l'accès (compte PWC) > copier l'URL qui finit par /exec.
 *  4. Dans les secrets Streamlit :   [drive]   url = "…/exec"   token = "le même jeton"
 * Les fichiers sont rangés dans « PWC – Documents achats / AAAA / MM » du Drive de PWC et restent privés.
 */
const TOKEN = 'CHANGER-CE-JETON';
const RACINE = 'PWC – Documents achats';

function doPost(e) {
  try {
    const d = JSON.parse(e.postData.contents);
    if (d.token !== TOKEN) return sortie({ok: false, erreur: 'jeton invalide'});
    const blob = Utilities.newBlob(Utilities.base64Decode(d.data), d.mime, d.nom);
    const f = sousDossier(d.dossier || '').createFile(blob);
    if (d.description) f.setDescription(d.description);
    return sortie({ok: true, id: f.getId(), url: f.getUrl()});
  } catch (err) {
    return sortie({ok: false, erreur: String(err)});
  }
}

function doGet(e) {
  try {
    if (e.parameter.token !== TOKEN) return sortie({ok: false, erreur: 'jeton invalide'});
    const f = DriveApp.getFileById(e.parameter.id);
    return sortie({ok: true, nom: f.getName(), mime: f.getMimeType(),
                   data: Utilities.base64Encode(f.getBlob().getBytes())});
  } catch (err) {
    return sortie({ok: false, erreur: String(err)});
  }
}

function racine() {
  const it = DriveApp.getFoldersByName(RACINE);
  return it.hasNext() ? it.next() : DriveApp.createFolder(RACINE);
}

function sousDossier(chemin) {
  let f = racine();
  chemin.split('/').filter(String).forEach(function (n) {
    const it = f.getFoldersByName(n);
    f = it.hasNext() ? it.next() : f.createFolder(n);
  });
  return f;
}

function sortie(o) {
  return ContentService.createTextOutput(JSON.stringify(o)).setMimeType(ContentService.MimeType.JSON);
}
