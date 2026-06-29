"""Tests for OpenPro ASCII mascot helpers."""

from openagents_cli.mascot import animated_face, status_bar_prefix


def test_animated_face_returns_known_frame():
    face = animated_face()
    assert face.startswith("(") and face.endswith(")")


def test_status_bar_prefix_includes_brand():
    label = status_bar_prefix("OpenPro")
    assert "OpenPro" in label
    assert label.startswith("(")


def test_status_bar_prefix_face_only_when_no_brand():
    label = status_bar_prefix("")
    assert "OpenPro" not in label
    assert label.startswith("(")
