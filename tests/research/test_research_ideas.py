"""kit.research.ideas and kit/schema/ideas.schema.json: the ranked ideas file and the hooks brief."""

import copy
import json

import pytest
from jsonschema import Draft202012Validator

from kit.research import ideas

EM_DASH, EN_DASH = chr(0x2014), chr(0x2013)  # written as code points so this file holds no dash

# The shape the other skills read, as agreed for the whole kit.
AGREED = [
    {
        "n": 1,
        "title": "Why windowsill basil dies in week three",
        "angle": "The cause is the saucer, not the sun",
        "why": "A basil video from a watched creator ran 5.5 times above their normal",
        "format": "Tutorial",
        "evidence": [{"url": "https://example.com/a", "creator": "alpha", "outlier": 5.5}],
        "hooks": ["Your basil is not dying from the sun.", "Three weeks in, every basil plant does this."],
    }
]


def idea(n=1, **changes):
    entry = copy.deepcopy(AGREED[0])
    entry["n"] = n
    entry.update(changes)
    return entry


# --- the schema -----------------------------------------------------------------------------------


def test_ideas_schema():
    schema = json.loads(ideas.SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    assert list(Draft202012Validator(schema).iter_errors(AGREED)) == []
    assert ideas.validate(AGREED) == []

    # the agreed fields, no more and no fewer
    item = schema["items"]
    assert set(item["required"]) == {"n", "title", "angle", "why", "format", "evidence", "hooks"}
    assert set(item["properties"]) == set(item["required"])
    assert set(item["properties"]["evidence"]["items"]["required"]) == {"url", "creator", "outlier"}

    # a file of several ideas, an idea with no evidence yet and an idea with no hooks yet are all fine
    several = [idea(1), idea(2, evidence=[], hooks=[]), idea(3, hooks=["One line."])]
    assert ideas.validate(several) == []


BAD = [
    ("an object instead of a list", lambda: {"n": 1}, "must hold a list of ideas"),
    ("no ideas at all", lambda: [], "has no ideas"),
    ("an idea that is not an object", lambda: ["text"], "Idea 1 is not a group of fields"),
    ("a missing field", lambda: [{k: v for k, v in idea().items() if k != "why"}], "Idea 1 is missing why"),
    ("a misspelt field", lambda: [{**idea(), "hook": ["x"]}], "Idea 1 has a field called hook"),
    ("n as text", lambda: [idea(n="1")], "n should be a whole number"),
    ("n of zero", lambda: [idea(n=0)], "n should be 1 or more"),
    ("a gap in the numbering", lambda: [idea(1), idea(2), idea(4)], "numbered 1, 2, 3"),
    ("a repeated number", lambda: [idea(1), idea(1)], "numbered 1, 2"),
    ("an empty title", lambda: [idea(title="")], "title is empty"),
    ("a title that is not text", lambda: [idea(title=5)], "title should be text"),
    ("evidence that is not a list", lambda: [idea(evidence="https://example.com/a")], "evidence should be a list"),
    ("evidence with no url", lambda: [idea(evidence=[{"creator": "alpha", "outlier": 1}])], "evidence 1 is missing url"),
    (
        "evidence with a link that is not a web address",
        lambda: [idea(evidence=[{"url": "javascript:alert(1)", "creator": "alpha", "outlier": 1}])],
        "web link",
    ),
    (
        "evidence with a negative score",
        lambda: [idea(evidence=[{"url": "https://example.com/a", "creator": "alpha", "outlier": -2}])],
        "outlier should be a number of 0 or more",
    ),
    (
        "evidence with a score in words",
        lambda: [idea(evidence=[{"url": "https://example.com/a", "creator": "alpha", "outlier": "high"}])],
        "outlier should be a number",
    ),
    (
        "evidence with an extra field",
        lambda: [idea(evidence=[{"url": "https://example.com/a", "creator": "alpha", "outlier": 1, "note": "x"}])],
        "evidence 1 has a field called note",
    ),
    ("hooks that are not a list", lambda: [idea(hooks="one line")], "hooks should be a list"),
    ("a hook that is not text", lambda: [idea(hooks=["fine", 7])], "hook 2 should be text"),
    ("an empty hook", lambda: [idea(hooks=["fine", "  "])], "hook 2 is empty"),
    ("a hook that is far too long", lambda: [idea(hooks=["x" * 400])], "hook 1 is longer than"),
]


@pytest.mark.parametrize("what, make, fragment", BAD, ids=[b[0] for b in BAD])
def test_a_bad_ideas_file_gets_a_plain_sentence(what, make, fragment):
    problems = ideas.validate(make())
    assert any(fragment in p for p in problems), problems
    for problem in problems:
        assert EM_DASH not in problem and EN_DASH not in problem
        assert "Traceback" not in problem and "jsonschema" not in problem


def test_a_misspelt_field_suggests_the_right_one():
    (problem,) = [p for p in ideas.validate([{**idea(), "hook": ["x"]}]) if "hook" in p and "field" in p]
    assert "Did you mean hooks?" in problem


def test_every_problem_is_reported_not_just_the_first():
    broken = [idea(1, title=""), idea(3, hooks="one line")]
    problems = ideas.validate(broken)
    assert any("title is empty" in p for p in problems)
    assert any("hooks should be a list" in p for p in problems)
    assert any("numbered" in p for p in problems)


# --- reading a run's ideas ------------------------------------------------------------------------


def test_reading_ideas_from_a_run_folder(tmp_path):
    assert ideas.read(tmp_path) == (None, [])  # nothing written yet
    (tmp_path / "ideas.json").write_text(json.dumps(AGREED), encoding="utf-8")
    found, problems = ideas.read(tmp_path)
    assert found == AGREED and problems == []


def test_broken_json_is_explained(tmp_path):
    (tmp_path / "ideas.json").write_text('[{"n": 1,}]', encoding="utf-8")
    found, problems = ideas.read(tmp_path)
    assert found is None
    (problem,) = problems
    assert "not valid JSON" in problem and "line 1" in problem and "missing comma" in problem


def test_ideas_with_problems_are_not_returned(tmp_path):
    (tmp_path / "ideas.json").write_text(json.dumps([idea(n=0)]), encoding="utf-8")
    found, problems = ideas.read(tmp_path)
    assert found is None and problems


def test_the_latest_run_is_the_newest_folder_that_has_the_file(tmp_path):
    runs = tmp_path / "runs"
    for day, files in (("2026-10-26", ["ideas.json", "items.json"]), ("2026-10-27", ["ideas.json"]),
                       ("2026-10-28", ["items.json"]), ("notes", ["ideas.json"])):
        (runs / day).mkdir(parents=True)
        for name in files:
            (runs / day / name).write_text("[]")
    assert ideas.latest_run(runs, "ideas.json").name == "2026-10-27"
    assert ideas.latest_run(runs, "items.json").name == "2026-10-28"
    assert ideas.latest_run(tmp_path / "missing", "items.json") is None


# --- the hooks brief ------------------------------------------------------------------------------


def video(vid, outlier, hook, **extra):
    base = {
        "id": f"ig:{vid}",
        "platform": "instagram",
        "creator": f"creator_{vid}",
        "url": f"https://example.com/{vid}",
        "outlier": outlier,
        "views": 50000,
        "hook": hook,
        "hook_source": "transcript",
        "transcript": hook + " Then the rest of the video goes on for a while with more words.",
        "low_sample": False,
    }
    base.update(extra)
    return base


DATA = {
    "videos": [
        video("a", 3.0, "Hook of the middle video."),
        video("b", 8.0, "Hook of the best video."),
        video("c", 1.5, "Hook nobody cites."),
        video("d", 12.0, "Hook of the top video overall."),
    ],
    "x_posts": [],
}


def cited(*pairs):
    return [{"url": f"https://example.com/{vid}", "creator": f"creator_{vid}", "outlier": o} for vid, o in pairs]


def test_the_brief_puts_the_strongest_evidence_first():
    chosen = idea(2, evidence=cited(("a", 3.0), ("b", 8.0), ("missing", 2.0)))
    text = ideas.hooks_brief(chosen, DATA, run_label="research/runs/2026-10-28")
    assert text.index("Hook of the best video.") < text.index("Hook of the middle video.")
    assert "Hook nobody cites." not in text and "Hook of the top video overall." not in text
    assert "8.0x" in text and "3.0x" in text
    assert "Idea 2: Why windowsill basil dies in week three" in text
    assert "me/voice/VOICE.md" in text
    assert "between 5 and 10" in text
    assert "research/runs/2026-10-28" in text
    for existing in chosen["hooks"]:
        assert existing in text  # so new hooks do not repeat them


def test_the_brief_matches_evidence_by_address_not_by_spelling():
    chosen = idea(1, evidence=[{"url": "HTTPS://www.example.com/b/?utm=1", "creator": "creator_b", "outlier": 8.0}])
    assert "Hook of the best video." in ideas.hooks_brief(chosen, DATA, run_label="run")


def test_without_usable_evidence_the_brief_falls_back_to_the_runs_best_and_says_so():
    chosen = idea(1, evidence=cited(("missing", 4.0)))
    text = ideas.hooks_brief(chosen, DATA, run_label="run")
    assert "Hook of the top video overall." in text
    assert "strongest videos in this run instead" in text


def test_a_video_with_no_transcript_shows_its_caption_and_says_so():
    data = {"videos": [video("e", 5.0, "Caption line only.", hook_source="caption", transcript="")], "x_posts": []}
    chosen = idea(1, evidence=cited(("e", 5.0)))
    text = ideas.hooks_brief(chosen, data, run_label="run")
    assert "Caption line only." in text and "no transcript" in text


def test_a_low_sample_creator_is_flagged_in_the_brief():
    data = {"videos": [video("f", 9.0, "Rough numbers hook.", low_sample=True)], "x_posts": []}
    text = ideas.hooks_brief(idea(1, evidence=cited(("f", 9.0))), data, run_label="run")
    assert "low sample" in text


def test_a_video_with_no_score_does_not_break_the_brief():
    data = {"videos": [video("g", None, "A hook with no score behind it.")], "x_posts": []}
    text = ideas.hooks_brief(idea(1, evidence=cited(("g", 0))), data, run_label="run")
    assert "A hook with no score behind it." in text and "no score" in text


def test_the_words_after_the_opening_line_survive_odd_spacing():
    spoken = "Basil dies   for one\nreason.  Here is   the fix.\n\nWater it in the morning."
    data = {"videos": [video("h", 2.0, "Basil dies for one reason.", transcript=spoken)], "x_posts": []}
    text = ideas.hooks_brief(idea(1, evidence=cited(("h", 2.0))), data, run_label="run")
    assert 'Opening line: "Basil dies for one reason."' in text
    assert 'Then: "Here is the fix. Water it in the morning."' in text


def test_an_x_post_in_the_evidence_is_quoted():
    data = {
        "videos": [],
        "x_posts": [{"url": "https://x.com/example_account/status/9", "handle": "example_account", "status": "read",
                     "text": "Nobody tells you this about basil.", "views": 120000}],
    }
    chosen = idea(1, evidence=[{"url": "https://x.com/example_account/status/9", "creator": "example_account", "outlier": 0}])
    text = ideas.hooks_brief(chosen, data, run_label="run")
    assert "Nobody tells you this about basil." in text


def test_asking_for_an_idea_that_is_not_there(tmp_path):
    with pytest.raises(ValueError) as caught:
        ideas.find(AGREED, 7, run_label="research/runs/2026-10-28")
    assert str(caught.value) == "There is no idea number 7 in research/runs/2026-10-28. It has idea 1."
    several = [idea(1), idea(2), idea(3)]
    with pytest.raises(ValueError) as caught:
        ideas.find(several, 9, run_label="research/runs/2026-10-28")
    assert "It has ideas 1 to 3." in str(caught.value)
    with pytest.raises(ValueError) as caught:
        ideas.find(several[:2], 3, run_label="x")
    assert "It has ideas 1 and 2." in str(caught.value)
    assert ideas.find(several, 2, run_label="x")["n"] == 2


# --- saving hooks ---------------------------------------------------------------------------------


def make_run(tmp_path, data=None):
    folder = tmp_path / "run"
    folder.mkdir()
    (folder / "ideas.json").write_text(json.dumps(data or [idea(1), idea(2)]), encoding="utf-8")
    return folder


GOOD_HOOKS = [f"Hook number {n} goes here." for n in range(1, 7)]


def test_saved_hooks_replace_that_ideas_hooks_and_leave_the_rest_alone(tmp_path):
    folder = make_run(tmp_path)
    ideas.save_hooks(folder, 2, GOOD_HOOKS)
    saved = json.loads((folder / "ideas.json").read_text(encoding="utf-8"))
    assert saved[1]["hooks"] == GOOD_HOOKS
    assert saved[0] == idea(1)
    assert [p.name for p in folder.iterdir()] == ["ideas.json"]


@pytest.mark.parametrize("count", [0, 4, 11])
def test_between_five_and_ten_hooks_are_expected(tmp_path, count):
    folder = make_run(tmp_path)
    with pytest.raises(ValueError) as caught:
        ideas.save_hooks(folder, 1, [f"Hook {n}." for n in range(count)])
    assert f"Write between 5 and 10 hooks for each idea. This is {count}." in str(caught.value)


def test_an_empty_hook_is_refused(tmp_path):
    folder = make_run(tmp_path)
    with pytest.raises(ValueError) as caught:
        ideas.save_hooks(folder, 1, GOOD_HOOKS[:5] + ["   "])
    assert "hook 6 is empty" in str(caught.value)


def test_hooks_cannot_be_saved_for_an_idea_that_is_not_there(tmp_path):
    folder = make_run(tmp_path)
    with pytest.raises(ValueError) as caught:
        ideas.save_hooks(folder, 5, GOOD_HOOKS)
    assert "There is no idea number 5" in str(caught.value)


def test_hooks_cannot_be_saved_when_there_is_no_ideas_file(tmp_path):
    with pytest.raises(ValueError) as caught:
        ideas.save_hooks(tmp_path, 1, GOOD_HOOKS)
    assert "no ideas" in str(caught.value)


def test_pasted_hooks_lose_their_list_marks_and_quotes():
    text = '''
    - "Your basil is not dying from the sun."
    2. Three weeks in, every basil plant does this.
    * 'Stop watering at night.'

    4) Plain line stays as it is.
    '''
    assert ideas.parse_hooks(text) == [
        "Your basil is not dying from the sun.",
        "Three weeks in, every basil plant does this.",
        "Stop watering at night.",
        "Plain line stays as it is.",
    ]
