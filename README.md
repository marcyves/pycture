# Pycture

Application GUI Python pour nettoyer et organiser un dossier de photos (et ses sous-dossiers).

**Dépôt :** [github.com/marcyves/pycture](https://github.com/marcyves/pycture)

[![CI](https://github.com/marcyves/pycture/actions/workflows/python-app.yml/badge.svg)](https://github.com/marcyves/pycture/actions/workflows/python-app.yml)
[![Issues](https://img.shields.io/github/issues/marcyves/pycture?style=flat-square)](https://github.com/marcyves/pycture/issues)
[![License: GPL-3.0](https://img.shields.io/badge/License-GPL%20v3-blue.svg?style=flat-square)](./LICENSE)
[![LinkedIn](https://img.shields.io/badge/LinkedIn-Marc%20Augier-0A66C2?style=flat-square&logo=linkedin)](https://linkedin.com/in/marcaugier)

## Fonctionnalités

- Organisation des photos en dossiers :
  - `année / mois / jour`
  - `année / mois / événement`
  - `année / événement`
- Si le dossier choisi s'appelle déjà une année (`…/2003`), l'organisation se fait dans le **dossier parent** (évite `2003/2003/…`)
- Vidéos (AVI, MP4, MOV, …) regroupées dans `année / video`
- Renommage optionnel au format `aaaa-mm-jj hh-mm-ss`
- Alignement optionnel des dates filesystem (création / modification) sur une date fiable
- Détection des doublons (empreinte SHA-256 du fichier) : `_doublons`, suppression, ou conservation
- Suppression des fichiers parasites macOS (`._*`, `.DS_Store`, …)
- Nettoyage des dossiers vides
- Aperçu avant application + miniatures
- Mémorisation du dernier dossier utilisé (`~/.pycture/settings.json`)
- **Import photothèque Apple** (`.photoslibrary`) : copie les originaux vers un dossier, puis organisation Pycture

## Stratégie de datation

Pour ranger / renommer une photo, Pycture choisit une date dans cet ordre :

1. **EXIF `DateTimeOriginal`** (prise de vue)
2. **EXIF `DateTimeDigitized`**
3. **Date explicite dans le nom de fichier**  
   ex. `2005-08-15 14-30-22`, `20050815_143022`, `2005-08-15`
4. **EXIF `DateTime`** (souvent une date d’édition — peu fiable)
5. **Date de modification du fichier** (mtime — souvent une date d’export / copie)

### Cas particuliers

- L’alignement des dates filesystem et le renommage « date/heure » ne s’appliquent que si la date est **fiable** (EXIF prise de vue / numérisation, ou date dans le nom).
- Si vous travaillez dans un dossier nommé comme une année (`…/Photos/2005`) :
  - une date fiable d’une **autre** année provenant seulement du mtime, d’un `DateTime` faible, ou d’un nom « mauvaise année » → le fichier reste dans `2005/_sans_exif` (on ne sort pas du dossier année) ;
  - un vrai `DateTimeOriginal` / `Digitized` d’une autre année peut encore classer la photo hors de `2005` (EXIF prioritaire).

## Stratégie des doublons

### Qu’est-ce qu’un doublon ?

Deux fichiers sont des doublons si et seulement si leur **contenu binaire est identique** :

1. même **taille** (filtre rapide) ;
2. même empreinte **SHA-256** calculée sur **tout le fichier**.

Conséquences importantes :

- Même empreinte ⇒ **mêmes octets** ⇒ même image **et** mêmes métadonnées EXIF embarquées.
- On ne peut **pas** avoir la même empreinte et un EXIF différent : l’EXIF fait partie du fichier.
- En revanche, le **nom** et le **dossier** peuvent différer (`CER105.JPG` vs `2003-09-10 14-57-58.jpg`) tout en étant la même copie bit à bit.
- Ce ne sont **pas** des doublons : même scène recompressée, redimensionnée, ou avec EXIF modifié (l’empreinte change).

### Quelle copie est conservée ?

Dans un groupe de fichiers identiques, Pycture garde en priorité :

1. un nom qui n’est **pas** déjà au format `aaaa-mm-jj hh-mm-ss` ;
2. le fichier au **mtime** le plus ancien ;
3. le chemin le plus court.

Les autres copies suivent l’option choisie dans l’interface : déplacement vers `_doublons`, suppression, ou conservation de toutes les copies.

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

### Import depuis Photos (Apple)

Pycture peut **exporter** les originaux d’une photothèque macOS (paquet `.photoslibrary`) vers un dossier de votre choix, puis les organiser comme n’importe quel autre dossier source.

**Important :** la photothèque n’est jamais modifiée — uniquement des copies.

1. Renseigner le champ **Destination** (obligatoire pour cet import)
2. Cliquer sur **Photothèque Apple…**
3. Sélectionner un paquet `Nom.photoslibrary` (souvent dans `~/Pictures`)
4. Pycture parcourt `originals/`, lit éventuellement `Photos.sqlite` pour retrouver les noms d’origine, et **copie** les fichiers vers la destination
5. Le dossier source Pycture est basculé sur cette destination → **Analyser** puis **Appliquer**

Notes :

- Les médias uniquement dans iCloud (non téléchargés sur le Mac) sont ignorés et listés comme absents.
- Accordez à Terminal / Python un accès disque complet (Réglages → Confidentialité et sécurité) si macOS bloque la lecture de la photothèque.
- Les vidéos suivent l’option « Inclure les vidéos ».
- Sur place (sans destination) reste possible pour un dossier photos classique ; la photothèque Apple exige toujours une destination.

## Tests

La suite `pytest` couvre le cœur métier (dates, doublons, organisation, inventaire, export photothèque minimal) :

```bash
source .venv/bin/activate
pip install -r requirements.txt pytest
pytest
```

Fichiers concernés :

- `tests/test_core.py` — tests unitaires
- `pytest.ini` — `pythonpath = .` pour importer le package local
- `.github/workflows/python-app.yml` — CI (flake8 + pytest) sur chaque push / PR vers `main`

## Structure du projet

```
pycture/
├── main.py
├── requirements.txt
├── pytest.ini
├── LICENSE
├── .github/workflows/
│   └── python-app.yml  # CI GitHub Actions
├── pycture/
│   ├── gui.py           # Interface graphique
│   ├── organizer.py     # Organisation, renommage, plan d'actions
│   ├── duplicates.py    # Détection des doublons
│   ├── exif_utils.py    # Date EXIF + filtres fichiers
│   ├── photoslibrary.py # Export depuis .photoslibrary Apple
│   ├── thumbnails.py    # Miniatures
│   └── settings.py      # Préférences (dernier chemin)
├── tests/
│   └── test_core.py
└── README.md
```

## Licence

Distribué sous **GNU GPLv3** — voir le fichier [`LICENSE`](./LICENSE).

---

## Soutenir le projet

Si le projet vous est utile, vous pouvez soutenir le travail :  
[![Buy Me A Coffee](https://cdn.buymeacoffee.com/buttons/v2/default-blue.png)](https://www.buymeacoffee.com/marcyves)

---

## Contact

- LinkedIn : [Marc Augier](https://linkedin.com/in/marcaugier)
- GitHub : [marcyves](https://github.com/marcyves)
