"""Run headless CLI agents under a caller-supplied capability profile.

The shared agent runtime of the ReD ecosystem (Brain decision 3c5c56e1),
released from its own repository with ``vX.Y.Z`` tags:

    uv add "headless-agents @ git+https://github.com/hawkixs/red-ha.git@<tag>"

It executes; it never decides. Which provider, which model, which MCP server
with which bearer and which tool allowlist, which credentials, which tool
guard -- every one of those is data the caller hands in through a
:class:`~headless_agents.profile.CapabilityProfile`. The package knows nothing
about any particular MCP server, about the jobs that call it, or about any
project roster, and it must never import ``brain_v42``: the guard in
``tests/unit/headless_agents/test_package_boundary.py`` refuses it.
"""

from __future__ import annotations

from .profile import CapabilityProfile, Workspace

__all__ = ["CapabilityProfile", "Workspace"]
