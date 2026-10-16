# Scheduled worker proof refresh

The user timer detects changes to installed Codex, agy and OpenCode worker CLIs every
fifteen minutes (plus up to thirty seconds of random delay and one second of timer accuracy). It runs the installed
`ha prove RAIL --stale --json` for each installed worker. Vendor auto-updaters or
an operator can update the CLIs; this timer does not invoke vendor updaters.
Unchanged passing proofs spend no provider tokens. Missing workers are skipped.
Missing, stale or failed proofs are retried, using the models already declared in
ha's `models.toml`. Proof runs spend provider quota. Proof criteria and refusal
semantics are unchanged: this helper never writes proof records or grants trust.

Each attempted proof appends `PROVE RAIL VERSION passed|failed` to
`~/.local/state/red-coordination/events.log`. A fresh successful cycle is quiet.
Command, JSON and discovery failures emit failed lines and fail the service;
other workers still run after a per-worker failure. Discovery failures use version
`unknown`. The systemd journal retains command diagnostics. An exclusive cycle
lock prevents overlapping helper cycles (manual `ha-reprove-workers` or timer);
it does not lock a separate manual `ha prove` invocation. Discovery is bounded to one minute, each proof command to one
hour, and the service to three hours and five minutes; a timeout remains a failure. A failed proof
remains refused and is retried next cycle, so a persistent failure can spend tokens
each cycle; stop the timer while investigating persistent failures.

## Install

Install a reviewed, merged wheel with `uv tool install --force /path/to/headless_agents-VERSION-py3-none-any.whl`.
This must be a regular wheel installation, not an editable checkout: isolation
proofs deliberately refuse development installs. Then copy the units:

```sh
mkdir -p ~/.config/systemd/user
install -m 0644 contrib/systemd/ha-worker-proofs.service ~/.config/systemd/user/
install -m 0644 contrib/systemd/ha-worker-proofs.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now ha-worker-proofs.timer
systemctl --user start ha-worker-proofs.service
systemctl --user status ha-worker-proofs.timer ha-worker-proofs.service
journalctl --user -u ha-worker-proofs.service -n 30
```

The service runs from the user's home using the installed sibling `ha` executable,
not whichever `ha` happens to appear first in PATH. The service PATH includes common
user CLI install locations. For other locations, use a service drop-in to set PATH;
do not put credentials into unit files. Authentication uses the user's existing
provider setup. Timers run while the user manager runs; boot-time execution without
a login requires the operator's existing user-linger policy.

Override cadence with `systemctl --user edit ha-worker-proofs.timer` and clear
`OnCalendar=` before supplying a new value. To choose workers or an events path,
override `ExecStart=` with the absolute installed entry point and its `--rails`
or `--events` options. The default events path deliberately matches the coordination
consumer and ignores the `XDG_STATE_HOME` environment variable.

Disable with `systemctl --user disable --now ha-worker-proofs.timer`.
If a cycle is running, stop `ha-worker-proofs.service` separately; systemd stops its
whole cgroup. No proof files or historical events are removed.
