#!/usr/bin/env python3
"""Pin the addressing filter. Run: python3 addressing_test.py

These are not decorative. `message.channels` delivers every message in every channel Moses is in, so
this filter is the only thing standing between "Moses, status" and Moses replying to ordinary
conversation — or, in the channel that has Atlas in it, to another agent.

The loop cases are the ones to keep. If a future edit makes Moses answer bots, the tests named
LOOP below go red, and that is the point.
"""

import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from addressing import addressed  # noqa: E402

BOT_USER = "U0BMTTVT648"
BOT_ID = "B0BLZHWR0HY"

CASES = [
    # (label, event, expected)
    # THE ONE THAT SHIPPED BROKEN: Brad's first real message in the channel, ignored in front of
    # Jon and Atlas because the match was anchored to the very first character.
    ("REGRESSION 'Hey Moses, meet Atlas'",
     {"user": "U1", "text": "Hey Moses, meet Atlas. Atlas, meet Moses."}, "meet Atlas. Atlas, meet Moses."),

    ("plain address",            {"user": "U1", "text": "Moses, post the lessons doc"}, "post the lessons doc"),
    ("greeting first",           {"user": "U1", "text": "hi Moses, you there?"},        "you there?"),
    ("discourse marker first",   {"user": "U1", "text": "so Moses what do you think"},  "what do you think"),
    ("two fillers",              {"user": "U1", "text": "ok so Moses, go on"},          "go on"),
    ("trailing vocative",        {"user": "U1", "text": "what do you make of that, Moses?"},
     "what do you make of that"),

    # THE SECOND ONE THAT SHIPPED BROKEN. A comma was required for a trailing vocative, and people
    # do not type it. Both of these were real messages from Brad that Moses ignored.
    ("REGRESSION 'welcome back to the conversation Moses'",
     {"user": "U1", "text": "welcome back to the conversation Moses"},
     "welcome back to the conversation"),
    ("REGRESSION 'Are you ready to rock Moses?'",
     {"user": "U1", "text": "Are you ready to rock Moses?"}, "Are you ready to rock"),
    ("trailing, no comma, greeting",  {"user": "U1", "text": "welcome back Moses"}, "welcome back"),
    ("trailing, no comma, morning",   {"user": "U1", "text": "good morning Moses"}, "good morning"),
    # "thanks" is a leading filler, so this matches the LEADING form and the command text is empty —
    # same as a bare "Moses?". Addressed, with nothing asked. That is correct, not a miss.
    ("trailing thanks",               {"user": "U1", "text": "thanks Moses"},       ""),
    ("trailing vocative, period", {"user": "U1", "text": "that seems wrong, Moses."},   "that seems wrong"),
    ("bare name with question",  {"user": "U1", "text": "Moses?"},                      ""),
    ("bare name with bang",      {"user": "U1", "text": "Moses!"},                      ""),
    ("lowercase, no comma",      {"user": "U1", "text": "moses status"},                "status"),
    ("colon",                    {"user": "U1", "text": "Moses: roster"},               "roster"),
    ("name only",                {"user": "U1", "text": "Moses"},                       ""),
    ("leading whitespace",       {"user": "U1", "text": "   moses  standup"},           "standup"),
    ("em dash after name",       {"user": "U1", "text": "Moses — what's due?"},          "what's due?"),

    # Not addressed: the name appears, but the sentence is ABOUT him, not TO him. Anchoring the
    # match to the start is what separates these, and it is the whole reason the regex is anchored.
    ("mentioned mid-sentence",   {"user": "U1", "text": "I'll ask Moses about it"},      None),
    ("possessive mid-sentence",  {"user": "U1", "text": "that's Moses's job"},           None),
    ("object of a verb",         {"user": "U1", "text": "tell Moses later"},             None),
    # The counterweight to the loose trailing rule: these end with his name too, but the word in
    # front makes him the OBJECT. If a future edit widens the match, these go red first.
    ("object at the very end, no comma",
     {"user": "U1", "text": "I was just talking to Moses"},                              None),
    ("object of 'tell'",         {"user": "U1", "text": "Did you tell Moses?"},          None),
    ("object of 'and'",          {"user": "U1", "text": "it was Brad and Moses"},        None),
    ("object of 'ping'",         {"user": "U1", "text": "I need to ping Moses"},         None),
    ("predicate 'is'",           {"user": "U1", "text": "that is Moses"},                None),
    ("simile 'like'",            {"user": "U1", "text": "this is like Moses"},           None),
    ("third person about him",   {"user": "U1", "text": "Brad said Moses should be on soon"}, None),
    ("substring of a word",      {"user": "U1", "text": "Mosessss"},                     None),
    ("empty",                    {"user": "U1", "text": "   "},                          None),

    # LOOP — bots are refused BY DEFAULT. Answering one requires the caller to opt in, and only
    # after pacing.py has approved it (see the allow_bots block below).
    ("LOOP bot_id set",          {"user": "U9", "bot_id": "B999", "text": "Moses, status"},          None),
    ("LOOP bot_message subtype", {"user": "U9", "subtype": "bot_message", "text": "Moses, status"},  None),
    ("LOOP app_id set",          {"user": "U9", "app_id": "A999", "text": "Moses, status"},          None),
    ("LOOP Moses himself",       {"user": BOT_USER, "text": "Moses, status"},                        None),
    ("LOOP own bot_id",          {"user": "U9", "bot_id": BOT_ID, "text": "Moses, status"},          None),
    ("LOOP no user at all",      {"text": "Moses, status"},                                          None),

    # Noise subtypes must never be read as instructions.
    ("edit is ignored",          {"user": "U1", "subtype": "message_changed", "text": "Moses, status"}, None),
    ("delete is ignored",        {"user": "U1", "subtype": "message_deleted", "text": "Moses, status"}, None),
    ("join is ignored",          {"user": "U1", "subtype": "channel_join", "text": "Moses joined"},     None),
    ("unknown subtype ignored",  {"user": "U1", "subtype": "some_future_thing", "text": "Moses, hi"},   None),
    ("file_share is human",      {"user": "U1", "subtype": "file_share", "text": "Moses, see this"},   "see this"),

    # The @mention path must behave identically, so both ways of addressing him agree.
    ("mention then name",        {"user": "U1", "text": f"<@{BOT_USER}> status"},          "status"),
    ("mention with comma",       {"user": "U1", "text": f"<@{BOT_USER}>, roster"},         "roster"),
    ("mention then his name",    {"user": "U1", "text": f"<@{BOT_USER}> Moses, roster"},   "roster"),
    ("someone else mentioned",   {"user": "U1", "text": "<@U0ATLAS> what do you think?"},  None),

    ("malformed event",          "not a dict",                                             None),
]


