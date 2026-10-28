"""beats.py: the narration's words into headline pages, one per spoken phrase.

Each page is {"t", "text", "pose", "field", "prop"}: when it lands, the keyword headline (four words at most),
the character's pose, the field it sits on (dark and light take turns) and what arrives with it.
"""

from __future__ import annotations

import json
import re

import pytest
from mini_me_helpers import load

from kit import config
from kit.transcribe import Word

beats = load("beats")

CORE = {"idle", "talking", "point_left", "point_right", "point_up", "thumbs_up", "shrug", "thinking", "mind_blown",
        "celebrate", "hold_card", "typing", "facepalm", "wave"}
NARRATION = (
    "Most basil on a windowsill dies in week three. Here's why. You water it at night, so the roots rot. "
    "Is that you? Water it in the morning instead. 3 of my 5 plants made it. Follow for more balcony tips."
)


def timed(text: str, start: float = 0.12, step: float = 0.31, pause: float = 0.34) -> list[Word]:
    """Words spoken at a steady pace, with a longer pause after each sentence."""
    out, t = [], start
    for token in text.split():
        out.append(Word(token, round(t, 3), round(t + step - 0.05, 3)))
        t += step + (pause if token[-1] in ".?!" else 0.0)
    return out


def bare(word: str) -> str:
    return re.sub(r"[^a-z0-9'%$]", "", word.lower())


def page_words(page: dict) -> list[str]:
    return [bare(w) for w in page["text"].replace("*", "").split()]


@pytest.fixture
def words():
    return timed(NARRATION)


def test_every_phrase_has_a_page(words):
    built = beats.build(words, cta="follow")
    groups = beats.phrases(words, cta="follow")
    pages = built["pages"]
    assert len(pages) == len(groups) >= 8
    assert [i for group in groups for i in group] == list(range(len(words))), "phrases cover every word, in order"
    for page, group in zip(pages, groups):
        shown = page_words(page)
        assert 1 <= len(shown) <= 4, page
        spoken = [bare(words[i].text) for i in group]
        if page["prop"] != "cta":
            assert all(w in spoken for w in shown), (page, spoken)
        assert page["text"] == page["text"].upper()
    assert pages[0]["t"] == 0.0, "the first page is on screen at frame 0"
    for page, group in list(zip(pages, groups))[1:]:
        assert page["t"] == words[group[0]].start


def test_fields_alternate(words):
    pages = beats.build(words, cta="follow")["pages"]
    fields = [p["field"] for p in pages]
    assert fields[0] == "dark" and set(fields) == {"dark", "light"}
    assert all(a != b for a, b in zip(fields, fields[1:]))


def test_pages_have_exactly_the_five_keys(words):
    built = beats.build(words, cta="follow")
    assert all(set(page) == {"t", "text", "pose", "field", "prop"} for page in built["pages"])
    assert built["duration"] > words[-1].end
    times = [p["t"] for p in built["pages"]]
    assert times == sorted(times) and len(set(times)) == len(times)


def test_a_new_pose_every_page_from_the_core_set(words):
    pages = beats.build(words, cta="follow")["pages"]
    assert all(p["pose"] in CORE for p in pages)
    assert all(a["pose"] != b["pose"] for a, b in zip(pages, pages[1:]))


def test_poses_and_props_follow_the_meaning(words):
    pages = beats.build(words, cta="follow")["pages"]
    by_text = {p["text"]: p for p in pages}
    assert pages[0]["pose"] == "point_up", "the hook points up at its headline"
    question = next(p for p in pages if "YOU" in p["text"] and "THAT" in p["text"])
    assert question["pose"] == "thinking"
    rot = next(p for p in pages if "ROT" in p["text"].split())
    assert rot["pose"] == "facepalm"
    counted = next(p for p in pages if "3" in p["text"])
    assert counted["prop"] != "count", "a number under 10 shows whole: counting up to 3 would show 1 and 2 first"
    made = next(p for p in pages if "MADE" in p["text"])
    assert made["pose"] in {"thumbs_up", "celebrate"}
    assert len(by_text) == len(pages), "no two pages share a headline"
    for page in pages:
        assert beats.prop_kind(page["prop"]) in beats.PROPS


