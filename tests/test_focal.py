"""Unit tests for AI focal-point derivation (increment ②)."""

from agents import FOCAL_POINT_INSTRUCTION, apply_focal_point


class _Art:
    """Minimal duck-typed stand-in for ArtworkModel's focal fields (defaults = centered)."""
    def __init__(self):
        self.focal_x = 0.5
        self.focal_y = 0.5


def test_applies_valid_focal_point():
    a = _Art()
    apply_focal_point(a, {"focal_point": [0.3, 0.8]})
    assert a.focal_x == 0.3 and a.focal_y == 0.8


def test_clamps_out_of_range():
    a = _Art()
    apply_focal_point(a, {"focal_point": [1.5, -0.2]})
    assert a.focal_x == 1.0 and a.focal_y == 0.0


def test_missing_focal_point_leaves_default():
    a = _Art()
    apply_focal_point(a, {"title": "x"})
    assert a.focal_x == 0.5 and a.focal_y == 0.5


def test_malformed_focal_point_is_atomic_and_ignored():
    a = _Art()
    # incl. a valid-x / bad-y pair: must NOT leave a half-applied focal point.
    for bad in ([0.3], "0.3,0.8", {"x": 1}, [0.3, "y"], None, []):
        apply_focal_point(a, {"focal_point": bad})
        assert a.focal_x == 0.5 and a.focal_y == 0.5


def test_instruction_is_wired():
    assert "focal_point" in FOCAL_POINT_INSTRUCTION
    assert "normalized" in FOCAL_POINT_INSTRUCTION.lower()
    # both vision passes pull the same instruction text
    import agents
    import curator
    assert curator.FOCAL_POINT_INSTRUCTION is agents.FOCAL_POINT_INSTRUCTION
