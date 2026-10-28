"""kit.onboarding: where the creator is in setup, the step that writes their profile, and the look preview.

It also checks the two skills and the guide that drive it: the onboard skill, the voice skill and
ONBOARDING.md. Every test works on a throwaway me/ folder (KIT_ME), so the committed example is
never touched.
"""

import copy
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from kit import brand, config, onboarding
from kit.config import KitError, NotOnboarded, ProfileError
from kit.onboarding import STEPS, State

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / ".claude" / "skills"
EXAMPLE = json.loads((ROOT / "me.example" / "profile.json").read_text(encoding="utf-8"))
EM_DASH, EN_DASH = chr(0x2014), chr(0x2013)  # written as code points so this file holds no dash
MARKER = "TO" + "DO"  # the word a half-written answer leaves behind, spelled in two parts so it never appears here

REQUIRED = ["name", "handles", "niche", "credibility", "audience", "cta", "platforms", "timezone", "colours", "fonts", "styles"]
PINNED_ORDER = [
    "version",
    "onboarded",
    "name",
    "handles",
    "niche",
    "credibility",
    "audience",
    "cta",
    "platforms",
    "timezone",
    "posting_times",
    "colours",
    "fonts",
    "logo",
    "styles",
    "voice_over",
    "image_provider",
    "hashtags",
]
# Which step each answer belongs to, the way the skill saves them.
STEP_OF = {
    "name": "who",
    "handles": "who",
    "niche": "who",
    "credibility": "who",
    "audience": "who",
    "platforms": "who",
    "cta": "who",
    "colours": "look",
    "fonts": "look",
    "logo": "look",
    "timezone": "posting",
    "posting_times": "posting",
    "hashtags": "posting",
    "styles": "styles",
    "voice_over": "styles",
    "image_provider": "styles",
}


@pytest.fixture
def me(tmp_path, monkeypatch) -> Path:
    """A creator folder that does not exist yet: the state of a fresh start."""
    folder = tmp_path / "me"
    monkeypatch.setenv("KIT_ME", str(folder))
    return folder


def answers_for(**changes) -> dict:
    """The example creator's answers. A value of None for a name in `changes` removes that answer."""
    data = {key: copy.deepcopy(value) for key, value in EXAMPLE.items() if key not in ("version", "onboarded")}
    for key, value in changes.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    return data


def fill(state: State, answers: dict | None = None, through: str = "styles") -> State:
    """Save the answers step by step, as the skill does, up to and including `through`."""
    answers = answers if answers is not None else answers_for()
    for step in STEPS[: STEPS.index(through) + 1]:
        state.save(step, {k: v for k, v in answers.items() if STEP_OF.get(k) == step})
    return state


def files_under(folder: Path) -> list[str]:
    if not folder.exists():
        return []
    return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file())


# --- the steps and the state file ------------------------------------------------------------------------


def test_the_ten_steps_in_the_order_the_skill_asks_them():
    assert STEPS == (
        "welcome",
        "machine",
        "who",
        "look",
        "voice",
        "watchlist",
        "posting",
        "styles",
        "test_clip",
        "tour",
    )


def test_a_fresh_start_begins_with_the_welcome(me):
    state = State()
    assert state.next_step() == "welcome"
    assert not me.exists(), "looking at the state creates nothing"


def test_resume_after_step_4(me):
    state = State()
    for step in STEPS[:4]:
        state.save(step, {})

    assert State().next_step() == "voice", "a new object reads the saved file and resumes at step 5"
    assert state.next_step() == "voice"


def test_next_step_is_the_first_one_not_done_even_when_steps_were_saved_out_of_order(me):
    state = State()
    state.save("look", {})
    assert state.next_step() == "welcome"
    state.save("welcome", {})
    state.save("machine", {})
    assert state.next_step() == "who"


def test_when_every_step_is_done_there_is_nothing_left(me):
    state = State()
    for step in STEPS:
        state.save(step, {})
    assert state.next_step() == "done"
    assert State().next_step() == "done"


def test_the_state_file_has_the_pinned_shape(me):
    state = State()
    state.save("welcome", {})
    state.save("machine", {})
    state.save("who", {"name": "Example Studio"})

    stored = json.loads((me / ".onboarding.json").read_text(encoding="utf-8"))

    assert stored == {"step": 3, "done": ["welcome", "machine", "who"], "answers": {"name": "Example Studio"}}
    assert state.step == 3 and state.done == ["welcome", "machine", "who"]


def test_out_of_order_saves_keep_done_in_step_order_and_step_is_the_furthest_one(me):
    state = State()
    state.save("look", {})
    state.save("who", {})
    stored = json.loads((me / ".onboarding.json").read_text(encoding="utf-8"))
    assert stored["done"] == ["who", "look"]
    assert stored["step"] == 4


def test_saving_a_step_again_keeps_it_once(me):
    state = State()
    state.save("who", {"name": "Example Studio"})
    state.save("who", {"niche": "Growing food on balconies"})
    assert state.done == ["who"]
    assert state.answers == {"name": "Example Studio", "niche": "Growing food on balconies"}


def test_save_merges_groups_and_replaces_everything_else(me):
    state = State()
    state.save("who", {"handles": {"instagram": "@examplestudio"}, "platforms": ["instagram", "tiktok"]})
    state.save("who", {"handles": {"tiktok": "@examplestudio"}, "platforms": ["youtube"]})
    assert state.answers["handles"] == {"instagram": "@examplestudio", "tiktok": "@examplestudio"}
    assert state.answers["platforms"] == ["youtube"]
    state.save("who", {"handles": {"tiktok": None}})
    assert state.answers["handles"] == {"instagram": "@examplestudio", "tiktok": None}


def test_save_with_no_answers_just_marks_the_step(me):
    state = State()
    state.save("welcome")
    assert state.done == ["welcome"] and state.answers == {}


def test_save_does_not_keep_a_reference_to_the_callers_dict(me):
    state = State()
    given = {"handles": {"instagram": "@examplestudio"}}
    state.save("who", given)
    given["handles"]["instagram"] = "@changed"
    assert state.answers["handles"]["instagram"] == "@examplestudio"


@pytest.mark.parametrize(
    "step, answers, words",
    [
        ("nonsense", {}, "welcome, machine, who"),
        ("", {}, "welcome, machine, who"),
        (3, {}, "welcome, machine, who"),
        ("who", ["name"], "group of answers"),
        ("who", "name", "group of answers"),
        ("who", {"name": object()}, "plain text"),
        ("who", {"niche": float("nan")}, "plain text"),
    ],
)
def test_save_refuses_what_it_cannot_store_and_changes_nothing(me, step, answers, words):
    state = State()
    state.save("welcome", {})
    before = (me / ".onboarding.json").read_bytes()

    with pytest.raises(ValueError) as caught:
        state.save(step, answers)

    assert words in str(caught.value)
    assert "Traceback" not in str(caught.value)
    assert (me / ".onboarding.json").read_bytes() == before
    assert state.done == ["welcome"] and state.answers == {}


def test_a_key_never_goes_into_the_setup_answers(me):
    state = State()
    for name in ("ZERNIO_API_KEY", "apify_token", "openai-api-key", "client_secret", "password"):
        with pytest.raises(ValueError) as caught:
            state.save("posting", {"hashtags": {"base": []}, name: "not-a-real-key"})
        assert ".env" in str(caught.value)
        assert "not-a-real-key" not in str(caught.value), "the message never repeats the value"
    with pytest.raises(ValueError):
        state.save("posting", {"zernio": {"api_key": "not-a-real-key"}})
    assert not (me / ".onboarding.json").exists()
    # words that only contain "key" are fine
    state.save("who", {"cta": {"type": "comment", "keyword": "GROW"}})
    assert state.answers["cta"]["keyword"] == "GROW"


