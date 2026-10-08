# Fautexprimer — Plan de debug & amélioration

## Phase 1 : Audits & bugs confirmés

### 🔴 Bugs bloquants
1. **Malformed Edge voice entries** : `Google_Gemini_Live_TTS.py:1147-1148` contient `«redacted:sk-…»` → plante `builtin_edge_voices()`
2. **Race condition dans history_store** : suppression de fichiers après fermeture de la connexion SQLite
3. **Clé API exposée dans .env.example** : la clé GEMINI_API_KEY est réelle et lisible

### 🟡 Problèmes de robustesse
4. **google-genai non versionné** dans requirements.txt
5. **Silent failure** sur entrées Edge invalides (pas de log/warning)
6. **Import `Google_Gemini_Live_TTS` avec nom de fichier non-PEP8** (contient des underscores et tirets)
7. **Pas de gestion d'erreur** si `edge_tts.list_voices()` timeout réseau dans le thread bg

### 🟢 Améliorations backend
8. **Découpage** du moteur monolithique (1898 lignes)
9. **Validation** des entrées utilisateur renforcée côté serveur
10. **Cache** des voix Edge avec TTL

### 🔵 Améliorations frontend
11. **Feedback utilisateur** pendant la synthèse (barre de progression)
12. **Gestion d'erreur** réseau plus élégante
13. **Accessibilité** : aria-live regions pour les notifications
14. **UX** : prévisualisation du texte restant avant dépassement