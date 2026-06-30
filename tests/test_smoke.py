"""Baseline tests so CI is green on the empty scaffold.

Replace/expand these as real modules land — see docs/04-roadmap.md.
"""

import pytest

import conciergent


def test_version_is_exposed():
    assert isinstance(conciergent.__version__, str)
    assert conciergent.__version__


@pytest.mark.smoke
def test_smoke_placeholder():
    # Real smoke tests will hit live Slack / LINE / MCP endpoints.
    assert True
