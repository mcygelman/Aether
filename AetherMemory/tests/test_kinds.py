import pytest

from memory_fabric.kinds import UnknownKind, resolve

PROFILES = ["project_fact", "safety_warning", "standing_lesson", "core_knowledge",
            "task_obligation", "scratch", "offloaded_context"]


@pytest.mark.parametrize("words,expected", [
    ("project fact", "project_fact"), ("Safety Warning", "safety_warning"),
    ("warning", "safety_warning"), ("a safety warning about doors", "safety_warning"),
    ("lesson learned", "standing_lesson"), ("todo", "task_obligation"),
    ("note to self", "scratch"), ("core principle", "core_knowledge"),
    ("standing_lesson", "standing_lesson"), ("set aside", "offloaded_context"),
])
def test_words_map_to_profiles(words, expected):
    assert resolve(words, PROFILES) == expected


def test_unknown_kind_lists_choices():
    with pytest.raises(UnknownKind, match="project_fact"):
        resolve("banana", PROFILES)


def test_custom_profile_names_resolve_exactly():
    assert resolve("gui conventions", PROFILES + ["gui_conventions"]) == "gui_conventions"
