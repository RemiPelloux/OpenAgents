"""Tests for OpenAgents fork metadata."""

from openagents_fork import (
    DISTRIBUTION_REPO_CANONICAL,
    HERMES_UPSTREAM_REPO_CANONICAL,
    IS_REBRANDED_HERMES_FORK,
)


def test_distribution_repo_points_at_openagents_fork():
    assert DISTRIBUTION_REPO_CANONICAL == "github.com/remipelloux/openagents"


def test_hermes_upstream_is_nous_hermes_agent():
    assert HERMES_UPSTREAM_REPO_CANONICAL == "github.com/nousresearch/hermes-agent"


def test_rebranded_fork_flag_enabled():
    assert IS_REBRANDED_HERMES_FORK is True
