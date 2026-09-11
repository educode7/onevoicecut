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

from onevoicecut.domain.framing import CropRect, TimeSpan, TrackingConfidence
from onevoicecut.domain.generation import ScriptVariant
from onevoicecut.domain.ids import make_clip_id, make_job_id
from onevoicecut.domain.rendering import (
    CaptionCoverage,
    ClipExport,
    ClipState,
    DurationCompliance,
    DurationComplianceKind,
    OutputQuality,
    OutputQualityKind,
    OutputSpec,
    RenderedClip,
    RenderProfile,
    SafeArea,
    SubtitleCue,
    SubtitleTimingSource,
    aspect_of,
    duration_compliance_of,
    export_key,
    quality_of,
)

CLIP_ID = make_clip_id("01ARZ3NDEKTSV4RRFFQ69G5FAV")
JOB_ID = make_job_id("01BX5ZZKBKACTAV9WEVGEMMVRZ")

# Illustrative only. The real per-destination values are measured against each
# app's current interface and go stale when that interface changes, which is why
# the registry ships shapes and the resolver refuses an unmeasured one.
SAFE_AREA = SafeArea(top=0.06, bottom=0.18, left=0.05, right=0.14)
PROFILE = RenderProfile(
    name="vertical",
    output=OutputSpec(width=1080, height=1920),
    safe_area=SAFE_AREA,
    max_duration_s=90.0,
)

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
        "duration": DurationCompliance(
            kind=DurationComplianceKind.WITHIN_CEILING, overrun_s=0.0
        ),
    }
    fields.update(overrides)
    return RenderedClip(**fields)  # type: ignore[arg-type]


def _variants(*targets: str) -> tuple[ScriptVariant, ...]:
    return tuple(
        ScriptVariant(
            target=target,
            format="plain",
            body=f"guion {target}",
            duration_target_s=45.0,
        )
        for target in targets
    )


def _export(**overrides: object) -> ClipExport:
    fields: dict[str, object] = {
        "job_id": JOB_ID,
        "clip_id": CLIP_ID,
        "clip": _clip(),
        "failure": None,
        "profile": "vertical",
        "title": "Un titulo",
        "description": "Una descripcion",
        "variants": _variants("tiktok"),
        "state": ClipState.DONE,
    }
    fields.update(overrides)
    return ClipExport(**fields)  # type: ignore[arg-type]


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
            SAFE_AREA,
            PROFILE,
            DurationCompliance(kind=DurationComplianceKind.WITHIN_CEILING, overrun_s=0.0),
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
        [
            OutputSpec,
            OutputQuality,
            SubtitleCue,
            RenderedClip,
            ClipExport,
            SafeArea,
            RenderProfile,
            DurationCompliance,
        ],
    )
    def test_every_entity_is_slotted(self, entity: type) -> None:
        """Every domain entity is. A clip carries per-cue and per-keyframe data,
        so the ones that scale with clip length pay for a `__dict__`."""
        assert "__slots__" in entity.__dict__


