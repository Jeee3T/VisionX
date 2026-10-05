"""The two SSE channels, and why the pointer needs its own.

The virtual pointer used to travel inside the 12 Hz `telemetry` event, sharing
one bounded queue with commands and state. Two things followed from that, and
both are visible to a presenter as "the pointer is laggy":

  * an 83 ms quantisation floor before a position even left the server, and
  * a browser that fell behind received a *backlog* of stale positions, so it
    drew where the hand had been rather than where it is.

The pointer channel is now a single-slot mailbox: publishing overwrites what has
not been read. A slow client skips positions instead of lagging behind them.
Discrete events keep the queue, because losing a slide change is not acceptable
at any speed.
"""

import threading

import pytest

from services.event_bus import MAX_QUEUE, EventBus


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


# ============================================ THE POINTER CHANNEL ============
def test_the_pointer_channel_keeps_only_the_newest_position(bus):
    """The whole point: a client that reads late reads *now*, not history."""
    subscriber = bus.subscribe()

    for step in range(200):
        bus.publish_pointer({"type": "pointer", "x": step / 200, "y": 0.5})

    events = subscriber.drain(timeout=0.1)
    pointers = [e for e in events if e["type"] == "pointer"]
    assert len(pointers) == 1, "stale pointer positions were queued behind the newest"
    assert pointers[0]["x"] == pytest.approx(199 / 200)


def test_a_flood_of_pointers_cannot_evict_a_command(bus):
    """The failure this design exists to prevent.

    Sharing one bounded queue meant a second of pointer traffic could push a
    slide change out of it. A command must survive any amount of hand movement.
    """
    subscriber = bus.subscribe()

    bus.publish({"type": "command", "command": "NEXT_SLIDE", "currentSlide": 2})
    for step in range(MAX_QUEUE * 5):
        bus.publish_pointer({"type": "pointer", "x": step / 1000, "y": 0.5})

    events = subscriber.drain(timeout=0.1)
    commands = [e for e in events if e["type"] == "command"]
    assert len(commands) == 1 and commands[0]["currentSlide"] == 2


def test_the_pointer_arrives_after_the_commands_in_the_same_batch(bus):
    """A position describes where the hand is *after* whatever just happened.

    Delivering it first would let the window draw the pointer against the old
    slide for one frame - a visible flash of ink on the wrong slide when a Next
    Slide and a pointer sample land together.
    """
    subscriber = bus.subscribe()
    bus.publish_pointer({"type": "pointer", "x": 0.5, "y": 0.5})
    bus.publish({"type": "command", "command": "NEXT_SLIDE"})

    events = subscriber.drain(timeout=0.1)
    assert [e["type"] for e in events] == ["command", "pointer"]


# ================================================ THE EVENT QUEUE ============
def test_discrete_events_are_delivered_in_order(bus):
    subscriber = bus.subscribe()
    for slide in range(1, 11):
        bus.publish({"type": "command", "currentSlide": slide})

    events = subscriber.drain(timeout=0.1)
    assert [e["currentSlide"] for e in events] == list(range(1, 11))


def test_a_stalled_subscriber_drops_its_oldest_events_not_the_newest(bus):
    """A browser that stopped reading must not stall the camera thread.

    The queue is bounded and drops from the front, so the events that survive are
    the most recent - the ones that still describe the deck.
    """
    subscriber = bus.subscribe()
    for slide in range(1, MAX_QUEUE + 51):
        bus.publish({"type": "command", "currentSlide": slide})

    events = subscriber.drain(timeout=0.1)
    assert len(events) == MAX_QUEUE
    assert events[-1]["currentSlide"] == MAX_QUEUE + 50


def test_publishing_never_blocks_on_a_subscriber_that_never_reads(bus):
    """The camera loop publishes from its own thread and cannot afford to wait."""
    bus.subscribe()   # deliberately never drained

    done = threading.Event()

    def flood():
        for step in range(5000):
            bus.publish_pointer({"type": "pointer", "x": step / 5000, "y": 0.5})
            bus.publish({"type": "telemetry", "fps": 30})
        done.set()

    thread = threading.Thread(target=flood, daemon=True)
    thread.start()
    assert done.wait(timeout=5.0), "publishing blocked on a subscriber that never read"


def test_drain_blocks_until_something_arrives(bus):
    """No polling: an idle stream costs one blocked thread, not a busy loop."""
    subscriber = bus.subscribe()
    assert subscriber.drain(timeout=0.05) == []

    def publish_soon():
        bus.publish({"type": "command", "currentSlide": 4})

    threading.Timer(0.05, publish_soon).start()
    events = subscriber.drain(timeout=2.0)
    assert [e["currentSlide"] for e in events] == [4]


# ================================================== FAN-OUT ==================
def test_every_window_sees_every_command(bus):
    """The control window and the presentation window are both subscribers."""
    control = bus.subscribe()
    presentation = bus.subscribe()

    bus.publish({"type": "command", "command": "NEXT_SLIDE", "currentSlide": 5})
    bus.publish_pointer({"type": "pointer", "x": 0.4, "y": 0.6})

    for subscriber in (control, presentation):
        events = subscriber.drain(timeout=0.1)
        assert [e["type"] for e in events] == ["command", "pointer"]


def test_a_new_subscriber_is_seeded_with_the_last_telemetry(bus):
    """A window opened mid-talk shows live state at once, not a blank screen."""
    bus.publish({"type": "telemetry", "fps": 29.5, "mode": "POINTER"})
    subscriber = bus.subscribe()

    events = subscriber.drain(timeout=0.1)
    assert events and events[0]["mode"] == "POINTER"


def test_the_seed_never_arrives_after_newer_telemetry(bus):
    """Registering and seeding must be atomic.

    Registering first and seeding afterwards leaves a window in which a publish
    delivers NEWER telemetry to this subscriber, which the stale seed then lands
    behind - so a presentation window opened mid-session renders the older
    snapshot last and shows state that had already been superseded.

    The interleaving is FORCED rather than raced for. A threaded version of this
    test passed against the broken code, because the window is a few instructions
    wide and hitting it by chance is unreliable - which would have made this a
    test that describes a bug it cannot catch. Hooking the lock puts a publish
    exactly where the bug lives, every run.
    """
    bus.publish({"type": "telemetry", "seq": 0, "mode": "IDLE"})

    class PublishOnRelease:
        """Publishes newer telemetry the first time the bus lock is released."""

        def __init__(self, inner):
            self.inner = inner
            self.fired = False

        def __enter__(self):
            return self.inner.__enter__()

        def __exit__(self, *exception):
            # Released FIRST, so the publish below can take the lock itself
            # rather than deadlocking on a non-reentrant one.
            result = self.inner.__exit__(*exception)
            if not self.fired:
                self.fired = True
                bus.publish({"type": "telemetry", "seq": 1, "mode": "POINTER"})
            return result

    bus._lock = PublishOnRelease(bus._lock)
    subscriber = bus.subscribe()

    events = subscriber.drain(timeout=0.5)
    sequence = [e["seq"] for e in events if e.get("type") == "telemetry"]
    assert sequence == sorted(sequence), (
        f"a new subscriber received telemetry out of order: {sequence} - the seed "
        "was delivered after newer state"
    )


def test_unsubscribing_stops_delivery(bus):
    subscriber = bus.subscribe()
    bus.unsubscribe(subscriber)
    bus.publish({"type": "command", "command": "NEXT_SLIDE"})
    assert subscriber.drain(timeout=0.05) == []
    assert bus.subscriber_count == 0
