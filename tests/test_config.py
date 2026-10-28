"""kit.config: where the creator's files live and how their profile is read.

Every test that touches the profile works on a throwaway copy of me.example/ (the
`example_me` fixture), so the committed example is never modified.
"""

import dataclasses
import json
import logging
import re
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from kit import config
from kit.config import KitError, NotOnboarded, Profile, ProfileError

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "me.example"
EM_DASH, EN_DASH = chr(0x2014), chr(0x2013)  # written as code points so this file holds no dash


def _read(me: Path) -> dict:
    return json.loads((me / "profile.json").read_text())


def _edit(me: Path, change) -> None:
    data = _read(me)
    change(data)
    (me / "profile.json").write_text(json.dumps(data, indent=2))


def _problems(me: Path) -> list[str]:
    with pytest.raises(ProfileError) as caught:
        config.load_profile()
    return caught.value.problems


# --- the example profile ------------------------------------------------------------


def test_load_example_profile(example_me):
    profile = config.load_profile()
    assert isinstance(profile, Profile)
    assert profile.version == 1
    assert profile.onboarded is True
    assert profile.name == "Example Studio"
    assert profile.handles.instagram == "@examplestudio"
    assert profile.platforms == ("instagram", "tiktok", "youtube", "facebook", "linkedin", "twitter")
    assert profile.cta.type == "comment"
    assert profile.cta.keyword
    assert set(profile.cta.comment_platforms) <= set(profile.platforms)
    assert profile.timezone == "Europe/London"
    assert profile.posting_times["instagram"] == "18:00"
    assert profile.fonts.display == "Inter Tight"
    assert profile.styles == ("ui-story", "mini-me")
    assert profile.hashtags.base and all(tag.startswith("#") for tag in profile.hashtags.base)
    for colour in dataclasses.astuple(profile.colours):
        assert re.fullmatch(r"#[0-9a-f]{6}", colour)


def test_the_example_palette_is_five_distinct_colours(example_me):
    colours = dataclasses.astuple(config.load_profile().colours)
    assert len(set(colours)) == 5


def test_profile_is_frozen(example_me):
    profile = config.load_profile()
    with pytest.raises(dataclasses.FrozenInstanceError):
        profile.name = "Someone Else"
    with pytest.raises(dataclasses.FrozenInstanceError):
        profile.colours.accent = "#000000"


def test_example_matches_the_schema_and_the_schema_is_valid():
    schema = json.loads(config.SCHEMA_PATH.read_text())
    Draft202012Validator.check_schema(schema)
    errors = list(Draft202012Validator(schema).iter_errors(json.loads((EXAMPLE / "profile.json").read_text())))
    assert errors == []


def test_every_schema_setting_has_a_description():
    # The descriptions double as the hint in each error message, so none may be missing.
    schema = json.loads(config.SCHEMA_PATH.read_text())
    missing = []

    def walk(node, trail):
        for name, child in node.get("properties", {}).items():
            where = ".".join(trail + [name])
            if not child.get("description"):
                missing.append(where)
            walk(child, trail + [name])

    walk(schema, [])
    assert missing == []


def test_the_posting_times_hint_fits_a_creator_who_posts_by_hand_too(example_me):
    # A creator who posts by hand has usual times as well: the file for posting by hand lists them. The hint the
    # error repeats says how to leave the times out, not that posting by hand means having none.
    schema = json.loads(config.SCHEMA_PATH.read_text())
    hint = schema["properties"]["posting_times"]["description"]
    assert "usual posting time" in hint and "give a time with each post" in hint
    assert "post by hand" not in hint

    _edit(example_me, lambda data: data.update(posting_times="18:00"))
    said = "\n".join(_problems(example_me))
    assert "usual posting time for each platform" in said, said


def test_the_hashtags_hint_does_not_promise_them_on_every_post(example_me):
    # LinkedIn gets no hashtags, and profile errors repeat this hint to the creator.
    schema = json.loads(config.SCHEMA_PATH.read_text())
    hint = schema["properties"]["hashtags"]["properties"]["base"]["description"]
    assert "every post" not in hint and "LinkedIn gets none" in hint

    _edit(example_me, lambda data: data["hashtags"].update(base="#oneword"))
    said = "\n".join(_problems(example_me))
    assert "LinkedIn gets none" in said, said