class TestARenderedClipDeclaresFiveThings:
    def test_it_carries_one_declaration_per_axis(self) -> None:
        """Quality, caption coverage, subtitle timing, tracking and duration
        compliance. All five look identical in a file listing, which is why
        each is a field rather than something inferable from the video."""
        clip = _clip()

        assert clip.quality.kind is OutputQualityKind.NATIVE
        assert clip.captions is CaptionCoverage.CONFIRMED_SPEECH
        assert clip.subtitle_timing is SubtitleTimingSource.WORD_LEVEL
        assert clip.tracking is TrackingConfidence.WELL_TRACKED
        assert clip.duration.kind is DurationComplianceKind.WITHIN_CEILING

    def test_none_of_the_five_has_a_default(self) -> None:
        """The rule `non_speech_classification` set and `word_timing` repeated: a
        clip that never stated one of these is a gap no reader can reason about,
        and the safe reading of silence is not obvious enough to encode."""
        declarations = {
            "quality",
            "subtitle_timing",
            "captions",
            "tracking",
            "duration",
        }
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
        """Title, description and the scripts the file delivers — the spec names
        those alongside the file, and an export without them is a video nobody
        can post."""
        export = _export()

        assert export.clip is not None and export.clip.clip_id == CLIP_ID
        assert export.variants[0].target == "tiktok"
        assert export.state is ClipState.DONE

    def test_the_quality_declaration_travels_with_the_export(self) -> None:
        """Reachable without opening the video, which is the spec's third
        quality scenario stated as a structural fact."""
        export = _export(
            clip=_clip(quality=OutputQuality(OutputQualityKind.UPSCALED, 1.78))
        )

        assert export.clip is not None and export.clip.quality.factor == 1.78

    def test_it_names_the_profile_it_was_rendered_under(self) -> None:
        """The other half of the identity. One candidate now yields one export
        per distinct profile, so an export that did not say which profile it is
        would be indistinguishable from its own sibling."""
        assert _export(profile="square").profile == "square"

    def test_the_profile_is_a_name_rather_than_a_resolved_profile(self) -> None:
        """A name, for the reason a script target names one. The resolved object
        carries a measured safe area and a duration ceiling that go stale when a
        destination changes its interface, and an export is a record read back
        long after it was written — embedding them would resurrect last month's
        margin as though the registry still agreed with it. The registry stays
        the one place that geometry lives, and the name is what 13b-ii uses as a
        path component."""
        field = {f.name: f for f in dataclasses.fields(ClipExport)}["profile"]

        assert field.type is str

    def test_it_records_every_variant_the_file_delivers(self) -> None:
        """The spec's shared-file scenario: three networks naming one profile
        share one render, and each variant stays attributable to the network it
        was written for."""
        export = _export(variants=_variants("tiktok", "instagram", "facebook"))

        assert [v.target for v in export.variants] == [
            "tiktok",
            "instagram",
            "facebook",
        ]

    def test_no_single_variant_field_survives_the_re_keying(self) -> None:
        """The plural is load-bearing. A file serving three networks with one
        variant recorded loses the other two, and reconstructing them later means
        re-running generation against a transcript that may have been re-stitched
        since — the same reasoning that put title and description here rather
        than leaving them derivable."""
        names = {f.name for f in dataclasses.fields(ClipExport)}

        assert "variants" in names
        assert "variant" not in names


class TestAnExportDeliversSomething:
    """An export exists to make a rendered file postable. One delivering no
    variant is a video nobody can post, which is the exact failure the type was
    introduced to prevent — so it is refused where it is first expressible."""

    @pytest.mark.parametrize("coverage", list(CaptionCoverage))
    def test_an_export_delivering_no_variant_is_refused(
        self, coverage: CaptionCoverage
    ) -> None:
        """Unconditional across caption coverage, not scoped to the confirmed
        case the task named. Coverage says what the captions were built from and
        a variant says what the operator posts; a clip whose span carried no
        eligible segment is still perfectly postable material, and tying the two
        axes together is the inference `SegmentKind` and diarization are already
        forbidden from making. Scoping the check would leave the same unpostable
        export legal under two of the three values."""
        with pytest.raises(ValueError):
            _export(clip=_clip(captions=coverage), variants=())

    @pytest.mark.parametrize("state", list(ClipState))
    def test_the_refusal_does_not_wait_for_the_render_to_finish(
        self, state: ClipState
    ) -> None:
        """A `PENDING` export is written when the render is dispatched, and the
        profiles it is dispatched under are derived from the variants' targets —
        so an export with none was requested by nobody, whatever its state. A
        check that only ran at `DONE` would let the empty record reach disk and
        refuse it after the ffmpeg pass was already paid for."""
        with pytest.raises(ValueError):
            _export(state=state, variants=())

    def test_one_variant_is_enough(self) -> None:
        """The boundary on the legal side: a profile named by a single network
        delivers one script, and that is a complete export."""
        assert len(_export(variants=_variants("tiktok")).variants) == 1