def test_save_writes_in_one_step_and_leaves_no_temporary_file(me, monkeypatch):
    state = State()
    state.save("welcome", {})
    before = (me / ".onboarding.json").read_bytes()

    def fail(*args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(KitError) as caught:
        state.save("machine", {})

    assert "No space left on device" in str(caught.value)
    assert (me / ".onboarding.json").read_bytes() == before, "the old file is intact"
    assert files_under(me) == [".onboarding.json"], "no temporary file is left"
    assert state.done == ["welcome"], "the object still matches the file"


@pytest.mark.parametrize(
    "content",
    ["not json", "[]", '{"done": "welcome"}', '{"done": [3]}', '{"answers": []}', '{"step": 2, "done": [], "answers": 7}'],
)
def test_a_damaged_state_file_is_reported_in_a_sentence_and_never_overwritten(me, content):
    me.mkdir(parents=True)
    (me / ".onboarding.json").write_text(content, encoding="utf-8")

    with pytest.raises(KitError) as caught:
        State()

    message = str(caught.value)
    assert ".onboarding.json" in message and "Delete it" in message
    assert "Traceback" not in message
    assert EM_DASH not in message and EN_DASH not in message
    assert (me / ".onboarding.json").read_text(encoding="utf-8") == content


def test_an_unreadable_state_file_is_reported_in_a_sentence(me):
    me.mkdir(parents=True)
    (me / ".onboarding.json").write_bytes(b"\xff\xfe\x00 not utf-8")
    with pytest.raises(KitError) as caught:
        State()
    assert ".onboarding.json" in str(caught.value)


def test_step_names_from_another_version_are_ignored_not_fatal(me):
    me.mkdir(parents=True)
    (me / ".onboarding.json").write_text(json.dumps({"step": 2, "done": ["welcome", "intro"], "answers": {}}))
    state = State()
    assert state.done == ["welcome"]
    assert state.next_step() == "machine"


def test_state_takes_the_folder_it_is_given(tmp_path):
    folder = tmp_path / "somewhere"
    state = State(folder)
    state.save("welcome", {})
    assert (folder / ".onboarding.json").is_file()
    assert State.load(folder).next_step() == "machine"


def test_the_state_file_is_plain_utf8_with_a_final_newline(me):
    state = State()
    state.save("who", {"name": "Café Étoile"})
    raw = (me / ".onboarding.json").read_bytes()
    assert raw.endswith(b"\n")
    assert "Café Étoile" in raw.decode("utf-8"), "names are not escaped into \\u sequences"


# --- what is still to answer --------------------------------------------------------------------------------


def test_missing_answers_lists_what_the_profile_still_needs(me):
    state = State()
    assert state.missing_answers() == REQUIRED
    fill(state, through="who")
    assert state.missing_answers() == ["timezone", "colours", "fonts", "styles"]
    fill(state)
    assert state.missing_answers() == []


# --- finish ----------------------------------------------------------------------------------------------------


def test_finish_writes_the_profile_and_the_tokens(me):
    state = fill(State())

    profile = state.finish()

    assert files_under(me) == [".onboarding.json", "brand/tokens.css", "profile.json"]
    stored = json.loads((me / "profile.json").read_text(encoding="utf-8"))
    assert stored["onboarded"] is True and stored["version"] == 1
    assert list(stored) == PINNED_ORDER
    assert config.load_profile() == profile
    assert (me / "brand" / "tokens.css").read_text(encoding="utf-8") == brand.tokens_css(profile)
    assert profile.name == EXAMPLE["name"] and profile.cta.keyword == "GROW"


def test_finish_gives_the_profile_the_kit_reads_back_unchanged(me):
    profile = fill(State()).finish()
    assert config.Profile.from_dict(json.loads((me / "profile.json").read_text(encoding="utf-8")), base=me) == profile


def test_finish_does_not_need_the_later_steps_to_be_done(me):
    state = fill(State(), through="styles")
    assert state.next_step() == "test_clip"
    state.finish()
    assert State().next_step() == "test_clip", "finishing the profile does not mark the test clip or the tour"


@pytest.mark.parametrize("missing", REQUIRED)
def test_finish_refuses_partial(me, missing):
    state = fill(State(), answers_for(**{missing: None}))

    with pytest.raises(ProfileError) as caught:
        state.finish()

    assert any(problem.startswith(f"{missing} is missing") for problem in caught.value.problems), caught.value.problems
    assert files_under(me) == [".onboarding.json"], "nothing but the state file exists"
    with pytest.raises(NotOnboarded):
        config.load_profile()


@pytest.mark.parametrize(
    "change, path",
    [
        (lambda a: a["audience"].pop("wants"), "audience.wants"),
        (lambda a: a["colours"].pop("field_dark"), "colours.field_dark"),
        (lambda a: a["fonts"].pop("mono"), "fonts.mono"),
        (lambda a: a["cta"].pop("type"), "cta.type"),
    ],
)
def test_finish_refuses_a_group_with_a_part_missing(me, change, path):
    answers = answers_for()
    change(answers)
    state = fill(State(), answers)

    with pytest.raises(ProfileError) as caught:
        state.finish()

    assert any(problem.startswith(f"{path} is missing") for problem in caught.value.problems)
    assert files_under(me) == [".onboarding.json"]


def test_finish_with_nothing_saved_lists_every_missing_answer_and_writes_nothing(me):
    with pytest.raises(ProfileError) as caught:
        State().finish()
    problems = caught.value.problems
    assert [p.split(" ")[0] for p in problems] == REQUIRED
    assert not me.exists()


def test_finish_says_what_to_do_in_plain_words(me):
    with pytest.raises(ProfileError) as caught:
        fill(State(), answers_for(name=None, niche=None)).finish()
    message = str(caught.value)
    assert message.startswith("Setup cannot write your profile yet. 2 answers are missing or wrong:")
    assert "- name is missing. It should be" in message
    assert "Nothing was written." in message
    assert "me/profile.json" not in message, "the file does not exist yet, so the message does not send them to it"
    assert "Traceback" not in message
    assert EM_DASH not in message and EN_DASH not in message
    with pytest.raises(ProfileError) as one:
        fill(State(), answers_for(name=None)).finish()
    assert str(one.value).startswith("Setup cannot write your profile yet. One answer is missing or wrong:")


def test_finish_pickles_like_the_other_profile_errors(me):
    import pickle

    with pytest.raises(ProfileError) as caught:
        State().finish()
    copy_of = pickle.loads(pickle.dumps(caught.value))
    assert copy_of.problems == caught.value.problems and str(copy_of) == str(caught.value)


def test_finish_fills_in_the_answers_the_creator_may_skip(me):
    answers = answers_for(logo=None, posting_times=None, voice_over=None, image_provider=None, hashtags=None)
    profile = fill(State(), answers).finish()

    assert profile.logo is None
    assert profile.posting_times == {}
    assert profile.voice_over == "record"
    assert profile.image_provider == "manual"
    assert profile.hashtags.base == ()


def test_finish_ignores_answers_that_are_not_profile_fields(me):
    state = fill(State())
    state.save("watchlist", {"watchlist": {"youtube": ["@example_channel"]}, "zernio_checked": True})
    state.finish()
    stored = json.loads((me / "profile.json").read_text(encoding="utf-8"))
    assert "watchlist" not in stored and "zernio_checked" not in stored
    assert "watchlist" in State().answers, "they stay in the state file"


def test_finish_decides_version_and_onboarded_itself(me):
    state = fill(State())
    state.save("tour", {"version": 7, "onboarded": False})
    profile = state.finish()
    assert (profile.version, profile.onboarded) == (1, True)


def test_finish_keeps_a_research_override(me):
    state = fill(State())
    state.save("posting", {"research": {"cost_cap": {"tiktok": 1}}})
    profile = state.finish()
    assert profile.research.cost_cap == {"instagram": 0.25, "tiktok": 1.0}
    assert json.loads((me / "profile.json").read_text(encoding="utf-8"))["research"] == {"cost_cap": {"tiktok": 1}}


def test_finish_checks_the_cross_field_rules_before_writing(me):
    answers = answers_for(
        platforms=["instagram", "tiktok"],
        handles={"instagram": "@examplestudio", "tiktok": ""},
        cta={"type": "comment", "keyword": "GROW", "payload": "the checklist", "comment_platforms": ["youtube"]},
    )
    with pytest.raises(ProfileError) as caught:
        fill(State(), answers).finish()
    text = "\n".join(caught.value.problems)
    assert "handles.tiktok is empty, but tiktok is in platforms" in text
    assert "cta.comment_platforms lists youtube, which is not in platforms" in text
    assert files_under(me) == [".onboarding.json"]


def test_finish_refuses_a_font_file_that_is_not_there(me):
    answers = answers_for(fonts={"display": "brand/fonts/Missing.woff2", "body": "Inter", "mono": "JetBrains Mono"})
    with pytest.raises(ProfileError) as caught:
        fill(State(), answers).finish()
    assert any(p.startswith("fonts.display points to brand/fonts/Missing.woff2") for p in caught.value.problems)
    assert files_under(me) == [".onboarding.json"]


def test_finish_accepts_a_font_and_a_logo_the_creator_supplied(me):
    (me / "brand" / "fonts").mkdir(parents=True)
    (me / "brand" / "logo").mkdir(parents=True)
    (me / "brand" / "fonts" / "Custom.woff2").write_bytes(b"not really a font")
    (me / "brand" / "logo" / "logo.png").write_bytes(b"not really a picture")
    answers = answers_for(
        fonts={"display": "brand/fonts/Custom.woff2", "body": "Inter", "mono": "JetBrains Mono"},
        logo="brand/logo/logo.png",
    )
    profile = fill(State(), answers).finish()
    assert profile.logo == "brand/logo/logo.png"
    css = (me / "brand" / "tokens.css").read_text(encoding="utf-8")
    assert 'url("fonts/Custom.woff2")' in css


def test_a_state_for_another_folder_finishes_into_that_folder_and_finds_its_fonts(tmp_path, monkeypatch):
    elsewhere = tmp_path / "elsewhere"
    monkeypatch.setenv("KIT_ME", str(elsewhere))
    other = tmp_path / "other"
    (other / "brand" / "fonts").mkdir(parents=True)
    (other / "brand" / "fonts" / "Custom.woff2").write_bytes(b"not really a font")
    answers = answers_for(fonts={"display": "brand/fonts/Custom.woff2", "body": "Inter", "mono": "JetBrains Mono"})

    fill(State(other), answers).finish()

    assert files_under(other) == [".onboarding.json", "brand/fonts/Custom.woff2", "brand/tokens.css", "profile.json"]
    assert 'url("fonts/Custom.woff2")' in (other / "brand" / "tokens.css").read_text(encoding="utf-8")
    assert not elsewhere.exists()
    assert os.environ["KIT_ME"] == str(elsewhere), "the setting is put back"


def test_session_start_agrees_once_finish_has_run(me):
    start = ROOT / "bin" / "session_start.sh"
    env = {"PATH": "/usr/bin:/bin", "HOME": str(me.parent), "KIT_ME": str(me)}

    def says() -> str:
        return subprocess.run(["bash", str(start)], env=env, capture_output=True, text=True).stdout

    assert says() == "This kit is not set up yet. Say: set me up.\n"
    state = fill(State())
    assert says() == "This kit is not set up yet. Say: set me up.\n", "saved answers are not a finished setup"
    state.finish()
    assert says() == ""


def test_finish_refuses_a_bad_colour_and_never_replaces_a_good_profile(me):
    fill(State()).finish()
    good = (me / "profile.json").read_bytes()
    tokens = (me / "brand" / "tokens.css").read_bytes()

    state = State()
    state.save("look", {"colours": {**EXAMPLE["colours"], "accent": "green"}})
    with pytest.raises(ProfileError) as caught:
        state.finish()

    assert any(p.startswith("colours.accent has the value") for p in caught.value.problems)
    assert (me / "profile.json").read_bytes() == good
    assert (me / "brand" / "tokens.css").read_bytes() == tokens


def test_no_todo_written(me):
    fill(State()).finish()
    written = [p for p in me.rglob("*") if p.is_file()]
    assert written
    for path in written:
        assert MARKER not in path.read_text(encoding="utf-8"), path.name


def test_an_answer_that_is_still_a_placeholder_is_refused(me):
    answers = answers_for(niche=MARKER, audience={"who": f"People who like {MARKER} lists", "wants": "Simple projects"})
    with pytest.raises(ProfileError) as caught:
        fill(State(), answers).finish()
    text = "\n".join(caught.value.problems)
    assert f"niche still says {MARKER}" in text
    assert f"audience.who still says {MARKER}" in text
    assert files_under(me) == [".onboarding.json"]
    # the ordinary words are fine
    fine = answers_for(niche="Apps for your to-do list and todo habits")
    fill(State(), fine).finish()


def test_finish_twice_writes_the_same_files(me):
    state = fill(State())
    state.finish()
    first = {name: (me / name).read_bytes() for name in ("profile.json", "brand/tokens.css")}
    state.finish()
    assert {name: (me / name).read_bytes() for name in first} == first


def test_finish_leaves_no_temporary_files(me):
    fill(State()).finish()
    assert files_under(me) == [".onboarding.json", "brand/tokens.css", "profile.json"]


def test_a_failed_write_never_leaves_a_profile_that_says_setup_is_done(me, monkeypatch):
    state = fill(State())

    def fail(*args, **kwargs):
        raise OSError(28, "No space left on device")

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail)
        with pytest.raises(KitError) as caught:
            state.finish()

    assert "No space left on device" in str(caught.value)
    assert "Free some disk space" in str(caught.value)
    assert files_under(me) == [".onboarding.json"], "nothing was replaced, and no temporary file is left"
    with pytest.raises(NotOnboarded):
        config.load_profile()


