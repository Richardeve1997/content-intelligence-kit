"""kit.character.providers: the image tools that draw the character.

No test here reaches a real service. HTTP calls and the codex command are replaced, and every fake key
is a plain word.
"""

from __future__ import annotations

import base64
import io
import json
import os
import subprocess
from pathlib import Path

import pytest

from kit import config
from kit.character import providers

FAKE_KEY = "not-a-real-key"
NO_TOOL_WORDS = "No image tool found, so I'll give you the prompts to paste into ChatGPT or Gemini"


def _png_bytes(size=(8, 6)) -> bytes:
    pytest.importorskip("PIL", reason="needs the character group: uv sync --group character")
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", size, (200, 200, 200)).save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def no_tools(monkeypatch):
    """No key in the environment or .env, and no codex on this machine."""
    monkeypatch.setattr(config, "env", lambda name: None)
    monkeypatch.setattr(providers.shutil, "which", lambda name: None)
    providers._codex_ready.cache_clear()
    yield
    providers._codex_ready.cache_clear()


# --- manual ----------------------------------------------------------------------------------------


def test_manual_provider_waits_then_succeeds(me, tmp_path):
    ref = tmp_path / "front.png"
    ref.write_bytes(_png_bytes())
    out = me / "raw" / "sheet-r1.png"
    tool = providers.get("manual")

    with pytest.raises(providers.WaitingForImage) as caught:
        tool.generate("Draw the character sheet.", [ref], out)

    waiting = caught.value
    inbox = me / "inbox"
    assert waiting.prompt_file == inbox / "sheet-r1.prompt.txt"
    assert waiting.image_file == inbox / "sheet-r1.png"
    assert waiting.prompt_file.read_text(encoding="utf-8") == "Draw the character sheet.\n"
    assert (inbox / "sheet-r1.attach-1.png").read_bytes() == ref.read_bytes()
    message = str(waiting)
    assert "inbox/sheet-r1.prompt.txt" in message and "inbox/sheet-r1.png" in message
    assert "ChatGPT or Gemini" in message
    assert not out.exists()

    # asking again before the picture is there still waits, and writes the same prompt
    with pytest.raises(providers.WaitingForImage):
        tool.generate("Draw the character sheet.", [ref], out)

    (inbox / "sheet-r1.png").write_bytes(_png_bytes((12, 9)))
    assert tool.generate("Draw the character sheet.", [ref], out) == out
    from character_sprites import image_size

    assert image_size(out) == (12, 9)


def test_manual_provider_takes_a_jpeg_or_webp_saved_under_the_same_name(me):
    pytest.importorskip("numpy")
    pytest.importorskip("PIL")
    from PIL import Image

    from character_sprites import image_format

    tool = providers.get("manual")
    out = me / "raw" / "poses-1-r1.png"
    with pytest.raises(providers.WaitingForImage):
        tool.generate("Draw the poses.", [], out)
    Image.new("RGB", (10, 10), (120, 120, 120)).save(me / "inbox" / "poses-1-r1.jpg")
    assert tool.generate("Draw the poses.", [], out) == out
    assert image_format(out) == "PNG"


def test_the_manual_tools_copies_keep_their_type_and_go_once_the_picture_is_back(me, tmp_path):
    pytest.importorskip("PIL")
    from PIL import Image

    photo = tmp_path / "Holiday.JPG"
    Image.new("RGB", (8, 8), (200, 150, 120)).save(photo, format="JPEG")
    front = tmp_path / "front.png"
    front.write_bytes(_png_bytes())
    tool = providers.get("manual")
    out = me / "raw" / "sheet-r1.png"
    with pytest.raises(providers.WaitingForImage):
        tool.generate("Draw the character sheet.", [photo, front], out)
    inbox = me / "inbox"
    assert sorted(p.name for p in inbox.glob("sheet-r1.attach-*")) == ["sheet-r1.attach-1.jpg", "sheet-r1.attach-2.png"]
    assert (inbox / "sheet-r1.attach-1.jpg").read_bytes() == photo.read_bytes()

    (inbox / "sheet-r1.png").write_bytes(_png_bytes((12, 9)))
    assert tool.generate("Draw the character sheet.", [photo, front], out) == out
    assert not list(inbox.glob("sheet-r1.attach-*")), "the copies were only there to be attached"
    assert photo.is_file() and front.is_file(), "the pictures they were copied from stay"