def test_platform_list_comes_from_the_schema():
    assert config.PLATFORMS == ("instagram", "tiktok", "youtube", "facebook", "linkedin", "twitter")


def test_profile_round_trips_through_a_dict(example_me):
    profile = config.load_profile()
    data = profile.to_dict()
    json.dumps(data)  # plain JSON types only
    assert Profile.from_dict(data) == profile


def test_a_bom_in_the_file_is_tolerated(example_me):
    path = example_me / "profile.json"
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())
    assert config.load_profile().name == "Example Studio"


# --- not onboarded --------------------------------------------------------------------


@pytest.mark.parametrize("how", ["no profile file", "onboarded is false"])
def test_not_onboarded(example_me, how):
    if how == "no profile file":
        (example_me / "profile.json").unlink()
    else:
        _edit(example_me, lambda d: d.update(onboarded=False))
    with pytest.raises(NotOnboarded) as caught:
        config.load_profile()
    assert "set me up" in str(caught.value)
    assert isinstance(caught.value, KitError)


def test_not_onboarded_when_the_creator_folder_does_not_exist(tmp_path, monkeypatch):
    monkeypatch.setenv("KIT_ME", str(tmp_path / "no-such-folder"))
    with pytest.raises(NotOnboarded):
        config.load_profile()


def test_a_half_finished_profile_is_not_onboarded_rather_than_invalid(example_me):
    # Onboarding saves as it goes, so a flag of false with fields still missing is normal.
    _edit(example_me, lambda d: (d.update(onboarded=False), d["colours"].pop("accent")))
    with pytest.raises(NotOnboarded):
        config.load_profile()


def test_not_onboarded_when_the_flag_is_missing(example_me):
    _edit(example_me, lambda d: d.pop("onboarded"))
    with pytest.raises(NotOnboarded):
        config.load_profile()


# --- invalid profiles -----------------------------------------------------------------


def test_missing_field_named(example_me):
    _edit(example_me, lambda d: d["colours"].pop("accent"))
    problems = _problems(example_me)
    assert len(problems) == 1
    assert "colours.accent" in problems[0]
    assert "missing" in problems[0]


def test_the_closing_line_agrees_with_the_number_of_problems():
    one = str(ProfileError(["name is missing."]))
    several = str(ProfileError(["name is missing.", "niche is missing."]))
    assert one.endswith("\nFix it in me/profile.json, or say: set me up.")
    assert several.endswith("\nFix them in me/profile.json, or say: set me up.")
    assert "has a problem" in one and "has 2 problems" in several


@pytest.mark.parametrize("started", [False, True])
def test_every_not_onboarded_message_ends_with_a_full_stop(started):
    message = str(NotOnboarded(started=started))
    assert message.endswith(".")
    assert "set me up" in message


def test_the_error_message_lists_the_problems_and_says_what_to_do(example_me):
    _edit(example_me, lambda d: d["colours"].pop("accent"))
    with pytest.raises(ProfileError) as caught:
        config.load_profile()
    text = str(caught.value)
    assert "colours.accent" in text
    assert "me/profile.json" in text
    assert "set me up" in text


def test_every_problem_is_listed_at_once(example_me):
    def break_many(data):
        data["colours"].pop("accent")
        data["timezone"] = "Mars/Olympus"
        data["platforms"] = ["instagram", "tiktoc"]
        data["fonts"]["body"] = "Comic Sans"
        data["posting_times"]["instagram"] = "6pm"
        del data["niche"]

    _edit(example_me, break_many)
    problems = _problems(example_me)
    text = "\n".join(problems)
    for path in ("colours.accent", "timezone", "platforms", "fonts.body", "posting_times.instagram", "niche"):
        assert path in text
    assert len(problems) == 6


