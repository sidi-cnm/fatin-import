# FATIN – Service d'import de documents

Ce service reçoit un document du programme scolaire (PDF, Word ou texte), **en arabe ou en français**, et renvoie un
**brouillon structuré** au format d'import FATIN :

```
unités → leçons → 2 objectifs → skills → chunks → réponses (avec type d'erreur et message)
```

Deux façons de l'utiliser :
- **une unité à la fois** : `POST /api/parse-document/` ;
- **un livre entier**, découpé automatiquement en 4 étapes (plan → leçon par leçon → liens
  entre leçons → assemblage) : voir « Livre entier » plus bas.

Il **n'enregistre rien**. FATIN affiche le brouillon dans l'écran de relecture, l'admin corrige
et valide, puis FATIN vérifie et enregistre (et génère les codes U01, SK-…).

---

## Le parcours d'un document

```
FATIN (CMS)                         CE SERVICE                                  IA
───────────                         ──────────                                  ──
L'admin dépose le PDF
  → serveur FATIN ── POST /api/parse-document/ ──►
                                    1. lit le fichier
                                    2. demande le découpage ───────────────────► Gemini ou Mistral
                                    3. nettoie le texte (arabe, français) ◄───── JSON brut
                                    4. vérifie le FORMAT (Pydantic)
                                       └─ raté ? 1 seconde chance à l'IA, avec les erreurs
                                    5. vérifie les RÈGLES pédagogiques
  ◄──────────── { draft, issues, meta } ──
L'écran de relecture affiche l'arbre + les problèmes
L'admin valide → FATIN enregistre
```

## Arborescence

```
fatin-import/
├── app/
│   ├── main.py          # l'API : /health, /api/parse-document/, /api/validate/,
│   │                    #         /api/outline/, /api/parse-lesson/, /api/assemble/
│   ├── cli.py           # tester un document en ligne de commande (--auto = livre entier)
│   ├── check_keys.py    # vérifier les clés d'IA et lister les modèles
│   ├── schemas.py       # LE CONTRAT : le format exact du JSON renvoyé
│   ├── rules.py         # les règles pédagogiques (2 objectifs, 1 rule + 1 example + 2 practice…)
│   ├── text_cleaning.py # nettoyage du texte arabe et français copié d'un PDF
│   ├── prompts.py       # les consignes données à l'IA
│   ├── pipeline.py      # traitement d'une unité + outils partagés (pages, appels à l'IA)
│   ├── outline.py       # livre entier, étape 1 : le plan et sa vérification
│   ├── units.py         # livre entier, étape 1 : création des unités (3 règles)
│   ├── lesson.py        # livre entier, étape 2 : une leçon (+ renommage des identifiants)
│   ├── assemble.py      # livre entier, étapes 3 et 4 : liens entre leçons, assemblage
│   ├── config.py        # réglages (.env)
│   ├── security.py      # clé secrète X-API-Key
│   └── providers/       # un fichier par fournisseur d'IA, interchangeables
│       ├── base.py      #   l'interface commune
│       ├── gemini.py    #   Google Gemini : lit le PDF directement
│       ├── mistral.py   #   Mistral : OCR du PDF, puis découpage
│       └── fake.py      #   faux fournisseur : renvoie l'exemple, sans IA
├── samples/
│   └── u01_l01.json     # exemple complet (leçon U01-L01, guide v1.2)
├── tests/               # 107 tests (pytest)
├── .env.example
└── requirements.txt
```

## Installation

```bash
python -m venv .venv
source .venv/bin/activate          # Windows : .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # puis remplir SERVICE_API_KEY et la clé du fournisseur
```

## Lancer

```bash
uvicorn app.main:app --reload
```

Documentation interactive (Swagger) : http://localhost:8000/docs

Pour commencer **sans clé d'IA**, laisser `AI_PROVIDER=fake` : le service renvoie l'exemple
`samples/u01_l01.json`. C'est aussi ce que les développeurs FATIN peuvent utiliser pour
construire l'écran de relecture.

## Tester un vrai document (sans lancer le serveur)

```bash
python -m app.check_keys                                  # vérifie les clés + liste les modèles
python -m app.cli unite1.pdf --language ar --provider gemini
python -m app.cli unite1.pdf --language ar --provider mistral
python -m app.cli livre_complet.pdf --pages 5-24          # seulement les pages de l'unité 1
```

**Une unité à la fois.** Un PDF de plus de 40 pages est refusé sans `--pages` ou `--auto` : c'est
sans doute un livre entier, trop long pour un seul appel. `--pages` accepte `5-24` ou `5-10,12,15-18`.
L'API accepte le même réglage dans le champ `pages`.