# --- choosing a tool -----------------------------------------------------------------------------------


def test_no_provider_offers_manual(me, no_tools):
    assert providers.NO_TOOL.rstrip(".") == NO_TOOL_WORDS
    for wanted in (None, "codex", "openai", "gemini"):
        choice = providers.choose(wanted)
        assert choice.name == "manual"
        assert isinstance(choice.provider, providers.ManualProvider)
        assert choice.message == providers.NO_TOOL


def test_choosing_manual_on_purpose_says_nothing(me, no_tools):
    choice = providers.choose("manual")
    assert (choice.name, choice.message) == ("manual", None)


def test_the_chosen_tool_is_used_when_it_is_ready(me, monkeypatch, no_tools):
    monkeypatch.setattr(config, "env", lambda name: FAKE_KEY if name == "GEMINI_API_KEY" else None)
    choice = providers.choose("gemini")
    assert (choice.name, choice.message) == ("gemini", None)
    # with no preference the first ready tool is used
    assert providers.choose(None).name == "gemini"


def test_a_chosen_tool_that_is_not_ready_names_the_one_that_is(me, monkeypatch, no_tools):
    monkeypatch.setattr(config, "env", lambda name: FAKE_KEY if name == "OPENAI_API_KEY" else None)
    choice = providers.choose("gemini")
    assert choice.name == "manual"
    assert "GEMINI_API_KEY" in choice.message and "openai" in choice.message
    assert FAKE_KEY not in choice.message


def test_status_command_prints_the_sentence(me, no_tools, capsys):
    from kit.character import __main__ as cli

    assert cli.main(["status"]) == 0
    assert NO_TOOL_WORDS in capsys.readouterr().out


def test_an_unknown_tool_is_a_plain_error():
    with pytest.raises(providers.ProviderError) as caught:
        providers.get("crayons")
    assert "manual, openai, gemini, codex" in str(caught.value)


# --- openai ----------------------------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status: int, body: dict):
        self.status_code = status
        self._body = body
        self.text = json.dumps(body)

    def json(self):
        return self._body


def test_openai_sends_the_reference_images_and_reads_the_picture(me, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "env", lambda name: FAKE_KEY if name == "OPENAI_API_KEY" else None)
    picture = _png_bytes((16, 9))
    sent = {}

    def fake_post(url, **kwargs):
        sent.update(url=url, **kwargs)
        return FakeResponse(200, {"data": [{"b64_json": base64.b64encode(picture).decode()}]})

    monkeypatch.setattr(providers, "_http_post", fake_post)
    ref = tmp_path / "ref.png"
    ref.write_bytes(_png_bytes())
    out = tmp_path / "out.png"

    assert providers.get("openai").generate("Draw it.", [ref, ref], out) == out

    assert sent["url"] == "https://api.openai.com/v1/images/edits"
    assert sent["headers"]["Authorization"] == f"Bearer {FAKE_KEY}"
    assert sent["data"]["model"] == providers.OPENAI_MODEL
    assert sent["data"]["prompt"] == "Draw it."
    assert [name for name, _ in sent["files"]] == ["image[]", "image[]"]
    from character_sprites import image_size

    assert image_size(out) == (16, 9)


def test_openai_with_no_reference_uses_the_generation_endpoint(me, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "env", lambda name: FAKE_KEY)
    sent = {}

    def fake_post(url, **kwargs):
        sent.update(url=url, **kwargs)
        return FakeResponse(200, {"data": [{"b64_json": base64.b64encode(_png_bytes()).decode()}]})

    monkeypatch.setattr(providers, "_http_post", fake_post)
    providers.get("openai").generate("Draw it.", [], tmp_path / "out.png")
    assert sent["url"] == "https://api.openai.com/v1/images/generations"
    assert sent["json"]["model"] == providers.OPENAI_MODEL and sent["json"]["prompt"] == "Draw it."


def test_an_openai_error_is_a_plain_sentence_without_the_key(me, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "env", lambda name: FAKE_KEY)
    monkeypatch.setattr(
        providers, "_http_post", lambda url, **kw: FakeResponse(400, {"error": {"message": "Your prompt was rejected."}})
    )
    with pytest.raises(providers.ProviderError) as caught:
        providers.get("openai").generate("Draw it.", [], tmp_path / "out.png")
    assert "Your prompt was rejected." in str(caught.value)
    assert FAKE_KEY not in str(caught.value)


