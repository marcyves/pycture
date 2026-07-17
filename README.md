# Pycture

Application GUI Python pour nettoyer et organiser un dossier de photos (et ses sous-dossiers).

## Fonctionnalités

- Organisation en dossiers :
  - `année / mois / jour`
  - `année / mois / événement`
  - `année / événement`
- Renommage optionnel au format `aaaa-mm-jj hh-mm-ss` (date EXIF, sinon date de modification)
- Détection des doublons (empreinte SHA-256) : déplacement vers `_doublons`, suppression, ou conservation
- Suppression des fichiers parasites macOS (`._*`, `.DS_Store`, …)
- Nettoyage des dossiers vides
- Aperçu avant application + miniatures
- Mémorisation du dernier dossier utilisé (`~/.pycture/settings.json`)

## Prérequis

- Python 3.10+
- macOS, Linux ou Windows (interface Tkinter)

## Installation

```bash
git clone https://github.com/marcyves/pycture.git
cd pycture
python3 -m venv .venv
source .venv/bin/activate   # Windows : .venv\Scripts\activate
pip install -r requirements.txt
```

## Lancement

```bash
python main.py
# ou
python -m pycture
```

## Utilisation

1. Choisir le **dossier de travail** (et éventuellement une destination)
2. Régler la structure, le renommage, le traitement des doublons
3. Cliquer sur **Analyser (aperçu)** et vérifier le journal / les miniatures
4. Cliquer sur **Appliquer** pour exécuter les actions

## Structure du projet

```
pycture/
├── main.py
├── requirements.txt
├── pycture/
│   ├── gui.py          # Interface graphique
│   ├── organizer.py    # Organisation, renommage, plan d'actions
│   ├── duplicates.py   # Détection des doublons
│   ├── exif_utils.py   # Date EXIF + filtres fichiers
│   ├── thumbnails.py   # Miniatures
│   └── settings.py     # Préférences (dernier chemin)
└── README.md
```

## Licence

Usage personnel / projet libre — adaptez selon vos besoins.
