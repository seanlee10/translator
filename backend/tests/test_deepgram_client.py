from dataclasses import dataclass

from app.config import Settings
from app.deepgram_client import PhraseTracker, connect_params, message_to_dict


def make_settings(**overrides) -> Settings:
    base = dict(deepgram_api_key="secret", anthropic_api_key="a", groq_api_key="g")
    base.update(overrides)
    return Settings(**base)


def test_connect_params_include_everything_the_policy_depends_on():
    params = connect_params(make_settings(), sample_rate=48000)

    assert params["model"] == "nova-3"
    assert params["language"] == "en"
    assert params["encoding"] == "linear16"
    assert params["channels"] == "1"
    assert params["interim_results"] == "true"
    assert params["punctuate"] == "true"
    assert params["smart_format"] == "true"


def test_every_connect_param_is_a_string():
    # The SDK types all of these as Optional[str]; passing True or 16000
    # fails confusingly at request time.
    params = connect_params(make_settings(), sample_rate=48000)

    assert all(isinstance(v, str) for v in params.values()), params


def test_sample_rate_comes_from_the_browser_not_from_settings():
    params = connect_params(make_settings(sample_rate=16000), sample_rate=44100)
    assert params["sample_rate"] == "44100"


def results(transcript: str, is_final: bool) -> dict:
    return {
        "type": "Results",
        "is_final": is_final,
        "speech_final": is_final,
        "channel": {"alternatives": [{"transcript": transcript, "confidence": 0.9}]},
    }


def test_interims_share_a_phrase_id_until_a_final_closes_it():
    tracker = PhraseTracker()

    first = tracker.normalize(results("hel", False))
    second = tracker.normalize(results("hello", False))
    final = tracker.normalize(results("hello there", True))
    following = tracker.normalize(results("how", False))

    assert (first.phrase_id, first.text, first.is_final) == (0, "hel", False)
    assert second.phrase_id == 0
    assert (final.phrase_id, final.is_final) == (0, True)
    assert following.phrase_id == 1


def test_non_results_messages_are_ignored():
    tracker = PhraseTracker()
    assert tracker.normalize({"type": "Metadata"}) is None
    assert tracker.normalize({"type": "UtteranceEnd", "channel": [0]}) is None
    assert tracker.normalize({"type": "SpeechStarted", "channel": [0]}) is None


def test_empty_transcripts_are_dropped_without_advancing_the_phrase():
    tracker = PhraseTracker()

    tracker.normalize(results("hel", False))
    assert tracker.normalize(results("", True)) is None
    assert tracker.normalize(results("   ", False)) is None

    # Still the same phrase — an empty final must not strand the open one.
    assert tracker.normalize(results("hello", False)).phrase_id == 0


def test_missing_alternatives_do_not_raise():
    tracker = PhraseTracker()
    assert tracker.normalize({"type": "Results", "is_final": False, "channel": {}}) is None


def test_message_to_dict_accepts_pydantic_dataclass_and_plain_dict():
    class Pydanticish:
        def model_dump(self):
            return {"type": "Results", "is_final": True}

    class Legacy:
        def dict(self):
            return {"type": "Results", "is_final": False}

    @dataclass
    class Plain:
        type: str = "Results"

    assert message_to_dict(Pydanticish())["is_final"] is True
    assert message_to_dict(Legacy())["is_final"] is False
    assert message_to_dict(Plain())["type"] == "Results"
    assert message_to_dict({"type": "Results"})["type"] == "Results"