def test_a_failure_on_the_very_last_rename_still_leaves_no_profile(me, monkeypatch):
    state = fill(State())
    real_replace = os.replace

    def fail_for_the_profile(source, target):
        if Path(target).name == "profile.json":
            raise OSError(28, "No space left on device")
        return real_replace(source, target)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail_for_the_profile)
        with pytest.raises(KitError):
            state.finish()

    assert files_under(me) == [".onboarding.json", "brand/tokens.css"], "the tokens landed, the profile did not, nothing is left over"
    with pytest.raises(NotOnboarded):
        config.load_profile()


def test_finish_stages_both_files_before_it_replaces_either_and_the_profile_goes_last(me, monkeypatch):
    state = fill(State())
    seen = []
    real_replace = os.replace

    def spy_replace(source, target):
        seen.append((Path(target).name, Path(source).name.endswith(".tmp"), sorted(p.name for p in me.rglob("*.tmp"))))
        return real_replace(source, target)

    monkeypatch.setattr(os, "replace", spy_replace)
    state.finish()

    assert [name for name, _, _ in seen] == ["tokens.css", "profile.json"]
    assert all(from_temp for _, from_temp, _ in seen), "both are renamed into place from staged temporary files"
    first_name, _, staged_at_first_rename = seen[0]
    assert len(staged_at_first_rename) == 2, "the profile is staged before the tokens are renamed"
    assert files_under(me) == [".onboarding.json", "brand/tokens.css", "profile.json"]


def test_a_failing_tokens_write_leaves_no_profile_and_no_temporary_file(me, monkeypatch):
    state = fill(State())

    def fail_halfway(profile, out):
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text("half a file", encoding="utf-8")
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(brand, "write_tokens", fail_halfway)
    with pytest.raises(KitError) as caught:
        state.finish()

    assert "Free some disk space" in str(caught.value)
    assert files_under(me) == [".onboarding.json"], "no profile, no tokens, and the half-written temporary file is gone"
    with pytest.raises(NotOnboarded):
        config.load_profile()


def test_a_failing_tokens_write_leaves_an_existing_profile_and_tokens_as_they_were(me, monkeypatch):
    fill(State()).finish()
    profile_before = (me / "profile.json").read_bytes()
    tokens_before = (me / "brand" / "tokens.css").read_bytes()
    state = State()
    state.save("look", {"colours": {**EXAMPLE["colours"], "accent": "#123456"}})

    def fail(profile, out):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(brand, "write_tokens", fail)
    with pytest.raises(KitError):
        state.finish()

    assert (me / "profile.json").read_bytes() == profile_before
    assert (me / "brand" / "tokens.css").read_bytes() == tokens_before
    assert files_under(me) == [".onboarding.json", "brand/tokens.css", "profile.json"]


PERMISSION = (
    "not allowed to write in your me folder. Check that the folder belongs to you and is not read-only, then try again."
)


@pytest.mark.parametrize(
    "error",
    [PermissionError(13, "Permission denied"), OSError(1, "Operation not permitted"), OSError(30, "Read-only file system")],
    ids=["permission-denied", "not-permitted", "read-only"],
)
def test_a_permission_problem_has_its_own_sentence_and_never_blames_the_disk(me, monkeypatch, error):
    state = fill(State())
    sentences = []

    def refuse(*args, **kwargs):
        raise error

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", refuse)
        for action in (lambda: state.finish(), lambda: state.save("tour", {}), lambda: onboarding.preview(LOOK)):
            with pytest.raises(KitError) as caught:
                action()
            sentences.append(str(caught.value))

    for sentence in sentences:
        assert sentence.startswith("Setup could not save ") and sentence.endswith(PERMISSION)
        assert "disk space" not in sentence and "No space" not in sentence
    assert [s.split(",")[0] for s in sentences] == [
        "Setup could not save your files",
        "Setup could not save your progress",
        "Setup could not save the preview",
    ]


def test_another_failure_to_write_is_named_and_not_called_a_full_disk(me, monkeypatch):
    state = fill(State())

    def fail(*args, **kwargs):
        raise OSError(5, "Input/output error")

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail)
        with pytest.raises(KitError) as caught:
            state.finish()
    assert str(caught.value) == (
        "Setup could not save your files (Input/output error). Try again. If it fails the same way, that reason is what to fix."
    )
    assert "agent" not in str(caught.value), "words for the creator, who may be reading this in a terminal"


# --- platform names ------------------------------------------------------------------------------------------

NAMES = 'The platform names are instagram, tiktok, youtube, facebook, linkedin and twitter. X is saved as "twitter".'


@pytest.mark.parametrize(
    "answers, start",
    [
        ({"platforms": ["instagram", "x"]}, 'platforms has "x", which is not a platform name the kit knows.'),
        ({"platforms": ["Instagram"]}, 'platforms has "Instagram", which is not a platform name the kit knows.'),
        ({"platforms": ["myspace", "orkut"]}, 'platforms has "myspace" and "orkut", which are not platform names the kit knows.'),
        ({"platforms": ["a", "b", "c"]}, 'platforms has "a", "b" and "c", which are not platform names the kit knows.'),
        ({"platforms": ["x", "x"]}, 'platforms has "x", which is not a platform name the kit knows.'),
        ({"handles": {"x": "@examplestudio"}}, 'handles has "x", which is not a platform name the kit knows.'),
        ({"handles": {"instagram": "@examplestudio", "Twitter": "@examplestudio"}}, 'handles has "Twitter", which is not a platform name the kit knows.'),
        ({"posting_times": {"X": "18:00"}}, 'posting_times has "X", which is not a platform name the kit knows.'),
        (
            {"cta": {"type": "comment", "keyword": "GROW", "comment_platforms": ["x"]}},
            'cta.comment_platforms has "x", which is not a platform name the kit knows.',
        ),
        ({"platforms": [5]}, "platforms has 5, which is not a platform name the kit knows."),
    ],
)
def test_save_rejects_names_that_are_not_platforms_and_lists_the_right_ones(me, answers, start):
    state = State()
    state.save("welcome", {})
    before = (me / ".onboarding.json").read_bytes()

    with pytest.raises(ValueError) as caught:
        state.save("who", answers)

    message = str(caught.value)
    assert message.startswith(start)
    assert message.endswith(NAMES)
    assert "Traceback" not in message and EM_DASH not in message and EN_DASH not in message
    assert (me / ".onboarding.json").read_bytes() == before
    assert state.done == ["welcome"] and state.answers == {}


