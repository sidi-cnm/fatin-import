"""
Tester un document directement, sans lancer le serveur.

    python -m app.cli manuel_unite1.pdf --language ar --provider gemini
    python -m app.cli livre_complet.pdf --pages 5-24        # seulement les pages de l'unité 1

Livre entier, découpage automatique (les 4 étapes) :

    python -m app.cli livre.pdf --auto                      # tout d'un coup
    python -m app.cli livre.pdf --auto --plan-only          # étape 1 seule : le plan, à vérifier
    python -m app.cli livre.pdf --auto --plan resultats/livre_plan.json --units 1   # étapes 2-4, unité 1

Chaque leçon détaillée est enregistrée dans resultats/brouillons/. Si la commande s'arrête
(quota épuisé, coupure), relancer la même commande reprend là où elle s'était arrêtée.
--fresh refait tout.

Écrit le résultat complet dans un fichier JSON (par défaut : resultats/<nom>_<fournisseur>.json)
et affiche un résumé : nombre d'unités, leçons, skills, chunks, et les problèmes détectés.
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

from pydantic import ValidationError

from .assemble import run_assemble
from .config import get_settings
from .lesson import lesson_fingerprint, run_lesson
from .outline import check_outline, outline_errors, run_outline
from .pipeline import (
    MAX_PAGES_WITHOUT_SELECTION,
    PipelineError,
    UnsupportedFile,
    count_pdf_pages,
    extract_pdf_pages,
    read_document,
    run_pipeline,
)
from .providers import PROVIDERS, ProviderError, QuotaExhaustedError, TransientProviderError, get_provider
from .schemas import AssembleRequest, LessonPart, LessonResponse, Outline


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Découper un document au format d'import FATIN.")
    parser.add_argument("file", help="Le document : .pdf, .docx ou .txt (une unité)")
    parser.add_argument("--language", choices=["ar", "fr"], default="ar", help="Langue du document")
    parser.add_argument("--provider", choices=list(PROVIDERS), default=None)
    parser.add_argument("--model", default=None, help="Remplace le modèle du .env (ex. gpt-6-luna)")
    parser.add_argument("--subject", type=int, default=16, help="id de la matière dans FATIN")
    parser.add_argument("--stage", default="", help="ex. الابتدائية")
    parser.add_argument("--level", default="", help="ex. السنة الأولى")
    parser.add_argument("--subject-name", default="", help="ex. الرياضيات")
    parser.add_argument("--pages", default="", help="Pages à garder (PDF), ex. 5-24 ou 5-10,12")
    parser.add_argument("--all-pages", action="store_true", help="Forcer l'envoi d'un long PDF entier")
    parser.add_argument("--out", default=None, help="Fichier JSON de sortie")
    auto = parser.add_argument_group("livre entier (découpage automatique)")
    auto.add_argument("--auto", action="store_true", help="Découper un livre entier : plan, leçons, liens, assemblage")
    auto.add_argument("--plan", default=None, help="Utiliser un plan déjà vérifié (fichier JSON) au lieu de l'étape 1")
    auto.add_argument("--plan-only", action="store_true", help="S'arrêter après l'étape 1 (le plan)")
    auto.add_argument("--units", default="", help="Unités à traiter, ex. 1 ou 1-2,4 (vide = toutes)")
    auto.add_argument("--no-links", action="store_true", help="Sans l'étape 3 (prérequis entre leçons)")
    auto.add_argument("--fresh", action="store_true",
                      help="Refaire toutes les leçons, même celles déjà enregistrées dans resultats/brouillons/")
    args = parser.parse_args(argv)

    path = Path(args.file)
    if not path.exists():
        print(f"Fichier introuvable : {path}")
        return 1

    settings = get_settings()
    provider_name = args.provider or settings.provider
    print(f"Document : {path.name}  |  langue : {args.language}  |  fournisseur : {provider_name}")
    if args.auto:
        return _run_auto(args, path, settings, provider_name)

    try:
        data = path.read_bytes()
        if path.suffix.lower() == ".pdf":
            total = count_pdf_pages(data)
            if args.pages:
                data = extract_pdf_pages(data, args.pages)
                print(f"Pages gardées : {args.pages} ({count_pdf_pages(data)} sur {total})")
            elif total > MAX_PAGES_WITHOUT_SELECTION and not args.all_pages:
                print(f"Ce PDF a {total} pages : c'est sans doute un livre entier.")
                print("Deux solutions :")
                print(f"  - découpage automatique du livre :  python -m app.cli \"{path.name}\" --auto")
                print(f"  - une seule unité :                 python -m app.cli \"{path.name}\" --pages 5-24")
                print("(ou ajoute --all-pages pour forcer l'envoi complet, déconseillé)")
                return 1
            else:
                print(f"Pages : {total}")
        print("Analyse en cours (30 s à 2 min)...")
        doc = read_document(path.name, data)
        ai = get_provider(settings, subject_id=args.subject, name=provider_name, model=args.model)
        result = run_pipeline(
            doc, ai, args.subject, args.language, args.stage, args.level, args.subject_name,
            retry_attempts=settings.retry_attempts,
            retry_wait_seconds=settings.retry_wait_seconds,
            progress=print,
        )
    except UnsupportedFile as exc:
        print(f"Fichier refusé : {exc}")
        return 1
    except TransientProviderError as exc:
        print(f"Le serveur d'IA est toujours occupé après {settings.retry_attempts} essais : {exc}")
        other = "mistral" if provider_name == "gemini" else "gemini"
        print(f"Réessaie dans quelques minutes, ou avec l'autre fournisseur : --provider {other}")
        return 2
    except ProviderError as exc:
        print(f"Erreur du fournisseur d'IA : {exc}")
        return 2
    except PipelineError as exc:
        print("L'IA n'a pas produit un JSON valide après 2 essais :")
        print(exc.errors)
        failed = Path("resultats") / f"{path.stem}_{provider_name}_ECHEC.txt"
        failed.parent.mkdir(exist_ok=True)
        failed.write_text(exc.raw_output, encoding="utf-8")
        print(f"Dernière réponse brute de l'IA enregistrée dans : {failed}")
        return 3

    out = Path(args.out) if args.out else Path("resultats") / f"{path.stem}_{provider_name}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")

    _print_summary(result)
    print(f"\nRésultat complet : {out}")
    return 0


def _print_summary(result) -> None:
    units = result.draft.units
    lessons = [l for u in units for l in u.lessons]
    skills = [s for l in lessons for g in l.goals for s in g.skills]
    chunks = [c for s in skills for c in s.chunks]
    proposed = sum(1 for c in chunks if c.origin == "proposed")
    errors = [i for i in result.issues if i.level == "error"]
    warnings = [i for i in result.issues if i.level == "warning"]

    print("\n=== Résumé ===")
    print(f"Unités : {len(units)}  |  Leçons : {len(lessons)}  |  Skills : {len(skills)}  |  Chunks : {len(chunks)}")
    print(f"Chunks proposés par l'IA : {proposed} / {len(chunks)}")
    images = sum(1 for c in chunks if c.needs_image)
    if images:
        print(f"Chunks qui attendent une image (à ajouter par l'admin) : {images}")
    to_check = sum(1 for i in result.issues if i.code == "ANSWER_FROM_PICTURE")
    if to_check:
        print(f"Exercices dont la bonne réponse est à vérifier sur la page du livre : {to_check}")
    print(f"Durée : {result.meta.duration_seconds} s  |  Modèle : {result.meta.model}"
          f"  |  Seconde chance utilisée : {'oui' if result.meta.repaired else 'non'}")
    print(f"Problèmes : {len(errors)} erreur(s), {len(warnings)} avertissement(s)")
    for issue in (errors + warnings)[:15]:
        print(f"  [{issue.level}] {issue.code} – {issue.message}")
    if len(result.issues) > 15:
        print(f"  ... et {len(result.issues) - 15} autres (voir le fichier JSON)")


# --- Livre entier : les 4 étapes ---------------------------------------------------------
def _run_auto(args, path: Path, settings, provider_name: str) -> int:
    if path.suffix.lower() != ".pdf":
        print("Le découpage automatique accepte seulement un PDF.")
        return 1
    started = time.monotonic()
    common = dict(
        language=args.language, stage=args.stage, level=args.level, subject_name=args.subject_name,
        retry_attempts=settings.retry_attempts, retry_wait_seconds=settings.retry_wait_seconds,
    )
    try:
        data = path.read_bytes()
        total = count_pdf_pages(data)
        ai = get_provider(settings, subject_id=args.subject, name=provider_name, model=args.model)

        # ÉTAPE 1 : le plan (calculé par l'IA, ou lu dans un fichier déjà vérifié)
        if args.plan:
            plan_path = Path(args.plan)
            raw = json.loads(plan_path.read_text(encoding="utf-8"))
            outline = Outline.model_validate(raw.get("outline", raw))
            issues = check_outline(outline, total, settings.max_lessons_per_unit)
            print(f"\nÉtape 1 : plan lu dans {plan_path}")
        else:
            print(f"\nÉtape 1 : lecture du plan du livre ({total} pages, environ 1 min)...")
            response = run_outline(
                data, path.name, ai, progress=print,
                min_lessons=settings.min_lessons_per_unit, max_lessons=settings.max_lessons_per_unit, **common,
            )
            outline, issues = response.outline, response.issues
            plan_path = Path("resultats") / f"{path.stem}_plan.json"
            plan_path.parent.mkdir(parents=True, exist_ok=True)
            plan_path.write_text(json.dumps(outline.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"Plan enregistré : {plan_path}")
    except (OSError, ValueError, ValidationError) as exc:
        print(f"Plan ou fichier illisible : {exc}")
        return 1
    except (ProviderError, PipelineError) as exc:
        print(f"Étape 1 impossible : {exc}")
        return 2

    _print_outline(outline, issues, total)
    again = f'python3 -m app.cli "{path}" --auto --plan "{plan_path}"'
    if outline_errors(issues):
        print(f"\nLe plan a des erreurs. Corrige le fichier {plan_path}, puis relance :\n  {again}")
        return 4
    if args.plan_only:
        print(f"\nVérifie le plan (titres, pages) dans {plan_path}, corrige-le si besoin, puis lance :\n  {again}")
        return 0

    try:
        units = _parse_units(args.units, len(outline.units))
    except ValueError as exc:
        print(exc)
        return 1

    # ÉTAPE 2 : leçon par leçon. Chaque leçon réussie est enregistrée tout de suite dans
    # resultats/brouillons/ : si la commande s'arrête (quota, coupure), la relancer REPREND
    # là où elle s'était arrêtée, sans refaire (ni repayer) les leçons déjà faites.
    saved_dir = Path("resultats") / "brouillons" / f"{path.stem}_{provider_name}"
    again_units = f"{again} --units {args.units}" if args.units else again
    targets = [(ui, li, lesson) for ui in units for li, lesson in enumerate(outline.units[ui - 1].lessons, start=1)]
    parts: list[LessonPart] = []
    failed: list[str] = []
    called = False  # une pause seulement entre 2 vrais appels à l'IA
    for k, (ui, li, planned) in enumerate(targets, start=1):
        print(f"\nÉtape 2 : leçon {k}/{len(targets)} (unité {ui}, leçon {li}) : {planned.title}"
              f"  [pages {planned.start_page}-{planned.end_page}]")
        fingerprint = lesson_fingerprint(outline, ui, li, ai.model, args.language, args.stage, args.level,
                                         args.subject_name)
        saved_file = saved_dir / f"u{ui}-l{li}.json"
        result = None if args.fresh else _load_saved_lesson(saved_file, fingerprint)
        if result is not None:
            print(f"   ↺ déjà faite (enregistrée dans {saved_file}) : reprise sans appeler l'IA")
            parts.append(LessonPart(unit_index=ui, lesson_index=li, lesson=result.lesson))
            continue
        if called:
            time.sleep(settings.pause_between_calls_seconds)
        called = True
        try:
            result = run_lesson(data, path.name, outline, ui, li, ai,
                                progress=lambda m: print(f"   {m}"), **common)
        except QuotaExhaustedError as exc:  # inutile d'essayer les leçons suivantes : même quota
            print(f"   ✘ quota épuisé : {str(exc)[:300]}")
            done = len(parts)
            print(f"\n⛔ Quota de l'IA épuisé : arrêt. Leçons enregistrées : {done}/{len(targets)} "
                  f"(dans {saved_dir}).")
            print("Que faire : attendre que le quota revienne (quota gratuit du jour : vers 7 h à Nouakchott)")
            print("ou activer la facturation, puis relancer LA MÊME commande. Les leçons déjà faites seront reprises :")
            print(f"  {again_units}")
            return 5
        except (TransientProviderError, PipelineError) as exc:
            print(f"   ✘ échec : {str(exc)[:300]}")
            failed.append(f"unité {ui}, leçon {li} : {planned.title}")
            continue
        except ProviderError as exc:  # clé invalide, modèle inconnu... inutile de continuer
            print(f"   ✘ erreur du fournisseur : {exc}")
            return 2
        _save_lesson(saved_file, fingerprint, result)
        skills = [s for g in result.lesson.goals for s in g.skills]
        chunks = sum(len(s.chunks) for s in skills)
        errors = sum(1 for i in result.issues if i.level == "error")
        fixed = "  (corrigée une fois)" if result.meta.repaired else ""
        print(f"   ✔ {len(skills)} skills, {chunks} chunks, {errors} erreur(s){fixed}  –  {result.meta.duration_seconds} s")
        parts.append(LessonPart(unit_index=ui, lesson_index=li, lesson=result.lesson))

    # ÉTAPES 3 et 4 : liens entre leçons, assemblage, vérification
    print()
    if called and len(targets) >= 2 and not args.no_links:
        time.sleep(settings.pause_between_calls_seconds)
    request = AssembleRequest(
        subject=args.subject, source_file=path.name, language=args.language, outline=outline,
        lessons=parts, units=units, find_links=not args.no_links,
        stage=args.stage, level=args.level, subject_name=args.subject_name,
    )
    result = run_assemble(request, ai, settings.retry_attempts, settings.retry_wait_seconds, progress=print)
    result.meta.duration_seconds = round(time.monotonic() - started, 2)

    suffix = f"_unites_{args.units.replace(',', '_')}" if args.units else ""  # un fichier par lot d'unités
    out = Path(args.out) if args.out else Path("resultats") / f"{path.stem}_{provider_name}_livre{suffix}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")

    _print_summary(result)
    if failed:
        print(f"\nLeçons non détaillées ({len(failed)}) : elles sont vides dans le brouillon (LESSON_MISSING).")
        for name in failed:
            print(f"  - {name}")
        print("Relance la même commande : les leçons déjà faites seront reprises, seules celles-ci seront refaites :")
        print(f"  {again_units}")
    print(f"\nRésultat complet : {out}")
    return 0


def _load_saved_lesson(file: Path, fingerprint: str):
    """Une leçon déjà détaillée, si elle a été faite avec le même plan, le même prompt et le même modèle."""
    if not file.exists():
        return None
    try:
        saved = json.loads(file.read_text(encoding="utf-8"))
        if saved.get("fingerprint") != fingerprint:
            print("   (une version enregistrée existe, mais le plan, le prompt ou le modèle a changé : on la refait)")
            return None
        return LessonResponse.model_validate(saved["result"])
    except (OSError, ValueError, KeyError, ValidationError):
        return None  # fichier abîmé : on refait la leçon


def _save_lesson(file: Path, fingerprint: str, result: LessonResponse) -> None:
    file.parent.mkdir(parents=True, exist_ok=True)
    payload = {"fingerprint": fingerprint, "result": result.model_dump()}
    file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _parse_units(spec: str, count: int) -> list[int]:
    if not spec.strip():
        return list(range(1, count + 1))
    units: list[int] = []
    for part in spec.replace(" ", "").split(","):
        match = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not match:
            raise ValueError(f"--units invalide : « {part} ». Exemple : 1 ou 1-2,4")
        start, end = int(match.group(1)), int(match.group(2) or match.group(1))
        if start < 1 or end < start or end > count:
            raise ValueError(f"--units « {part} » : le plan a {count} unité(s).")
        units.extend(u for u in range(start, end + 1) if u not in units)
    return units


def _print_outline(outline: Outline, issues, total_pages: int) -> None:
    lessons = sum(len(u.lessons) for u in outline.units)
    print(f"\n=== Plan : {len(outline.units)} unité(s), {lessons} leçon(s), livre de {total_pages} pages ===")
    origins = {"book": "écrite dans le livre", "assessment": "proposée : coupée aux bilans",
               "theme": "proposée : coupée par thème"}
    breaks = sorted(outline.breaks, key=lambda b: b.start_page)
    shown = set()
    for ui, unit in enumerate(outline.units, start=1):
        first = unit.lessons[0].start_page if unit.lessons else 0
        for b in breaks:  # les pages de bilan placées avant cette unité
            if b.start_page < first and id(b) not in shown:
                shown.add(id(b))
                print(f"   ▓ {b.title}   [pages {b.start_page}-{b.end_page}]")
        print(f"Unité {ui} : {unit.title}   ({len(unit.lessons)} leçons, {origins.get(unit.basis, unit.basis)})")
        if unit.central_question:
            print(f"   ? {unit.central_question}")
        for li, lesson in enumerate(unit.lessons, start=1):
            print(f"   {li}. {lesson.title}   [pages {lesson.start_page}-{lesson.end_page}]")
    for b in breaks:
        if id(b) not in shown:
            print(f"   ▓ {b.title}   [pages {b.start_page}-{b.end_page}]")
    for issue in issues:
        print(f"  [{issue.level}] {issue.code} – {issue.message}")


if __name__ == "__main__":
    sys.exit(main())
