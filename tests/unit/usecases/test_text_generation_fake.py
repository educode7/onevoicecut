"""The fake every generation test will be written against, held to its port.

Structural typing means nothing forces a fake to match the Protocol it stands in
for, and a fake that drifts is not a fake — it is a second implementation with
its own contract, and the code written against it breaks in production. The local
ASR fakes already carry this lesson; this is the same guard for
`TextGenerationPort`, applied before anything depends on it rather than after.

The two failure modes it has to be able to produce are not decoration.
`ContextLengthExceeded` is the whole subject of 10a-iv's halving retry, and
`GenerationFailed` is what a provider does on one call in eighty. Both are
domain errors raised across the port, so a fake that could only succeed would let
a use case ship with no recovery path at all.
"""

import pytest

from onevoicecut.domain.errors import ContextLengthExceeded, GenerationFailed
from onevoicecut.ports.text_generation import TextGenerationPort
from tests.fakes.text_generation import FakeTextGenerationPort


def _port(**kwargs: object) -> TextGenerationPort:
    """Typed as the port on purpose: this call is the structural check."""
    return FakeTextGenerationPort(**kwargs)  # type: ignore[arg-type]


class TestItSatisfiesThePort:
    def test_it_names_the_model_that_produced_the_output(self) -> None:
        """Provenance, same rule as `engine_id` on a chunk result: a summary
        nobody can attribute to a model is a summary nobody can reproduce."""
        assert _port().model_id()

    def test_complete_takes_the_ports_keyword_arguments(self) -> None:
        assert isinstance(
            _port().complete("resume esto", max_output_tokens=100, temperature=0.2),
            str,
        )

    def test_temperature_has_the_ports_default(self) -> None:
        """Omitting it must be legal, because most callers will."""
        assert _port().complete("resume esto", max_output_tokens=100)


class TestItRecordsWhatItWasAsked:
    def test_every_prompt_is_kept_in_order(self) -> None:
        """MAP makes one call per window and REDUCE folds sequentially, so
        "which prompts, in what order" is the assertion most generation tests
        will actually make."""
        port = FakeTextGenerationPort()

        port.complete("primero", max_output_tokens=10)
        port.complete("segundo", max_output_tokens=10)

        assert port.prompts == ["primero", "segundo"]

    def test_the_output_budget_is_recorded_too(self) -> None:
        port = FakeTextGenerationPort()

        port.complete("resume", max_output_tokens=512)

        assert port.calls[0].max_output_tokens == 512


class TestItCanAnswerAndItCanFail:
    def test_scripted_replies_come_back_in_order(self) -> None:
        port = FakeTextGenerationPort(replies=("uno", "dos"))

        assert [
            port.complete("a", max_output_tokens=10),
            port.complete("b", max_output_tokens=10),
        ] == ["uno", "dos"]

    def test_it_reuses_the_last_reply_rather_than_running_out(self) -> None:
        """A windowing test does not know in advance how many windows it will
        produce, and should not have to script a reply per window to assert
        something about the windows."""
        port = FakeTextGenerationPort(replies=("solo una",))

        port.complete("a", max_output_tokens=10)

        assert port.complete("b", max_output_tokens=10) == "solo una"

    def test_it_can_refuse_for_context_length(self) -> None:
        """The subject of 10a-iv. A fake that could not raise this would let the
        halving retry ship untested."""
        port = FakeTextGenerationPort(fail_with=ContextLengthExceeded("too long"))

        with pytest.raises(ContextLengthExceeded):
            port.complete("a", max_output_tokens=10)

    def test_it_can_fail_the_way_a_provider_does(self) -> None:
        port = FakeTextGenerationPort(fail_with=GenerationFailed("upstream 503"))

        with pytest.raises(GenerationFailed):
            port.complete("a", max_output_tokens=10)

    def test_it_can_fail_only_the_first_call(self) -> None:
        """Which is what a retry test needs: fail, then succeed, and assert the
        caller recovered rather than that it gave up."""
        port = FakeTextGenerationPort(
            fail_with=GenerationFailed("blip"), fail_times=1, replies=("ok",)
        )

        with pytest.raises(GenerationFailed):
            port.complete("a", max_output_tokens=10)

        assert port.complete("a", max_output_tokens=10) == "ok"

    def test_a_failed_call_is_still_recorded(self) -> None:
        """A test asserting "it retried three times" reads this list, so a fake
        that only recorded successes could not tell three attempts from one."""
        port = FakeTextGenerationPort(fail_with=GenerationFailed("blip"), fail_times=1)

        with pytest.raises(GenerationFailed):
            port.complete("a", max_output_tokens=10)

        assert port.prompts == ["a"]
