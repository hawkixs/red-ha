# Run retention and cleanup safety — proposed design

Date: 2026-10-16. Status: proposed; independent design review and operator
approval are required before implementation. This document does not authorize
deletion, a scheduled job, a release, or changes to live state.

## 1. Problem

Terminal runs retain complete worktrees, including disposable environments and
build output. Existing `ha clean` removes a run directory rather than retaining
its measurement evidence. A missing source repository is now refused cleanly,
but there is no supported way to retire its residue. Cleanup also has remaining
journal validation, ordering, and filesystem race hardening work.

### Success criteria

Success means reclaiming disposable worktree storage while keeping run evidence,
refusing active or uncertain state, and recovering interrupted cleanup without
overwriting another file or following a substituted path outside the pinned tree.

Scope: retention, orphan retirement, cleanup safety, and refusal vocabulary.

### Non-goals

Provider environment passthrough, isolation-proof relaxation, provider warning
filtering, release installation, and a background scheduler are outside scope.

## 2. Proposed decisions

These are recommendations for approval, not already approved product behavior.

| Choice | Recommendation | Alternatives and trade-off |
| --- | --- | --- |
| Trigger | Explicit `ha gc --older-than 7d`, preview by default; `--apply` mutates | Cleanup after each run adds latency and hidden deletion; an internal scheduler adds lifecycle and operational scope |
| Age | At least seven days after a trusted terminal observation | Creation time can expire a run that only just finished; filesystem mtime is not a terminal-state witness |
| Retained data | Remove only the eligible owner's `wt/`; keep reports, prompts, patches, logs, registry and lineage evidence | Whole-run deletion loses evidence; archiving every generated environment duplicates the storage being reclaimed |
| Missing source | Explicit `ha clean RUN --orphan`, never an automatic GC fallback | Treating any git failure as an orphan can misclassify a moved or tampered repository |
| Filesystem operations | Pinned directory descriptors, no-follow opens, no-clobber publication and recoverable steps | Path checks followed by rename or recursive deletion leave substitution windows |

`ha gc` is a new command proposed here; it does not exist in the current release.
`--older-than` accepts a positive integer followed by `h` or `d`; the default is
`7d`. Invalid, zero, overflowing or negative ages refuse before inspection.
`ha clean --orphan` is mutually exclusive with `--force` and `--keep-branch`.

## 3. Design

### 3.1 Eligibility and age

Use registry and lineage state as authority, never `run.json` alone. Eligible
statuses are `answered`, `failed`, `committed`, `no_change`, `approved`, `changes`
and `budget_exhausted`. A lineage is eligible only if every member is terminal,
there is no pending write, no compromised state, and no applicable quarantine.
An incomplete cleanup journal, unreadable record or occupied lifecycle lock
excludes the whole owner. `waiting`, `running`, inferred `incomplete` and unknown
states never qualify. Enumerate each shared worktree once, through its owner.

Current registry entries have no authoritative terminal timestamp. Add a nullable
terminal-observation timestamp to registry state. The engine records it under the
existing run lock when publishing terminal status. For legacy entries, the first
mutating GC inspection may record the current UTC time only after validating
terminal state under the required locks. It must not backdate from `created_at`,
report contents or mtimes. Preview remains read-only and reports missing age
evidence. A first application may therefore reclaim nothing for legacy entries;
the next eligible application can do so after the full retention period.

Compute age against one UTC timestamp captured for the command. Future,
malformed or missing timestamps do not qualify. A change in lineage membership,
pending state or terminal observation invalidates the preview. Application
rechecks every condition under locks; a preview is never deletion authority.
For a shared worktree, its age starts at the maximum terminal-observation
timestamp across all current members, including the most recent continuation.
One young or age-unverified member excludes the entire owner.

### 3.2 Worktree-only retention

Preserve the complete run directory except its owner's `wt/`: `run.json`,
`prompt.md`, `change.patch`, `steps/`, all provider logs and any other artifacts
remain. Preserve registry entries, lineage records, cleanup journals and any
existing residue archives or commit bundles. Never remove the source branch.
Do not claim reclaimed bytes until removal has actually succeeded.

Before removal, require the expected worktree identity and a healthy, pinned
source git directory. Refuse unexpected tracked changes or nonignored untracked
files; a caller must preserve those through explicit cleanup instead. Ignored
worktree contents are disposable under this command, including environments and
build output; the preview and help must say so. Generated directories are not
selected by guessed names. A missing or tampered source requires the explicit
orphan procedure, not a more permissive retention path.

All tree removal uses the fd-based primitive below, including healthy worktrees.
Administrative deregistration is a separate fd-based operation on the single
recorded `<common_git_dir>/worktrees/<registration_id>` directory. Before tree
removal, pin that directory through the trusted common-dir descriptor and record
its identity and complete inventory. Validate that its `gitdir` names the
expected worktree's `.git`, `commondir` names the pinned source, and `HEAD`
matches the recorded lineage branch/tip (or detached review head). Refuse an
unrecognized layout, lock file, replacement or mismatch. Support only layouts
covered by fixtures from supported Git versions; no recursive Git command and
no unscoped prune may substitute for this operation.