BAD_PROFILES = [
    ("accent is missing", lambda d: d["colours"].pop("accent"), "colours.accent"),
    ("a whole group is missing", lambda d: d.pop("colours"), "colours"),
    ("niche is missing", lambda d: d.pop("niche"), "niche"),
    (
        "custom font file that does not exist",
        lambda d: d["fonts"].update(display="brand/fonts/Nope.woff2"),
        "fonts.display",
    ),
    ("font path outside the creator folder", lambda d: d["fonts"].update(body="../x.ttf"), "fonts.body"),
    ("font file of the wrong type", lambda d: d["fonts"].update(mono="brand/fonts/x.exe"), "fonts.mono"),
    ("mistyped bundled font", lambda d: d["fonts"].update(display="Inter Tigth"), "fonts.display"),
    ("logo that does not exist", lambda d: d.update(logo="brand/logo/logo.png"), "logo"),
    ("logo path outside the creator folder", lambda d: d.update(logo="../logo.png"), "logo"),
    ("accent is not a hex code", lambda d: d["colours"].update(accent="blue"), "colours.accent"),
    ("hex code with three digits", lambda d: d["colours"].update(canvas="#fff"), "colours.canvas"),
    ("hex code with no hash", lambda d: d["colours"].update(ink="101820"), "colours.ink"),
    ("empty name", lambda d: d.update(name=""), "name"),
    ("name of the wrong kind", lambda d: d.update(name=7), "name"),
    ("unknown platform", lambda d: d.update(platforms=["instagram", "myspace"]), "platforms"),
    ("repeated platform", lambda d: d.update(platforms=["instagram", "instagram"]), "platforms"),
    ("no platforms", lambda d: d.update(platforms=[]), "platforms"),
    ("unknown style", lambda d: d.update(styles=["ui-story", "comic"]), "styles"),
    ("unknown voice-over", lambda d: d.update(voice_over="robot"), "voice_over"),
    ("unknown image provider", lambda d: d.update(image_provider="dalle"), "image_provider"),
    ("posting time in the wrong form", lambda d: d["posting_times"].update(instagram="6pm"), "posting_times.instagram"),
    ("posting time past midnight", lambda d: d["posting_times"].update(instagram="24:30"), "posting_times.instagram"),
    ("posting time for an unknown platform", lambda d: d["posting_times"].update(myspace="18:00"), "posting_times"),
    ("hashtag without a hash", lambda d: d["hashtags"].update(base=["growyourown"]), "hashtags.base"),
    ("hashtag with a space", lambda d: d["hashtags"].update(base=["#grow your own"]), "hashtags.base"),
    ("comment call to action without a keyword", lambda d: d["cta"].update(keyword=None), "cta.keyword"),
    ("comment call to action with an empty keyword", lambda d: d["cta"].update(keyword=""), "cta.keyword"),
    (
        "comment call to action without platforms",
        lambda d: d["cta"].update(comment_platforms=[]),
        "cta.comment_platforms",
    ),
    ("unknown call to action type", lambda d: d["cta"].update(type="dm"), "cta.type"),
    ("future profile version", lambda d: d.update(version=2), "version"),
    ("negative cost cap", lambda d: d.update(research={"cost_cap": {"instagram": -1}}), "research.cost_cap.instagram"),
    (
        "cost cap for a platform with no cap",
        lambda d: d.update(research={"cost_cap": {"youtube": 1}}),
        "research.cost_cap.youtube",
    ),
    ("handles not a group", lambda d: d.update(handles="@me"), "handles"),
    ("timezone that is not a zone", lambda d: d.update(timezone="Exampleton"), "timezone"),
    ("font family the kit does not ship", lambda d: d["fonts"].update(mono="Courier Prime"), "fonts.mono"),
    ("empty logo", lambda d: d.update(logo=""), "logo"),
    ("a setting the kit does not know", lambda d: d.update(favourite_colour="green"), "favourite_colour"),
]


@pytest.mark.parametrize("change,expected", [(c, e) for _, c, e in BAD_PROFILES], ids=[n for n, _, _ in BAD_PROFILES])
def test_invalid_values_are_named_with_their_path(example_me, change, expected):
    _edit(example_me, change)
    problems = _problems(example_me)
    assert len(problems) == 1, problems
    assert problems[0].startswith(expected), problems


def test_a_typo_in_a_setting_name_gets_a_suggestion(example_me):
    _edit(example_me, lambda d: d.update(colors=d.pop("colours")))
    text = "\n".join(_problems(example_me))
    assert "colors is not a setting the kit knows" in text
    assert "Did you mean colours?" in text
    assert "colours is missing" in text


