"""
Nettoyage du texte, pour les documents en arabe ET en français.

Arabe : un texte copié d'un PDF contient souvent des « formes de présentation »
(dessins de lettres, ex. ﻟ ﺔ) et des lettres persanes (ی ک ھ). On les remplace par
les vraies lettres arabes, sinon la recherche et les comparaisons ne marchent plus.

Français : on répare les ligatures de PDF (ﬁ -> fi) et les tirets de coupure invisibles.

On ne touche à RIEN d'autre : les exposants (m², 10³), les fractions (½), les espaces
insécables du français (« : », « ? ») et les accents restent intacts.
"""
import re
import unicodedata

_ARABIC_LETTERS = re.compile("[؀-ۿݐ-ݿ]")

_REPLACEMENTS = str.maketrans(
    {
        # lettres persanes / ourdoues fréquentes dans les PDF arabes
        "ی": "ي",  # ی -> ي
        "ک": "ك",  # ک -> ك
        "ھ": "ه",  # ھ -> ه
        "ہ": "ه",  # ہ -> ه
        # ligatures latines des PDF français
        "ﬀ": "ff",
        "ﬁ": "fi",
        "ﬂ": "fl",
        "ﬃ": "ffi",
        "ﬄ": "ffl",
        "ﬅ": "st",
        "ﬆ": "st",
        # caractères invisibles à supprimer
        "­": None,  # tiret de coupure invisible
        "﻿": None,  # BOM / espace insécable de largeur nulle
        "​": None,  # espace de largeur nulle
    }
)

# Marques de direction invisibles (LRM, RLM, LRE..., isolates)
_BIDI_CONTROLS = re.compile("[‎‏‪-‮⁦-⁩]")
# Espaces ordinaires répétés (l'espace insécable n'est PAS touchée)
_SPACES = re.compile(r"[ \t]{2,}")


def _is_arabic_presentation_form(char: str) -> bool:
    code = ord(char)
    return 0xFB50 <= code <= 0xFDFF or 0xFE70 <= code <= 0xFEFC


def has_presentation_forms(text: str) -> bool:
    """True si le texte contient encore des dessins de lettres arabes."""
    return any(_is_arabic_presentation_form(c) for c in text)


def has_arabic(text: str) -> bool:
    return bool(_ARABIC_LETTERS.search(text))


def normalize_text(text: str) -> str:
    """Répare un texte arabe ou français copié d'un PDF. Ne change pas l'ordre des mots."""
    if not text:
        return text
    text = unicodedata.normalize("NFC", text)  # accents composés : e + ´ -> é
    # dessins de lettres arabes -> vraies lettres, caractère par caractère (rien d'autre)
    text = "".join(
        unicodedata.normalize("NFKC", c) if _is_arabic_presentation_form(c) else c for c in text
    )
    text = text.translate(_REPLACEMENTS)
    text = _BIDI_CONTROLS.sub("", text)
    text = _SPACES.sub(" ", text)
    return text.strip()


def normalize_tree(value):
    """Applique normalize_text à toutes les chaînes d'un dict/list JSON, récursivement."""
    if isinstance(value, str):
        return normalize_text(value)
    if isinstance(value, list):
        return [normalize_tree(v) for v in value]
    if isinstance(value, dict):
        return {k: normalize_tree(v) for k, v in value.items()}
    return value