def test_save_reports_every_answer_that_has_a_wrong_platform_name_in_one_message(me):
    state = State()
    with pytest.raises(ValueError) as caught:
        state.save("who", {"platforms": ["x"], "handles": {"x": "@a"}, "cta": {"type": "comment", "comment_platforms": ["X"]}})
    assert str(caught.value) == (
        'platforms has "x", which is not a platform name the kit knows. '
        'handles has "x", which is not a platform name the kit knows. '
        'cta.comment_platforms has "X", which is not a platform name the kit knows. ' + NAMES
    )


def test_save_accepts_all_six_platform_names_in_every_answer_that_holds_them(me):
    names = list(config.PLATFORMS)
    assert names == ["instagram", "tiktok", "youtube", "facebook", "linkedin", "twitter"]
    state = State()
    state.save(
        "who",
        {
            "platforms": names,
            "handles": {name: "@examplestudio" for name in names},
            "posting_times": {name: "18:00" for name in names},
            "cta": {"type": "comment", "keyword": "GROW", "comment_platforms": names},
        },
    )
    assert state.answers["platforms"] == names


def test_save_leaves_other_kinds_of_value_for_finish_to_report(me):
    state = State()
    state.save("who", {"platforms": "instagram", "handles": ["instagram"], "posting_times": 5, "cta": "follow"})
    assert state.answers["platforms"] == "instagram"
    with pytest.raises(ProfileError) as caught:
        State().finish()
    assert any(problem.startswith("platforms has the wrong kind of value") for problem in caught.value.problems)


def test_a_platform_name_problem_on_the_command_line_is_one_sentence_with_exit_1(me, capsys):
    code, out, err = run(["save", "who", '{"platforms": ["instagram", "x"]}'], capsys)
    assert (code, out) == (1, "")
    assert err == 'platforms has "x", which is not a platform name the kit knows. ' + NAMES + "\n"
    assert not (me / ".onboarding.json").exists()


# --- clearing a group that was saved wrong ---------------------------------------------------------------------


def test_saving_a_group_again_keeps_an_entry_the_new_one_leaves_out(me):
    state = State()
    state.save("posting", {"posting_times": {"instagram": "18:00", "tiktok": "19:00"}})
    state.save("posting", {"posting_times": {"instagram": "17:00"}})
    assert state.answers["posting_times"] == {"instagram": "17:00", "tiktok": "19:00"}


def test_a_group_saved_as_null_and_then_in_full_holds_only_the_full_group(me, capsys):
    state = State()
    state.save("posting", {"posting_times": {"instagram": "18:00", "tiktok": "19:00"}})

    state.save("posting", {"posting_times": None})
    assert state.answers["posting_times"] is None
    state.save("posting", {"posting_times": {"instagram": "17:00"}})

    assert state.answers["posting_times"] == {"instagram": "17:00"}
    assert State().answers["posting_times"] == {"instagram": "17:00"}, "and that is what the file holds"


def test_the_same_reset_works_on_the_command_line_and_reaches_the_profile(me, capsys):
    fill(State(), answers_for(posting_times={"instagram": "18:00", "tiktok": "19:00"}))
    assert run(["save", "posting", '{"posting_times": null}'], capsys)[0] == 0
    assert run(["save", "posting", '{"posting_times": {"instagram": "17:00"}}'], capsys)[0] == 0

    profile = State().finish()

    assert profile.posting_times == {"instagram": "17:00"}
    assert json.loads((me / "profile.json").read_text(encoding="utf-8"))["posting_times"] == {"instagram": "17:00"}


def test_an_answer_saved_as_null_counts_as_not_answered_yet(me):
    state = fill(State())
    state.save("who", {"handles": None, "cta": None})
    assert state.missing_answers() == ["handles", "cta"]
    with pytest.raises(ProfileError) as caught:
        state.finish()
    assert [p.split(" ")[0] for p in caught.value.problems] == ["handles", "cta"]
    assert all(" is missing" in p for p in caught.value.problems)
    assert files_under(me) == [".onboarding.json"]


def test_an_optional_answer_saved_as_null_falls_back_to_its_default(me):
    state = fill(State(), answers_for(posting_times={"instagram": "18:00"}, voice_over="elevenlabs"))
    state.save("posting", {"posting_times": None})
    state.save("styles", {"voice_over": None})
    profile = state.finish()
    assert profile.posting_times == {} and profile.voice_over == "record"


# --- saying that a profile was replaced -------------------------------------------------------------------------


def test_finish_says_so_when_it_replaces_a_profile_that_was_already_there(me, capsys):
    fill(State())
    first = run(["finish"], capsys)[1].splitlines()
    second = run(["finish"], capsys)[1].splitlines()

    assert first == ["Wrote me/profile.json and me/brand/tokens.css."]
    assert second == [
        "Wrote me/profile.json and me/brand/tokens.css.",
        "It replaced the me/profile.json that was already there.",
    ]


def test_finish_says_so_for_a_profile_that_setup_did_not_write(me, capsys):
    write_profile(me)
    fill(State())
    lines = run(["finish"], capsys)[1].splitlines()
    assert lines[1] == "It replaced the me/profile.json that was already there."


def test_the_state_remembers_whether_finish_replaced_a_profile(me):
    state = fill(State())
    assert state.replaced_profile is False
    state.finish()
    assert state.replaced_profile is False
    state.finish()
    assert state.replaced_profile is True


# --- the cross-field rules in the loader ----------------------------------------------------------------------


def write_profile(me: Path, **changes) -> None:
    data = copy.deepcopy(EXAMPLE)
    for key, value in changes.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    me.mkdir(parents=True, exist_ok=True)
    (me / "profile.json").write_text(json.dumps(data, indent=2), encoding="utf-8")


def problems_for(me: Path) -> list[str]:
    with pytest.raises(ProfileError) as caught:
        config.load_profile()
    return caught.value.problems


def test_the_example_profile_passes_the_cross_field_rules(me):
    write_profile(me)
    assert config.load_profile().name == EXAMPLE["name"]


def test_a_comment_platform_that_is_not_a_posting_platform_is_a_problem(me):
    write_profile(
        me,
        platforms=["instagram", "tiktok"],
        handles={"instagram": "@examplestudio", "tiktok": "@examplestudio"},
        cta={"type": "comment", "keyword": "GROW", "payload": "the checklist", "comment_platforms": ["instagram", "facebook"]},
    )
    assert problems_for(me) == [
        "cta.comment_platforms lists facebook, which is not in platforms. "
        "Add facebook to platforms, or take it out of cta.comment_platforms."
    ]


def test_every_comment_platform_outside_platforms_gets_its_own_sentence(me):
    write_profile(
        me,
        platforms=["instagram"],
        handles={"instagram": "@examplestudio"},
        cta={"type": "comment", "keyword": "GROW", "payload": "x", "comment_platforms": ["youtube", "tiktok"]},
    )
    found = problems_for(me)
    assert len(found) == 2
    assert found[0].startswith("cta.comment_platforms lists youtube,")
    assert found[1].startswith("cta.comment_platforms lists tiktok,")


@pytest.mark.parametrize("empty", ["", "   ", None])
def test_a_platform_without_a_handle_is_a_problem(me, empty):
    handles = {**EXAMPLE["handles"], "tiktok": empty}
    if empty is None:
        handles.pop("tiktok")
    write_profile(me, handles=handles)
    assert problems_for(me) == [
        "handles.tiktok is empty, but tiktok is in platforms. It should be your TikTok account name, "
        "such as @yourname. If you do not post there, take tiktok out of platforms."
    ]


def test_a_handle_for_a_platform_you_do_not_post_on_is_fine(me):
    # the example profile still has handles for all six platforms, and it comments on facebook too, so that goes
    write_profile(
        me,
        platforms=["instagram"],
        cta={"type": "comment", "keyword": "GROW", "payload": "x", "comment_platforms": ["instagram"]},
    )
    assert config.load_profile().platforms == ("instagram",)


def test_a_handle_that_is_not_text_is_reported_once(me):
    write_profile(me, handles={**EXAMPLE["handles"], "tiktok": 5})
    found = problems_for(me)
    assert len(found) == 1 and found[0].startswith("handles.tiktok has the wrong kind of value")


def test_cross_field_rules_stay_quiet_when_the_lists_they_read_are_themselves_wrong(me):
    write_profile(me, platforms="instagram")
    found = problems_for(me)
    assert len(found) == 1 and found[0].startswith("platforms has the wrong kind of value")
    write_profile(me, platforms=["myspace"])
    found = problems_for(me)
    assert len(found) == 1 and "myspace" in found[0]


@pytest.mark.parametrize("literal, word", [("NaN", "NaN"), ("Infinity", "Infinity")])
def test_a_cost_cap_that_is_not_a_real_number_is_a_problem(me, literal, word):
    write_profile(me)
    text = (me / "profile.json").read_text(encoding="utf-8")
    text = text.rstrip().removesuffix("}") + ', "research": {"cost_cap": {"instagram": ' + literal + "}}}"
    (me / "profile.json").write_text(text, encoding="utf-8")
    assert problems_for(me) == [
        f"research.cost_cap.instagram is {word}, which is not a usable amount. "
        "It should be an amount in US dollars: the most one research run may spend on Instagram."
    ]


def test_a_negative_cost_cap_is_still_reported_once_by_the_schema(me):
    write_profile(me, research={"cost_cap": {"tiktok": -1}})
    found = problems_for(me)
    assert len(found) == 1
    assert found[0].startswith("research.cost_cap.tiktok is -1, which is below the lowest allowed value of 0")


