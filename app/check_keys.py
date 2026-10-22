"""
Vérifie les clés d'IA et affiche les modèles disponibles.

    python -m app.check_keys

Gemini et Mistral, puis GPT et Claude (facultatifs : seulement pour le benchmark).

Pour Gemini, affiche les modèles « flash » : copier le nom voulu dans GEMINI_MODEL (.env).
"""
from .config import get_settings


def check_gemini(api_key: str, current_model: str, fallback_model: str = "") -> None:
    print("=== Gemini ===")
    if not api_key:
        print("  GEMINI_API_KEY vide dans .env")
        return
    try:
        from google import genai

        client = genai.Client(api_key=api_key)
        names = [m.name.removeprefix("models/") for m in client.models.list()]
    except Exception as exc:
        print(f"  ÉCHEC : {exc}")
        return
    flash = [n for n in names if "flash" in n]
    print(f"  Clé OK : {len(names)} modèles accessibles.")
    print("  Modèles « flash » (à mettre dans GEMINI_MODEL) :")
    for name in flash:
        print(f"    - {name}")
    status = "trouvé ✅" if current_model in names else "INTROUVABLE ❌ (changer GEMINI_MODEL)"
    print(f"  Modèle configuré : {current_model} → {status}")
    if fallback_model:
        status = "trouvé ✅" if fallback_model in names else "INTROUVABLE ❌ (changer GEMINI_FALLBACK_MODEL)"
        print(f"  Modèle de secours : {fallback_model} → {status}")


def check_mistral(api_key: str, current_model: str) -> None:
    print("=== Mistral ===")
    if not api_key:
        print("  MISTRAL_API_KEY vide dans .env")
        return
    try:
        try:
            from mistralai.client import Mistral
        except ImportError:
            from mistralai import Mistral
        client = Mistral(api_key=api_key)
        names = [m.id for m in client.models.list().data]
    except Exception as exc:
        print(f"  ÉCHEC : {exc}")
        return
    print(f"  Clé OK : {len(names)} modèles accessibles.")
    for keyword in ("large", "medium", "small", "ocr"):
        found = [n for n in names if keyword in n]
        if found:
            print(f"    {keyword:<7}: {', '.join(found[:6])}")
    status = "trouvé ✅" if current_model in names else "INTROUVABLE ❌ (changer MISTRAL_MODEL)"
    print(f"  Modèle configuré : {current_model} → {status}")


def check_openai(api_key: str, current_model: str) -> None:
    print("=== OpenAI (GPT) ===")
    if not api_key:
        print("  OPENAI_API_KEY vide dans .env (facultatif : seulement pour comparer)")
        return
    try:
        from openai import OpenAI

        names = sorted(m.id for m in OpenAI(api_key=api_key).models.list())
    except Exception as exc:
        print(f"  ÉCHEC : {exc}")
        return
    gpt = [n for n in names if n.startswith("gpt-")]
    print(f"  Clé OK : {len(names)} modèles accessibles. Modèles « gpt » : {', '.join(gpt[:15])}")
    status = "trouvé ✅" if current_model in names else "INTROUVABLE ❌ (changer OPENAI_MODEL)"
    print(f"  Modèle configuré : {current_model} → {status}")


def check_anthropic(api_key: str, current_model: str) -> None:
    print("=== Anthropic (Claude) ===")
    if not api_key:
        print("  ANTHROPIC_API_KEY vide dans .env (facultatif : seulement pour comparer)")
        return
    try:
        from anthropic import Anthropic

        names = sorted(m.id for m in Anthropic(api_key=api_key).models.list())
    except Exception as exc:
        print(f"  ÉCHEC : {exc}")
        return
    print(f"  Clé OK : {len(names)} modèles accessibles : {', '.join(names[:15])}")
    status = "trouvé ✅" if current_model in names else "INTROUVABLE ❌ (changer ANTHROPIC_MODEL)"
    print(f"  Modèle configuré : {current_model} → {status}")


if __name__ == "__main__":
    s = get_settings()
    check_gemini(s.gemini_api_key, s.gemini_model, s.gemini_fallback_model)
    print()
    check_mistral(s.mistral_api_key, s.mistral_model)
    print()
    check_openai(s.openai_api_key, s.openai_model)
    print()
    check_anthropic(s.anthropic_api_key, s.anthropic_model)
