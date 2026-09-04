"""The vocabulary of a delivered clip, and the four things it must declare.

Rendering is where every no-silent-degradation axis in this system finally
arrives at a file somebody watches. A clip can be soft, it can be captioned from
audio nobody verified, it can be captioned from timings nobody measured, and it
can be framed on an empty pulpit — and all four look identical in a directory
listing. `RenderedClip` carries one declaration for each, so none of them is
inferable only by watching the video.

**The declarations are computed above the port, never reported by the adapter.**
All four are known *before* ffmpeg is spawned: quality from the frame and the
target, subtitle timing from whether the segments carried words, coverage from
their `SegmentKind`, tracking from the trajectory. Letting the adapter report them
would put pure arithmetic behind an `integration` marker, and would make the
adapter capable of lying about a value it never computed.

**`factor` is `target_width / crop_width`, and the direction is the readable
one.** Above 1.0 the clip is being stretched, which is the number an operator
acts on; a factor at or below 1.0 is native. Inverting it would make "1.78" mean
a *better* clip than "0.89", which is exactly backwards from how the word
"upscale factor" reads.
"""

import dataclasses
from pathlib import Path

import pytest

from onevoicecut.domain.framing import CropRect, TrackingConfidence
from onevoicecut.domain.generation import ScriptVariant
from onevoicecut.domain.ids import make_clip_id, make_job_id
from onevoicecut.domain.rendering import (
    CaptionCoverage,
    ClipExport,
    ClipState,
    OutputQuality,
    OutputQualityKind,
    OutputSpec,
    RenderedClip,
    SubtitleCue,
    SubtitleTimingSource,
    quality_of,
)

CLIP_ID = make_clip_id("01ARZ3NDEKTSV4RRFFQ69G5FAV")
JOB_ID = make_job_id("01BX5ZZKBKACTAV9WEVGEMMVRZ")

# The authoritative pair from design.md, both derived by `crop_size_for` from a
# real frame rather than written down here independently.
FOUR_K_CROP = CropRect(x=0, y=0, width=1214, height=2160)
TEN_EIGHTY_CROP = CropRect(x=0, y=0, width=606, height=1080)
TARGET = OutputSpec(width=1080, height=1920)


def _clip(**overrides: object) -> RenderedClip:
    fields: dict[str, object] = {
        "clip_id": CLIP_ID,
        "job_id": JOB_ID,
        "path": Path("clips/01ARZ3NDEKTSV4RRFFQ69G5FAV.mp4"),
        "source_start_s": 120.0,
        "source_end_s": 150.0,
        "quality": OutputQuality(kind=OutputQualityKind.NATIVE, factor=0.89),
        "subtitle_timing": SubtitleTimingSource.WORD_LEVEL,
        "captions": CaptionCoverage.CONFIRMED_SPEECH,
        "tracking": TrackingConfidence.WELL_TRACKED,
    }
    fields.update(overrides)
    return RenderedClip(**fields)  # type: ignore[arg-type]


class TestTheEnumerations:
    def test_quality_is_native_or_upscaled(self) -> None:
        assert {m.value for m in OutputQualityKind} == {"native", "upscaled"}

    def test_subtitle_timing_names_its_two_sources(self) -> None:
        """Two states for the same reason `WordTimingSupport` has two: an engine
        either produced word timings or it did not, and a clip built from the
        fallback must say which."""
        assert {m.value for m in SubtitleTimingSource} == {
            "word_level",
            "segment_level",
        }

    def test_caption_coverage_has_exactly_three_members(self) -> None:
        """One basis for all three: the eligible segments in the span, never the
        cues. Cue construction is total over that set, so "no eligible segment"
        and "zero cues" are the same condition rather than two."""
        assert {m.value for m in CaptionCoverage} == {
            "confirmed_speech",
            "includes_unverified",
            "none",
        }

    def test_clip_state_mirrors_the_chunk_lifecycle(self) -> None:
        """The same four answers a chunk gives, because a clip is dispatched,
        worked and finished the same way — and a reader who knows one lifecycle
        should not have to learn a second vocabulary for the other."""
        assert {m.value for m in ClipState} == {
            "pending",
            "rendering",
            "done",
            "failed",
        }


