# Fautexprimer

Application de bureau Windows de synthèse vocale avec quatre moteurs :

- **Gemini** — voix Gemini Live, 30 voix et styles expressifs ;
- **Edge** — voix Edge TTS multilingues, sans clé API ;
- **MiMo** — clonage vocal à partir d’un clip de référence ;
- **Fish** — clonage zero-shot via Fish Audio, palier développeur gratuit (`s2.1-pro`), 83 langues.

L’interface est affichée dans une fenêtre PyWebView. FastAPI (uvicorn) assure les communications locales entre le frontend HTML/CSS/JavaScript et les moteurs Python. Le frontend ne contient aucun framework et ne nécessite aucune étape de build.

## Fonctionnalités

- Synthèse et lecture dans l’application ;
- export WAV, MP3, OGG, FLAC et M4A ;
- sélection de plusieurs formats et téléchargement groupé dans un ZIP ;
- vitesse de 0,5× à 2× et volume de 0 à 200 % ;
- catalogue Edge hors ligne et recherche multilingue ;
- import d’un clip ou enregistrement direct au microphone ;
- téléchargement audio via la boîte de dialogue Windows ;
- historique local des synthèses, avec lecture, téléchargement, réutilisation et suppression ;
- bibliothèque des clips importés ou enregistrés, avec renommage et sélection pour MiMo ;
- icône Windows et favicon intégrés à l’exécutable et à l’interface ;
- base SQLite créée automatiquement, jamais incluse dans le dossier de l’application ;
- clés API et modèles Gemini/MiMo configurables depuis l’interface, avec sauvegarde locale ;
- compteur dynamique des caractères, mots et phrases selon le moteur sélectionné ;

## Installation depuis les sources

Python 3.11 ou supérieur et `ffmpeg` doivent être disponibles dans le `PATH`.

```powershell
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Lancer l’application

```powershell
.\venv\Scripts\python.exe desktop_launcher.py
```

PyWebView démarre automatiquement le serveur FastAPI sur un port libre local.

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

Ouvrez **Connexion & modèles** dans l’application pour saisir les clés Gemini et MiMo, choisir le modèle Gemini Live et l’endpoint MiMo. Le bouton **Actualiser** interroge l’API Gemini et liste les modèles Live disponibles avec la clé courante. Les changements de modèle sont enregistrés automatiquement ; le bouton **Enregistrer** conserve l’ensemble des options.

La configuration est écrite dans :

```text
%LOCALAPPDATA%\fautexprimer\settings.json
```

Ce fichier est prioritaire au démarrage. Un fichier `.env` reste accepté comme solution de repli :

```env
GEMINI_API_KEY=...
MIMO_API_KEY=...
```

Ordre de priorité pour l’exécutable :

1. `settings.json` (interface) ;
2. `%LOCALAPPDATA%\fautexprimer\.env` ;
3. `.env` dans le dossier de l’exécutable.

Les clés ne sont jamais renvoyées au navigateur par l’API locale. Aucune clé réelle n’est incluse dans le bundle.

## Limites de texte recommandées

Ces plafonds sont des limites prudentes de l’application, validés par des synthèses réelles avec la version actuelle. Ils ne constituent pas une garantie officielle des fournisseurs et peuvent varier selon la langue, la voix, la qualité du réseau et le quota disponible.

| Moteur | Caractères | Mots | Phrases |
| --- | ---: | ---: | ---: |
| Gemini Live | 8 000 | 1 400 | 200 |
| Edge TTS | 20 000 | 3 500 | 600 |
| MiMo | 4 000 | 700 | 120 |
| Fish | 4 000 | 700 | 120 |

Le serveur FastAPI applique ces plafonds également. Pour un texte plus long, utilisez plusieurs synthèses afin d’éviter les timeouts et de conserver une qualité audio stable.

## Données utilisateur

Dans l’exécutable, les données sont stockées dans :

```text
%LOCALAPPDATA%\fautexprimer\
├── settings.json
├── fautexprimer.db
├── .env
├── history\synthesis\
├── uploads\
├── voices\
├── runtime\
│   └── app.log
└── webview\
```

La base et les audios historiques ne sont jamais copiés dans `dist\Fautexprimer`. Le build refuse de produire une distribution si un fichier `.db` y est détecté. L’historique conserve automatiquement les 500 dernières synthèses et les 200 dernières voix ; les éléments plus anciens sont purgés.

## API locale

FastAPI expose notamment :

| Route | Méthode | Rôle |
| --- | --- | --- |
| `/api/health` | GET | État des moteurs et modèles actifs |
| `/api/config` | GET | Voix, vitesses, formats et modèles |
| `/api/api-catalog` | GET | Catalogue des sites et modèles d'API compatibles, avec l'état des clés |
| `/api/settings` | GET / POST | Lire ou enregistrer la configuration locale |
| `/api/test-endpoint` | POST | Tester un endpoint MiMo avec la clé et le clip fournis |
| `/api/models/gemini` | GET | Découvrir les modèles Gemini Live disponibles |
| `/api/voices/edge` | GET | Catalogue Edge |
| `/api/synthesize` | POST | Génération audio |
| `/api/upload-ref` | POST | Importer un clip de référence |
| `/api/history` | GET | Statistiques, synthèses et voix |
| `/api/history/syntheses/<id>/audio` | GET | Lire une synthèse archivée |
| `/api/history/syntheses/<id>/as-voice` | POST | Réutiliser une synthèse comme clip MiMo |
| `/api/history/syntheses/<id>/formats` | GET | Télécharger plusieurs formats en ZIP |
| `/api/history/voices/<id>/audio` | GET | Lire une voix archivée |
| `/api/history/voices/<id>` | PATCH | Renommer une voix |
| `/api/history/...` | DELETE | Supprimer une entrée ou vider une section |

## Licence

MIT — voir `LICENSE`.
