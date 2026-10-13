# Vendor rule by model vendor — design

Date: 2026-10-13. Status: approved (design review by codex, rounds 1-3 and closure verification), with
amendment A1 from the plan review. Follows the vibe rail (PR #9). Amends the
vendor rule of spec 0.5.0 §3.8.4 (`src/headless_agents/vendor.py`).

## 1. Problem

The vendor rule says no reviewer may share a vendor with the code's author. Today
`check_independence` compares **ha provider names**: a reviewer is refused only when it
runs the very provider that wrote a commit of the range. A provider name is not a vendor:

- `vibe` and the `mistral` preset both run Mistral models, yet pass as independent;
- `claude` and `openrouter` with `anthropic/*` both run Anthropic models;
- `codex` and `openrouter`/`opencode` with `openai/*` both run OpenAI models;
- `opencode` with `openrouter/deepseek/*` and `opencode-go/deepseek-*` are the same lab
  behind two routers.

Conversely, two `opencode` links running DeepSeek and Anthropic models are refused today
although their labs differ. The independence that matters is the **lab that trained the
model**, not the CLI or the router that carried the request.

Success: a review whose reviewer or judge shares a model vendor with the author is
refused before any panel member starts; a model whose vendor ha cannot name is refused
too, with a message that says what to declare.

## 2. Decisions

- **D1 — vendor means the model's lab.** Anthropic, OpenAI, Google, Mistral, DeepSeek,
  Zhipu (GLM), Moonshot (Kimi), Alibaba (Qwen), Meta, xAI, NVIDIA… Routers and hosts
  (OpenRouter, OpenCode Zen/Go, NVIDIA NIM) are never vendors.
- **D2 — one shipped table, extended only where it says `unknown`.** A declarative table
  shipped in the package and versioned with the code resolves a `(provider, model)` pair.
  The operator's `vendors.toml` (next to `models.toml`) holds **exact
  `(provider, model) → vendor` pairs only**, consulted **only when the shipped table
  resolves the pair to `unknown`**. An operator entry can therefore never change a
  resolution the shipped table makes; an entry for a pair the shipped table already
  resolves is a configuration error (it is dead, and naming it is better than ignoring
  it).
- **D2bis — a fixed-vendor CLI rail is guarded against another backend.** `claude`,
  `codex` and `vibe` take their vendor from the provider, which holds only while the
  rail talks to its vendor's own API. Measured at `43c8d80`:
  - `codex`: `--ignore-user-config`, an ephemeral `CODEX_HOME` holding only a link to
    `auth.json`, no `--profile`; but its child environment is the operator environment
    minus parent-session names (`engine.operator_environment`, `codex.py` `run_codex`),
    so `OPENAI_BASE_URL` reaches it;
  - `claude`: same, so `ANTHROPIC_BASE_URL` reaches it;
  - `vibe`: an environment built from scratch and a configuration ha writes for the
    Mistral API (`vibe.child_environment`), so no ambient variable redirects it.

  Each fixed-vendor rail declares its **backend switches**: the argv keys and child
  environment variables that can point it elsewhere (`codex`: `model_provider`,
  `model_providers.*`, `--profile`/`-p`, `--oss`, `--local-provider`,
  `OPENAI_BASE_URL`, `OPENAI_API_BASE`; `claude`: `ANTHROPIC_BASE_URL`). The
  `CLAUDE_CODE_*` provider selectors (`CLAUDE_CODE_USE_BEDROCK`, `…_VERTEX`) are not
  switches: `engine.operator_environment` already removes every `CLAUDE_CODE_*`
  variable before any child sees it, and that filter stays unchanged. A pure
  `backend_switches(provider, argv, environment)` names the switches present, applied
  to the **effective child environment** (after `operator_environment` and the rail's
  own filtering) and the argv the rail will run. A link with a switch present has the
  vendor `unknown`: in a review panel this refuses the review at admission, before any
  member starts; in a write, provenance records the switch (§3.3) so the commit's author
  is `unknown`. ha adds no stripping of base-URL variables: they may be the operator's
  deliberate setup for other work.
- **D3 — fail closed.** A pair the table cannot resolve has the vendor `unknown`.
  `unknown` is never independent of anything: as a reviewer or judge it is refused; as an
  author it makes independence unprovable and refuses the review. The refusal names the
  provider, the model and the `vendors.toml` line to add.
- **D4 — vendors are derived at review time from recorded links.** Provenance records
  every link of the writing role with its effective model and its backend switches, not
  vendors, so a corrected table applies to past commits. Records written before this
  change carry no links: their authors are `unknown` (the backend of those runs was
  never recorded), which refuses a review whose range holds one. Such ranges are short
  lived (a review covers `merge-base..head`); the refusal says the commit predates model
  provenance.
- **D5 — the judge is constrained too.** (Change from §3.8.4, which constrains only the
  reviewers; confirmed by the coordination.) A judge from the author's lab arbitrates
  the findings against that lab's code; the rule applies to every slot of a review
  panel.

## 3. Design

### 3.1 The shipped table

Package data `src/headless_agents/vendors.toml`:

```toml
[providers]   # how each provider names its vendor
claude     = { vendor = "anthropic" }   # guarded by D2bis
codex      = { vendor = "openai" }      # guarded by D2bis
vibe       = { vendor = "mistral" }
mistral    = { vendor = "mistral" }     # the Mistral API serves Mistral models only
agy        = { grammar = "bare" }       # Antigravity hosts several labs
openrouter = { grammar = "org" }        # "<org>/<model>"
nvidia     = { grammar = "org" }        # "<org>/<model>"
opencode   = { grammar = "opencode" }   # "<opencode provider>/<rest>"

[orgs]        # the org segment of an "org" grammar -> vendor
anthropic = "anthropic"
openai = "openai"
google = "google"
mistralai = "mistral"
deepseek = "deepseek"
deepseek-ai = "deepseek"
z-ai = "zhipu"
moonshotai = "moonshot"
qwen = "alibaba"
meta = "meta"
meta-llama = "meta"
x-ai = "xai"
nvidia = "nvidia"

[bare]        # model-name glob -> vendor, for "bare" grammars only
"claude-*" = "anthropic"
"gpt-*" = "openai"
"o[0-9]*" = "openai"
"gemini-*" = "google"
"mistral-*" = "mistral"
"devstral-*" = "mistral"
"magistral-*" = "mistral"
"codestral-*" = "mistral"
"deepseek-*" = "deepseek"
"glm-*" = "zhipu"
"kimi-*" = "moonshot"
"qwen*" = "alibaba"

[opencode]    # opencode's own provider id -> how to read the rest
openrouter = { grammar = "org" }
nvidia = { grammar = "org" }
opencode-go = { grammar = "bare" }
opencode = { grammar = "bare" }         # OpenCode Zen
anthropic = { vendor = "anthropic" }
openai = { vendor = "openai" }
google = { vendor = "google" }
mistral = { vendor = "mistral" }
deepseek = { vendor = "deepseek" }
```

The generic `openai-compat` provider has no entry: its endpoint is caller supplied, so it
is `unknown` unless the operator declares the exact pair.

### 3.2 Resolution

`model_vendor.resolve(provider, model, switches) -> str`, pure; `"unknown"` whenever a
step below does not name a vendor:

1. A backend switch present (D2bis) → `unknown`.
2. `vendor = "…"` → that vendor, whatever the model.
3. `grammar = "org"` → the model must be exactly `<org>/<name>` with both segments
   non-empty; `<org>` must be in `[orgs]`. No inference from the name: an unlisted org
   is `unknown` even when the name looks like a known family.
4. `grammar = "bare"` → the model must contain no `/`; the first `[bare]` glob matching
   it names the vendor.
5. `grammar = "opencode"` → split once on `/`: the first segment must be in
   `[opencode]`; its entry either names the vendor or gives the grammar the rest is read
   with (steps 3–4).
6. Only when steps 2–5 give `unknown` (never after step 1), the operator's exact pair, if
   any.

So: `openrouter:z-ai/glm-5.3-flash` → zhipu; `openrouter:unlisted/gpt-x` → unknown;
`nvidia:meta/llama-3.1-405b-instruct` → meta;
`opencode:nvidia/meta/llama-3.1-405b-instruct` → meta;
`opencode:openrouter/deepseek/deepseek-v4-pro` and `opencode:opencode-go/deepseek-v4-pro`
→ deepseek; `opencode:anthropic/claude-x` → anthropic; `agy:gemini-3.8-flash-high` →
google.

### 3.3 Where the rule reads it

- **Effective models are the panel's snapshot.** A slot's models are frozen once in its
  `SlotPlan` (`engine._slot_plan`, used for execution); admission reads the same
  snapshot for every reviewer and judge link, and a write records the snapshot of its
  role. Nothing recomputes `_models` against configuration that may have changed.
  Review workflows keep refusing `--model` overrides.
- **Provenance** (`provenance.record`) and each writer of the unconfined-writers list
  gain `links`: `[{provider, model, switches}]`, one per link of the role, in the order
  of `providers`. A present `links` must correspond one to one with `providers` (same
  providers, same order), each `model` a string and each `switches` a list of non-empty
  strings; otherwise the record is malformed and refuses the review, as a malformed
  `providers` does today. Only an **absent** `links` is a legacy record (D4).
- **`check_independence`** takes the commits and the panel slots (reviewers and judge)
  with their links, resolves every vendor, and refuses when any panel vendor is
  `unknown`, any author vendor is `unknown`, or a panel vendor is among the authors'
  vendors. Messages name the role, provider, model, vendor and commit.
- **`VendorCheck` documents, new shape (`"rule": "model-vendor"`):** a vendor is
  recorded in exactly one place, on the link it was resolved for: each link is
  `{provider, model, switches, vendor}`. Each commit carries its `links`; `panel` maps
  each role (reviewers and judge) to its links; `authors` is derived evidence, the
  sorted union of the commits' link vendors. There is no separate per-commit or
  per-role vendor list that could contradict the links. The document also records
  `table_digest` (shipped table plus operator pairs) as evidence of what it was judged
  against; `from_document` does not re-resolve with today's table. It validates a new
  document by vendor: every link carries one non-empty vendor, `authors` equals the
  sorted union of the commits' link vendors, no author vendor and no panel vendor is
  `unknown`, no panel vendor is in `authors`, the links of each commit match its
  `providers` one to one, `panel` is non-empty and every panel role has at least one
  link. A commit may have no links only when it is a presumed hand-written commit
  (`made_by: hand`, as `_attribution_ha_makes` allows today).
  A document without `rule` is a legacy document and keeps today's provider validation,
  so old reports, `ha show` and `--findings` (`reviews.load_result`) still read.

## 4. Testing

TDD, unit only (no provider is called):

- resolution, one test per row of §3.2's examples, plus: `vibe` and `mistral` same
  vendor; `claude` vs `openrouter:anthropic/claude-*`; `codex` vs
  `opencode:openrouter/openai/gpt-*`; `openrouter:moonshotai/kimi-*` → moonshot;
  `agy:claude-*` → anthropic; `openrouter:deepseek` (no slash) → unknown;
  `agy:google/gemini-x` (slash in a bare grammar) → unknown; empty model under any
  grammar → unknown; `openai-compat` → unknown;
- the operator file: an exact pair resolves an otherwise unknown model; a pair the
  shipped table already resolves is a configuration error; an operator pair never wins
  over a backend switch;
- backend switches: the production `codex` and `claude` commands with a clean
  environment have none; `OPENAI_BASE_URL`, `-c model_provider=…`,
  `-c model_providers.x.base_url=…`, `--profile`, `--oss` for codex and
  `ANTHROPIC_BASE_URL` for claude are each named; a panel
  member with a switch is refused at admission before any member starts;
- provenance: `links` recorded from the role's snapshot with switches; malformed or
  incomplete `links` (empty, a provider of `providers` missing, reordered, non-string
  model) refuse; absent `links` is legacy and its authors are `unknown`;
- the rule: same-vendor reviewer refused across different providers; same provider,
  different labs (`opencode` DeepSeek author, `opencode` Anthropic reviewer) passes;
  unknown reviewer refused; unknown author refuses; judge from the author's lab refused;
  every link of a fallback chain checked;
- `VendorCheck`: new-shape round-trip including the same-provider/different-lab case;
  inconsistent vendor evidence rejected; rejected through both `from_document` and
  `reviews.load_result`: an `unknown` author vendor, an empty `panel`, a panel role with
  no links, a non-hand commit without links, `authors` differing from the union of the
  commits' link vendors (an Anthropic link under `authors = ["openai"]`), a link without
  a vendor; a legacy document still reads;
- snapshot integration: the models of a chain link, of the role and of `models.toml`'s
  default reach admission unchanged from the `SlotPlan`; an author's `--model` override
  reaches provenance; changing `models.toml` after planning does not change what
  admission or provenance records; the existing refusal of `--model` on a review
  workflow keeps passing;
- effective environment: an operator environment carrying `ANTHROPIC_BASE_URL` (or
  `OPENAI_BASE_URL`) refuses a review whose panel has `claude` (or `codex`), tested from
  `operator_environment` through admission; `CLAUDE_CODE_USE_BEDROCK` in the operator
  environment is filtered out and does not refuse.

## 5. Non-goals

- Judging independence inside red-rail's reviewer (decision c2c46a2a governs that
  layer; this spec only aligns ha's rule with its principle).
- Detecting distilled or fine-tuned models: a model's vendor is its declared org or
  family.
- Stripping backend variables from the child environment.
- A `ha vendors` listing command (a later lot if the refusal message is not enough).

## 6. Success criteria

- `ha review` with an author on `vibe` and a reviewer or judge on `mistral` is refused,
  exit 2, before any panel member starts; the same for every cross-router pair of §1.
- A panel member whose model the table cannot resolve, or whose rail carries a backend
  switch, is refused with a message naming the cause and, for an unresolved model, the
  `vendors.toml` line to add.
- Reviews with genuinely independent labs pass; old review documents still read;
  `make ci` and `rail check` green.

## Amendments

### A1 — argv switches are refused where the argv is built (plan review, round 1)

D2bis named one detector over "the argv the rail will run" and the environment. The argv
of a ha run is built by ha's own code from fixed parameters (`build_codex_command`); no
operator state reaches it. So an argv switch is not a property of a link to record, it
is a defect of ha: `build_codex_command` refuses to return an argv carrying one
(`model_provider`, `model_providers.*`, `--profile`/`-p`, `--oss`, `--local-provider`),
so no run, no commit and no provenance can ever carry it, writes included. The detector
recorded in links and checked at admission, `backend_switches(provider, environment)`,
reads the effective child environment only. `claude`'s argv has no backend flag ha could
pass, so it has no argv check.