Once the tree is absent, remove only those inventoried administrative files and
the recorded registration directory through pinned descriptors. Keep refs and
objects untouched. Journal the tree and administrative stages separately. After
a crash, an absent inventoried administrative entry means done; a present one
must retain its recorded identity/content, otherwise refuse. An absent target
registration is already done only if this operation's durable journal recorded
that exact registration beforehand. Sibling registrations are never candidates.
This deliberately adds a Git-layout compatibility boundary and needs independent
design approval plus missing-tree crash fixtures before implementation.

Record an idempotent worktree-retention receipt in state, naming the owner,
validated members, identity, terminal observation, action time and outcome. Do
not mark the entire run cleaned: its reports still exist. Future `--continue`
must refuse a retained-away worktree before any provider starts. It names the
kept branch and evidence; it must not silently reconstruct a workspace.

### 3.3 Explicit orphan retirement

Require both the recorded source repository and its recorded common git directory
to be absent, with absence distinguishable from permission errors or unreadable
ancestors. An existing replacement at either location refuses. A repository path
missing while its common directory still exists is not an orphan. Do not execute
git in a surviving worktree or interpret its `.git` as new authority.

Resolve the owner and members from validated registry, lineage and journal
records. Pin the configured state and cache roots and validate all descendant
components without following symlinks. Restrict worktrees to the recorded
owner's run directory beneath the configured runs root; refuse external custom
run directories in orphan mode. An occupied member lock or ambiguous ownership
refuses before mutation. Any operator-wide quarantine that names unrelated runs
is preserved; never clear it as a side effect of retiring this owner.

Archive the surviving worktree before deleting it, including untracked and
ignored files; store symlinks as links and never follow them. Keep reports and
logs in place. Archives live in the owner's cleanup-residue directory and are
published without replacement. If saving residue fails, including insufficient
space, delete nothing. Reclaiming this orphan's archive later requires a separate
explicit policy; this command does not promise an immediate disk-space saving.
Before publication, persist the archive's unique attempt name, staged inode and
digest in the journal and fsync both. Publish by no-clobber link, then record its
completion. A crash after publication but before completion may adopt the
existing destination only when it is the recorded inode with the recorded
digest. Otherwise retain it, refuse, and require a new explicit archive attempt
with a new name. Never overwrite or discard an interrupted attempt's archive.

The orphan journal records git registration and branch handling as unavailable,
not verified deleted. State retirement uses the existing fixed lifting order:
stale intent before its matching quarantines, member records and lineage state,
then lock files last. Never lift a quarantine containing unrelated membership.
Do not invent a bundle when the source object database is absent. Record that
commits may be unrecoverable and require this warning before application.

### 3.4 Descriptor-based deletion and publication

Preserve existing lock ranks: lifecycle first, global exclusive admission next,
then lineage registry and lineage locks. Acquire needed member lifecycle locks
in stable run-ID order before global admission, without waiting on an active
member. Discover membership first, then recheck it under registry/lineage locks;
if it changed, release and restart inspection without mutation. Never acquire a
new lifecycle lock while holding a higher-rank lock. GC handles one owner at a
time. Recheck quarantine before writing a destructive journal.

Pin trusted roots once with directory descriptors. Walk descendants using
descriptor-relative operations and `O_DIRECTORY | O_NOFOLLOW`; inspect entries
with no-follow metadata. Record device/inode identities and refuse substitutions
or mount transitions. Do not use `resolve()`, `realpath()`, or a reconstructed
absolute path to reopen a checked descendant during mutation.

Persist an inventory with relative names, types, identities and the necessary
content digests before deletion. In orphan/force mode every removed entry must
be covered by a complete verified archive. In retention mode, no duplicate
worktree archive is required: the inventory explicitly classifies ignored data
as disposable, and tracked files must be recoverable from the retained source
branch; unexpected modifications or nonignored untracked files refuse.

Delete children relative to their pinned parent descriptors. Never traverse a
symlink; unlink a link itself only when the inventory authorizes that entry.
On resume an inventoried absent entry is accepted as already removed, including
a crash before its progress update. A present entry must retain its recorded
identity and digest. New or replaced entries observed by these checks refuse.
Directories whose
children were removed may have changed mtimes; compare their pinned identity
and remaining entries instead. Detect other changes and refuse with the
inventory, any archive and journal intact. This inventory is the durable
authority for partial-deletion recovery; do not rebuild it from a partial tree.
Before removing the root directory entry, confirm its recorded identity through
the pinned parent. Refuse unexpected identity instead of deleting a replacement.
The guarantee covers cooperating ha processes respecting these locks and
confinement against traversal through substituted symlinks. Identity-check then
`unlinkat` is not an atomic compare-and-delete operation: there is no promise to
reject every replacement by an uncooperative writer between those syscalls.
Concurrent adversarial same-user mutation is outside the guarantee, even if that
writer does not alter the process or trusted state. Deployment must keep trusted
parents/state outside provider write scopes. Tests must distinguish detected
substitutions and outside-tree confinement from this excluded race, rather than
claim impossible atomic deletion semantics.