class TestTheExportKey:
    """One candidate now yields one export per distinct profile, so the clip id
    stopped being an identity the day a second profile became expressible."""

    def test_clip_and_profile_together_resolve_to_exactly_one_export(self) -> None:
        """The spec scenario stated as a lookup: two exports of one clip, keyed,
        and each key finding its own."""
        vertical = _export(profile="vertical")
        square = _export(profile="square")

        by_key = {export_key(e.clip_id, e.profile): e for e in (vertical, square)}

        assert len(by_key) == 2
        assert by_key[export_key(CLIP_ID, "square")] is square

    def test_the_clip_id_alone_does_not_identify_a_rendered_file(self) -> None:
        """Two renders of one clip differ only by profile, and a key that ignored
        the profile would collapse them — which is how an operator ends up
        publishing the wrong one of two different files bearing one name."""
        assert export_key(CLIP_ID, "vertical") != export_key(CLIP_ID, "square")

    def test_the_profile_alone_does_not_identify_one_either(self) -> None:
        """The other half, so neither component is asserted alone. Every clip in
        a job is rendered under the same profile set."""
        other_clip = make_clip_id("01ARZ3NDEKTSV4RRFFQ69G5FBW")

        assert export_key(CLIP_ID, "vertical") != export_key(other_clip, "vertical")

    def test_it_is_the_relative_path_the_stored_export_lands_at(self) -> None:
        """13b-ii persists an export at `render/{clip_id}/{profile}.json`, and
        this key is that path without its suffix rather than a second spelling of
        one identity. Two derivations of one fact eventually disagree, and on the
        day they did there would be no way to tell which one named the operator's
        file. A storage adapter still resolves what it builds from this inside
        the job directory: a domain string is not a trust boundary."""
        assert (
            Path("render") / f"{export_key(CLIP_ID, 'vertical')}.json"
            == Path("render") / str(CLIP_ID) / "vertical.json"
        )


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


class TestTheDurationCeilingArithmetic:
    """[rev 5] A candidate's range is never trimmed to fit a profile's ceiling --
    the render result declares the overrun instead, with its magnitude, so the
    editorial call about which end to cut stays with a person."""

    def test_a_range_within_the_ceiling_is_declared_in_range(self) -> None:
        compliance = duration_compliance_of(TimeSpan(0.0, 60.0), PROFILE)

        assert compliance.kind is DurationComplianceKind.WITHIN_CEILING
        assert compliance.overrun_s == 0.0

    def test_a_range_exactly_at_the_ceiling_is_within_it(self) -> None:
        """The boundary belongs on the legal side, the same reasoning
        `check_clip_range` already applies to its own ceiling: a ceiling that
        refused the figure it advertises would be wrong by a second."""
        compliance = duration_compliance_of(TimeSpan(0.0, PROFILE.max_duration_s), PROFILE)

        assert compliance.kind is DurationComplianceKind.WITHIN_CEILING
        assert compliance.overrun_s == 0.0

    def test_a_range_past_the_ceiling_is_declared_with_its_overrun(self) -> None:
        compliance = duration_compliance_of(
            TimeSpan(0.0, PROFILE.max_duration_s + 12.0), PROFILE
        )

        assert compliance.kind is DurationComplianceKind.OVER_CEILING
        assert compliance.overrun_s == 12.0

    def test_it_carries_exactly_two_members(self) -> None:
        assert {m.value for m in DurationComplianceKind} == {
            "within_ceiling",
            "over_ceiling",
        }