def test_minus_infinity_is_reported_once(me):
    write_profile(me)
    text = (me / "profile.json").read_text(encoding="utf-8")
    text = text.rstrip().removesuffix("}") + ', "research": {"cost_cap": {"tiktok": -Infinity}}}'
    (me / "profile.json").write_text(text, encoding="utf-8")
    assert len(problems_for(me)) == 1


def test_a_zero_or_ordinary_cost_cap_is_fine(me):
    write_profile(me, research={"cost_cap": {"instagram": 0, "tiktok": 1.5}})
    assert config.load_profile().research.cost_cap == {"instagram": 0.0, "tiktok": 1.5}


def test_all_the_cross_field_problems_are_listed_together_in_profile_order(me):
    write_profile(
        me,
        platforms=["instagram", "tiktok"],
        handles={"instagram": "@examplestudio"},
        cta={"type": "comment", "keyword": "GROW", "payload": "x", "comment_platforms": ["youtube"]},
        research={"cost_cap": {"instagram": 0.2}},
    )
    found = problems_for(me)
    assert [p.split(" ")[0] for p in found] == ["handles.tiktok", "cta.comment_platforms"]


def test_the_cross_field_rules_apply_to_profile_problems_directly():
    data = copy.deepcopy(EXAMPLE)
    data["handles"]["linkedin"] = ""
    assert config.profile_problems(data)[0].startswith("handles.linkedin is empty, but linkedin is in platforms")


# --- the command line ------------------------------------------------------------------------------------------


def run(argv, capsys, stdin=None, monkeypatch=None):
    if stdin is not None:
        monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    code = onboarding.main(argv)
    out = capsys.readouterr()
    assert "Traceback" not in out.out + out.err
    assert EM_DASH not in out.out + out.err and EN_DASH not in out.out + out.err
    return code, out.out, out.err


def test_status_on_a_fresh_start(me, capsys):
    code, out, err = run(["status"], capsys)
    assert (code, err) == (0, "")
    assert out.splitlines()[0] == "Setup: 0 of 10 steps done. Next: welcome (step 1)."
    assert "Profile: not written yet." in out
    assert "Answers still needed before the profile can be written: " + ", ".join(REQUIRED) + "." in out


def test_status_part_way_through(me, capsys):
    fill(State(), through="look")
    code, out, err = run(["status"], capsys)
    lines = out.splitlines()
    assert lines[0] == "Setup: 4 of 10 steps done. Next: voice (step 5)."
    assert "Done: welcome, machine, who, look." in out
    assert "Still to do: voice, watchlist, posting, styles, test_clip, tour." in out
    assert "Answers still needed before the profile can be written: timezone, styles." in out


def test_status_after_the_profile_is_written(me, capsys):
    fill(State()).finish()
    code, out, err = run(["status"], capsys)
    assert "Profile: written (me/profile.json)." in out
    assert "Answers still needed" not in out
    assert out.splitlines()[0] == "Setup: 8 of 10 steps done. Next: test_clip (step 9)."


def test_status_when_everything_is_done(me, capsys):
    state = fill(State())
    state.save("test_clip")
    state.save("tour")
    state.finish()
    code, out, err = run(["status"], capsys)
    assert out.splitlines()[0] == "Setup: all 10 steps are done."
    assert "Still to do" not in out


def test_status_notices_a_profile_that_exists_without_a_state_file(me, capsys):
    write_profile(me)
    code, out, err = run(["status"], capsys)
    assert "Profile: written (me/profile.json)." in out


def test_next_prints_only_the_step_name(me, capsys):
    assert run(["next"], capsys)[1] == "welcome\n"
    fill(State(), through="who")
    assert run(["next"], capsys)[1] == "look\n"


def test_save_takes_the_answers_on_the_command_line(me, capsys):
    code, out, err = run(["save", "who", '{"name": "Example Studio"}'], capsys)
    assert (code, err) == (0, "")
    assert out == "Saved who. 1 of 10 steps done. Next: welcome.\n"
    assert State().answers == {"name": "Example Studio"}


def test_save_reads_the_answers_from_standard_input(me, capsys, monkeypatch):
    text = json.dumps({"name": "Café Étoile", "niche": "Apostrophes, like it's here"})
    code, out, err = run(["save", "who", "-"], capsys, stdin=text, monkeypatch=monkeypatch)
    assert code == 0
    assert State().answers["name"] == "Café Étoile"


def test_save_with_no_answers_is_fine(me, capsys):
    assert run(["save", "welcome"], capsys)[0] == 0
    assert State().done == ["welcome"]


def test_save_says_when_the_last_step_is_done(me, capsys):
    state = State()
    for step in STEPS[:-1]:
        state.save(step)
    code, out, err = run(["save", "tour"], capsys)
    assert out == "Saved tour. All 10 steps are done.\n"


@pytest.mark.parametrize(
    "argv, stdin, words",
    [
        (["save", "who", "{not json"], None, "not valid JSON"),
        (["save", "who", "[1, 2]"], None, "one group of answers"),
        (["save", "who", "-"], "", "not valid JSON"),
        (["save", "nonsense", "{}"], None, "welcome, machine, who"),
        (["save", "posting", '{"ZERNIO_API_KEY": "not-a-real-key"}'], None, ".env"),
    ],
)
def test_save_problems_are_one_sentence_on_stderr_with_exit_1(me, capsys, monkeypatch, argv, stdin, words):
    code, out, err = run(argv, capsys, stdin=stdin, monkeypatch=monkeypatch)
    assert code == 1 and out == ""
    assert words in err
    assert "not-a-real-key" not in err
    assert not (me / ".onboarding.json").exists()


def test_finish_prints_what_it_wrote(me, capsys):
    fill(State())
    code, out, err = run(["finish"], capsys)
    assert (code, err) == (0, "")
    assert out.splitlines()[0] == "Wrote me/profile.json and me/brand/tokens.css."
    assert config.load_profile().name == EXAMPLE["name"]


def test_finish_mentions_hard_to_read_colours_but_still_writes(me, capsys):
    fill(State(), answers_for(colours={**EXAMPLE["colours"], "accent": "#f2f2f2"}))
    code, out, err = run(["finish"], capsys)
    assert code == 0
    assert "Heads up" in out and "accent" in out
    assert (me / "profile.json").is_file()


def test_finish_problems_go_to_stderr_with_exit_1(me, capsys):
    fill(State(), answers_for(name=None))
    code, out, err = run(["finish"], capsys)
    assert code == 1 and out == ""
    assert "- name is missing." in err and "Nothing was written." in err


def test_tokens_rewrites_the_tokens_from_the_profile(me, capsys):
    fill(State()).finish()
    (me / "brand" / "tokens.css").write_text("stale", encoding="utf-8")
    code, out, err = run(["tokens"], capsys)
    assert (code, err) == (0, "")
    assert out.splitlines()[0] == "Wrote me/brand/tokens.css from your profile."
    assert (me / "brand" / "tokens.css").read_text(encoding="utf-8") == brand.tokens_css(config.load_profile())


def test_tokens_before_setup_says_so(me, capsys):
    code, out, err = run(["tokens"], capsys)
    assert code == 1 and "set me up" in err


def test_a_damaged_state_file_is_a_sentence_on_the_command_line_too(me, capsys):
    me.mkdir(parents=True)
    (me / ".onboarding.json").write_text("nope")
    code, out, err = run(["status"], capsys)
    assert code == 1 and "Delete it" in err


def test_no_command_prints_usage(me, capsys):
    with pytest.raises(SystemExit) as stop:
        onboarding.main([])
    assert stop.value.code == 2
    assert "usage" in capsys.readouterr().err.lower()


def test_every_command_has_help(capsys):
    for name in onboarding.COMMANDS:
        with pytest.raises(SystemExit) as stop:
            onboarding.main([name, "--help"])
        assert stop.value.code == 0
        assert capsys.readouterr().out.strip()


# --- the look preview ------------------------------------------------------------------------------------------


LOOK = {"colours": EXAMPLE["colours"], "fonts": EXAMPLE["fonts"], "logo": None}


def test_the_preview_is_one_self_contained_page(me):
    path, warnings = onboarding.preview(LOOK, name="Café <Étoile>")

    assert path == me / "brand" / "preview.html"
    text = path.read_text(encoding="utf-8")
    assert warnings == []
    assert text.startswith("<!doctype html>")
    assert "@font-face" in text and "var(--accent)" in text and "var(--font-display)" in text
    assert "Café &lt;Étoile&gt;" in text and "<Étoile>" not in text, "the name is escaped"
    for value in EXAMPLE["colours"].values():
        assert value in text, "each colour is shown with its code"
    assert "<script" not in text and "http://" not in text and "https://" not in text
    assert "Every colour pair is easy to read." in text
    assert files_under(me) == ["brand/preview.html"], "a preview does not write the tokens or the profile"


def compositor_grid() -> tuple[int, int, float]:
    """(cell size, line thickness, strength) of the grid the UI story compositor draws, read from its own file."""
    path = ROOT / ".claude" / "skills" / "ui-story" / "kit" / "composite_v2.py"
    source = path.read_text(encoding="utf-8")
    size = int(re.search(r'"--grid", type=int, default=(\d+)', source).group(1))
    line = int(re.search(r'"--grid-t", type=int, default=(\d+)', source).group(1))
    strength = float(re.search(r"^GRID_ALPHA = ([0-9.]+)", source, re.M).group(1))
    return size, line, strength