def test_error_messages_are_plain_sentences_without_dashes(example_me):
    for _, change, _ in BAD_PROFILES:
        _edit(example_me, change)
        with pytest.raises(ProfileError) as caught:
            config.load_profile()
        text = str(caught.value)
        assert EM_DASH not in text and EN_DASH not in text
        assert "Traceback" not in text
        assert "ValidationError" not in text and "jsonschema" not in text
        # restore for the next case
        (example_me / "profile.json").write_text((EXAMPLE / "profile.json").read_text())


def test_the_other_messages_a_creator_can_see_have_no_dashes(example_me, tmp_path, monkeypatch):
    messages = [str(NotOnboarded()), str(NotOnboarded(started=True))]
    (example_me / "profile.json").write_text('{"name": ')
    with pytest.raises(ProfileError) as caught:
        config.load_profile()
    messages.append(str(caught.value))
    with pytest.raises(ValueError) as caught:
        config.work_dir("../escape")
    messages.append(str(caught.value))
    with pytest.raises(ProfileError) as caught:
        Profile.from_dict([])
    messages.append(str(caught.value))
    (example_me / "profile.json").write_bytes(b'{"name": "Caf\xe9"}')
    with pytest.raises(ProfileError) as caught:
        config.load_profile()
    messages.append(str(caught.value))
    for message in messages:
        assert EM_DASH not in message and EN_DASH not in message, message
        assert "Traceback" not in message


def test_not_valid_json_is_one_plain_sentence(example_me):
    (example_me / "profile.json").write_text('{"name": "Example Studio",')
    with pytest.raises(ProfileError) as caught:
        config.load_profile()
    assert "profile.json" in str(caught.value)
    assert "valid JSON" in str(caught.value)


def test_a_profile_that_is_not_utf8_text_is_one_plain_sentence(example_me):
    # 0xe9 is "e with an acute accent" in Latin-1, and is not valid UTF-8 on its own.
    (example_me / "profile.json").write_bytes(b'{"name": "Caf\xe9 Studio"}')
    with pytest.raises(ProfileError) as caught:
        config.load_profile()
    text = str(caught.value)
    assert "me/profile.json must be saved as UTF-8 text" in text
    assert "Traceback" not in text and "codec" not in text
    assert len(caught.value.problems) == 1


def test_a_profile_that_is_not_a_group_of_settings_is_refused(example_me):
    (example_me / "profile.json").write_text("[1, 2, 3]")
    with pytest.raises(ProfileError) as caught:
        config.load_profile()
    assert "profile.json" in str(caught.value)


def test_validation_works_on_a_dict_without_touching_disk():
    good = json.loads((EXAMPLE / "profile.json").read_text())
    assert config.profile_problems(good) == []
    with pytest.raises(ProfileError) as caught:
        Profile.from_dict({})
    assert any(problem.startswith("name") for problem in caught.value.problems)
    assert len(caught.value.problems) > 5


def test_bundled_font_names_ignore_case_and_spacing(example_me):
    _edit(example_me, lambda d: d["fonts"].update(display="inter  TIGHT"))
    assert config.load_profile().fonts.display == "inter  TIGHT"


def test_a_mistyped_font_name_gets_a_suggestion(example_me):
    _edit(example_me, lambda d: d["fonts"].update(display="Inter Tigth"))
    problems = _problems(example_me)
    assert len(problems) == 1 and problems[0].startswith("fonts.display")
    assert "Did you mean Inter Tight?" in problems[0]


def test_a_custom_font_must_exist(example_me):
    _edit(example_me, lambda d: d["fonts"].update(display="brand/fonts/YourFont.woff2"))
    problems = _problems(example_me)
    assert len(problems) == 1 and problems[0].startswith("fonts.display")
    assert "brand/fonts/YourFont.woff2" in problems[0]

    font = example_me / "brand" / "fonts" / "YourFont.woff2"
    font.parent.mkdir(parents=True)
    font.write_bytes(b"wOF2")
    assert config.load_profile().fonts.display == "brand/fonts/YourFont.woff2"


