import os
import json
import tempfile
import io
import pandas as pd
import numpy as np
import gdown
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# ==========================================
# 1. CONFIGURATION ET IDENTIFIANTS
# ==========================================
FILE_ID_ADS = '1_8quOdA863-70Q-vOfIRrvC6qe9eGAht'
FILE_ID_FLUX = '1aCJeea5ZzVpvhFjYWeOBfigaGEi8R86-'
DRIVE_FOLDER_ID = '1wz2Ke7rnmicVzSBl_-ALsK0EKJzQPQQw'

# 2. TÉLÉCHARGEMENT DES DONNÉES SOURCES
def download_data(temp_dir):
    print("Téléchargement des fichiers sources...")
    path_ads = os.path.join(temp_dir, 'ads_data.xlsx')
    path_flux = os.path.join(temp_dir, 'flux_shopping.xml')
    
    gdown.download(id=FILE_ID_ADS, output=path_ads, quiet=True)
    gdown.download(id=FILE_ID_FLUX, output=path_flux, quiet=True)
    
    return path_ads, path_flux

# 3. CHARGEMENT ET NETTOYAGE
def load_data(path_ads, path_flux):
    print("Chargement des données Google Ads...")
    df_ads = pd.read_excel(path_ads, header=2)
    df_ads.columns = df_ads.columns.str.strip()
    
    print("Nettoyage et chargement du flux WooCommerce en mémoire...")
    # OPTIMISATION 2 : Nettoyage et chargement 100% en mémoire vive (sans réécriture disque)
    with open(path_flux, 'rb') as f:
        content_clean = f.read().replace(b'\x00', b'')
        
    try:
        df_flux = pd.read_xml(io.BytesIO(content_clean), xpath='.//item')
    except Exception:
        df_flux = pd.read_xml(io.BytesIO(content_clean))
        
    return df_ads, df_flux

# 4. MATCHING ET ANALYSE
def process_matching(df_ads, df_flux, temp_dir):
    print("Traitement et filtrage des mots-clés...")

    # Nettoyage strict des totaux et lignes vides
    df_ads = df_ads[df_ads['Terme de recherche'].notna()].copy()
    df_ads['Terme de recherche'] = df_ads['Terme de recherche'].astype(str).str.strip()
    df_ads = df_ads[~df_ads['Terme de recherche'].str.startswith('Total')].copy()
    df_ads = df_ads[~df_ads['Terme de recherche'].str.contains('Total :', case=False, na=False)].copy()

    # Conversions numériques
    df_ads['Conversions'] = pd.to_numeric(df_ads['Conversions'], errors='coerce').fillna(0)
    df_ads['Coût'] = pd.to_numeric(df_ads['Coût'], errors='coerce').fillna(0)

    # Termes convertisseurs
    termes_convertisseurs = df_ads[df_ads['Conversions'] > 0].copy()
    termes_convertisseurs['CPA'] = termes_convertisseurs['Coût'] / termes_convertisseurs['Conversions']
    
    if 'Valeur de conv./coût' in termes_convertisseurs.columns:
        termes_convertisseurs['ROAS'] = pd.to_numeric(termes_convertisseurs['Valeur de conv./coût'], errors='coerce')
    else:
        termes_convertisseurs['ROAS'] = 0

    # OPTIMISATION 1 : Super-chaîne pour un matching vectorisé ultra-rapide
    all_titles_blob = " || ".join(df_flux['title'].astype(str).str.lower().tolist())
    
    termes_convertisseurs['Present_dans_WooCommerce'] = termes_convertisseurs['Terme de recherche'].apply(
        lambda terme: str(terme).lower().strip() in all_titles_blob
    )

    # Opportunités & Négatifs
    opportunites = termes_convertisseurs[termes_convertisseurs['Present_dans_WooCommerce'] == False].sort_values(
        by='Conversions', ascending=False
    )
    
    mots_cles_negatifs = df_ads[(df_ads['Conversions'] == 0) & (df_ads['Coût'] > 10)].sort_values(
        by='Coût', ascending=False
    )

    # Sauvegarde temporaire avant upload
    path_opp = os.path.join(temp_dir, 'opportunites_titres_woocommerce.csv')
    path_neg = os.path.join(temp_dir, 'mots_cles_negatifs_shopping.csv')
    
    opportunites.to_csv(path_opp, index=False)
    mots_cles_negatifs.to_csv(path_neg, index=False)

    print(f"Analyse terminée : {len(opportunites)} opportunités et {len(mots_cles_negatifs)} termes négatifs trouvés.")
    return path_opp, path_neg

# 5. UPLOAD / MISE À JOUR GOOGLE DRIVE
def get_drive_service():
    """Authentification via Compte de Service (GitHub Secret ou credentials.json local)."""
    scopes = ['https://www.googleapis.com/auth/drive']
    
    if 'GDRIVE_CREDENTIALS' in os.environ:
        creds_json = json.loads(os.environ['GDRIVE_CREDENTIALS'])
        creds = service_account.Credentials.from_service_account_info(creds_json, scopes=scopes)
    elif os.path.exists('credentials.json'):
        creds = service_account.Credentials.from_service_account_file('credentials.json', scopes=scopes)
    else:
        print("Aucune clé Google Drive détectée (GDRIVE_CREDENTIALS ou credentials.json). Upload ignoré.")
        return None

    return build('drive', 'v3', credentials=creds)

def upload_or_update_file(service, file_path, folder_id):
    """Téléverse un fichier sur Drive ou met à jour le fichier existant du même nom."""
    file_name = os.path.basename(file_path)
    media = MediaFileUpload(file_path, mimetype='text/csv', resumable=True)

    query = f"'{folder_id}' in parents and name = '{file_name}' and trashed = false"
    results = service.files().list(q=query, fields="files(id, name)").execute()
    items = results.get('files', [])

    if items:
        file_id = items[0]['id']
        updated_file = service.files().update(fileId=file_id, media_body=media).execute()
        print(f"Fichier '{file_name}' mis à jour sur Google Drive (ID: {updated_file.get('id')})")
    else:
        file_metadata = {'name': file_name, 'parents': [folder_id]}
        new_file = service.files().create(body=file_metadata, media_body=media, fields='id').execute()
        print(f"Fichier '{file_name}' créé sur Google Drive (ID: {new_file.get('id')})")

# 6. EXÉCUTION PRINCIPALE
if __name__ == "__main__":
    # OPTIMISATION 3 : Verification corrigée
    if not DRIVE_FOLDER_ID or DRIVE_FOLDER_ID == '1wz2Ke7rnmicVzSBl_-ALsK0EKJzQPQQw':
        print("Erreur : Veuillez renseigner DRIVE_FOLDER_ID dans main.py avec l'ID de votre dossier Google Drive.")
    else:
        with tempfile.TemporaryDirectory() as temp_dir:
            path_ads, path_flux = download_data(temp_dir)
            df_ads, df_flux = load_data(path_ads, path_flux)
            path_opp, path_neg = process_matching(df_ads, df_flux, temp_dir)
            
            service = get_drive_service()
            if service:
                upload_or_update_file(service, path_opp, DRIVE_FOLDER_ID)
                upload_or_update_file(service, path_neg, DRIVE_FOLDER_ID)