def test_the_preview_draws_the_same_grid_as_the_ui_story_canvas(me, tmp_path):
    """The compositor draws a thin line every few pixels in the accent, faint over the canvas colour.

    The preview must show that grid, in the colour ffmpeg really makes, so the card looks like the canvas
    the videos sit on. A frame is drawn with the compositor's own filter and compared with the page.
    """
    from PIL import Image

    size, line, strength = compositor_grid()
    page = onboarding.preview(LOOK)[0].read_text(encoding="utf-8")
    assert f"background-size: {size}px {size}px" in page
    assert page.count(f"{line}px") >= 4, "each of the two gradients draws a line of that thickness"
    drawn = re.search(r"--canvas-grid: (#[0-9a-f]{6});", page)
    assert drawn, "the grid colour is one variable the page reads"

    canvas, accent = EXAMPLE["colours"]["canvas"], EXAMPLE["colours"]["accent"]
    frame = tmp_path / "grid.png"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
         f"color=c=0x{canvas[1:]}:s=200x200:r=60:d=0.1,drawgrid=w={size}:h={size}:t={line}:c=0x{accent[1:]}@{strength},format=rgba",
         "-frames:v", "1", str(frame)],
        check=True,
    )
    image = Image.open(frame).convert("RGB")

    def near(pixel, hex_colour):
        wanted = tuple(int(hex_colour[i : i + 2], 16) for i in (1, 3, 5))
        return max(abs(a - b) for a, b in zip(pixel, wanted)) <= 3

    assert near(image.getpixel((0, 0)), drawn.group(1)), "where the lines cross"
    assert near(image.getpixel((size + 40, 0)), drawn.group(1)), "on a horizontal line"
    assert near(image.getpixel((0, size + 40)), drawn.group(1)), "on a vertical line"
    assert near(image.getpixel((size // 2, size // 2)), canvas), "between the lines"
    assert not near(image.getpixel((0, 0)), canvas), "the grid has to show"


def test_the_preview_grid_follows_the_accent_and_the_canvas(me):
    look = {**LOOK, "colours": {**EXAMPLE["colours"], "accent": "#ff0000", "canvas": "#ffffff"}}
    page = onboarding.preview(look)[0].read_text(encoding="utf-8")
    # 9% red over white: the red channel stays full and the others drop by 9% of 255 (23)
    assert "--canvas-grid: #ffe8e8;" in page


def test_the_preview_uses_the_saved_name_when_none_is_given(me):
    State().save("who", {"name": "Example Studio"})
    path, _ = onboarding.preview(LOOK)
    assert "Example Studio" in path.read_text(encoding="utf-8")


def test_the_preview_without_any_name_says_your_name(me):
    path, _ = onboarding.preview(LOOK)
    assert "Your name" in path.read_text(encoding="utf-8")


def test_the_preview_takes_the_saved_look_when_none_is_given(me):
    State().save("look", LOOK)
    path, warnings = onboarding.preview()
    assert path.is_file() and warnings == []


def test_the_preview_without_any_look_says_what_is_missing(me):
    with pytest.raises(ProfileError) as caught:
        onboarding.preview()
    assert any(p.startswith("colours is missing") for p in caught.value.problems)
    assert any(p.startswith("fonts is missing") for p in caught.value.problems)


def test_the_preview_lists_the_pairs_that_are_hard_to_read(me):
    look = {**LOOK, "colours": {**EXAMPLE["colours"], "accent": "#f2f2f2"}}
    path, warnings = onboarding.preview(look)
    text = path.read_text(encoding="utf-8")
    assert warnings and warnings == brand.check(config.Profile.from_dict({**EXAMPLE, "colours": look["colours"]}))
    assert "Every colour pair is easy to read." not in text
    for warning in warnings:
        assert warning.split(" has a contrast")[0].replace("&", "&amp;") in text


def test_the_preview_refuses_a_bad_colour_with_the_same_sentences_as_finish(me):
    look = {**LOOK, "colours": {**EXAMPLE["colours"], "ink": "black"}}
    with pytest.raises(ProfileError) as caught:
        onboarding.preview(look)
    assert caught.value.problems[0].startswith("colours.ink has the value")
    assert not (me / "brand" / "preview.html").exists()


def test_the_preview_shows_the_logo_and_the_creators_own_font(me):
    (me / "brand" / "fonts").mkdir(parents=True)
    (me / "brand" / "logo").mkdir(parents=True)
    (me / "brand" / "fonts" / "Custom.woff2").write_bytes(b"x")
    (me / "brand" / "logo" / "logo.png").write_bytes(b"x")
    look = {
        "colours": EXAMPLE["colours"],
        "fonts": {"display": "brand/fonts/Custom.woff2", "body": "Inter", "mono": "JetBrains Mono"},
        "logo": "brand/logo/logo.png",
    }
    text = onboarding.preview(look)[0].read_text(encoding="utf-8")
    assert '<img src="logo/logo.png"' in text
    assert 'url("fonts/Custom.woff2")' in text


def test_the_preview_refuses_a_logo_that_is_not_there(me):
    look = {**LOOK, "logo": "brand/logo/logo.png"}
    with pytest.raises(ProfileError) as caught:
        onboarding.preview(look)
    assert caught.value.problems[0].startswith("logo points to brand/logo/logo.png")


def test_the_preview_command(me, capsys):
    code, out, err = run(["preview", json.dumps(LOOK)], capsys)
    assert (code, err) == (0, "")
    assert out.splitlines()[0] == "Wrote me/brand/preview.html. Open it in a browser to see the card."
    assert "Every colour pair is easy to read." in out


def test_the_preview_command_lists_the_warnings(me, capsys):
    look = {**LOOK, "colours": {**EXAMPLE["colours"], "accent": "#f2f2f2"}}
    code, out, err = run(["preview", json.dumps(look)], capsys)
    assert code == 0
    assert "Hard to read:" in out and "- The accent" in out


def test_the_preview_command_reports_a_problem_as_a_sentence(me, capsys):
    code, out, err = run(["preview", json.dumps({"colours": {}, "fonts": EXAMPLE["fonts"]})], capsys)
    assert code == 1 and out == ""
    assert "colours.canvas is missing" in err


# --- step 7: which platforms are connected in Zernio ---------------------------------------------------------------


def zernio_says(monkeypatch, **states):
    """Make kit.post.accounts_check answer without asking Zernio: every platform is connected unless named."""
    from kit import post

    answer = {name: states.get(name, "connected") for name in config.PLATFORMS}
    monkeypatch.setattr(post, "accounts_check", lambda *args, **kwargs: dict(answer))


@pytest.fixture
def no_key(tmp_path, monkeypatch):
    """No Zernio key anywhere: not in the environment, and no .env in the kit folder."""
    monkeypatch.setattr(config, "kit_root", lambda: tmp_path / "kit")
    monkeypatch.delenv("ZERNIO_API_KEY", raising=False)


def test_each_platform_is_shown_as_connected_or_not(me, monkeypatch):
    zernio_says(monkeypatch, tiktok="not connected")
    lines = onboarding.account_lines(["instagram", "tiktok", "twitter"])
    assert lines[:3] == ["Instagram: connected", "TikTok: not connected", "X: connected"]


def test_a_platform_that_is_not_connected_is_named_with_what_to_do(me, monkeypatch):
    zernio_says(monkeypatch, tiktok="not connected", youtube="not connected")
    lines = onboarding.account_lines(["instagram", "tiktok", "youtube"])
    assert len(lines) == 4
    assert lines[3].startswith("Connect TikTok and YouTube inside Zernio to post there.")
    assert "never posts it another way" in lines[3]


def test_when_every_platform_is_connected_there_is_nothing_to_fix(me, monkeypatch):
    zernio_says(monkeypatch)
    assert onboarding.account_lines(["instagram", "tiktok"]) == ["Instagram: connected", "TikTok: connected"]


def test_every_platform_the_kit_knows_is_listed_when_the_creator_has_not_chosen_any(me, monkeypatch):
    zernio_says(monkeypatch)
    names = [line.split(":")[0] for line in onboarding.account_lines()]
    assert names == ["Instagram", "TikTok", "YouTube", "Facebook", "LinkedIn", "X"]


def test_the_command_shows_the_platforms_the_creator_chose(me, monkeypatch, capsys):
    zernio_says(monkeypatch, instagram="not connected")
    State().save("who", {"platforms": ["instagram", "twitter"]})
    code, out, err = run(["accounts"], capsys)
    assert (code, err) == (0, "")
    assert out.splitlines()[:2] == ["Instagram: not connected", "X: connected"]
    assert "TikTok" not in out and "Connect Instagram inside Zernio" in out


def test_without_a_key_posting_by_hand_is_one_plain_sentence_and_setup_moves_on(me, no_key, capsys):
    code, out, err = run(["accounts"], capsys)
    assert (code, err) == (0, ""), "not a problem: the creator posts by hand"
    assert out.strip() == onboarding.NO_ZERNIO
    assert out.count("\n") == 1, "one sentence"
    assert "a file with a caption for each platform (post/manual.md)" in out and "by hand" in out
    assert "Traceback" not in out


def test_a_key_that_zernio_turns_away_is_a_sentence_on_the_error_stream(me, monkeypatch, capsys):
    from kit import post

    def refused(*args, **kwargs):
        raise post.ZernioError("Zernio did not accept the key (HTTP 401). Check ZERNIO_API_KEY in .env.", status=401)

    monkeypatch.setattr(post, "accounts_check", refused)
    code, out, err = run(["accounts"], capsys)
    assert code == 1 and out == ""
    assert "did not accept the key" in err


def test_the_accounts_command_never_shows_the_key(me, monkeypatch, capsys):
    monkeypatch.setenv("ZERNIO_API_KEY", "not-a-real-key")
    zernio_says(monkeypatch)
    code, out, err = run(["accounts"], capsys)
    assert "not-a-real-key" not in out + err


# --- the onboard and voice skills and the guide -------------------------------------------------------------


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def frontmatter(path: Path) -> dict:
    block = read(path).split("---\n")[1]
    return dict(re.findall(r"^(\w+):\s*(.+)$", block, re.M))


def sections(body: str) -> list[str]:
    return re.findall(r"^## (.+)$", body, re.M)


@pytest.mark.parametrize("name", ["onboard", "voice"])
def test_the_skill_follows_the_shared_contract(name):
    path = SKILLS / name / "SKILL.md"
    meta = frontmatter(path)
    assert meta["name"] == name
    assert len(meta["description"]) > 80 and "Use when" in meta["description"]
    body = read(path)
    assert f'kit.rules.read("{name}")' in body
    assert sections(body)[-1] == "Feedback", "Feedback is the last section"
    first_step = re.search(r"^1\. (.+)$", body, re.M).group(1)
    assert "kit.rules.read" in first_step, "reading the creator's rules is the first numbered step"
    feedback = body[body.index("## Feedback") :]
    assert f'kit.rules.add("{name}"' in feedback and f"python -m kit.rules add {name}" in feedback
    for command in re.findall(r"python -m kit\.rules (\w+)", body):
        assert command in {"read", "add", "undo", "remove", "recent"}, command


@pytest.mark.parametrize("name", ["onboard", "voice"])
def test_the_skill_prose_has_no_dashes_no_unfinished_markers_and_no_leftover_voice(name):
    body = read(SKILLS / name / "SKILL.md")
    assert EM_DASH not in body and EN_DASH not in body
    assert MARKER not in body and "FIX" + "ME" not in body
    assert not any(chr(code) in body for code in (0x201C, 0x201D, 0x2018, 0x2019)), "straight quotes only"
    assert "/" + "Users" + "/" not in body
    handles = set(re.findall(r"@\w+", body)) - {"@yourname", "@examplestudio"}
    assert not [h for h in handles if not h.startswith("@example_")], "only made-up handles may appear"


def test_the_onboard_description_names_the_phrases_people_use():
    description = frontmatter(SKILLS / "onboard" / "SKILL.md")["description"]
    for phrase in ("set me up", "onboard", "get started"):
        assert phrase in description


STEP_TITLES = [
    ("welcome", "Welcome"),
    ("machine", "Machine check"),
    ("who", "Who they are"),
    ("look", "Their look"),
    ("voice", "Their voice"),
    ("watchlist", "Their watch list"),
    ("posting", "Posting"),
    ("styles", "Styles"),
    ("test_clip", "Test clip"),
    ("tour", "Tour"),
]


def step_sections(body: str) -> dict:
    parts = re.split(r"^### Step (\d+): (.+)$", body, flags=re.M)
    found = {}
    for number, title, text in zip(parts[1::3], parts[2::3], parts[3::3]):
        found[int(number)] = (title.strip(), text)
    return found


def test_the_onboard_skill_walks_the_ten_steps_in_order():
    found = step_sections(read(SKILLS / "onboard" / "SKILL.md"))
    assert sorted(found) == list(range(1, 11))
    assert [found[n][0] for n in range(1, 11)] == [title for _, title in STEP_TITLES]
    assert [step for step, _ in STEP_TITLES] == list(STEPS)


def test_the_onboard_skill_saves_each_step_by_a_name_the_code_knows():
    body = read(SKILLS / "onboard" / "SKILL.md")
    saved = re.findall(r"python -m kit\.onboarding save (\w+)", body)
    assert set(saved) == set(STEPS), set(STEPS) ^ set(saved)
    for command in set(re.findall(r"python -m kit\.onboarding (\w+)", body)):
        assert command in onboarding.COMMANDS, command
    # the profile is written after the styles step and before the test clip
    assert body.index("onboarding save styles") < body.index("onboarding finish") < body.index("onboarding save test_clip")


def test_each_step_section_names_what_it_runs_and_says_so_when_a_piece_is_missing():
    found = step_sections(read(SKILLS / "onboard" / "SKILL.md"))
    machine = found[2][1]
    assert "python3 bin/doctor.py" in machine and "permission" in machine
    look = found[4][1]
    assert "kit.brand.check" in look and "onboarding preview" in look
    assert "what they want to change" in look
    assert "`voice` skill" in found[5][1]
    posting = found[7][1]
    assert "kit.post.accounts_check()" in posting and "kit.onboarding accounts" in posting
    assert "not there yet" not in posting, "the posting code ships with the kit, so there is no gap to explain"
    styles = found[8][1]
    assert "`character` skill" in styles and "not there yet" in styles
    test_clip = found[9][1]
    assert ".claude/skills/ui-story/kit/test_clip.py" in test_clip
    assert ".claude/skills/mini-me/kit/test_clip.py" in test_clip
    assert "not there yet" in test_clip
    assert "run my research" in found[10][1]


def test_posting_without_a_key_is_one_plain_sentence_and_setup_goes_on():
    posting = step_sections(read(SKILLS / "onboard" / "SKILL.md"))[7][1]
    assert "ready to post by hand" in posting and "a file with a caption for each platform" in posting
    assert "post/manual.md" in posting and "caption files" not in posting, "the kit writes one file, not one per platform"
    assert "exits with an error" in posting and "go on" in posting, "a key Zernio turns away does not stop setup"


def test_the_styles_step_hands_mini_me_to_the_character_skill_and_keeps_its_gates():
    styles = step_sections(read(SKILLS / "onboard" / "SKILL.md"))[8][1]
    assert "hand over to the `character` skill" in styles and "its own approval gates" in styles
    for gate in ("character sheet", "poses", "in-between drawings", "pack"):
        assert gate in styles, gate
    for command in ("kit.character status", "kit.character start", "kit.character sheet", "approve sheet",
                    "kit.character poses", "approve poses", "redraw <pose> --note", "kit.character moves",
                    "approve moves all", "kit.character pack"):
        assert command in styles, command
    assert "uv sync --group character --inexact" in styles
    assert "me/character/manifest.json" in styles, "how the handover is known to be done"
    assert "not there yet" in styles, "a copy of the kit without the character skill says so and goes on"
    # the profile has to exist before the handover, because the character skill reads the image tool from it
    assert styles.index("onboarding finish") < styles.index("hand over to the `character` skill")
    # a copy of the kit without the character skill is told so before any of its commands are given
    assert styles.index("not there yet") < styles.index("kit.character start")


def test_the_pack_step_names_the_separate_check_and_does_not_claim_the_pack_does_it():
    styles = step_sections(read(SKILLS / "onboard" / "SKILL.md"))[8][1]
    check = "node .claude/skills/mini-me/kit/character/test_state.mjs me/character/manifest.json"
    assert check in styles and "all passed" in styles
    assert (ROOT / ".claude" / "skills" / "mini-me" / "kit" / "character" / "test_state.mjs").is_file()
    assert styles.index("kit.character pack") < styles.index(check), "the check runs on the pack"
    assert "checks the character against the mini me runtime" not in styles, "pack only writes the manifest"
    done = styles[styles.index("The handover is done when") :]
    assert "all passed" in done.split("\n")[0], "the check is part of being done"


def test_the_styles_step_has_the_hdr_tip_and_links_the_test_take():
    styles = step_sections(read(SKILLS / "onboard" / "SKILL.md"))[8][1]
    assert "Settings, Camera, Record Video, HDR Video off" in styles
    assert "work/onboarding-test/take" in styles and "ln -s" in styles
    assert "Never copy or move it" in styles
    assert "ls -L work/onboarding-test/take/*" in styles, "a link that points nowhere is found right after it is made"
    assert styles.index("ln -s") < styles.index("ls -L work/onboarding-test/take/*")


def link_check_command() -> str:
    """The check the skill gives for the take link, as the line in its code block."""
    styles = step_sections(read(SKILLS / "onboard" / "SKILL.md"))[8][1]
    found = re.findall(r"^(ls -L work/onboarding-test/take/\S*)$", styles, re.M)
    assert len(found) == 1, found
    return found[0]


@pytest.mark.parametrize("shell", ["sh", "zsh"])
def test_the_take_link_check_says_so_when_the_link_points_nowhere(tmp_path, shell):
    """`ls -L` on the folder lists a broken link without a word on macOS. The check has to fail on one."""
    if shutil.which(shell) is None:
        pytest.skip(f"no {shell} here")
    folder = tmp_path / "work" / "onboarding-test" / "take"
    folder.mkdir(parents=True)
    run_check = lambda: subprocess.run([shell, "-c", link_check_command()], cwd=tmp_path, capture_output=True, text=True)  # noqa: E731

    assert run_check().returncode != 0, "no link at all"

    (folder / "take-01.mov").symlink_to(tmp_path / "typo-in-the-path.mov")
    broken = run_check()
    assert broken.returncode != 0 and "take-01.mov" in broken.stdout + broken.stderr

    (folder / "take-01.mov").unlink()
    real = tmp_path / "raw-take.mov"
    real.write_bytes(b"the raw take")
    (folder / "take-01.mov").symlink_to(real)
    fine = run_check()
    assert fine.returncode == 0 and "take-01.mov" in fine.stdout


def test_the_folder_alone_would_not_have_caught_it(tmp_path):
    """Why the check ends in /*: on this system a bare `ls -L` of the folder does not mind a broken link."""
    folder = tmp_path / "take"
    folder.mkdir()
    (folder / "take-01.mov").symlink_to(tmp_path / "nowhere.mov")
    done = subprocess.run(["ls", "-L", str(folder) + "/"], capture_output=True, text=True)
    if done.returncode != 0:
        pytest.skip("this ls already minds a broken link, so the /* is only a belt")
    assert "take-01.mov" in done.stdout
    assert link_check_command().endswith("/*")


def test_the_test_clip_step_runs_the_clips_command_and_says_what_it_leaves_out():
    clip_step = step_sections(read(SKILLS / "onboard" / "SKILL.md"))[9][1]
    assert "uv run python -m kit.onboarding clips" in clip_step
    assert "one at a time" in clip_step and "5 GB" in clip_step
    assert "full path" in clip_step
    assert "exits with status 1" in clip_step and "carry on" in clip_step
    # how long it takes, and so how to run it
    assert "about 90 seconds" in clip_step and "downloads the speech models" in clip_step
    assert "long timeout" in clip_step and "in the background" in clip_step
    assert "any note a clip script left" in clip_step and "Pass those on" in clip_step, "the notes are told to the creator"
    body = read(SKILLS / "onboard" / "SKILL.md")
    assert "uv run python -m kit.onboarding clips" in body.split("## Coming back later")[1], "it can be run again later"


def test_the_mini_me_clip_step_names_the_voices_in_the_order_the_script_tries_them():
    clip_step = step_sections(read(SKILLS / "onboard" / "SKILL.md"))[9][1]
    mini = clip_step[clip_step.index("- Mini me:") :]
    assert "uv run python -m kit.onboarding clips --voice <file>" in mini
    assert mini.index("--voice") < mini.index("ElevenLabs") < mini.index("scratch voice")
    # the line they are asked to record is the one the script says
    script = ROOT / ".claude" / "skills" / "mini-me" / "kit" / "test_clip.py"
    spec = importlib.util.spec_from_file_location("mini_me_test_clip_for_the_skill", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    line = module.default_line(type("Creator", (), {"name": "<their name>"})())
    assert f'"{line}"' in mini


def test_ffmpeg_full_is_offered_with_what_it_costs_on_an_older_mac():
    machine = step_sections(read(SKILLS / "onboard" / "SKILL.md"))[2][1]
    guide = read(ROOT / "ONBOARDING.md")
    for text_ in (machine, guide):
        assert "macOS 15" in text_ and "from source" in text_ and "long time" in text_


def test_the_machine_check_step_covers_the_hdr_row():
    machine = step_sections(read(SKILLS / "onboard" / "SKILL.md"))[2][1]
    assert "HDR tone mapping row only warns" in machine
    assert "turning HDR video off on the phone" in machine
    assert "uv sync --group video --group research --inexact" in machine


def test_the_skill_says_in_steps_3_and_7_that_x_is_saved_as_twitter():
    found = step_sections(read(SKILLS / "onboard" / "SKILL.md"))
    for number in (3, 7):
        assert 'X is saved as "twitter"' in found[number][1], number
    assert found[3][1].count('X is saved as "twitter"') == 1 and found[7][1].count('X is saved as "twitter"') == 1


def test_the_skill_has_the_recipe_for_clearing_a_group():
    body = read(SKILLS / "onboard" / "SKILL.md")
    section = body.split("## Fixing a saved answer", 1)[1].split("\n## ", 1)[0]
    assert "save it as null, then save the full group" in section
    assert 'save posting \'{"posting_times": null}\'' in section
    assert 'save posting \'{"posting_times": {"instagram": "17:00"}}\'' in section
    assert sections(body).index("Fixing a saved answer") < sections(body).index("Coming back later")
    # the commands in the recipe are real: run them
    for line in re.findall(r"^uv run python -m kit\.onboarding (save posting .+)$", section, re.M):
        assert line.startswith("save posting '{") and line.endswith("}'")


def test_the_onboard_skill_keeps_keys_out_of_the_conversation():
    body = read(SKILLS / "onboard" / "SKILL.md")
    assert "cp -n .env.example .env" in body
    assert "themselves" in body and "never print" in body.lower()


def test_the_onboard_skill_asks_one_topic_at_a_time_and_never_stalls_on_one_question():
    body = read(SKILLS / "onboard" / "SKILL.md")
    assert "one topic at a time" in body.lower()
    assert "picks up where they left off" in body
    assert "carry on with whatever does not depend on it" in body


def test_the_commands_the_skill_names_exist():
    body = read(SKILLS / "onboard" / "SKILL.md")
    assert (ROOT / "bin" / "doctor.py").is_file()
    assert hasattr(brand, "check")
    assert "bin/fetch_gsap.sh" in read(ROOT / "ONBOARDING.md") and (ROOT / "bin" / "fetch_gsap.sh").is_file()
    for path in re.findall(r"`(bin/[\w./-]+)`", body):
        assert (ROOT / path).is_file(), path


def test_the_voice_skill_writes_the_sections_of_the_example_voice_model():
    body = read(SKILLS / "voice" / "SKILL.md")
    example = read(ROOT / "me.example" / "voice" / "VOICE.md")
    headings = re.findall(r"^## (.+)$", example, re.M)
    assert headings == ["Tone", "Rhythm", "Sentence shapes", "Openings and closings", "Never does"]
    for heading in headings:
        assert f"## {heading}" in body, heading
    assert "me/voice/VOICE.md" in body and "me/voice/samples/" in body


def test_the_voice_skill_takes_samples_links_or_adjectives():
    body = read(SKILLS / "voice" / "SKILL.md")
    assert "pasted" in body and "adjectives" in body
    assert "kit.transcribe.words" in body and "paste" in body.lower()
    assert "humanizer" in body and "embedded mode" in body
    assert "their own" in body, "only the creator's own videos count as samples of their voice"


def test_the_voice_skill_does_not_copy_the_example_creator_into_a_real_model():
    body = read(SKILLS / "voice" / "SKILL.md")
    assert "made-up" in body or "example" in body.lower()
    assert "Example Studio" not in body


def test_onboarding_guide_has_the_fallback_prompt_the_time_and_the_optional_keys():
    guide = read(ROOT / "ONBOARDING.md")
    assert "Read .claude/skills/onboard/SKILL.md and follow it from step 1." in guide
    assert re.search(r"\b\d+ (to \d+ )?minutes\b", guide)
    for name in ("APIFY_TOKEN", "OPENAI_API_KEY", "GEMINI_API_KEY", "ELEVENLABS_API_KEY", "ZERNIO_API_KEY"):
        assert name in guide
    assert "optional" in guide.lower()
    assert "Built by" not in guide, "the README carries the credit line"
    assert EM_DASH not in guide and EN_DASH not in guide and MARKER not in guide


def test_the_fallback_prompt_names_a_file_that_is_there():
    guide = read(ROOT / "ONBOARDING.md")
    prompt = re.search(r"```\n(Read (\S+) and follow it from step 1\.)\n```", guide)
    assert prompt, "the fallback prompt sits alone in a code block"
    assert (ROOT / prompt.group(2)).is_file()


def test_the_skill_and_the_guide_describe_the_one_optional_keys_line_the_doctor_prints():
    skill = read(SKILLS / "onboard" / "SKILL.md")
    guide = read(ROOT / "ONBOARDING.md")
    assert "one line naming the optional keys that are not set yet" in skill
    assert "once it is set" in skill, "setting a key makes it leave that line"
    assert "Under the table, one line names the optional keys that are not set yet." in guide
    assert "and only the rows" in guide, "the JSON carries no key information"
    for words in ("lists the key names that are set", "listed as set", "see that `ZERNIO_API_KEY` is set"):
        assert words not in skill + guide, "the old wording, from when each key was a row"


def test_onboarding_guide_covers_every_step_in_plain_words():
    guide = read(ROOT / "ONBOARDING.md").lower()
    for words in (
        "welcome",
        "machine check",
        "who you are",
        "your look",
        "your voice",
        "watch list",
        "posting",
        "styles",
        "test clip",
        "tour",
    ):
        assert words in guide, words
    assert "python3 bin/doctor.py" in guide


def test_the_guide_and_the_skill_agree_on_the_time_to_budget():
    skill = read(SKILLS / "onboard" / "SKILL.md")
    guide = read(ROOT / "ONBOARDING.md")
    budget = re.search(r"\b(\d+ to \d+) minutes\b", guide).group(1)
    assert budget in skill


def test_nothing_the_onboarding_files_say_is_private():
    forbidden = [
        "/" + "Users" + "/",
        r"\." + "env.local",
        r"(?<![A-Za-z])sk" + r"-[A-Za-z0-9]",
        "ghp" + "_[A-Za-z0-9]",
    ]
    for path in (SKILLS / "onboard" / "SKILL.md", SKILLS / "voice" / "SKILL.md", ROOT / "ONBOARDING.md"):
        text = read(path)
        for pattern in forbidden:
            assert re.search(pattern, text) is None, f"{path.name} matches {pattern!r}"