@pytest.mark.parametrize(
    "spec",
    ["../outside.woff2", "/etc/some.ttf", "~/Library/Fonts/Some.ttf", "brand/fonts/Some.exe", "brand\\fonts\\Some.ttf"],
)
def test_font_paths_stay_inside_the_creator_folder(example_me, spec):
    _edit(example_me, lambda d: d["fonts"].update(body=spec))
    problems = _problems(example_me)
    assert len(problems) == 1 and problems[0].startswith("fonts.body")


def test_a_logo_must_exist_inside_the_creator_folder(example_me):
    _edit(example_me, lambda d: d.update(logo="brand/logo/logo.png"))
    problems = _problems(example_me)
    assert len(problems) == 1 and problems[0].startswith("logo")

    logo = example_me / "brand" / "logo" / "logo.png"
    logo.parent.mkdir(parents=True)
    logo.write_bytes(b"\x89PNG")
    assert config.load_profile().logo == "brand/logo/logo.png"

    _edit(example_me, lambda d: d.update(logo="../logo.png"))
    assert _problems(example_me)[0].startswith("logo")


def test_no_logo_is_allowed(example_me):
    _edit(example_me, lambda d: d.update(logo=None))
    assert config.load_profile().logo is None


def test_a_plain_follow_call_to_action_needs_no_keyword(example_me):
    plain_follow = {"type": "follow", "keyword": None, "payload": None, "comment_platforms": []}
    _edit(example_me, lambda d: d.update(cta=plain_follow))
    profile = config.load_profile()
    assert profile.cta.type == "follow" and profile.cta.keyword is None
    assert profile.cta.comment_platforms == ()


# --- research settings -------------------------------------------------------------------


def test_cost_caps_default_when_absent(example_me):
    assert "research" not in _read(example_me)
    assert config.load_profile().research.cost_cap == {"instagram": 0.25, "tiktok": 0.50}


def test_cost_caps_can_be_overridden_one_platform_at_a_time(example_me):
    _edit(example_me, lambda d: d.update(research={"cost_cap": {"tiktok": 1}}))
    cap = config.load_profile().research.cost_cap
    assert cap == {"instagram": 0.25, "tiktok": 1.0}
    assert all(isinstance(value, float) for value in cap.values())


def test_an_empty_research_group_still_gets_the_defaults(example_me):
    _edit(example_me, lambda d: d.update(research={}))
    assert config.load_profile().research.cost_cap == {"instagram": 0.25, "tiktok": 0.50}


def test_a_cost_cap_of_zero_is_allowed(example_me):
    _edit(example_me, lambda d: d.update(research={"cost_cap": {"instagram": 0}}))
    assert config.load_profile().research.cost_cap["instagram"] == 0.0


# --- env() ----------------------------------------------------------------------------------


def test_env_reads_dotenv_without_logging(tmp_path, monkeypatch, capsys, caplog):
    secret = "fake-value-for-tests"
    other = "not-a-real-key"
    (tmp_path / ".env").write_text(
        "# keys the kit may use\n"
        "\n"
        f"APIFY_TOKEN={secret}\n"
        f'export OPENAI_API_KEY="{other}"\n'
        "GEMINI_API_KEY=\n"
    )
    monkeypatch.setattr(config, "kit_root", lambda: tmp_path)
    for name in ("APIFY_TOKEN", "OPENAI_API_KEY", "GEMINI_API_KEY", "ELEVENLABS_API_KEY"):
        monkeypatch.delenv(name, raising=False)

    with caplog.at_level(logging.DEBUG):
        assert config.env("APIFY_TOKEN") == secret
        assert config.env("OPENAI_API_KEY") == other
        assert config.env("GEMINI_API_KEY") is None  # present but blank
        assert config.env("ELEVENLABS_API_KEY") is None  # absent

    shown = capsys.readouterr()
    assert secret not in shown.out + shown.err
    assert other not in shown.out + shown.err
    assert secret not in caplog.text and other not in caplog.text


KEY_NAMES = ("APIFY_TOKEN", "OPENAI_API_KEY", "GEMINI_API_KEY", "ELEVENLABS_API_KEY", "ZERNIO_API_KEY")


def test_the_kit_knows_its_five_keys_and_they_match_the_example_file():
    assert config.KEYS == KEY_NAMES
    example = (Path(__file__).resolve().parent.parent / ".env.example").read_text(encoding="utf-8")
    assert [line.split("=")[0] for line in example.splitlines() if "=" in line] == list(KEY_NAMES)


