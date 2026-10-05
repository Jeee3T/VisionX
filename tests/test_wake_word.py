"""Continuous listening: fixes.md §4.

    [Listening] -> "Vision" -> [Command] -> "go to next slide" -> "OK" -> execute
                <- back to listening

The machine is pure text, so it can be tested exhaustively - and it must be,
because it is the only thing standing between a presenter's ordinary speech and
their slides. Two properties matter more than any single case:

  * ordinary speech NEVER produces a command, whatever it contains;
  * "Vision <command> OK" ALWAYS produces one, however it is chopped into
    segments by the recorder.

The trained intent model is not involved here and is not changed by any of this.
The machine decides *when* there is something to classify; the model still decides
what it means, with the same confidence bands as before.
"""

import pytest

from voice_assistant.wake import (
    ACTION_ARMED,
    ACTION_CAPTURING,
    ACTION_EXECUTE,
    ACTION_IDLE,
    ACTION_TIMEOUT,
    STATE_CAPTURING,
    STATE_LISTENING,
    WakeWordSession,
    strip_terminator,
    strip_wake_word,
)


@pytest.fixture
def session() -> WakeWordSession:
    return WakeWordSession()


def _run(session, *segments):
    return [session.observe(segment) for segment in segments]


# ================================================== THE HEADLINE BEHAVIOUR ====
def test_the_example_from_the_brief_executes_immediately(session):
    """'Vision go to next slide OK' -> NEXT_SLIDE, in one breath."""
    result = session.observe("Vision go to next slide OK")

    assert result.action == ACTION_EXECUTE
    assert result.command == "go to next slide"
    assert result.should_execute
    assert session.state == STATE_LISTENING, "the machine must re-arm itself"


def test_the_same_command_split_across_three_segments(session):
    """The recorder cuts audio into fixed segments, so the words arrive apart."""
    armed, capturing, executed = _run(session, "Vision", "go to next slide", "OK")

    assert armed.action == ACTION_ARMED and armed.state == STATE_CAPTURING
    assert capturing.action == ACTION_CAPTURING
    assert capturing.buffered == "go to next slide"
    assert executed.action == ACTION_EXECUTE
    assert executed.command == "go to next slide"
    assert session.state == STATE_LISTENING


def test_a_command_split_mid_phrase_is_reassembled(session):
    results = _run(session, "vision go", "to next", "slide ok")
    assert results[-1].command == "go to next slide"


def test_the_wake_word_and_command_arrive_together_then_ok_separately(session):
    first, second = _run(session, "vision go to slide seven", "ok")
    assert first.action == ACTION_CAPTURING
    assert second.command == "go to slide seven"


def test_listening_resumes_after_every_command(session):
    """Three commands in a row, with no interaction between them."""
    for phrase, expected in (
        ("vision next slide ok", "next slide"),
        ("vision go back ok", "go back"),
        ("vision black screen ok", "black screen"),
    ):
        result = session.observe(phrase)
        assert result.command == expected
        assert session.state == STATE_LISTENING


# ============================================ ORDINARY SPEECH DOES NOTHING ====
@pytest.mark.parametrize("speech", [
    "so as you can see on this slide the revenue grew",
    "let's move on to the next topic",
    "next slide please",                      # a command shape, without the wake word
    "ok everyone let us begin",               # the terminator alone
    "that's all for today, any questions",
    "go to slide seven",
    "okay so the next slide shows our roadmap",
    "",
    "   ",
])
def test_ordinary_speech_never_produces_a_command(session, speech):
    result = session.observe(speech)
    assert result.action == ACTION_IDLE
    assert not result.should_execute
    assert session.state == STATE_LISTENING


def test_a_long_talk_containing_every_command_phrase_fires_nothing(session):
    """The realistic worst case: a presenter whose talk is *about* slides."""
    talk = [
        "good morning everyone thanks for coming",
        "on the next slide we will look at the numbers",
        "let me go back one slide for a moment",
        "ok so the last slide showed our roadmap",
        "if you go to slide seven you will see the detail",
        "any questions before we move on",
    ]
    for line in talk:
        assert session.observe(line).should_execute is False
    assert session.state == STATE_LISTENING


def test_the_wake_word_must_be_a_whole_word(session):
    """'envisioning' and 'provisional' contain the letters, not the word."""
    for speech in ("our envisioning process", "the provisional results", "revision two"):
        assert session.observe(speech).action == ACTION_IDLE
    assert session.state == STATE_LISTENING