@pytest.mark.parametrize("code", [401, 403])
def test_a_refused_key_is_reported_without_the_services_words(me, tmp_path, monkeypatch, code):
    """The service's own message for a bad key can repeat part of the key, so it is left out."""
    monkeypatch.setattr(config, "env", lambda name: FAKE_KEY)
    echo = {"error": {"message": f"Incorrect API key provided: {FAKE_KEY[:6]}****"}}
    monkeypatch.setattr(providers, "_http_post", lambda url, **kw: FakeResponse(code, echo))
    with pytest.raises(providers.ProviderError) as caught:
        providers.get("openai").generate("Draw it.", [], tmp_path / "out.png")
    message = str(caught.value)
    assert "refused the key in .env" in message
    assert FAKE_KEY[:6] not in message and "Incorrect" not in message


def test_openai_without_a_key_says_which_setting_is_missing(me, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "env", lambda name: None)
    with pytest.raises(providers.ProviderError) as caught:
        providers.get("openai").generate("Draw it.", [], tmp_path / "out.png")
    assert "OPENAI_API_KEY" in str(caught.value) and ".env" in str(caught.value)


# --- gemini ----------------------------------------------------------------------------------------------


def test_gemini_sends_text_and_images_and_finds_the_picture_in_the_reply(me, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "env", lambda name: FAKE_KEY if name == "GEMINI_API_KEY" else None)
    picture = _png_bytes((20, 10))
    sent = {}

    def fake_post(url, **kwargs):
        sent.update(url=url, **kwargs)
        reply = {"steps": [{"type": "thought", "content": []},
                           {"type": "model_output", "content": [{"type": "text", "text": "Here it is."},
                                                                {"type": "image", "mime_type": "image/png",
                                                                 "data": base64.b64encode(picture).decode()}]}]}  # fmt: skip
        return FakeResponse(200, reply)

    monkeypatch.setattr(providers, "_http_post", fake_post)
    ref = tmp_path / "ref.png"
    ref.write_bytes(_png_bytes())
    out = tmp_path / "out.png"

    providers.get("gemini").generate("Draw it.", [ref], out)

    assert sent["url"] == "https://generativelanguage.googleapis.com/v1beta/interactions"
    assert sent["headers"]["x-goog-api-key"] == FAKE_KEY
    body = sent["json"]
    assert body["model"] == providers.GEMINI_MODEL
    assert body["input"][0] == {"type": "text", "text": "Draw it."}
    assert body["input"][1]["type"] == "image" and body["input"][1]["mime_type"] == "image/png"
    assert body["response_format"]["type"] == "image"
    from character_sprites import image_size

    assert image_size(out) == (20, 10)