@pytest.mark.parametrize("name", KEY_NAMES)
def test_a_key_comes_from_dotenv_only_never_from_the_environment(tmp_path, monkeypatch, name):
    # An agent's shell can carry a key the creator never gave the kit, so the environment is never read for one.
    monkeypatch.setattr(config, "kit_root", lambda: tmp_path)
    monkeypatch.setenv(name, "from-the-environment")
    assert config.key(name) is None and config.env(name) is None
    (tmp_path / ".env").write_text(f"{name}=from-the-file\n")
    assert config.key(name) == "from-the-file" and config.env(name) == "from-the-file"


def test_only_the_five_keys_are_read_through_the_key_helper(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "kit_root", lambda: tmp_path)
    with pytest.raises(ValueError):
        config.key("CODEX_IMAGE_MODEL")


def test_a_setting_that_is_not_a_key_still_prefers_the_environment(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("CODEX_IMAGE_MODEL=from-file\n")
    monkeypatch.setattr(config, "kit_root", lambda: tmp_path)
    monkeypatch.setenv("CODEX_IMAGE_MODEL", "from-environment")
    assert config.env("CODEX_IMAGE_MODEL") == "from-environment"
    monkeypatch.setenv("CODEX_IMAGE_MODEL", "   ")
    assert config.env("CODEX_IMAGE_MODEL") == "from-file", "a blank one falls back to the file"


def test_the_kit_never_says_a_key_is_read_with_config_env():
    root = Path(__file__).resolve().parent.parent
    stale = [str(p.relative_to(root)) for p in sorted((root / "kit").rglob("*.py")) if "kit.config.env" in p.read_text(encoding="utf-8")]
    assert stale == [], "these still say a key is read with kit.config.env, but keys come through kit.config.key"


def test_no_provider_takes_a_key_from_the_environment(tmp_path, monkeypatch, example_me):
    from kit import voice, zernio
    from kit.character import providers

    monkeypatch.setattr(config, "kit_root", lambda: tmp_path)
    for name in KEY_NAMES:
        monkeypatch.setenv(name, "from-the-environment")
    with pytest.raises(zernio.NoZernio):
        zernio.Client.from_env()
    assert providers.ready("openai")[0] is False and providers.ready("gemini")[0] is False
    with pytest.raises(voice.NoElevenLabsKey):
        voice._key("example-reel")
    with pytest.raises(KitError):
        voice.stock_voices()


def test_env_without_a_dotenv_file_is_none(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "kit_root", lambda: tmp_path)
    monkeypatch.delenv("APIFY_TOKEN", raising=False)
    assert config.env("APIFY_TOKEN") is None


def test_dotenv_parsing_handles_the_usual_shapes(tmp_path, monkeypatch):
    (tmp_path / ".env").write_bytes(
        b"# comment\r\n"
        b"A=plain\r\n"
        b"B = spaced \r\n"
        b"C='single quoted'\r\n"
        b'D="has # inside"\r\n'
        b"E=value # trailing note\r\n"
        b"F=keeps#hash\r\n"
        b"not a line\r\n"
        b"G=first\r\n"
        b"G=second\r\n"
    )
    monkeypatch.setattr(config, "kit_root", lambda: tmp_path)
    for name in "ABCDEFG":
        monkeypatch.delenv(name, raising=False)
    got = {name: config.env(name) for name in "ABCDEFG"}
    assert got == {
        "A": "plain",
        "B": "spaced",
        "C": "single quoted",
        "D": "has # inside",
        "E": "value",
        "F": "keeps#hash",
        "G": "second",
    }


# --- folders ---------------------------------------------------------------------------------


def test_kit_root_is_the_folder_that_holds_the_kit_package():
    assert (config.kit_root() / "kit" / "config.py").is_file()
    assert config.kit_root() == ROOT


def test_me_dir_defaults_to_me_beside_the_kit(monkeypatch):
    monkeypatch.delenv("KIT_ME", raising=False)
    assert config.me_dir() == config.kit_root() / "me"


def test_me_dir_honours_kit_me(tmp_path, monkeypatch):
    monkeypatch.setenv("KIT_ME", str(tmp_path / "somewhere"))
    assert config.me_dir() == tmp_path / "somewhere"
    monkeypatch.setenv("KIT_ME", "")
    assert config.me_dir() == config.kit_root() / "me"


def test_work_and_research_folders():
    assert config.work_dir("my-first-reel") == config.kit_root() / "work" / "my-first-reel"
    assert config.research_dir() == config.kit_root() / "research"


def test_these_functions_only_compute_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "kit_root", lambda: tmp_path)
    config.work_dir("a-reel")
    config.research_dir()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("slug", ["", ".", "..", "../escape", "a/b", "a\\b", ".hidden", "two words", "x" * 101, "/abs"])