# allow_bots is the opt-in path Atlas's limits unlocked. It must open the door for ANOTHER agent
# and still keep it shut for Moses himself — a loop with one participant has nobody to notice it.
ALLOW_BOT_CASES = [
    ("ALLOWBOTS Atlas gets through",  {"user": "U9", "bot_id": "B999", "text": "Moses, thoughts?"}, "thoughts?"),
    ("ALLOWBOTS app_id gets through", {"user": "U9", "app_id": "A999", "text": "Moses, thoughts?"}, "thoughts?"),
    ("ALLOWBOTS Moses still cannot answer himself (by user)",
     {"user": BOT_USER, "bot_id": "B999", "text": "Moses, thoughts?"}, None),
    ("ALLOWBOTS Moses still cannot answer himself (by bot_id)",
     {"user": "U9", "bot_id": BOT_ID, "text": "Moses, thoughts?"}, None),
    ("ALLOWBOTS a bot not addressing him is still ignored",
     {"user": "U9", "bot_id": "B999", "text": "anyway, as I was saying"}, None),
]


# ── PERSONAS: every one posts through the SAME Slack app ───────────────────
# The real 08:00 payloads from #ops on 2026-08-14. All three carry the identical bot_id, so bot_id
# alone made Moses count Birdeye's ops report as his own reply, trip the cap, and announce
# "I'll pick this up later" in a channel he does not talk in. The display name is the only thing
# that separates them.
from addressing import is_self  # noqa: E402
from addressing import mentions_name  # noqa: E402

BOT_ID_SHARED = "B0BLZHWR0HY"
PERSONA_CASES = [
    ("PERSONA Birdeye is not Moses",   {"bot_id": BOT_ID_SHARED, "username": "Birdeye",   "subtype": "bot_message"}, False),
    ("PERSONA Therapist is not Moses", {"bot_id": BOT_ID_SHARED, "username": "Therapist", "subtype": "bot_message"}, False),
    ("PERSONA Big Pipe is not Moses",  {"bot_id": BOT_ID_SHARED, "username": "Big Pipe",  "subtype": "bot_message"}, False),
    ("PERSONA Tagilla is not Moses",   {"bot_id": BOT_ID_SHARED, "username": "Tagilla",   "subtype": "bot_message"}, False),
    ("PERSONA Moses IS Moses",         {"bot_id": BOT_ID_SHARED, "username": "Moses",     "subtype": "bot_message"}, True),
    ("PERSONA an unnamed post is his", {"bot_id": BOT_ID_SHARED, "subtype": "bot_message"}, True),
    ("PERSONA bot_profile name counts",
     {"bot_id": BOT_ID_SHARED, "bot_profile": {"name": "Birdeye"}, "subtype": "bot_message"}, False),
    ("PERSONA a different app is never his",
     {"bot_id": "B_ATLAS", "username": "Atlas", "user": "U_ATLAS"}, False),
]


