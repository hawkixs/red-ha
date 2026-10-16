# Hygiene lot — local review report

Date: 2026-10-16. Report only. No publication, merge, release, live cleanup or
implementation of the proposed retention design has been performed.

## Scope and evidence

- `9648e25`: pre-start JSON refusals, the CLI regression tests and user-facing
  documentation. Full local gates passed: 2,970 unit tests, Ruff, mypy, package
  build; rail 11/11. A started provider's exit 2 retains its full run report.
- `624ccae`: fairness test synchronization and its changelog entry. A controlled
  delay before the writer's actual queue admission failed the original test on
  R7, then passed with the correction. The admission file's 18 tests and ten
  single-CPU stress rounds passed. Full local gates passed: 2,960 unit tests,
  Ruff, mypy, package build; rail 11/11. No production locking code changed.
- `docs/specs/2026-10-16-ha-hygiene-design.md`: proposed design only. It requires
  an independent non-builder judge and operator approval before implementation.

Both full test runs reported the pre-existing fork DeprecationWarning in the
process-group tests. No live provider tests or remote CI runs were performed.
GitNexus has no index for this repository; graph evidence is degraded and local
Git/source inspection was used. A short successful stress run does not close the
separate historical expired-wait intermittence report.

## Review perspectives

| Perspective | Local score | Result |
| --- | --- | --- |
| Requirements | 8/10 | Bounded fixes meet scope; proposed design needs policy and recovery clarification |
| Architecture | 8/10 | Earlier safety inconsistencies corrected; three design questions remain |
| Code quality | 9/10 | No confirmed defect in either bounded correction |

Mean score: 8.3/10. Scores describe local review, not a merge verdict. The reviews
were requested through read-only ReD roles; the dispatch interface does not
independently attest runtime model/role selection. These reviews do not replace
the required independent vendor judge. The independent requirements and
architecture perspectives agree on the retention and archive-recovery gaps;
there is no unresolved disagreement between reviewers about the bounded fixes.

## Findings to resolve before design approval

1. **Retention threshold (high).** The proposed decision table says at least
   seven days, while the CLI section accepts every positive integer age in hours
   or days. Choose a mandatory seven-day floor or explicitly approve a
   configurable default that may be shortened. The current draft is ambiguous.
2. **New archive attempt (medium).** The draft requires an explicit re-archive
   after an interrupted or conflicting publication, but does not define its
   invocation or journal attempt-rotation protocol. Specify how the operator
   requests it and how the prior archive remains referenced without replacement.
3. **Legacy journal recovery (medium).** An unfinished legacy journal is refused
   and preserved while a new safe journal is proposed, but the journal currently
   occupies a single path per owner. Define no-clobber preservation and the link
   from a new journal, or require a separate manual recovery procedure.

The targeted descriptor-based Git administrative cleanup also introduces an
explicit Git-version/layout compatibility boundary. Its supported layouts and
missing-tree crash recovery need fixtures and independent design review; no
implementation or safety proof is claimed by the prose.

## Required next gates

Resolve the three proposed-design findings with the operator through
coordination, obtain the required independent design judgment, and record
operator approval before writing retention/deletion code. The two bounded fixes
can proceed to their separate PR gates after explicit publication authorization:
remote CI, red-rail review, the independent merge judge and approval of the exact
head. No local review grants any of those permissions.

The docs-only branch also passed `make ci` (2,960 tests plus lint, types and
build). Its initial rail check rejected two missing explicit section headings;
adding the existing success criteria and non-goals headings made rail pass
11/11. This was a document-layout correction, not a change to the proposed
retention or recovery policy. The three findings above remain open for design
review and operator arbitration.