**Serveur d'IA surchargé** (erreur 503 ou 429) : le service réessaie tout seul, en attendant
15 s, puis 30 s, puis 60 s (`RETRY_ATTEMPTS`, `RETRY_WAIT_SECONDS`). Pour Gemini, il bascule aussi
sur le modèle de secours `GEMINI_FALLBACK_MODEL`. Si ça échoue encore, essayer plus tard ou
avec l'autre fournisseur.

Le résultat complet est écrit dans `resultats/<nom>_<fournisseur>.json`, avec un résumé à l'écran
(unités, leçons, skills, chunks, problèmes détectés). Si l'IA échoue deux fois, sa dernière
réponse brute est enregistrée dans `resultats/<nom>_<fournisseur>_ECHEC.txt`.

## Livre entier : le découpage automatique

Un seul appel ne peut pas détailler tout un livre (la réponse de l'IA serait coupée). On découpe
donc le travail en petites demandes :

```
Livre (PDF)
  │
  ├─ ÉTAPE 1  LE PLAN              1 ou 2 appels, réponses courtes  outline.py + units.py
  │           1a. l'IA repère : leçons, unités imprimées, pages de bilan
  │           1b. le code crée les unités (3 règles, voir plus bas)
  │           1c. vérification sans IA (pages, chevauchements, oublis, tailles)
  │           → l'admin vérifie et corrige le plan
  │
  ├─ ÉTAPE 2  LEÇON PAR LEÇON      1 appel par leçon               lesson.py
  │           pypdf garde SES pages ; l'IA détaille la leçon ;
  │           s1 devient u1-l3-s1 ; règles vérifiées ; 1 correction si besoin
  │
  ├─ ÉTAPE 3  LES LIENS            1 appel, réponse courte         assemble.py
  │           liste des skills (sans contenu) → prérequis entre leçons
  │
  └─ ÉTAPE 4  L'ASSEMBLAGE         sans IA                         assemble.py
              1 brouillon, même format que /api/parse-document/, revérifié en entier
```

### Création des unités : la même méthode pour tous les livres

L'IA ne crée pas les unités : elle repère seulement ce qui est écrit dans le livre. Le code se
pose ensuite 3 questions, dans l'ordre :

| Règle | Question | Résultat | `origin` / `basis` |
|---|---|---|---|
| 1 | Le livre a des unités imprimées (وحدة، محور، مجال / unité, chapitre) ? | on les garde, avec leur nom | `extracted` / `book` |
| 2 | Sinon, il a des pages de bilan (الحصيلة، إدماج، أوظف مكتسباتي / bilan) ? | on coupe à chaque bilan | `proposed` / `assessment` |
| 3 | Un groupe a plus de `MAX_LESSONS_PER_UNIT` leçons (10) ? | l'IA le recoupe par thème, à partir des seuls titres | `proposed` / `theme` |

Contraintes, pour tous les livres :
- l'ordre des leçons ne change **jamais** : une unité = des leçons qui se suivent ;
- une unité a entre `MIN_LESSONS_PER_UNIT` (3) et `MAX_LESSONS_PER_UNIT` (10) leçons : un groupe
  trop petit rejoint son voisin, un groupe trop gros est recoupé. Les unités imprimées dans le
  livre ne sont jamais modifiées (seulement signalées si elles sont trop grosses) ;
- l'IA nomme les unités proposées et leur donne une question centrale ; le code vérifie que ses
  coupures respectent l'ordre et les tailles. Sinon (ou si l'appel échoue), il coupe en parts
  égales nommées « الوحدة 1 », « Unité 1 »... (`UNITS_FALLBACK`).