def test_unsafe_video_names_are_refused(slug):
    with pytest.raises(ValueError) as caught:
        config.work_dir(slug)
    assert "video name" in str(caught.value)


@pytest.mark.parametrize("slug", ["reel-1", "Take_01-excerpt", "2026-10-28.demo", "a"])
def test_ordinary_video_names_are_accepted(slug):
    assert config.work_dir(slug).name == slug


@pytest.mark.parametrize(
    "title,name",
    [
        ("Ask the AI to draw your app as a map before you fix it", "ask-the-ai-to-draw-your-app"),
        ("Build a little tool that writes your shop's Facebook ads", "build-a-little-tool"),
        ("Why windowsill basil dies in week three", "why-windowsill-basil-dies"),
        ("3 of my 5 plants made it", "3-of-my-5-plants-made-it"),
        ("Supercalifragilisticexpialidociousnessisms everywhere", "supercalifragilisticexpialidoc"),
        ("¿¡!?", "video"),
    ],
)
def test_a_video_name_is_short_and_readable(title, name):
    """A folder name from an idea's title: its first words, under 30 characters, cut between words, with no small
    word left dangling at the end. Every name is one work_dir accepts."""
    assert config.video_name(title) == name
    assert len(name) <= 30 and config.work_dir(name).name == name


def test_a_new_video_never_takes_over_another_ideas_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "kit_root", lambda: tmp_path)
    title = "Why windowsill basil dies in week three"
    assert config.free_video_name(title) == "why-windowsill-basil-dies"
    folder = tmp_path / "work" / "why-windowsill-basil-dies"
    folder.mkdir(parents=True)
    (folder / "idea.json").write_text(json.dumps({"title": title}))
    assert config.free_video_name(title) == "why-windowsill-basil-dies", "the same idea keeps its folder"
    other = "Why windowsill basil dies in week four"
    assert config.free_video_name(other) == "why-windowsill-basil-dies-2"


# --- the example creator's files ----------------------------------------------------------


def test_example_watchlist_shape():
    data = json.loads((EXAMPLE / "watchlist.json").read_text())
    assert set(data) == {"instagram", "tiktok", "youtube"}
    entries = [entry for names in data.values() for entry in names]
    assert 1 <= len(entries) <= 6
    assert all(isinstance(entry, str) and "example" in entry.lower() for entry in entries)


def test_example_ideas_are_two_bullets():
    bullets = [line for line in (EXAMPLE / "ideas.md").read_text().splitlines() if line.startswith("- ")]
    assert len(bullets) == 2


def test_example_music_log_is_an_empty_table():
    lines = [line for line in (EXAMPLE / "music" / "MUSIC.md").read_text().splitlines() if line.startswith("|")]
    assert lines == ["| date | slug | track |", "| --- | --- | --- |"]


def test_example_voice_model_has_the_five_sections():
    text = (EXAMPLE / "voice" / "VOICE.md").read_text()
    headings = re.findall(r"^## (.+)$", text, re.M)
    assert headings == ["Tone", "Rhythm", "Sentence shapes", "Openings and closings", "Never does"]


def test_example_prose_has_no_em_or_en_dashes():
    text_suffixes = {".json", ".md", ".css", ".txt"}
    checked = [p for p in EXAMPLE.rglob("*") if p.suffix in text_suffixes]
    checked += [config.SCHEMA_PATH, config.FONTS_DIR / "fonts.json"]
    assert len(checked) >= 8
    for path in checked:
        text = path.read_text(encoding="utf-8")
        assert EM_DASH not in text and EN_DASH not in text, path
