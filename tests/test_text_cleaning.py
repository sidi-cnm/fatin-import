from app.text_cleaning import has_presentation_forms, normalize_text, normalize_tree


# --- Arabe ---------------------------------------------------------------------------
def test_pdf_copied_subject_name_becomes_real_letters():
    broken = "اﻟرﯾﺎﺿﯾﺎت"  # texte réel reçu de l'API FATIN
    assert broken != "الرياضيات"
    assert has_presentation_forms(broken)
    fixed = normalize_text(broken)
    assert fixed == "الرياضيات"
    assert not has_presentation_forms(fixed)


def test_heh_and_spaces_are_cleaned():
    assert normalize_text("اﻟﮭﻧدﺳﺔ   واﻟﻘﯾﺎس") == "الهندسة والقياس"


def test_clean_arabic_is_unchanged():
    text = "الرياضيات هي علم يدرس الأعداد."
    assert normalize_text(text) == text


# --- Français et maths -----------------------------------------------------------------
def test_math_symbols_are_kept():
    assert normalize_text("Surface : 12 m²") == "Surface : 12 m²"
    assert normalize_text("La moitié : ½") == "La moitié : ½"
    assert normalize_text("المساحة 12 م²") == "المساحة 12 م²"


def test_french_non_breaking_spaces_are_kept():
    text = "Il dit : « bonjour »"
    assert normalize_text(text) == text


def test_pdf_ligatures_and_soft_hyphens_are_fixed():
    assert normalize_text("ﬁn de la leçon") == "fin de la leçon"
    assert normalize_text("appren­dre") == "apprendre"


def test_decomposed_accents_are_composed():
    assert normalize_text("leçon") == "leçon"


def test_bom_is_removed_and_not_flagged():
    assert not has_presentation_forms("﻿Titre")
    assert normalize_text("﻿Titre") == "Titre"


def test_tree_normalizes_values_not_keys():
    out = normalize_tree({"title": "اﻟرﯾﺎﺿﯾﺎت", "items": ["ﬁn"], "n": 3})
    assert out == {"title": "الرياضيات", "items": ["fin"], "n": 3}