@pytest.mark.parametrize("speech", [
    # Every one of these moved a slide before the vocabulary was tightened: the
    # wake word really was spoken, and what followed really was a command phrase,
    # so the intent model classified it with high confidence. The gate cannot
    # catch this - only refusing to arm can.
    "our vision going forward is to move on to the next slide okay",
    "we need to provision more servers and then move to the next slide okay",
    "the vision is simple lets go back one slide okay so",
    "my vision for this product is to go to the last slide okay",
    "that vision of ours means we should move to the next slide ok",
    "we envision a future where you just go to the next slide okay",
])
def test_a_talk_that_is_about_vision_does_not_drive_the_deck(session, speech):
    session.reset()
    result = session.observe(speech)
    assert not result.should_execute, f"{speech!r} produced {result.command!r}"


def test_a_wake_word_that_is_genuinely_addressed_still_arms(session):
    """The prefix rule must not swallow real commands. 'so', 'and', 'okay then'
    and a bare start are all normal ways to precede a summons."""
    for speech in ("vision next slide ok", "so vision next slide ok",
                   "and vision next slide ok", "right vision next slide ok"):
        session.reset()
        assert session.observe(speech).command == "next slide", speech


def test_a_run_on_capture_is_abandoned_rather_than_classified(session):
    """After a stray wake word, the rest of a sentence must not become a command
    just because it happens to contain 'next slide'."""
    result = session.observe(
        "vision so anyway what i wanted to say before we move on to the next slide ok"
    )
    assert not result.should_execute
    assert result.action == ACTION_TIMEOUT
    assert session.state == STATE_LISTENING


def test_the_terminator_must_be_a_whole_word(session):
    session.observe("vision")
    result = session.observe("okra and okapi")
    assert result.action == ACTION_CAPTURING
    assert result.buffered == "okra and okapi"


# ================================================== RECOGNITION VARIANTS ======
@pytest.mark.parametrize("wake", ["vision", "Vision", "VISION", "visions", "vision x", "visionx"])
def test_common_mis_transcriptions_of_the_wake_word_still_arm(wake, session):
    """Whisper hears a short spoken word several ways. A wake word that works two
    times in three is worse than none: the presenter stops trusting it."""
    session.reset()
    result = session.observe(f"{wake} next slide ok")
    assert result.should_execute, f"{wake!r} did not arm the machine"
    assert result.command == "next slide"


@pytest.mark.parametrize("terminator", ["ok", "OK", "okay", "Okay", "ok."])
def test_common_mis_transcriptions_of_the_terminator_still_execute(terminator, session):
    session.reset()
    result = session.observe(f"vision next slide {terminator}")
    assert result.should_execute
    assert result.command == "next slide"


def test_punctuation_and_case_are_normalised(session):
    result = session.observe("Vision, go to slide 7. OK!")
    assert result.command == "go to slide 7"


# ================================================== EDGE CASES ================
def test_the_wake_word_alone_arms_without_executing(session):
    result = session.observe("vision")
    assert result.action == ACTION_ARMED
    assert session.is_capturing
    assert not result.should_execute


def test_wake_word_immediately_followed_by_ok_executes_nothing(session):
    """'Vision OK' is not a command; it must not reach the intent model."""
    result = session.observe("vision ok")
    assert result.action == ACTION_EXECUTE
    assert result.command == ""
    assert not result.should_execute
    assert session.state == STATE_LISTENING


def test_a_terminator_before_a_wake_word_still_completes_the_command(session):
    """Recorder segments do not respect sentence boundaries.

    "...slide five. OK." and the next "Vision" routinely land in one 3-second
    segment. Handling the wake word first - which a single left-to-right pass
    does not - silently threw the finished command away.
    """
    session.observe("vision go to slide five")
    result = session.observe("ok vision")

    assert result.commands == ["go to slide five"], result.commands
    assert session.is_capturing, "the trailing wake word should have re-armed"

    following = session.observe("next slide ok")
    assert following.command == "next slide"


def test_two_commands_in_one_segment_are_both_reported(session):
    result = session.observe("vision next slide ok vision previous slide ok")
    assert result.commands == ["next slide", "previous slide"]
    assert session.state == STATE_LISTENING


def test_a_timeout_does_not_swallow_the_segment_that_restarts_it(session):
    """The presenter should not have to say the whole command twice."""
    session.observe("vision", now=1000.0)
    result = session.observe("vision go to next slide ok",
                             now=1000.0 + session.capture_timeout + 1)

    assert result.should_execute
    assert result.command == "go to next slide"
    assert session.state == STATE_LISTENING