def test_gemini_also_reads_the_older_reply_shape(me, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "env", lambda name: FAKE_KEY)
    picture = base64.b64encode(_png_bytes((7, 7))).decode()
    reply = {"candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "image/png", "data": picture}}]}}]}
    monkeypatch.setattr(providers, "_http_post", lambda url, **kw: FakeResponse(200, reply))
    out = tmp_path / "out.png"
    providers.get("gemini").generate("Draw it.", [], out)
    assert out.exists()


def test_gemini_with_no_picture_in_the_reply_is_a_plain_error(me, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "env", lambda name: FAKE_KEY)
    monkeypatch.setattr(providers, "_http_post", lambda url, **kw: FakeResponse(200, {"steps": []}))
    with pytest.raises(providers.ProviderError) as caught:
        providers.get("gemini").generate("Draw it.", [], tmp_path / "out.png")
    assert "no picture" in str(caught.value).lower()


# --- codex -----------------------------------------------------------------------------------------------


def test_codex_runs_one_exec_with_the_references_and_copies_the_picture(me, tmp_path, monkeypatch):
    made = tmp_path / "codex-home" / "generated_images" / "session" / "image.png"
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen.update(kwargs)
        made.parent.mkdir(parents=True, exist_ok=True)
        made.write_bytes(_png_bytes((30, 20)))  # the image tool saves its picture during the run
        last = Path(cmd[cmd.index("--output-last-message") + 1])
        last.write_text(f"{made}\n")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(providers.subprocess, "run", fake_run)
    monkeypatch.setattr(providers.shutil, "which", lambda name: "/usr/local/bin/codex")
    ref = tmp_path / "ref.png"
    ref.write_bytes(_png_bytes())
    out = tmp_path / "out.png"

    assert providers.get("codex").generate("Draw it.", [ref], out) == out

    cmd = seen["cmd"]
    assert cmd[:2] == ["codex", "exec"]
    assert "--enable" in cmd and cmd[cmd.index("--enable") + 1] == "image_generation"
    assert cmd[cmd.index("--sandbox") + 1] == "read-only"
    assert ["-i", str(ref.resolve())] == cmd[cmd.index("-i") : cmd.index("-i") + 2]
    assert "Draw it." in seen["input"]
    from character_sprites import image_size

    assert image_size(out) == (30, 20)


def test_codex_pins_its_model_unless_the_creator_names_another(me, tmp_path, monkeypatch):
    seen = []

    def fake_run(cmd, **kwargs):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="stopped")

    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "empty-home"))
    monkeypatch.setattr(providers.subprocess, "run", fake_run)
    monkeypatch.setattr(providers.shutil, "which", lambda name: "/usr/local/bin/codex")
    for setting, expected in ((None, providers.CODEX_MODEL), ("another-model", "another-model"), ("default", None)):
        monkeypatch.setattr(config, "env", lambda name, setting=setting: setting if name == "CODEX_IMAGE_MODEL" else None)
        with pytest.raises(providers.ProviderError):
            providers.get("codex").generate("Draw it.", [], tmp_path / "out.png")
        cmd = seen[-1]
        assert (cmd[cmd.index("--model") + 1] if "--model" in cmd else None) == expected


@pytest.mark.parametrize("where", ["a reference image", "a picture outside generated_images", "an old picture"])
def test_codex_only_takes_a_picture_it_saved_during_the_run(me, tmp_path, monkeypatch, where):
    home = tmp_path / "codex-home"
    monkeypatch.setenv("CODEX_HOME", str(home))
    ref = home / "generated_images" / "refs" / "ref.png" if where == "a reference image" else tmp_path / "ref.png"
    ref.parent.mkdir(parents=True, exist_ok=True)
    ref.write_bytes(_png_bytes())
    old = home / "generated_images" / "earlier" / "old.png"
    old.parent.mkdir(parents=True, exist_ok=True)
    old.write_bytes(_png_bytes())
    os.utime(old, (1_000_000_000, 1_000_000_000))
    elsewhere = tmp_path / "elsewhere.png"

    def fake_run(cmd, **kwargs):
        if where == "a reference image":
            os.utime(ref)  # even touched during the run, a reference is never the result
            named = ref
        elif where == "a picture outside generated_images":
            elsewhere.write_bytes(_png_bytes())
            named = elsewhere
        else:
            named = old
        Path(cmd[cmd.index("--output-last-message") + 1]).write_text(f"{named}\n")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="done")

    monkeypatch.setattr(providers.subprocess, "run", fake_run)
    monkeypatch.setattr(providers.shutil, "which", lambda name: "/usr/local/bin/codex")
    with pytest.raises(providers.ProviderError) as caught:
        providers.get("codex").generate("Draw it.", [ref], tmp_path / "out.png")
    assert "did not make a picture" in str(caught.value)
    assert not (tmp_path / "out.png").exists()


def test_codex_that_makes_no_picture_is_a_plain_error(me, tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "empty-home"))
    monkeypatch.setattr(
        providers.subprocess,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout="", stderr="error: usage limit reached"),
    )
    monkeypatch.setattr(providers.shutil, "which", lambda name: "/usr/local/bin/codex")
    with pytest.raises(providers.ProviderError) as caught:
        providers.get("codex").generate("Draw it.", [], tmp_path / "out.png")
    assert "usage limit reached" in str(caught.value)


def test_codex_counts_as_ready_only_when_signed_in(monkeypatch):
    monkeypatch.setattr(providers.shutil, "which", lambda name: "/usr/local/bin/codex")
    for code, ready in ((0, True), (1, False)):
        providers._codex_ready.cache_clear()
        monkeypatch.setattr(providers.subprocess, "run", lambda cmd, code=code, **kw: subprocess.CompletedProcess(cmd, code))
        assert providers.ready("codex")[0] is ready
    providers._codex_ready.cache_clear()
