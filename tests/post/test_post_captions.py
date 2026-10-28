"""kit.post.captions: the rules a caption has to follow before it can be scheduled.

The example creator in me.example/ answers comments on Instagram and Facebook, so those two end on a
comment call to action and every other platform ends on a follow line.
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from kit.config import PLATFORMS, Cta, Hashtags
from kit.post import captions, payloads
from kit.post.errors import PostError

EM_DASH, EN_DASH = chr(0x2014), chr(0x2013)  # written as code points so this file holds no dash

CAPS = {"instagram": 5, "tiktok": 5, "youtube": 8, "facebook": 5, "twitter": 2, "linkedin": 0}


def tags(count: int) -> list[str]:
    return [f"#tag{letter}" for letter in "abcdefghijklmnop"[:count]]


def problems_for(platform: str, captions_set: dict, profile) -> list[str]:
    return captions.check(captions_set, profile, platforms=[platform])


# --- a good set ---------------------------------------------------------------------------------------


def test_a_good_set_has_no_problems(captions_set, profile):
    assert captions.check(captions_set, profile) == []


def test_the_example_set_follows_the_creators_own_lines(captions_set, profile):
    for platform in PLATFORMS:
        last = captions_set[platform]["body"].splitlines()[-1]
        assert last == captions.cta_line(profile, platform)


# --- hashtags ----------------------------------------------------------------------------------------------


def test_hashtag_caps(captions_set, profile):
    assert captions.HASHTAG_CAPS == CAPS
    for platform, cap in CAPS.items():
        captions_set[platform]["hashtags"] = tags(cap)
        assert problems_for(platform, captions_set, profile) == [], platform
        captions_set[platform]["hashtags"] = tags(cap + 1)
        found = problems_for(platform, captions_set, profile)
        assert len(found) == 1 and captions.display_name(platform) in found[0], (platform, found)


def test_linkedin_takes_no_hashtags_at_all(captions_set, profile):
    captions_set["linkedin"]["hashtags"] = ["#basil"]
    (found,) = problems_for("linkedin", captions_set, profile)
    assert "LinkedIn" in found and "no hashtags" in found


@pytest.mark.parametrize("tag", ["#shorts", "#Shorts", "#fyp", "#FYP", "#foryou", "#ForYou"])
def test_the_never_list_is_refused_on_every_platform(captions_set, profile, tag):
    assert captions.NEVER_TAGS == ("#shorts", "#fyp", "#foryou")
    for platform in ("instagram", "tiktok", "youtube", "facebook", "twitter"):
        captions_set[platform]["hashtags"] = ["#basil", tag]
        found = problems_for(platform, captions_set, profile)
        assert len(found) == 1 and tag in found[0] and "never list" in found[0], (platform, found)


@pytest.mark.parametrize("bad", ["basil", "#two words", "#", "#12", "##double", 7, None, ""])
def test_a_hashtag_has_to_look_like_one(captions_set, profile, bad):
    captions_set["instagram"]["hashtags"] = ["#basil", bad]
    found = problems_for("instagram", captions_set, profile)
    assert len(found) == 1 and "hashtag" in found[0]


def test_the_same_tag_twice_is_a_problem_whatever_its_case(captions_set, profile):
    captions_set["instagram"]["hashtags"] = ["#Basil", "#basil"]
    (found,) = problems_for("instagram", captions_set, profile)
    assert "twice" in found


def test_tags_belong_in_the_list_not_in_the_text(captions_set, profile):
    captions_set["tiktok"]["body"] = "Water the #base, never the leaves.\n\nFollow @examplestudio for more."
    (found,) = problems_for("tiktok", captions_set, profile)
    assert "#base" in found and "hashtags list" in found


def test_a_number_sign_that_is_not_a_tag_is_left_alone(captions_set, profile):
    captions_set["tiktok"]["body"] = "Pot #4 outlived pot #2 (see page#3).\n\nFollow @examplestudio for more."
    assert problems_for("tiktok", captions_set, profile) == []


def test_a_youtube_title_may_not_carry_a_hashtag_either(captions_set, profile):
    captions_set["youtube"]["title"] = "Basil in week three #windowsill"
    (found,) = problems_for("youtube", captions_set, profile)
    assert "title has a hashtag (#windowsill)" in found and "hashtags list" in found
    captions_set["youtube"]["title"] = "The #1 reason basil dies in week three"  # a number sign before a number is not a tag
    assert problems_for("youtube", captions_set, profile) == []


def test_hashtags_must_be_a_list(captions_set, profile):
    captions_set["instagram"]["hashtags"] = "#basil #mint"
    (found,) = problems_for("instagram", captions_set, profile)
    assert "list" in found


# --- choosing the tags --------------------------------------------------------------------------------------


def test_the_tags_are_the_base_first_then_the_videos_own(profile):
    assert profile.hashtags.base == ("#balconygarden", "#growyourown", "#smallspaces")
    video = ["#basil", "#windowsill", "#herbs"]
    assert captions.select_hashtags(profile, "instagram", video) == [
        "#balconygarden", "#growyourown", "#smallspaces", "#basil", "#windowsill",
    ]
    assert captions.select_hashtags(profile, "youtube", video) == [
        "#balconygarden", "#growyourown", "#smallspaces", "#basil", "#windowsill", "#herbs",
    ]
    assert captions.select_hashtags(profile, "twitter", video) == ["#balconygarden", "#basil"]
    assert captions.select_hashtags(profile, "linkedin", video) == []


def test_the_rest_of_the_base_fills_the_room_the_videos_tags_leave(profile):
    assert captions.select_hashtags(profile, "twitter", []) == ["#balconygarden", "#growyourown"]
    assert captions.select_hashtags(profile, "instagram", ["#basil"]) == [
        "#balconygarden", "#growyourown", "#smallspaces", "#basil",
    ]
    wide = dataclasses.replace(profile, hashtags=Hashtags(base=tuple(f"#base{n}" for n in range(1, 8))))
    assert captions.select_hashtags(wide, "instagram", ["#basil", "#herbs"]) == [
        "#base1", "#base2", "#base3", "#basil", "#herbs",
    ]
    assert captions.select_hashtags(wide, "youtube", ["#basil", "#herbs"]) == [
        "#base1", "#base2", "#base3", "#base4", "#base5", "#basil", "#herbs", "#base6",
    ]


def test_selection_drops_repeats_and_the_never_list_and_respects_every_cap(profile):
    video = ["#Basil", "#basil", "#shorts", "#FYP", "#herbs", "#balconygarden"]
    for platform, cap in CAPS.items():
        picked = captions.select_hashtags(profile, platform, video)
        assert len(picked) <= cap
        assert len({t.casefold() for t in picked}) == len(picked)
        assert not {t.casefold() for t in picked} & set(captions.NEVER_TAGS)
    assert captions.select_hashtags(profile, "tiktok", video) == [
        "#balconygarden", "#growyourown", "#smallspaces", "#Basil", "#herbs",
    ]


def test_a_video_tag_has_to_be_a_hashtag(profile):
    with pytest.raises(ValueError, match="not a hashtag"):
        captions.select_hashtags(profile, "instagram", ["two words"])


# --- the call to action ----------------------------------------------------------------------------------------


def test_cta_line(captions_set, profile):
    # the lines themselves
    assert captions.cta_line(profile, "instagram") == 'Comment "GROW" for the balcony planting checklist.'
    assert captions.cta_line(profile, "facebook") == 'Comment "GROW" for the balcony planting checklist.'
    assert captions.cta_line(profile, "tiktok") == "Follow @examplestudio for more."
    assert captions.cta_line(profile, "youtube") == "Follow @examplestudio for more."
    assert captions.cta_line(profile, "twitter") == "Follow @examplestudio for more."
    assert captions.cta_line(profile, "linkedin") == "Follow Example Studio for more."  # its handle has no @

    # the keyword is allowed only where the creator answers comments
    for platform in PLATFORMS:
        assert ("GROW" in captions.cta_line(profile, platform)) is (platform in ("instagram", "facebook"))

    # a keyword on a platform that does not answer it is refused
    for platform in ("tiktok", "youtube", "twitter"):
        captions_set[platform]["body"] = 'Water the base.\n\nComment "GROW" for the balcony planting checklist.'
        found = problems_for(platform, captions_set, profile)
        assert any("GROW" in line and "nothing answers" in line for line in found), (platform, found)

    # and every other platform has to end on the follow line
    for platform in ("tiktok", "youtube", "twitter", "linkedin"):
        captions_set[platform]["body"] = "Water the base, never the leaves."
        found = problems_for(platform, captions_set, profile)
        assert len(found) == 1 and "follow line" in found[0], (platform, found)
    for platform in ("instagram", "facebook"):
        captions_set[platform]["body"] = "Water the base, never the leaves.\n\nFollow @examplestudio for more."
        found = problems_for(platform, captions_set, profile)
        assert len(found) == 1 and "comment" in found[0], (platform, found)


def test_a_follow_line_in_the_creators_own_words_is_fine(captions_set, profile):
    captions_set["tiktok"]["body"] = "Water the base.\n\nCome and follow along for the next one."
    assert problems_for("tiktok", captions_set, profile) == []


def test_the_word_comment_without_the_keyword_is_not_a_keyword_ask(captions_set, profile):
    captions_set["tiktok"]["body"] = "Every comment so far says the same grow tip.\n\nFollow @examplestudio for more."
    assert problems_for("tiktok", captions_set, profile) == []


def test_a_creator_who_only_asks_for_follows_gets_follow_lines_everywhere(captions_set, profile):
    follow_only = dataclasses.replace(profile, cta=Cta(type="follow", keyword=None, payload=None, comment_platforms=()))
    for platform in PLATFORMS:
        assert captions.cta_line(follow_only, platform).startswith("Follow ")
        captions_set[platform]["body"] = captions_set[platform]["body"].replace(
            'Comment "GROW" for the balcony planting checklist.', "Follow @examplestudio for more."
        )
    assert captions.check(captions_set, follow_only) == []


def test_a_comment_cta_without_a_payload_still_reads_sensibly(profile):
    bare = dataclasses.replace(
        profile, cta=Cta(type="comment", keyword="GROW", payload=None, comment_platforms=("instagram",))
    )
    assert captions.cta_line(bare, "instagram") == 'Comment "GROW" and I will send it to you.'


def test_linkedin_never_gets_a_keyword_line_and_a_profile_that_lists_it_is_reported_once(captions_set, profile):
    odd = dataclasses.replace(
        profile, cta=dataclasses.replace(profile.cta, comment_platforms=("instagram", "facebook", "linkedin"))
    )
    assert captions.cta_line(odd, "linkedin") == "Follow Example Studio for more."
    found = captions.check(captions_set, odd)
    assert found == [
        "LinkedIn always gets a follow line, because comment prompts cut its reach. "
        "Take linkedin out of cta.comment_platforms in me/profile.json."
    ]
    # it is a problem with the profile, so it shows only when LinkedIn is among the platforms being checked
    assert captions.check(captions_set, odd, platforms=["instagram", "facebook", "tiktok"]) == []
    assert captions.check(captions_set, profile) == []  # and not for a profile that does not list it


# --- LinkedIn --------------------------------------------------------------------------------------------------------


def linkedin(body: str) -> dict:
    return {"title": None, "body": body, "hashtags": []}


def linkedin_problems(body: str, captions_set: dict, profile) -> list[str]:
    captions_set["linkedin"] = linkedin(body)
    return problems_for("linkedin", captions_set, profile)


FOLLOW = "\n\nFollow Example Studio for more."


def test_linkedin_rules(captions_set, profile):
    clean = "Windowsill basil dies in week three because the leaves stay wet." + FOLLOW
    assert linkedin_problems(clean, captions_set, profile) == []

    # no hashtags, in the list or in the text
    captions_set["linkedin"] = linkedin(clean) | {"hashtags": ["#basil"]}
    assert any("no hashtags" in line for line in problems_for("linkedin", captions_set, profile))
    found = linkedin_problems("Basil dies in week three, so try #basil care." + FOLLOW, captions_set, profile)
    assert any("#basil" in line for line in found)

    # no links, however they are written
    for link in ("https://example.com/guide", "http://example.org", "www.example.com", "example.com/guide", "example.ai"):
        found = linkedin_problems(f"Basil dies in week three. Read more at {link}" + FOLLOW, captions_set, profile)
        assert any("link" in line and link.rstrip(".") in line for line in found), (link, found)
    assert linkedin_problems("Basil dies in week three, e.g. when kept wet." + FOLLOW, captions_set, profile) == []

    # none of the phrases LinkedIn holds posts back for
    bait = [
        'Comment "GROW" and I will send you the checklist.',
        "Comment GROW for the checklist.",
        "Comment below with your setup.",
        "Drop a word below if you agree.",
        "Drop a fire emoji in the comments.",
        "Like for part 2.",
        "Like this post for part 2 on soil.",
        "Tag someone who overwaters.",
        "Tag a friend who forgets to water.",
        "Link in bio for the checklist.",
        "Link in my bio for the checklist.",
        'Type "YES" below and I will send it.',
    ]
    for phrase in bait:
        found = linkedin_problems(f"Basil dies in week three. {phrase}" + FOLLOW, captions_set, profile)
        assert any("engagement bait" in line for line in found), (phrase, found)

    # a real question is welcome, and so is the follow line
    asking = "Basil dies in week three because the leaves stay wet.\n\nWhat is the first herb you lose each year?" + FOLLOW
    assert linkedin_problems(asking, captions_set, profile) == []


@pytest.mark.parametrize(
    "link",
    [
        "https://example.com/guide", "http://example.org", "ftp://files.example.net/a", "www.example.com",
        "example.com/guide", "example.ai", "linktr.ee/mine", "youtu.be/abc123", "example.me", "bit.ly/3xyz",
        "t.co/abc", "Example.COM", "my.site.co.uk/page", "claude.ai/new", "docs.example.dev", "example.xyz",
    ],
)
def test_anything_shaped_like_a_web_address_is_a_link_on_linkedin(captions_set, profile, link):
    found = linkedin_problems(f"Basil dies in week three. Read more at {link}." + FOLLOW, captions_set, profile)
    assert len(found) == 1 and "has a link" in found[0] and link in found[0], found


@pytest.mark.parametrize(
    "text",
    [
        "Node.js", "Next.js and Vue.js", "README.md", "install.sh", "e.g. when kept wet", "i.e. like this",
        "at 9 a.m. and 5 p.m.", "version 2.5 and 3.10.1", "the U.S. and the U.K.", "a Ph.D", "week three.Then it dies",
        "St.Louis", "final.mp4", "and so on...", "No.1 tip", "vs. the rest", "gpt-4.1", "a 3.5mm jack", "script.py",
    ],
)
def test_words_that_only_look_a_little_like_an_address_are_not_links(captions_set, profile, text):
    assert linkedin_problems(f"Basil dies in week three. Notes: {text}." + FOLLOW, captions_set, profile) == []


@pytest.mark.parametrize(
    "sentence",
    [
        "The comment section and the DMs were quiet.",
        "Comment threads for small accounts are quiet.",
        "I like it for part of the day.",
        "A type system checks if the code compiles.",
        "Drop the lid on the pot and wait.",
        "Tag the pot with the date you planted it.",
    ],
)
def test_ordinary_sentences_are_not_taken_for_engagement_bait(captions_set, profile, sentence):
    assert linkedin_problems(f"Basil dies in week three. {sentence}" + FOLLOW, captions_set, profile) == []


def test_the_first_150_characters_have_to_carry_the_claim(captions_set, profile):
    long_opening = "Basil on a windowsill dies in week three for one reason, and it is the same reason " + (
        "for almost every herb that sits on a sill " * 3
    )
    assert len(long_opening) > 150
    found = linkedin_problems(long_opening.strip() + "." + FOLLOW, captions_set, profile)
    assert len(found) == 1 and "150" in found[0] and "first sentence" in found[0]

    exact = ("x" * 149) + "."
    assert len(exact) == 150
    assert linkedin_problems(exact + FOLLOW, captions_set, profile) == []

    # the second sentence may be as long as it likes
    assert linkedin_problems("Basil dies in week three. " + "More detail here. " * 30 + FOLLOW, captions_set, profile) == []


@pytest.mark.parametrize(
    "opening",
    ["Hey everyone, basil dies in week three.", "Hi all. Basil dies in week three.", "So, basil dies in week three.",
     "Okay, basil dies in week three.", "I'm excited to share that basil dies in week three.",
     "Just wanted to say basil dies in week three.", "Quick update: basil dies in week three."],
)
def test_a_throat_clearing_opening_is_refused(captions_set, profile, opening):
    found = linkedin_problems(opening + FOLLOW, captions_set, profile)
    assert len(found) == 1 and "opens with" in found[0] and "claim" in found[0], found


@pytest.mark.parametrize("opening", ["High tide ruins basil.", "Solving week three: basil needs dry leaves.", "Okayama's basil dies in week three."])
def test_an_opening_that_only_looks_like_one_is_fine(captions_set, profile, opening):
    assert linkedin_problems(opening + FOLLOW, captions_set, profile) == []


# --- X -------------------------------------------------------------------------------------------------------------------


def test_x_length(captions_set, profile):
    follow = "\n\nFollow @examplestudio for more."
    tag_line = "\n\n#balconygarden #basil"
    room = 280 - len(follow) - len(tag_line)
    captions_set["twitter"]["body"] = "w" * room + follow
    text = payloads.content_for(captions_set["twitter"], "twitter")
    assert len(text) == 280
    assert problems_for("twitter", captions_set, profile) == []

    captions_set["twitter"]["body"] = "w" * (room + 1) + follow
    (found,) = problems_for("twitter", captions_set, profile)
    assert "X" in found and "281" in found and "280" in found

    # the tags count, so dropping them brings the post back under
    captions_set["twitter"]["hashtags"] = []
    assert problems_for("twitter", captions_set, profile) == []


FIRE = chr(0x1F525)
ZWJ, VS16 = chr(0x200D), chr(0xFE0F)
EMOJI_THAT_COUNT_AS_ONE = {
    "a face or object": FIRE,
    "with a skin tone": chr(0x1F44D) + chr(0x1F3FD),
    "a family joined by zero width joiners": chr(0x1F468) + ZWJ + chr(0x1F469) + ZWJ + chr(0x1F467),
    "a flag": chr(0x1F1EC) + chr(0x1F1E7),
    "a heart with the selector": chr(0x2764) + VS16,
    "a check mark with the selector": chr(0x2714) + VS16,
    "a green check box": chr(0x2705),
    "a keycap": "1" + VS16 + chr(0x20E3),
}


@pytest.mark.parametrize("emoji", EMOJI_THAT_COUNT_AS_ONE.values(), ids=EMOJI_THAT_COUNT_AS_ONE.keys())
def test_x_counts_each_emoji_once_as_two(emoji):
    assert captions.x_length(emoji) == 2
    assert captions.x_length("a" + emoji + "b") == 4
    assert captions.x_length(emoji * 3) == 6


def test_x_counts_a_symbol_that_is_not_an_emoji_as_one():
    for plain in (chr(0x2764), chr(0x2605), chr(0x2192), "(c)", "1"):
        assert captions.x_length(plain) == len(plain)


def test_x_counts_a_link_as_23_however_long_it_is():
    long_link = "https://example.com/" + "a" * 250
    assert captions.x_length(long_link) == 23
    assert captions.x_length(f"See {long_link}.") == 4 + 23 + 1  # the full stop that ends the sentence is not the link's
    assert captions.x_length("two: example.com and linktr.ee/mine") == 5 + 23 + 5 + 23
    assert captions.x_length("Node.js is not a link") == len("Node.js is not a link")


def test_x_length_uses_xs_weights_not_the_raw_count(captions_set, profile):
    follow = "\n\nFollow @examplestudio for more."
    tag_line = "\n\n#balconygarden #basil"
    room = 280 - len(follow) - len(tag_line)
    fits = room // 2  # each emoji takes two of the characters left

    captions_set["twitter"]["body"] = FIRE * fits + follow
    text = payloads.content_for(captions_set["twitter"], "twitter")
    assert captions.x_length(text) == 2 * fits + len(follow) + len(tag_line) == 280
    assert problems_for("twitter", captions_set, profile) == []

    captions_set["twitter"]["body"] = FIRE * (fits + 1) + follow
    text = payloads.content_for(captions_set["twitter"], "twitter")
    assert len(text) < 280 < captions.x_length(text)  # few enough characters, too heavy for X
    (found,) = problems_for("twitter", captions_set, profile)
    assert "X" in found and str(captions.x_length(text)) in found and "280" in found and "emoji as 2" in found


def test_a_long_link_does_not_use_up_the_room_on_x(captions_set, profile):
    long_link = "https://example.com/" + "a" * 250
    captions_set["twitter"]["body"] = long_link + "\n\nFollow @examplestudio for more."
    text = payloads.content_for(captions_set["twitter"], "twitter")
    assert len(text) > 280 and captions.x_length(text) < 280
    assert problems_for("twitter", captions_set, profile) == []


def test_other_platforms_have_no_short_limit(captions_set, profile):
    captions_set["facebook"]["body"] = "w" * 1500 + '\n\nComment "GROW" for the balcony planting checklist.'
    assert problems_for("facebook", captions_set, profile) == []


# --- YouTube ---------------------------------------------------------------------------------------------------------------


def test_youtube_needs_a_title_of_at_most_100_characters(captions_set, profile):
    captions_set["youtube"]["title"] = "t" * 100
    assert problems_for("youtube", captions_set, profile) == []
    captions_set["youtube"]["title"] = "t" * 101
    (found,) = problems_for("youtube", captions_set, profile)
    assert "101" in found and "100" in found
    for empty in (None, "", "   "):
        captions_set["youtube"]["title"] = empty
        (found,) = problems_for("youtube", captions_set, profile)
        assert "needs a title" in found


def test_other_platforms_may_leave_the_title_empty(captions_set, profile):
    for platform in PLATFORMS:
        if platform != "youtube":
            assert captions_set[platform]["title"] is None
    assert captions.check(captions_set, profile) == []


# --- everything else a caption can get wrong -------------------------------------------------------------------------------------


@pytest.mark.parametrize("dash", [EM_DASH, EN_DASH])
def test_dashes_are_refused_in_the_body_and_the_title(captions_set, profile, dash):
    captions_set["tiktok"]["body"] = f"Water the base {dash} never the leaves.\n\nFollow @examplestudio for more."
    (found,) = problems_for("tiktok", captions_set, profile)
    assert "dash" in found
    captions_set["youtube"]["title"] = f"Basil {dash} week three"
    (found,) = problems_for("youtube", captions_set, profile)
    assert "dash" in found


@pytest.mark.parametrize("left_over", ["<keyword>", "{payload}", "TODO", "TBD", "FIXME", "Lorem ipsum dolor"])
def test_a_placeholder_left_in_the_text_is_refused(captions_set, profile, left_over):
    captions_set["tiktok"]["body"] = f"Water the base {left_over} never the leaves.\n\nFollow @examplestudio for more."
    (found,) = problems_for("tiktok", captions_set, profile)
    assert "placeholder" in found


def test_a_todo_in_ordinary_words_is_not_a_placeholder(captions_set, profile):
    captions_set["tiktok"]["body"] = "Add it to your todo list.\n\nFollow @examplestudio for more."
    assert problems_for("tiktok", captions_set, profile) == []


def test_the_shape_of_the_file_is_checked(captions_set, profile):
    assert captions.check([], profile) == ["captions.json must hold one entry for each platform."]
    assert captions.check("nope", profile) == ["captions.json must hold one entry for each platform."]

    del captions_set["tiktok"]
    assert captions.check(captions_set, profile) == ["TikTok: there is no caption."]

    captions_set["tiktok"] = "just text"
    (found,) = problems_for("tiktok", captions_set, profile)
    assert "title, a body and hashtags" in found

    captions_set["tiktok"] = {"title": None, "body": "   ", "hashtags": []}
    (found,) = problems_for("tiktok", captions_set, profile)
    assert "body is empty" in found

    captions_set["tiktok"] = {"title": None, "body": 12, "hashtags": []}
    (found,) = problems_for("tiktok", captions_set, profile)
    assert "body is empty" in found


def test_only_the_platforms_asked_about_are_checked(captions_set, profile):
    captions_set["linkedin"]["hashtags"] = ["#basil"]
    captions_set["twitter"]["hashtags"] = tags(3)
    assert len(captions.check(captions_set, profile)) == 2
    assert len(captions.check(captions_set, profile, platforms=["linkedin"])) == 1
    assert captions.check(captions_set, profile, platforms=["instagram", "tiktok"]) == []


def test_problems_come_back_in_platform_order(captions_set, profile):
    captions_set["twitter"]["hashtags"] = tags(3)
    captions_set["instagram"]["hashtags"] = tags(6)
    found = captions.check(captions_set, profile)
    assert [line.split(":")[0] for line in found] == ["Instagram", "X"]


def test_platform_names_read_naturally():
    assert [captions.display_name(p) for p in PLATFORMS] == ["Instagram", "TikTok", "YouTube", "Facebook", "LinkedIn", "X"]


# --- loading the file ----------------------------------------------------------------------------------------------------------------


def test_load_reads_the_captions_file(reel):
    assert captions.load("example-reel")["youtube"]["title"] == "Why windowsill basil dies in week three"


def test_load_says_what_is_wrong_with_the_file(reel):
    (reel.folder / "captions.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(PostError, match="not valid JSON"):
        captions.load("example-reel")
    (reel.folder / "captions.json").write_text("[]", encoding="utf-8")
    with pytest.raises(PostError, match="one entry for each platform"):
        captions.load("example-reel")
    (reel.folder / "captions.json").unlink()
    with pytest.raises(PostError, match="no captions.json"):
        captions.load("example-reel")


def test_load_refuses_an_unsafe_video_name(studio):
    with pytest.raises(ValueError, match="cannot be used as a video name"):
        captions.load("../elsewhere")


# --- the creator's yes ------------------------------------------------------------------------------------------------------------------


def test_nothing_is_approved_until_the_creator_says_so(reel):
    assert "not been approved" in captions.approval_problem("example-reel")


def test_an_approval_holds_until_a_caption_changes(reel):
    path = captions.approve("example-reel")
    assert path == reel.folder / "captions.approved.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    assert set(record) == {"captions_sha256", "approved_at"}
    assert captions.approval_problem("example-reel") is None

    # the same captions written out differently are still the captions they approved
    data = json.loads((reel.folder / "captions.json").read_text(encoding="utf-8"))
    (reel.folder / "captions.json").write_text(json.dumps(dict(reversed(list(data.items()))), indent=4), encoding="utf-8")
    assert captions.approval_problem("example-reel") is None

    data["tiktok"]["body"] += " One more thing."
    (reel.folder / "captions.json").write_text(json.dumps(data), encoding="utf-8")
    assert "changed after" in captions.approval_problem("example-reel")


def test_the_approval_can_be_checked_against_the_set_that_is_about_to_be_sent(reel):
    captions.approve("example-reel")
    on_disk = captions.load("example-reel")
    assert captions.approval_problem("example-reel", on_disk) is None

    about_to_go = json.loads(json.dumps(on_disk))
    about_to_go["tiktok"]["body"] += " Sneaked in."
    assert "changed after" in captions.approval_problem("example-reel", about_to_go)  # the file on disk still matches
    assert captions.approval_problem("example-reel") is None


def test_approving_new_words_replaces_the_old_approval(reel):
    captions.approve("example-reel")
    data = json.loads((reel.folder / "captions.json").read_text(encoding="utf-8"))
    data["tiktok"]["body"] += " One more thing."
    (reel.folder / "captions.json").write_text(json.dumps(data), encoding="utf-8")
    captions.approve("example-reel")
    assert captions.approval_problem("example-reel") is None


def test_a_damaged_approval_file_counts_as_no_approval(reel):
    (reel.folder / "captions.approved.json").write_text("{nope", encoding="utf-8")
    assert "not been approved" in captions.approval_problem("example-reel")
