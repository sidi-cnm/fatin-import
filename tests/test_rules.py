from app.pipeline import validate_draft_data


def codes(result):
    return {i.code for i in result.issues if i.level == "error"}


def lesson(sample):
    return sample["units"][0]["lessons"][0]


def test_sample_is_valid(sample):
    result = validate_draft_data(sample)
    assert result.valid, result.issues


def test_unknown_field_is_refused(sample):
    sample["units"][0]["code"] = "U01"  # les codes sont créés par FATIN, jamais par le service
    result = validate_draft_data(sample)
    assert not result.valid
    assert codes(result) == {"FORMAT"}


def test_bad_chunk_type_is_refused(sample):
    lesson(sample)["goals"][0]["skills"][0]["chunks"][0]["chunk_type"] = "Concept"
    assert codes(validate_draft_data(sample)) == {"FORMAT"}


def test_lesson_needs_two_goals(sample):
    lesson(sample)["goals"].pop()
    result = validate_draft_data(sample)
    assert "LESSON_GOALS" in codes(result)


def test_skill_type_must_match_goal(sample):
    lesson(sample)["goals"][0]["skills"][0]["skill_type"] = "operational"
    assert "SKILL_TYPE" in codes(validate_draft_data(sample))


def test_minimum_chunks(sample):
    chunks = lesson(sample)["goals"][0]["skills"][0]["chunks"]
    del chunks[3]  # retire un exercice -> 1 seul practice
    assert "SKILL_MIN_CHUNKS" in codes(validate_draft_data(sample))


def test_exactly_one_correct_answer(sample):
    options = lesson(sample)["goals"][0]["skills"][0]["chunks"][2]["options"]
    options[0]["is_correct"] = True
    options[0]["error_category"] = None
    assert "ONE_CORRECT" in codes(validate_draft_data(sample))


def test_wrong_answer_needs_error_type(sample):
    options = lesson(sample)["goals"][0]["skills"][0]["chunks"][2]["options"]
    options[0]["error_category"] = None
    assert "WRONG_NO_ERROR" in codes(validate_draft_data(sample))


def test_prerequisite_cycle_is_detected(sample):
    lesson(sample)["goals"][0]["skills"][0]["prerequisites"] = ["s-c02"]  # c01 <- c02 <- c01
    assert "PREREQ_CYCLE" in codes(validate_draft_data(sample))


def test_unknown_prerequisite(sample):
    lesson(sample)["goals"][1]["skills"][1]["prerequisites"] = ["s-zzz"]
    assert "PREREQ_UNKNOWN" in codes(validate_draft_data(sample))


def test_language_is_required(sample):
    del sample["language"]
    assert codes(validate_draft_data(sample)) == {"FORMAT"}


def test_wrong_language_is_a_warning(sample):
    sample["language"] = "fr"  # brouillon arabe déclaré en français
    result = validate_draft_data(sample)
    assert result.valid
    assert any(i.code == "LANGUAGE_MISMATCH" for i in result.issues)


def test_french_draft(sample):
    sample["language"] = "fr"
    skill = lesson(sample)["goals"][1]["skills"][0]
    for chunk in skill["chunks"]:
        if chunk["origin"] == "proposed":
            chunk["content"] = "L'enfant regarde devant lui. Où se trouve le ballon ?"
            for o in chunk["options"] or []:
                o["text"] = "à sa droite"
    mismatch = {i.path for i in validate_draft_data(sample).issues if i.code == "LANGUAGE_MISMATCH"}
    assert not any(".goals[1].skills[0]." in p for p in mismatch)


def test_broken_arabic_is_a_warning(sample):
    sample["units"][0]["title"] = "اﻟرﯾﺎﺿﯾﺎت"
    result = validate_draft_data(sample)
    assert result.valid  # simple avertissement
    assert any(i.code == "ARABIC_BROKEN" for i in result.issues)
