"""Stub for lowlighter/metrics integration.

We do NOT generate our own contribution graph. In v1 we just return an
empty block; users opt in by setting config/profile.yml -> badges.metrics
to true (then we emit the upstream SVG <img> tags).
"""
from __future__ import annotations


def render() -> str:
    return ""  # placeholder; v1 does not embed third-party metrics