class TestEverythingIsFrozen:
    @pytest.mark.parametrize(
        "instance",
        [
            OutputSpec(width=1080, height=1920),
            OutputQuality(kind=OutputQualityKind.NATIVE, factor=1.0),
            SubtitleCue(start_s=0.0, end_s=1.0, text="hola"),
        ],
    )
    def test_a_value_cannot_be_rewritten_after_construction(
        self, instance: object
    ) -> None:
        field = dataclasses.fields(instance)[0].name  # type: ignore[arg-type]

        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(instance, field, 0)

    @pytest.mark.parametrize(
        "entity",
        [OutputSpec, OutputQuality, SubtitleCue, RenderedClip, ClipExport],
    )
    def test_every_entity_is_slotted(self, entity: type) -> None:
        """Every domain entity is. A clip carries per-cue and per-keyframe data,
        so the ones that scale with clip length pay for a `__dict__`."""
        assert "__slots__" in entity.__dict__


class TestARenderedClipDeclaresFourThings:
    def test_it_carries_one_declaration_per_axis(self) -> None:
        """Quality, caption coverage, subtitle timing and tracking. All four
        look identical in a file listing, which is why each is a field rather
        than something inferable from the video."""
        clip = _clip()

        assert clip.quality.kind is OutputQualityKind.NATIVE
        assert clip.captions is CaptionCoverage.CONFIRMED_SPEECH
        assert clip.subtitle_timing is SubtitleTimingSource.WORD_LEVEL
        assert clip.tracking is TrackingConfidence.WELL_TRACKED

    def test_none_of_the_four_has_a_default(self) -> None:
        """The rule `non_speech_classification` set and `word_timing` repeated: a
        clip that never stated one of these is a gap no reader can reason about,
        and the safe reading of silence is not obvious enough to encode."""
        declarations = {"quality", "subtitle_timing", "captions", "tracking"}
        fields = {f.name: f for f in dataclasses.fields(RenderedClip)}

        for name in declarations:
            assert fields[name].default is dataclasses.MISSING
            assert fields[name].default_factory is dataclasses.MISSING

    def test_it_keeps_the_source_range_it_came_from(self) -> None:
        """Source-absolute, so the clip can be traced back into a three-hour
        recording. Everything inside the render is clip-local; this pair is the
        one place the original coordinate survives."""
        clip = _clip()

        assert (clip.source_start_s, clip.source_end_s) == (120.0, 150.0)

    def test_a_low_confidence_trajectory_is_visible_on_the_result(self) -> None:
        """The spec's own requirement: a mostly-fallback reframe must not be
        presented indistinguishably from a well-tracked one."""
        assert (
            _clip(tracking=TrackingConfidence.LOW_CONFIDENCE).tracking
            is TrackingConfidence.LOW_CONFIDENCE
        )


class TestTheExportRecord:
    def test_it_carries_the_clip_and_what_an_operator_publishes_with_it(self) -> None:
        """Title, description and the script variant that was used — the spec
        names those alongside the file, and an export without them is a video
        nobody can post."""
        export = ClipExport(
            clip=_clip(),
            title="Un titulo",
            description="Una descripcion",
            variant=ScriptVariant(
                target="generic", format="plain", body="guion", duration_target_s=45.0
            ),
            state=ClipState.DONE,
        )

        assert export.clip.clip_id == CLIP_ID
        assert export.variant.target == "generic"
        assert export.state is ClipState.DONE

    def test_the_quality_declaration_travels_with_the_export(self) -> None:
        """Reachable without opening the video, which is the spec's third
        quality scenario stated as a structural fact."""
        export = ClipExport(
            clip=_clip(quality=OutputQuality(OutputQualityKind.UPSCALED, 1.78)),
            title="t",
            description="d",
            variant=ScriptVariant("generic", "plain", "g", 45.0),
            state=ClipState.DONE,
        )

        assert export.clip.quality.factor == 1.78


