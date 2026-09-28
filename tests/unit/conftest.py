import pytest


@pytest.fixture(autouse=True)
def _no_real_group_watcher(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Rails started with a fake Popen must never get a real group watcher. A watcher attached to a fake provider pid would SIGKILL whatever process group owns that number on this machine if the test died before releasing it. Tests of the watcher itself opt in with real_watcher."""
    if request.node.get_closest_marker("real_watcher") is not None:
        return
    from headless_agents import procgroup

    monkeypatch.setattr(procgroup, "start_watcher", procgroup.Lifeline.unwatched)