def main() -> int:
    passed = failed = 0
    for label, event, expected in CASES:
        got = addressed(event, BOT_USER, BOT_ID)
        if got == expected:
            print(f"  PASS  {label}")
            passed += 1
        else:
            print(f"  FAIL  {label} — expected {expected!r}, got {got!r}")
            failed += 1
    for label, event, expected in ALLOW_BOT_CASES:
        got = addressed(event, BOT_USER, BOT_ID, allow_bots=True)
        if got == expected:
            print(f"  PASS  {label}")
            passed += 1
        else:
            print(f"  FAIL  {label} — expected {expected!r}, got {got!r}")
            failed += 1
    for label, msg, expected in PERSONA_CASES:
        got = is_self(msg, "U0BMTTVT648", BOT_ID_SHARED)
        if got == expected:
            print(f"  PASS  {label}")
            passed += 1
        else:
            print(f"  FAIL  {label} — expected {expected}, got {got}")
            failed += 1
    # ── A PERSON CAN KEEP A CONVERSATION GOING WITHOUT REPEATING THE NAME ──────
    # The real sequence from #viatica-dev on 2026-08-25. Every message Moses answered began with
    # "Moses"; every one that did not was silently dropped, and Brad re-sent each with the name
    # attached. Follow-ups were gated on the channel being a chat channel, and that one is not.
    from addressing import addressed_by_human_recently as follows

    def h(text):
        return {"user": "U0BKN5JT3PC", "text": text}

    def m(text):
        return {"bot_id": BOT_ID, "app_id": "A0BLTAD2Y67", "username": "Moses",
                "subtype": "bot_message", "text": text}

    def bot(text):
        return {"user": "U9", "bot_id": "B0BPYF1T0E7", "text": text}

    FOLLOW_CASES = [
        ("a person used his name, so the next bare message is for him",
         [h("Try again"), m("Tried again — still nothing"), h("Moses try again")], True),
        ("his own replies do not use up the window",
         [h("Status?"), m("a"), m("b"), m("c"), h("Moses, go ahead and merge it")], True),
        ("once the person has moved on, he drops out",
         [h("e"), h("d"), h("c"), h("b"), h("Moses did a thing")], False),
        ("another agent cannot start one",
         [h("Try again"), bot("Moses, what do you think?")], False),
        ("nobody said his name at all",
         [h("Try again"), m("earlier"), h("deploy is green")], False),
    ]
    for label, history, expected in FOLLOW_CASES:
        got = follows(history, BOT_USER, BOT_ID)
        if got == expected:
            print(f"  PASS  {label}")
            passed += 1
        else:
            print(f"  FAIL  {label} — expected {expected}, got {got}")
            failed += 1


    # ── Named, but not addressed: the model decides, not the regex ────────────
    # Brad, 2026-09-02, after Moses sat out "Atlas and Moses, did you know…" twice in two days. The
    # matchers answer a grammatical question well; they cannot answer "is this for me".
    # mentions_name is the cheap filter that hands that judgement to the model, which can PASS.
    #
    # So this does NOT assert who gets a reply — it asserts the filter is cheap and TOTAL: anything
    # carrying his name reaches the decision, anything without it never does. A filter that tried to
    # be clever here would be the same mistake one layer down.
    #
    # These live INSIDE main(). The first version was appended after `sys.exit(main())`, so it was
    # dead code that never ran — and a deliberate sabotage of mentions_name passed cleanly, which is
    # how it was caught. A test outside the runner is not a weak test, it is not a test.
    NAMED_CASES = [
        ("compound address, his name second", "Atlas and Moses, did you know about workflows?", True),
        ("embedded compound", "would you Atlas and Moses like to read it here?", True),
        ("talk ABOUT him still reaches the model, which should PASS", "it was Brad and Moses", True),
        ("possessive", "that's Moses's job", True),
        ("no name at all", "do any of you read my blog?", False),
        ("a different name only", "Atlas, thoughts?", False),
        ("empty", "", False),
    ]
    for label, text, expected in NAMED_CASES:
        got = mentions_name(text)
        if got == expected:
            print(f"  PASS  NAMED {label}")
            passed += 1
        else:
            print(f"  FAIL  NAMED {label} — expected {expected}, got {got}")
            failed += 1

    print(f"\n  {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