class TestTheQualityArithmetic:
    def test_a_4k_derived_crop_is_native(self) -> None:
        """1214 px of crop against a 1080 px target: nothing is stretched."""
        quality = quality_of(FOUR_K_CROP, TARGET)

        assert quality.kind is OutputQualityKind.NATIVE
        assert round(quality.factor, 2) == 0.89

    def test_a_1080p_derived_crop_is_upscaled_with_its_factor(self) -> None:
        """606 px stretched to 1080. Unremarkable in a listing and soft only
        once published full-screen on a phone, which is why it is declared."""
        quality = quality_of(TEN_EIGHTY_CROP, TARGET)

        assert quality.kind is OutputQualityKind.UPSCALED
        assert round(quality.factor, 2) == 1.78

    def test_a_crop_exactly_the_target_width_is_native(self) -> None:
        """The boundary, and it belongs on the native side: a factor of exactly
        1.0 stretches nothing. Placing it on the upscaled side would flag every
        perfectly-matched render as degraded."""
        quality = quality_of(CropRect(0, 0, 1080, 1920), TARGET)

        assert quality.kind is OutputQualityKind.NATIVE
        assert quality.factor == 1.0

    def test_one_pixel_narrower_than_the_target_is_upscaled(self) -> None:
        """The other side of the same boundary, so neither is asserted alone."""
        assert quality_of(CropRect(0, 0, 1078, 1920), TARGET).kind is (
            OutputQualityKind.UPSCALED
        )

    def test_the_factor_is_target_over_crop(self) -> None:
        """The readable direction: above 1.0 means the clip is being stretched.
        Inverting it would make 1.78 read as better than 0.89, which is exactly
        backwards from how "upscale factor" is spoken."""
        assert quality_of(CropRect(0, 0, 540, 960), TARGET).factor == 2.0

    def test_it_reads_only_the_width(self) -> None:
        """Height cannot disagree. `crop_size_for` derives one from the other at
        a fixed aspect and `CropTrajectory` holds the pair constant for the whole
        clip, so a second axis here could only ever restate the first — or
        contradict it, which is worse."""
        wide = quality_of(CropRect(0, 0, 1214, 2160), TARGET)
        same_width_absurd_height = quality_of(CropRect(0, 0, 1214, 7), TARGET)

        assert wide == same_width_absurd_height

    def test_a_degenerate_crop_is_refused_rather_than_dividing_by_zero(self) -> None:
        """`crop_size_for` is total and returns `(0, 0)` for a frame under two
        pixels — an honest answer that has no quality. The refusal belongs here
        rather than a fabricated factor, and slice 13b's worker turns it into
        `FrameGeometryUnavailable` before a render is ever dispatched."""
        with pytest.raises(ValueError):
            quality_of(CropRect(0, 0, 0, 0), TARGET)


def test_the_authoritative_crops_come_from_the_real_derivation() -> None:
    """The two numbers this module pins are design.md's authoritative pair, and
    they are re-derived here rather than trusted: a fixture that drifted from
    `crop_size_for` would pin the arithmetic to a value the pipeline never
    produces."""
    from onevoicecut.domain.framing import TrajectoryPolicy, crop_size_for
    from onevoicecut.domain.media import FrameSize

    policy = TrajectoryPolicy()

    assert crop_size_for(FrameSize(3840, 2160), policy) == (
        FOUR_K_CROP.width,
        FOUR_K_CROP.height,
    )
    assert crop_size_for(FrameSize(1920, 1080), policy) == (
        TEN_EIGHTY_CROP.width,
        TEN_EIGHTY_CROP.height,
    )