def test_the_closing_line_is_one_cta_page(words):
    pages = beats.build(words, cta="follow")["pages"]
    last = pages[-1]
    assert last["prop"] == "cta" and last["text"] == "FOLLOW" and last["pose"] == beats.CLOSE_POSE == "idle"
    assert pages[-2]["pose"] != "idle", "a new pose every page, the close included"
    follow = next(w for w in words if w.text.startswith("Follow"))
    assert last["t"] == follow.start
    assert sum(1 for p in pages if p["prop"] == "cta") == 1


def test_a_comment_cta_shows_the_keyword():
    words = timed("Fresh basil in March. It took ten minutes. Comment GROW and I'll send you the checklist.")
    pages = beats.build(words, cta="comment", keyword="GROW")["pages"]
    assert pages[-1]["text"] == "COMMENT GROW" and pages[-1]["prop"] == "cta"
    comment = next(w for w in words if w.text == "Comment")
    assert pages[-1]["t"] == comment.start


def test_the_profile_gives_the_call_to_action(kit_home, example_me):
    words = timed("Fresh basil in March. Comment GROW and I'll send you the checklist.")
    pages = beats.build(words)["pages"]  # me.example asks for a comment with the keyword GROW
    assert pages[-1]["text"] == "COMMENT GROW"


def test_no_closing_line_means_no_cta_page():
    words = timed("Fresh basil in March. It took ten minutes.")
    pages = beats.build(words, cta="follow")["pages"]
    assert all(p["prop"] != "cta" for p in pages)


def test_long_phrases_split_and_short_words_join():
    words = timed("So, most basil on a windowsill dies in week three because the light drops off fast.")
    groups = beats.phrases(words, cta="follow")
    pages = beats.build(words, cta="follow")["pages"]
    assert all(len(page_words(p)) <= 4 for p in pages)
    assert len(groups[0]) > 1, "a lone 'So,' joins the next phrase instead of flashing on its own"
    starts = [p["t"] for p in pages] + [words[-1].end]
    assert min(b - a for a, b in zip(starts, starts[1:])) >= 0.45


def test_a_pause_starts_a_new_phrase():
    words = [Word("Fresh", 0.1, 0.4), Word("basil", 0.45, 0.8), Word("today", 1.4, 1.8), Word("friends", 1.85, 2.2)]
    assert beats.phrases(words, cta="follow") == [[0, 1], [2, 3]]


def test_the_cli_writes_beats_beside_the_words(kit_home, tmp_path, capsys):
    folder = config.work_dir("example-reel")
    folder.mkdir(parents=True)
    words = timed(NARRATION)
    (folder / "words.json").write_text(json.dumps({"dur": words[-1].end + 0.3,
                                                   "words": [{"w": w.text, "s": w.start, "e": w.end} for w in words]}))
    assert beats.main([str(folder / "words.json"), "--cta", "follow"]) == 0
    written = json.loads((folder / "beats.json").read_text())
    assert written["pages"] and written["duration"] == pytest.approx(words[-1].end + 0.3)
    assert beats.main(["example-reel", "--cta", "follow"]) == 0, "a video name works too"
    out = capsys.readouterr().out
    assert "FOLLOW" in out and "beats.json" in out


def test_check_names_each_mistake():
    pages = [
        {"t": 0.0, "text": "FIVE WORDS ARE TOO MANY", "pose": "point_up", "field": "dark", "prop": "none"},
        {"t": 1.0, "text": "SAME FIELD", "pose": "moonwalk", "field": "dark", "prop": "confetti"},
        {"t": 0.5, "text": "BACKWARDS", "pose": "moonwalk", "field": "light", "prop": "card"},
    ]
    problems = "\n".join(beats.check({"duration": 3.0, "pages": pages}))
    for words in ("more than 4 words", "moonwalk", "same field", "confetti", "before the page", "card"):
        assert words in problems, (words, problems)
    clean = beats.build(timed(NARRATION), cta="follow")
    assert [p for p in beats.check(clean) if not p.startswith("note:")] == []


