# Fautexprimer

Application de bureau Windows de synthèse vocale avec trois moteurs :

- **Eben** — voix Gemini Live, 30 voix et styles expressifs ;
- **Dane** — voix Edge TTS multilingues, sans clé API ;
- **Michel** — clonage vocal à partir d’un clip de référence.

L’interface est affichée dans une fenêtre PyWebView. Flask assure les communications locales entre le frontend HTML/CSS/JavaScript et les moteurs Python. Le frontend ne contient aucun framework et ne nécessite aucune étape de build.

## Fonctionnalités

- Synthèse et lecture dans l’application ;
- export WAV, MP3, OGG, FLAC et M4A ;
- sélection de plusieurs formats et téléchargement groupé dans un ZIP ;
- vitesse de 0,5× à 2× et volume de 0 à 200 % ;
- catalogue Edge hors ligne et recherche multilingue ;
- import d’un clip ou enregistrement direct au microphone ;
- téléchargement audio via la boîte de dialogue Windows ;
- historique local des synthèses, avec lecture, téléchargement, réutilisation et suppression ;
- bibliothèque des clips importés ou enregistrés, avec renommage et sélection pour Michel ;
- icône Windows et favicon intégrés à l’exécutable et à l’interface ;
- base SQLite créée automatiquement, jamais incluse dans le dossier de l’application ;
- compteur dynamique des caractères, mots et phrases selon le moteur sélectionné ;

## Installation depuis les sources

Python 3.11 ou supérieur et `ffmpeg` doivent être disponibles dans le `PATH`.

```powershell
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Lancer l’application

```powershell
.\venv\Scripts\python.exe desktop_app.py
```

PyWebView démarre automatiquement le serveur Flask sur un port libre local.

## Compiler l’exécutable Windows

```powershell
.\Compiler-Fautexprimer.ps1
```

La distribution est produite dans :

```text
dist\Fautexprimer\Fautexprimer.exe
```

Le dossier `dist\Fautexprimer` constitue la distribution complète. Il contient le frontend, PyWebView/WebView2, les moteurs TTS, le décodeur MP3 et FFmpeg. Conservez-le entier lors du déplacement ou de la distribution.

## Configuration

Copiez `.env.example` et renommez la copie `.env` :

```env
GEMINI_API_KEY=...
MIMO_API_KEY=...
```

Ordre de priorité pour l’exécutable :

1. `%LOCALAPPDATA%\Fautexprimer\.env` ;
2. `.env` dans le dossier de l’exécutable.

Aucune clé réelle n’est incluse dans le bundle.

## Limites de texte recommandées

Ces plafonds sont des limites prudentes de l’application, validés par des synthèses réelles avec la version actuelle. Ils ne constituent pas une garantie officielle des fournisseurs et peuvent varier selon la langue, la voix, la qualité du réseau et le quota disponible.

| Moteur | Caractères | Mots | Phrases |
| --- | ---: | ---: | ---: |
| Eben / Gemini Live | 8 000 | 1 400 | 200 |
| Dane / Edge TTS | 20 000 | 3 500 | 600 |
| Michel / MiMo | 4 000 | 700 | 120 |

Le serveur Flask applique ces plafonds également. Pour un texte plus long, utilisez plusieurs synthèses afin d’éviter les timeouts et de conserver une qualité audio stable.

## Données utilisateur

Dans l’exécutable, les données sont stockées dans :

```text
%LOCALAPPDATA%\Fautexprimer\
├── .env
├── fautexprimer.db
├── history\synthesis\
├── voices\
├── runtime\
│   └── app.log
└── webview\
```

La base et les audios historiques ne sont jamais copiés dans `dist\Fautexprimer`. Le build refuse de produire une distribution si un fichier `.db` y est détecté. L’historique conserve automatiquement les 500 dernières synthèses et les 200 dernières voix ; les éléments plus anciens sont purgés.

## API locale

Flask expose notamment :

| Route | Méthode | Rôle |
| --- | --- | --- |
| `/api/health` | GET | État des moteurs |
| `/api/config` | GET | Voix, vitesses et formats |
| `/api/voices/edge` | GET | Catalogue Edge |
| `/api/synthesize` | POST | Génération audio |
| `/api/upload-ref` | POST | Importer un clip de référence |
| `/api/history` | GET | Statistiques, synthèses et voix |
| `/api/history/syntheses/<id>/audio` | GET | Lire une synthèse archivée |
| `/api/history/syntheses/<id>/formats` | GET | Télécharger plusieurs formats en ZIP |
| `/api/history/voices/<id>/audio` | GET | Lire une voix archivée |
| `/api/history/voices/<id>` | PATCH | Renommer une voix |
| `/api/history/...` | DELETE | Supprimer une entrée ou vider une section |

## Licence

MIT — voir `LICENSE`.