Exemple, le livre de maths 1AP (pas d'unités, 3 bilans) : règle 2 → 4 groupes de 17, 17, 5 et
17 leçons ; règle 3 → environ 10 unités de 3 à 9 leçons. Les pages de bilan sont gardées dans le
plan (`breaks`).

**Numéros de pages.** Ce sont des **positions dans le fichier** (1 = première page du PDF), pas les
numéros imprimés. Pour que l'IA ne se trompe pas, chaque page lui est envoyée précédée de
« === PAGE n === » (Gemini) ; avec Mistral, le texte OCR porte les mêmes repères. Pour un livre
lourd (plein d'images), les pages partent en images JPEG légères (100 dpi, paquet `pypdfium2`) :
un livre de 145 pages et 37 Mo passe ainsi à environ 13 Mo.

### En ligne de commande

```bash
# tout d'un coup
python -m app.cli livre.pdf --auto --language ar --provider gemini

# recommandé : d'abord le plan seul, le vérifier, puis le reste
python -m app.cli livre.pdf --auto --plan-only                       # → resultats/livre_plan.json
python -m app.cli livre.pdf --auto --plan resultats/livre_plan.json --units 1    # unité 1 seulement
```

- `--plan-only` : s'arrête après l'étape 1. Ouvrir `resultats/<nom>_plan.json`, corriger les titres
  ou les pages, puis relancer avec `--plan`. Le plan peut aussi être écrit à la main.
- `--units 1` ou `--units 1-2,4` : traiter seulement certaines unités (économise le quota gratuit).
- `--no-links` : sans l'étape 3.
- Résultat : `resultats/<nom>_<fournisseur>_livre.json`.
- Une pause de `PAUSE_BETWEEN_CALLS_SECONDS` (4 s) sépare deux appels, pour les quotas gratuits.
- Une leçon qui échoue n'arrête pas les autres : elle reste vide dans le brouillon (`LESSON_MISSING`).

Durée indicative : environ 1 min pour le plan, puis environ 1 min par leçon.

### Depuis FATIN (3 adresses courtes)

Un livre complet prend plusieurs minutes : une seule requête serait coupée. C'est donc le CMS
qui enchaîne les étapes, et l'admin voit les leçons arriver une par une.

```
1. POST /api/outline/        (file)                         → { outline, total_pages, issues, meta }
      outline = { units: [ {title, central_question, origin, basis, lessons: [...]} ], breaks: [...] }
      l'admin vérifie / corrige le plan à l'écran
2. POST /api/parse-lesson/   (file, outline, unit_index, lesson_index)   ← une fois par leçon
                                                            → { unit_index, lesson_index, lesson, issues, meta }
3. POST /api/assemble/       (JSON : outline + les leçons)  → { draft, issues, meta }   (comme parse-document)
```

`/api/parse-lesson/` (formulaire) :

| Champ | Obligatoire | Exemple |
|---|---|---|
| `file` | oui | le livre entier, le même qu'à l'étape 1 |
| `outline` | oui | le plan validé, en JSON : `{"units": [...]}` |
| `unit_index`, `lesson_index` | oui | `1`, `3` (comptés à partir de 1) |
| `language`, `stage`, `level`, `subject_name`, `provider` | non | comme `/api/parse-document/` |

`/api/assemble/` (JSON) :

```json
{
  "subject": 16, "source_file": "math_1AP.pdf", "language": "ar",
  "outline": { "units": [ ... ] },
  "lessons": [ { "unit_index": 1, "lesson_index": 1, "lesson": { ... } }, ... ],
  "units": [1],
  "find_links": true
}
```

Les réponses de `/api/parse-lesson/` peuvent être mises telles quelles dans `lessons`.
`units` (facultatif) : unités à garder ; vide = toutes celles du plan.

### Problèmes signalés par le découpage automatique

| Code | Niveau | Étape | Signification |
|---|---|---|---|
| `OUTLINE_EMPTY` | error | 1 | Le plan ne contient aucune unité |
| `UNIT_TOO_LARGE` | warning | 1 | Une unité a plus de `MAX_LESSONS_PER_UNIT` leçons |
| `LESSON_DUPLICATE_TITLE` | warning | 1 | Deux leçons ont le même titre : vérifier |
| `UNITS_FALLBACK` | warning | 1 | Unités coupées en parts égales (proposition de l'IA invalide) |
| `UNITS_AI_FAILED` | warning | 1 | L'appel de la règle 3 a échoué |
| `UNIT_NO_LESSONS` | error | 1 | Une unité du plan n'a aucune leçon |
| `PAGE_ORDER` | error | 1 | Une leçon finit avant de commencer |
| `PAGE_OUT_OF_RANGE` | error | 1 | Une leçon dépasse la fin du livre |
| `PAGE_OVERLAP` | error | 1 | Deux leçons se chevauchent (partager UNE page est permis) |
| `PAGE_GAP` | warning | 1 | Des pages ne sont ni dans une leçon, ni dans un bilan repéré |
| `LESSON_TOO_LONG` | warning | 1 | Leçon de plus de 20 pages : découpage à vérifier |
| `LESSON_MISSING` | error | 4 | Une leçon du plan n'a pas été détaillée : la relancer seule |
| `PREREQ_LATER` | warning | 3 | Prérequis enseigné plus loin dans le livre : souvent à l'envers |
| `LINK_UNKNOWN` | warning | 3 | Lien proposé vers une skill qui n'existe pas : ignoré |
| `LINKS_FAILED` | warning | 3 | L'étape 3 a échoué : brouillon livré sans liens entre leçons |

Toutes les règles habituelles (`LESSON_GOALS`, `PREREQ_CYCLE`...) sont revérifiées à l'étape 4
sur le livre entier.

## Tests

```bash
python -m pytest -q
```

---

## Endpoints

Tous les appels (sauf `/health`) exigent l'en-tête `X-API-Key: <SERVICE_API_KEY>`.
Seul le **serveur** FATIN doit connaître cette clé, jamais le navigateur.

### `POST /api/parse-document/`

Formulaire `multipart/form-data` :

| Champ | Obligatoire | Exemple |
|---|---|---|
| `file` | oui | `math_1AP_U01.pdf` (une unité par fichier) |
| `subject` | oui | `16` (id de la matière dans FATIN) |
| `language` | non (défaut `ar`) | `ar` ou `fr` : langue principale du document |
| `stage` | non | `الابتدائية` |
| `level` | non | `السنة الأولى` |
| `subject_name` | non | `الرياضيات` |
| `provider` | non | `gemini`, `mistral` ou `fake` |
| `pages` | non | PDF : pages à garder, ex. `5-24` |

```bash
curl -X POST http://localhost:8000/api/parse-document/ \
  -H "X-API-Key: changez-moi" \
  -F "file=@math_1AP_U01.pdf" -F "subject=16" -F "language=ar" \
  -F "stage=الابتدائية" -F "level=السنة الأولى" -F "subject_name=الرياضيات"
```

Réponse `200` :

```json
{
  "draft":  { "subject": 16, "source_file": "math_1AP_U01.pdf", "language": "ar", "units": [ ... ] },
  "issues": [ { "code": "SKILL_MIN_CHUNKS", "level": "error",
                "path": "units[0].lessons[0].goals[1].skills[0].chunks",
                "message": "La skill « ... » a 1 chunk(s) practice, il en faut au moins 2." } ],
  "meta":   { "provider": "gemini", "model": "...", "duration_seconds": 41.2, "repaired": false }
}
```

- `draft` : le format d'import validé (voir `app/schemas.py`). C'est ce que FATIN enregistre
  après validation de l'admin.
- `issues` : les problèmes à corriger dans l'écran de relecture (`error` = bloquant à
  l'enregistrement, `warning` = à vérifier). `code` est stable : FATIN peut le traduire.
- `meta` : informations techniques.

Erreurs : `400` fichier refusé · `401` clé invalide · `413` fichier trop gros ·
`422` l'IA n'a pas produit un JSON valide après 2 essais · `502` erreur du fournisseur d'IA.

### `POST /api/validate/`

Envoie un brouillon (JSON) et reçoit `{ "valid": true/false, "issues": [...] }`.
Aucune IA n'est appelée. Utile pour tester l'écran de relecture.

---

## Les règles du format (rappel)

- Noms de champs identiques au Swagger FATIN.
- **Jamais** : `id`, `code`, `created_at`, `is_published`, `correct_answer` (FATIN les crée).
- `ref` : identifiant temporaire pour relier les prérequis, disparaît à l'enregistrement.
- `origin` : `extracted` (vient du document) ou `proposed` (écrit par l'IA, à relire de près).
- `needs_image` / `image_note` (sur chaque chunk) : `true` quand le chunk ne se comprend qu'avec une
  image du livre ; `image_note` dit laquelle (page et ce qu'elle montre). **L'admin ajoute l'image**
  dans l'écran de relecture. Côté FATIN, il faut donc un champ image sur le chunk.
- `language` : `ar` (affichage de droite à gauche) ou `fr` (de gauche à droite). Imposé par le service.
- Valeurs autorisées :
  - `goal_type` / `skill_type` : `conceptual`, `operational`
  - `chunk_type` : `rule`, `example`, `practice`, `error`, `hint`, `real_life`
  - `error_category` : `concept`, `procedure`, `careless`

## Règles pédagogiques vérifiées (`issues`)

| Code | Niveau | Règle |
|---|---|---|
| `LESSON_GOALS` | error | Chaque leçon a exactement 2 objectifs : 1 conceptuel, 1 opérationnel |
| `SKILL_TYPE` | error | Le type de la skill = le type de son objectif |
| `SKILL_MIN_CHUNKS` | error | Au moins 1 rule, 1 example, 2 practice par skill |
| `PRACTICE_OPTIONS` / `ONE_CORRECT` | error | Un exercice : au moins 2 réponses, exactement 1 bonne |
| `WRONG_NO_ERROR` / `CORRECT_HAS_ERROR` | error | Type d'erreur sur chaque mauvaise réponse, aucun sur la bonne |
| `OPTIONS_NOT_PRACTICE` | error | Seuls les practice ont des réponses |
| `DUPLICATE_REF`, `PREREQ_SELF`, `PREREQ_UNKNOWN`, `PREREQ_CYCLE` | error | Prérequis cohérents, sans boucle |
| `GOAL_NO_SKILLS`, `NO_UNITS` | error | Pas de niveau vide |
| `WRONG_NO_FEEDBACK` | warning | Message pour l'élève sur chaque mauvaise réponse |
| `ARABIC_BROKEN` | warning | Texte arabe encore abîmé après nettoyage |
| `IMAGE_NEEDED` | warning | Le chunk attend une image : l'admin l'ajoute (voir `image_note`) |
| `IMAGE_REFERENCE` | warning | Le texte parle d'une image (صورة، رسم / image, dessin) mais aucune n'est prévue |
| `LANGUAGE_MISMATCH` | warning | Contenu proposé par l'IA qui n'est pas dans la langue du document |
| `ORDER_DUPLICATE`, `UNIT_NO_LESSONS` | warning | Ordre et contenu à vérifier |

## Ce que l'IA doit produire pour chaque type de chunk

La consigne (`app/prompts.py`) définit strictement chaque type :

| Type | C'est… | Ce n'est pas… |
|---|---|---|
| `rule` | la connaissance à apprendre (définition, propriété, méthode) | une consigne (« colorie la bonne carte »), une simple légende |
| `example` | un cas **résolu**, avec sa réponse | un exercice sans réponse |
| `practice` | une question à choix unique, qui ne contient pas sa réponse | une question dont une réponse répète l'énoncé |
| `error` / `hint` / `real_life` | erreur fréquente expliquée / indice / usage quotidien | — |

Mauvaises réponses : une erreur qu'un enfant ferait vraiment, avec son vrai type
(`concept` = confond les notions, `procedure` = applique mal, `careless` = inattention) et un message
qui explique la bonne idée. Tout ce que l'IA écrit reste `proposed` : l'admin relit et corrige.

## Ajouter un fournisseur d'IA

1. Créer `app/providers/mon_fournisseur.py` avec une classe qui hérite de `AIProvider`
   et implémente `generate(doc, instructions, repair) -> str` (le JSON brut).
2. L'ajouter dans `get_provider()` (`app/providers/__init__.py`).

Rien d'autre ne change : nettoyage, vérifications et format restent les mêmes.

## Points d'attention

- **Un appel = une unité ou une leçon** : un appel à l'IA doit rester court. Pour un livre
  entier, utiliser le découpage automatique.
- **Taille du livre** : l'étape 1 envoie le livre entier. Au-delà de `MAX_FILE_MB` (60 Mo), l'API
  le refuse ; si l'IA refuse un fichier trop lourd, écrire le plan à la main (`--plan`).
- **Offres gratuites** (Gemini, Mistral) : les documents envoyés peuvent servir à entraîner
  leurs modèles. Acceptable pour des manuels publics ; **aucune donnée d'élève** ne passe ici.
- Les noms de modèles changent vite : vérifier dans Google AI Studio / la console Mistral
  le nom exact du modèle gratuit, et le mettre dans `.env`.
- Le nettoyage corrige les lettres (arabe : formes de présentation, lettres persanes ;
  français : ligatures ﬁ/ﬂ, tirets de coupure invisibles). Il ne touche ni aux exposants (m²),
  ni aux fractions (½), ni aux espaces insécables du français, ni à l'ordre des mots.
  C'est pourquoi on envoie le PDF **directement** à l'IA plutôt que d'en extraire le texte.
- Un document = une langue principale. Les termes d'une autre langue (un mot français dans un
  manuel de maths en arabe) sont conservés tels quels.

## Prochaines étapes

1. Tester Gemini et Mistral sur 3 ou 4 vraies leçons (maths en arabe, arabe, une matière en
   français) et comparer.
2. Ajuster `app/prompts.py` selon les erreurs observées.
3. Côté FATIN : appeler ce service depuis le serveur, construire l'écran de relecture,
   et l'endpoint « tout enregistrer » (`POST /api/curriculum/import/`).
4. Côté FATIN, pour un livre entier : écran du plan (corriger titres et pages), puis appeler
   `/api/parse-lesson/` pour chaque leçon et `/api/assemble/`.
5. Plus tard : images du manuel.