def test_accent_marks_and_card_content_pass_the_check():
    good = {"duration": 3.0, "pages": [
        {"t": 0.0, "text": "BASIL *DIES*", "pose": "point_up", "field": "dark", "prop": "card: A wilting pot"},
        {"t": 1.2, "text": "WEEK THREE", "pose": "facepalm", "field": "light", "prop": "stamp: DAY 21"},
    ]}
    assert beats.check(good) == []


def test_without_punctuation_the_pauses_mark_the_sentences():
    """A speech model sometimes writes no punctuation: the pauses then do the job of the full stops."""
    words, t = [], 0.1
    for sentence in ("three of my five plants made it", "comment grow and i'll send you the checklist"):
        for token in sentence.split():
            words.append(Word(token, round(t, 3), round(t + 0.25, 3)))
            t += 0.29
        t += 0.3
    groups = beats.phrases(words, cta="comment", keyword="GROW")
    said = [[words[i].text for i in group] for group in groups]
    assert said[-1][0] == "comment", said
    assert all("comment" not in group for group in said[:-1])


def test_running_it_again_keeps_an_edited_beats_json(kit_home, capsys):
    folder = config.work_dir("example-reel")
    folder.mkdir(parents=True)
    words = timed(NARRATION)
    (folder / "words.json").write_text(json.dumps({"words": [{"w": w.text, "s": w.start, "e": w.end} for w in words]}))
    assert beats.main(["example-reel", "--cta", "follow"]) == 0
    assert not (folder / "beats.backup.json").exists(), "an unedited draft needs no backup"
    edited = json.loads((folder / "beats.json").read_text())
    edited["pages"][0]["text"] = "NOT *THIRSTY*"
    (folder / "beats.json").write_text(json.dumps(edited))
    assert beats.main(["example-reel", "--cta", "follow"]) == 0
    assert json.loads((folder / "beats.backup.json").read_text())["pages"][0]["text"] == "NOT *THIRSTY*"
    (folder / "beats.json").write_text(json.dumps(edited))
    assert beats.main(["example-reel", "--cta", "follow"]) == 0
    assert (folder / "beats.backup-2.json").is_file(), "an earlier backup is never overwritten"
    assert "beats.backup-2.json" in capsys.readouterr().out


def test_only_numbers_of_10_or_more_are_drafted_to_count():
    pages = beats.build(timed("Pick 3 of 25 leaves. Basil roots in 21 days. It took 40 days."), cta="follow")["pages"]
    by_number = {re.search(r"\d+", p["text"]).group(): p for p in pages if re.search(r"\d", p["text"])}
    assert by_number["21"]["prop"] == "count" and by_number["40"]["prop"] == "count"
    assert by_number["3"]["prop"] != "count", "the counted number is the first one, 3, which shows whole"


def test_the_first_page_never_counts_so_frame_0_reads_right():
    pages = beats.build(timed("Basil roots in 21 days. Here is why."), cta="follow")["pages"]
    assert "21" in pages[0]["text"] and pages[0]["prop"] != "count"


def test_the_counted_number_is_the_accented_one_else_the_first():
    assert beats.counted_number("3 OF *25*") == 25
    assert beats.counted_number("3 OF 25") == 3
    assert beats.counted_number("$1,200 *SAVED*") == 1200
    assert beats.counted_number("50% OFF") == 50
    assert beats.counted_number("NO NUMBER") is None


def test_check_notes_a_count_under_10():
    pages = [{"t": 0.0, "text": "HELLO", "pose": "wave", "field": "dark", "prop": "none"},
             {"t": 1.0, "text": "3 OF 5", "pose": "point_up", "field": "light", "prop": "count"},
             {"t": 2.0, "text": "21 DAYS", "pose": "thumbs_up", "field": "dark", "prop": "count"}]
    problems = beats.check({"duration": 3.0, "pages": pages})
    assert problems == ["note: page 2 counts 3, which is under 10, so it shows whole instead."]