class TestTheSafeArea:
    """Where a caption may not go, expressed so it survives a resolution change.

    This is the fifth no-silent-degradation axis and the least visible of them.
    The other four are discoverable by inspecting the file; a caption under a
    destination's interface overlay is correct in the file, correct in a local
    player, and wrong only in the app it was made for — which is to say, wrong
    only after it is published.
    """

    def test_it_is_expressed_in_fractions_of_the_frame(self) -> None:
        """Fractions, never pixels. A profile retargeted to another resolution
        keeps its meaning instead of silently changing where the caption lands."""
        assert SAFE_AREA.bottom == 0.18

    @pytest.mark.parametrize("value", [1.0, 1.5, -0.01])
    def test_a_margin_outside_the_frame_is_refused(self, value: float) -> None:
        """A fraction at or above 1.0 consumes the whole frame, and a negative
        one places the caption outside it. Both are arithmetic refusing a
        nonsensical value, so `ValueError` rather than a domain error — the same
        boundary `quality_of` draws for a degenerate crop."""
        with pytest.raises(ValueError):
            SafeArea(top=value, bottom=0.1, left=0.05, right=0.05)

    def test_a_zero_margin_is_allowed(self) -> None:
        """A destination with no overlay on one edge is a real configuration,
        not an unmeasured one. Zero says "measured, and it is nothing"."""
        assert SafeArea(top=0.0, bottom=0.0, left=0.0, right=0.0).top == 0.0

    @pytest.mark.parametrize(
        ("top", "bottom", "left", "right"),
        [(0.6, 0.5, 0.0, 0.0), (0.0, 0.0, 0.7, 0.4)],
    )
    def test_opposing_margins_must_leave_a_frame_to_caption(
        self, top: float, bottom: float, left: float, right: float
    ) -> None:
        """Each value is individually legal and the pair is not. A top of 0.6
        with a bottom of 0.5 leaves negative room, which no later arithmetic can
        report honestly — so it is refused where the pair is first visible."""
        with pytest.raises(ValueError):
            SafeArea(top=top, bottom=bottom, left=left, right=right)


class TestARenderProfile:
    """What a destination implies about the file, and nothing about the script."""

    def test_it_declares_output_safe_area_and_duration_ceiling(self) -> None:
        assert PROFILE.output.width == 1080
        assert PROFILE.safe_area is SAFE_AREA
        assert PROFILE.max_duration_s == 90.0

    def test_the_safe_area_has_no_default(self) -> None:
        """The rule `OutputSpec.width` set: a default here is precisely the
        failure this axis exists to prevent, wearing the shape of a convenience.
        An inherited margin is right for the profile it was measured against and
        silently wrong for every profile that inherited it."""
        field = {f.name: f for f in dataclasses.fields(RenderProfile)}["safe_area"]

        assert field.default is dataclasses.MISSING
        assert field.default_factory is dataclasses.MISSING

    def test_an_unmeasured_safe_area_is_statable_but_never_silent(self) -> None:
        """`None` means "this destination exists and nobody has measured it yet",
        which is a real state: the registry must be able to record that TikTok is
        a target before somebody sits down with the app. It is refused at
        resolution, not at construction, so the gap is recorded rather than
        unrepresentable — and it can never be reached by omission, because the
        field has no default."""
        unmeasured = RenderProfile(
            name="unmeasured",
            output=OutputSpec(width=1080, height=1920),
            safe_area=None,
            max_duration_s=60.0,
        )

        assert unmeasured.safe_area is None

    def test_it_carries_no_aspect_field(self) -> None:
        """Derived, never declared. Two fields that can disagree about one fact
        eventually will, and the derived one is the one nobody can contradict —
        the same reasoning that makes `quality_of` read width only."""
        names = {f.name for f in dataclasses.fields(RenderProfile)}

        assert not names & {"aspect", "aspect_w", "aspect_h", "aspect_ratio"}


