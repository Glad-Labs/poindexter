"""Ken Burns motion must follow what the picture SHOWS, not its index.

Operator feedback 2026-09-23, on a long-form shot of a pipe hallway: the
image looks down a corridor to a central vanishing point and the pan drifted
to the top-left, away from the thing the composition is pointing at.

The rotation in `_KEN_BURNS_VARIANTS` is blind to content by design — it
exists so adjacent stills do not all drift the same way. The fix is not to
remove it but to let a caller that CAN see the prompt pin a preset.
"""

from __future__ import annotations

import pytest

from poindexter.plugins.media_compositor import CompositionScene
from poindexter.services.media_compositors.ffmpeg_local import (
    _KEN_BURNS_VARIANTS,
    KEN_BURNS_CENTER,
)
from poindexter.services.video_renderers.shot_list_renderer import kenburns_variant_for


class TestVanishingPointPinsTheCentre:
    @pytest.mark.parametrize(
        "prompt",
        [
            # the reported shot
            "retro-tech cyberpunk illustration, looking down a hallway of glowing pipes",
            "cinematic illustration, a server aisle receding into the distance",
            "isometric 3D, a tunnel of light converging on a single point",
            "flat vector, rows of racks stretching away",
            "one-point perspective view of a data centre",
            "a long corridor of blinking machines",
        ],
    )
    def test_a_receding_composition_zooms_to_centre(self, prompt):
        assert kenburns_variant_for(prompt) == KEN_BURNS_CENTER

    @pytest.mark.parametrize(
        "prompt",
        [
            "retro-tech cyberpunk illustration, a glowing crystal cube floating",
            "a massive monolithic server cluster with red status lights",
            "an orange folder icon emitting beams of light",
            "",
        ],
    )
    def test_everything_else_keeps_the_rotation(self, prompt):
        """The rotation is what stops adjacent stills drifting identically —
        pinning every shot to centre would trade one flaw for another."""
        assert kenburns_variant_for(prompt) is None

    def test_the_centre_preset_is_actually_centred(self):
        """KEN_BURNS_CENTER must index the centre-anchored expression, or the
        pin sends corridor shots somewhere worse than the rotation would."""
        start_x, start_y = _KEN_BURNS_VARIANTS[KEN_BURNS_CENTER]
        assert "iw/2" in start_x and "ih/2" in start_y


class TestSceneCarriesThePin:
    def test_default_is_none_so_the_rotation_still_applies(self):
        """Backcompat: every existing caller builds a scene without this
        field and must keep the by-index behaviour."""
        assert CompositionScene(clip_path="/tmp/x.png").ken_burns_variant is None

    def test_a_pin_survives_on_the_scene(self):
        scene = CompositionScene(clip_path="/tmp/x.png", ken_burns_variant=KEN_BURNS_CENTER)
        assert scene.ken_burns_variant == KEN_BURNS_CENTER