def test_a_timeout_with_nothing_else_in_the_segment_reports_the_timeout(session):
    session.observe("vision", now=1000.0)
    result = session.observe("just talking",
                             now=1000.0 + session.capture_timeout + 1)
    assert result.action == ACTION_TIMEOUT
    assert session.state == STATE_LISTENING


def test_observe_is_safe_under_concurrent_callers(session):
    """Flask is threaded, and one user can have two tabs open."""
    import threading

    executed: list[str] = []
    barrier = threading.Barrier(8)

    def run():
        barrier.wait()
        for _ in range(40):
            result = session.observe("vision next slide ok")
            executed.extend(result.commands)

    threads = [threading.Thread(target=run) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Every command must be intact - never a torn buffer like "next next slide".
    assert executed, "nothing executed at all"
    assert set(executed) == {"next slide"}, set(executed)


def test_a_second_wake_word_restarts_the_capture(session):
    """The presenter fumbled and started over. The correction is not the command."""
    _run(session, "vision go to slide", "no sorry vision next slide ok")
    # The buffer from before the second wake word must not survive.
    result = session.observe("vision previous slide ok")
    assert result.command == "previous slide"


def test_a_second_wake_word_within_one_segment_restarts_the_capture(session):
    """No terminator between them, so the first half was never a command."""
    result = session.observe("vision go back vision next slide ok")
    assert result.command == "next slide"
    assert result.commands == ["next slide"]


def test_speech_after_the_terminator_is_discarded(session):
    """The presenter said OK and went back to talking to the room."""
    result = session.observe("vision next slide ok and as you can see here")
    assert result.command == "next slide"
    assert session.state == STATE_LISTENING

    following = session.observe("as you can see the revenue grew")
    assert following.action == ACTION_IDLE


def test_only_the_first_terminator_ends_the_command(session):
    result = session.observe("vision next slide ok ok ok")
    assert result.command == "next slide"


def test_empty_segments_during_capture_do_not_end_it(session):
    """Silence between words is not the end of a command."""
    session.observe("vision")
    for _ in range(5):
        assert session.observe("").action == ACTION_CAPTURING
    assert session.observe("next slide ok").command == "next slide"


def test_empty_segments_while_listening_stay_idle(session):
    assert session.observe("").action == ACTION_IDLE
    assert session.state == STATE_LISTENING


def test_a_capture_that_never_ends_times_out(session):
    """An accidental wake word must not swallow the rest of the talk."""
    session.observe("vision", now=1000.0)
    result = session.observe("still talking", now=1000.0 + session.capture_timeout + 1)

    assert result.action == ACTION_TIMEOUT
    assert session.state == STATE_LISTENING


def test_a_capture_that_grows_absurdly_long_is_abandoned(session):
    session.observe("vision")
    result = None
    for _ in range(40):
        result = session.observe("and then we looked at the numbers again")
        if result.action == ACTION_TIMEOUT:
            break
    assert result.action == ACTION_TIMEOUT
    assert session.state == STATE_LISTENING


def test_a_timed_out_capture_leaves_the_machine_usable(session):
    session.observe("vision", now=0.0)
    session.observe("rambling", now=1000.0)
    assert session.observe("vision next slide ok").command == "next slide"


def test_reset_abandons_a_half_captured_command(session):
    session.observe("vision go to slide")
    assert session.is_capturing
    session.reset()
    assert session.state == STATE_LISTENING
    assert session.buffered == ""


def test_snapshot_describes_the_current_state(session):
    snapshot = session.snapshot()
    assert snapshot["state"] == STATE_LISTENING
    assert "vision" in snapshot["wakeWords"]
    assert "ok" in snapshot["terminators"]

    session.observe("vision next")
    assert session.snapshot()["state"] == STATE_CAPTURING
    assert session.snapshot()["buffered"] == "next"


def test_the_result_serialises_for_the_ui(session):
    payload = session.observe("vision next slide ok").as_dict()
    assert payload["action"] == ACTION_EXECUTE
    assert payload["command"] == "next slide"
    assert payload["shouldExecute"] is True
    assert payload["state"] == STATE_LISTENING


def test_custom_vocabulary_is_honoured():
    session = WakeWordSession(wake_words=("computer",), terminators=("execute",))
    assert session.observe("vision next slide ok").action == ACTION_IDLE
    result = session.observe("computer next slide execute")
    assert result.command == "next slide"


def test_an_empty_vocabulary_never_matches_anything():
    """An empty alternation in a regex matches everywhere - which would arm the
    machine on every word ever spoken."""
    session = WakeWordSession(wake_words=(), terminators=())
    for speech in ("vision next slide ok", "anything at all", ""):
        assert session.observe(speech).action == ACTION_IDLE


# ================================================== HELPERS ==================
def test_strip_wake_word_returns_what_follows_it():
    assert strip_wake_word("Vision go to next slide") == "go to next slide"
    assert strip_wake_word("no wake word here") == "no wake word here"
    assert strip_wake_word("vision one vision two") == "two"


def test_strip_terminator_returns_what_precedes_it():
    assert strip_terminator("go to next slide OK") == "go to next slide"
    assert strip_terminator("no terminator here") == "no terminator here"


# ============================== THE MACHINE PLUS THE TRAINED INTENT MODEL =====
def test_the_captured_command_is_what_the_trained_model_expects(interpreter):
    """The two halves must actually fit: the machine's output has to classify.

    This is the real end-to-end assertion for §4 - continuous listening feeding
    the *unchanged* TF-IDF + logistic-regression pipeline.
    """
    session = WakeWordSession()
    spoken = {
        "vision go to the next slide ok": "NEXT_SLIDE",
        "vision go back one slide ok": "PREVIOUS_SLIDE",
        "vision go to slide seven ok": "GO_TO_SLIDE",
        "vision go to the last slide ok": "LAST_SLIDE",
        "vision black screen ok": "BLACKOUT",
    }

    for utterance, expected in spoken.items():
        session.reset()
        wake = session.observe(utterance)
        assert wake.should_execute, f"{utterance!r} did not complete a command"

        decision = interpreter.interpret(wake.command, total_slides=20)
        assert decision.command == expected, (
            f"{wake.command!r} classified as {decision.command}, expected {expected}"
        )


@pytest.mark.parametrize("segments", [
    ("vision go to slide twelve ok",),
    ("vision", "go to slide twelve", "ok"),
    ("vision go to", "slide twelve", "ok"),
    ("vision go to slide", "twelve ok"),
    ("vision", "go", "to", "slide", "twelve", "ok"),
])
def test_where_the_recorder_cuts_a_command_does_not_change_its_meaning(interpreter, segments):
    """Segmentation is an artefact of the recorder's 3-second timer. A command
    must mean the same thing however that timer happens to fall."""
    session = WakeWordSession()
    results = _run(session, *segments)

    final = results[-1]
    assert final.should_execute, f"{segments} never completed a command"
    assert final.command == "go to slide twelve"

    decision = interpreter.interpret(final.command, total_slides=20)
    assert decision.command == "GO_TO_SLIDE"
    assert decision.parameters == {"slideNumber": 12}


def test_ordinary_speech_that_reaches_the_model_only_via_a_wake_word(interpreter):
    """Belt and braces: even if the wake word is spoken, an unrelated command
    still has to clear the model's own confidence gate."""
    session = WakeWordSession()
    result = session.observe("vision the weather is quite nice today ok")
    assert result.should_execute

    decision = interpreter.interpret(result.command, total_slides=20)
    assert decision.command_intent is None or decision.band == "REJECT", (
        f"chatter after the wake word produced {decision.command}"
    )


def test_the_machine_survives_arbitrary_speech():
    """Property test: whatever is said, the machine must stay well-formed.

    It sits between a presenter's live speech and their slides, so "it did
    something odd on that one sentence" is not an acceptable failure mode. Random
    word salad drawn from the vocabulary it is most likely to trip on - wake
    words, terminators, near-misses, empty segments.
    """
    import random

    words = ["vision", "ok", "okay", "next", "slide", "go", "to", "the", "our",
             "and", "", "previous", "seven", "provision", "envision", "visions",
             "black", "screen"]
    rng = random.Random(7)

    for _ in range(2000):
        session = WakeWordSession()
        for _ in range(rng.randint(1, 6)):
            segment = " ".join(rng.choice(words) for _ in range(rng.randint(0, 8)))
            result = session.observe(segment, now=1000.0 + rng.random() * 30)

            assert session.state in (STATE_LISTENING, STATE_CAPTURING)
            assert result.action in (
                ACTION_IDLE, ACTION_ARMED, ACTION_CAPTURING, ACTION_EXECUTE, ACTION_TIMEOUT,
            )
            # A reported command is never blank, and the buffer never grows past
            # the cap - the two ways a run-on capture used to reach the model.
            assert all(command.strip() for command in result.commands)
            assert len(session.buffered.split()) <= session.max_command_words