class TestTheAspectDerivation:
    @pytest.mark.parametrize(
        ("width", "height", "expected"),
        [
            (1080, 1920, (9, 16)),
            (1080, 1350, (4, 5)),
            (1080, 1080, (1, 1)),
            (1920, 1080, (16, 9)),
        ],
    )
    def test_it_reduces_the_output_spec_to_its_lowest_terms(
        self, width: int, height: int, expected: tuple[int, int]
    ) -> None:
        """`1080x1920` is `9:16` and `1080x1350` is `4:5`. The pair the
        trajectory needs is already implied by the delivery target, so asking a
        configuration to restate it would only create a second thing to keep
        true."""
        profile = dataclasses.replace(
            PROFILE, output=OutputSpec(width=width, height=height)
        )

        assert aspect_of(profile) == expected

    def test_the_derived_aspect_drives_the_crop(self) -> None:
        """The whole point of deriving it: the pair feeds `TrajectoryPolicy`
        directly, and `crop_size_for` then yields a crop of that shape. This is
        what makes a second aspect a value rather than a code path."""
        from onevoicecut.domain.framing import TrajectoryPolicy, crop_size_for
        from onevoicecut.domain.media import FrameSize

        four_by_five = dataclasses.replace(
            PROFILE, output=OutputSpec(width=1080, height=1350)
        )
        aspect_w, aspect_h = aspect_of(four_by_five)

        crop_w, crop_h = crop_size_for(
            FrameSize(1920, 1080),
            TrajectoryPolicy(aspect_w=aspect_w, aspect_h=aspect_h),
        )

        assert (crop_w, crop_h) == (864, 1080)
        assert crop_w * aspect_h == crop_h * aspect_w


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


class TestAnExportThatNeverRendered:
    """A refusal has to be recordable without inventing what it never measured.

    `RenderedClip` carries four declarations and none has a default, because a
    clip that never stated one of them is a gap no reader can reason about. A
    render refused before ffmpeg was spawned has no honest value for any of the
    four -- so the export names the failure and carries no clip, rather than
    fabricating a quality and a tracking confidence for a file that does not
    exist. Fabricating them is precisely the silent degradation those four
    declarations were added to prevent.
    """

    def test_the_identity_no_longer_depends_on_a_rendered_clip(self) -> None:
        """`job_id` and `clip_id` move onto the export itself. Storage needs both
        to place the file, and reading them off a clip that may not exist would
        make a failed render unrecordable."""
        names = {f.name for f in dataclasses.fields(ClipExport)}

        assert {"job_id", "clip_id"} <= names

    def test_a_failed_export_carries_no_clip_and_says_why(self) -> None:
        failed = _export(
            clip=None, state=ClipState.FAILED, failure="FrameGeometryUnavailable"
        )

        assert failed.clip is None
        assert "FrameGeometryUnavailable" in (failed.failure or "")

    def test_a_finished_export_must_name_a_file(self) -> None:
        """`DONE` with no clip is an export claiming a render nobody can open."""
        with pytest.raises(ValueError):
            _export(clip=None, state=ClipState.DONE)

    def test_a_failed_export_must_say_what_went_wrong(self) -> None:
        """`FAILED` with no reason sends an operator to a log that may say
        nothing -- the gap the worker reaping already has, not one to repeat."""
        with pytest.raises(ValueError):
            _export(clip=None, state=ClipState.FAILED, failure=None)

    def test_a_clip_that_disagrees_with_the_export_is_refused(self) -> None:
        """Two places holding one identity eventually disagree, and the day they
        did there would be no way to tell which named the operator's file."""
        other = make_clip_id("01ARZ3NDEKTSV4RRFFQ69G5FAW")

        with pytest.raises(ValueError):
            _export(clip=_clip(), clip_id=other)

    def test_the_key_reads_the_export_not_the_clip(self) -> None:
        """So a refused render is addressable exactly like a finished one."""
        failed = _export(
            clip=None, state=ClipState.FAILED, failure="TrackingUnavailable"
        )

        assert export_key(failed.clip_id, failed.profile) == f"{CLIP_ID}/vertical"