For lifting files, replace check-then-rename with descriptor-relative hard-link
creation using no replacement, then unlink the source after validating the
recorded identity. Refuse nonregular source files. On resume, two names referring
to the same recorded inode mean publication happened before source unlink; finish
that step. An existing target with another identity always refuses, even if its
bytes match. If hard links are unsupported or cross-device, refuse; do not fall
back to overwriting rename. Fsync affected files and parent directories before
marking a journal step complete.

### 3.5 Journal and step invariants

Introduce a versioned mode for retention versus orphan/ordinary retirement;
replay must use its original mode and options. Completed valid legacy journals
may answer already done without mutation. In-progress legacy journals lack the
inventory and inode witnesses and must refuse with a manual-recovery diagnostic;
never resume their old path-based deletion or silently upgrade them. Recovery
preserves the old journal and archives, and requires an explicit new inspection
and archive attempt before a versioned safe journal is created. Validate versions
as integers excluding booleans, ASCII timestamps, canonical bounded relative
paths and exact file categories. Lift sources are restricted to `runs/`,
`lineages/` and `quarantine/`; archive destinations are restricted to the owner's
cleanup-residue directory. Reject traversal, absolute paths, duplicate records,
unknown fields, malformed digests and directory records where files are required.

Steps are immutable mappings. Each callable step enforces its own predecessors.
For retention: persist eligibility/inventory, remove the disposable tree,
deregister its administrative directory, then publish the retention receipt;
there is no archive, branch deletion or state lifting. For orphan/force:
persist inspection, save verified residue (and a bundle only where possible),
remove the tree, perform applicable deregistration, then handle the branch and
lift state in the recorded order. A skipped inapplicable step is explicitly
recorded, never inferred from an error. A completed validated journal returns already done
before checking activity of records it no longer owns. A `prunable` worktree
stanza with a present path is ambiguous and refuses; a genuine registered worktree
whose `.git` was deleted follows the archive-backed safe path, not that refusal.

Propagate bounded, redacted failure diagnostics. A changed worktree in retention
mode must suggest preserving additions and explicitly starting a new eligibility
inspection/inventory, without adopting new files into an in-progress deletion.
In archive modes it must suggest an explicit re-archive operation that retains
the previous archive and records a new digest before deletion can resume. Lookup and
hash errors become a typed unreadable-state refusal, not an uncaught traceback.
No hash/parser exception may echo credential contents.

### 3.6 Refusal taxonomy and compatibility

The direct JSON correction preserves `{code: 2, reason: <diagnostic>}` for
pre-start run refusals. It does not make the diagnostic a stable identifier.
Under this spec, add a separate `reason_code` field to structured refusals;
preserve `code`, diagnostic `reason`, stderr, exit status and started-run reports.
Do not parse human text to derive a reason code: classify at the originating
typed error. Unknown classified errors use `refused`, not a guessed subtype.

Initial identifiers: `invalid_arguments`, `invalid_configuration`,
`isolation_proof_stale`, `admission_timeout`, `active_run`, `pending_lineage`,
`quarantined`, `state_unreadable`, `age_unverified`, `worktree_retired`,
`source_missing`, `source_replaced`, `path_unsafe`, `residue_changed`,
`archive_failed`, `target_exists`, and `refused`. Reserve no provider-specific
remedy/fallback fields until their contract is specified separately.

GC preview lists candidates and exclusions without reading file contents into
its output. Apply returns `0` when all selected eligible owners succeeded or
were already done, `1` on a refused/failed owner, and `2` for invalid invocation.
Noneligible runs are skips, not failures. An owner selected by preview that
becomes unsafe during application is a refusal, not a successful skip. Continue
to other independent owners after an owner failure and return nonzero. Existing
cleanup exit codes retain their current meanings.

## 4. Validation and approval

Failure-first tests must cover terminal versus pending/active/quarantined owners,
shared lineage deduplication, unknown/future age, legacy observation and age
boundaries, preview immutability, continuation after retention, source deletion
and replacement, denied ancestor access, archives failing or filling the disk,
and metadata/log/branch preservation. No live home directory or real run is a
test fixture.

Use barriers to substitute symlinks, swap parent/root entries, add files after
archive and race target creation between publication steps. Outside sentinel
files must retain both contents and identity. Interrupt after every durable
step and after link-before-unlink; resume must neither overwrite a target nor
delete unarchived data. Cover every journal validator and direct-step
precondition, registry-lock timeout, prunable registration, deleted `.git`, and
completed journals with stale member activity. Assert originating typed reason
codes, preserved started-run reports and redacted parser errors.

Approval must explicitly accept the seven-day trusted-observation clock, preview
default plus `--apply`, ignored worktree data being disposable, the separate
orphan archive policy and the proposed CLI/JSON additions. An independent
non-builder judge reviews this document before the operator's approval. After
approval, implementation planning can split retention, orphan retirement,
filesystem primitives, journal hardening and refusal typing into bounded PRs.
This document alone starts none of those implementations or scheduled actions.
