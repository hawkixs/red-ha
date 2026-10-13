# Vendor rule by model vendor — design

Date: 2026-10-13. Status: proposed. Follows the vibe rail (PR #9). Amends the vendor
rule of spec 0.5.0 §3.8.4 (`src/headless_agents/vendor.py`).

## 1. Problem

The vendor rule says no reviewer may share a vendor with the code's author. Today
`check_independence` compares **ha provider names**: a reviewer is refused only when it
runs the very provider that wrote a commit of the range. A provider name is not a vendor:

- `vibe` and the `mistral` preset both run Mistral models, yet pass as independent;
- `claude` and `openrouter` with `anthropic/*` both run Anthropic models;
- `codex` and `openrouter`/`opencode` with `openai/*` both run OpenAI models;
- `opencode` with `openrouter/deepseek/*` and `opencode-go/deepseek-*` are the same lab
  behind two routers.

So a review can be "independent" while its reviewer comes from the author's lab. The
independence that matters is the **lab that trained the model**, not the CLI or the
router that carried the request.

Success: a review whose reviewer and author share a model vendor is refused before any
reviewer starts; a model whose vendor ha cannot name is refused too, with a message
that says what to declare.

## 2. Decisions

- **D1 — vendor means the model's lab.** Anthropic, OpenAI, Google, Mistral, DeepSeek,
  Zhipu (GLM), Moonshot (Kimi), Alibaba (Qwen), Meta, xAI, NVIDIA… Routers (OpenRouter,
  OpenCode Zen/Go, NVIDIA NIM as a host) are never vendors.
- **D2 — one declarative table**, shipped in the package and versioned with the code,
  maps a `(provider, model)` pair to a vendor. The operator may **add** entries in
  `vendors.toml` next to `models.toml`; an entry that redefines a shipped key to another
  vendor is a configuration error (a local file can extend the table, never weaken it).
- **D2bis — `codex` is OpenAI by construction, and guarded.** ha never runs codex on
  the operator's codex configuration: every run passes `--ignore-user-config`, gets an
  ephemeral `CODEX_HOME` holding only a link to `auth.json`, passes no `--profile`, and
  its child environment (base allowlist plus `CODEX_HOME`) carries no `OPENAI_BASE_URL`.
  So `~/.codex/config.toml` cannot move a ha run to another backend; only ha's own argv
  or environment could. The guard therefore sits where the backend is chosen: a pure
  `codex_backend_is_openai(argv, env)` rejects any `model_provider`/`model_providers.*`
  override, `--profile`/`-p`, `--oss`, `--local-provider`, and any base-URL variable in
  the child environment. `run_codex` refuses to launch on such a command (no run, no
  commit, no provenance), and the vendor of a `codex` link whose recorded backend is not
  `openai` resolves to `unknown`. Provenance links for `codex` record `backend`.
- **D3 — fail closed.** A pair the table cannot resolve has the vendor `unknown`.
  `unknown` is never independent of anything: as a reviewer it is refused; as an author
  it makes independence unprovable and refuses the review. The refusal names the
  provider, the model and the key to add to `vendors.toml`.
- **D4 — vendors are derived at review time.** Provenance records the **links with their
  effective models**, not vendors, so a corrected table applies to past commits.
- **D5 — the judge is constrained too.** (Change from §3.8.4, which constrains only the
  reviewers; confirmed by the coordination.) A judge from the author's lab arbitrates
  the findings against that lab's code; the rule now applies to every slot of a review
  panel.

## 3. Design

### 3.1 The table

Package data `src/headless_agents/vendors.toml`, same schema as the operator's file:

```toml
[providers]          # how a provider names its vendor
claude  = { vendor = "anthropic" }   # the claude CLI only runs Anthropic models
codex   = { vendor = "openai" }      # guarded by D2bis
vibe    = { vendor = "mistral" }     # ha writes Vibe's config for the Mistral API
mistral = { vendor = "mistral" }     # the Mistral API serves Mistral models only
agy     = { by = "model" }           # Antigravity hosts several labs
openrouter = { by = "slug" }
opencode   = { by = "slug" }         # first segment is opencode's own provider id
nvidia     = { by = "slug" }

[orgs]               # slug organisation -> vendor
anthropic = "anthropic"
openai = "openai"
google = "google"
mistralai = "mistral"
deepseek = "deepseek"
deepseek-ai = "deepseek"
z-ai = "zhipu"
moonshotai = "moonshot"
qwen = "alibaba"
meta-llama = "meta"
x-ai = "xai"
nvidia = "nvidia"

[models]             # bare model-name glob -> vendor
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
```

The generic `openai-compat` provider has no entry: its endpoint is caller supplied, so
it is `unknown` unless the operator declares it.

### 3.2 Resolution

`model_vendor.resolve(provider, model) -> str` (`"unknown"` when unresolved), pure:

1. `vendor = "..."` → that vendor, whatever the model.
2. `by = "model"` → the first `[models]` glob matching the model; else `unknown`.
3. `by = "slug"` → split the model on `/`; walk the non-final segments from right to
   left and take the first one listed in `[orgs]` (so `openrouter/deepseek/x` → DeepSeek,
   `anthropic/claude-x` → Anthropic); if none, apply `[models]` to the final segment
   (so `opencode-go/deepseek-v4-pro` → DeepSeek); else `unknown`.
4. No provider entry, or an empty model under `by = …` → `unknown`.

The effective model of a link is the one ha already computes for a run from the link,
the role, the `--model` override and `models.toml` (`engine._models`); the rule reuses
that computation rather than duplicating its precedence.

### 3.3 Where the rule reads it

- **Provenance** (`provenance.record`) and the unconfined-writers list gain a `links`
  field: `[{provider, model}]` with effective models, beside the existing `providers`.
  Records written before this change have no `links`: their providers resolve with an
  empty model, which names the vendor for `claude`, `codex`, `vibe` and `mistral` and is
  `unknown` for the others (refusal message: the commit predates model provenance).
- **`check_independence`** takes the reviewer and judge links, resolves every vendor,
  and refuses when a reviewer vendor is `unknown`, an author vendor is `unknown`, or a
  reviewer vendor is among the authors' vendors. Messages name role, provider, model,
  vendor and the commit.
- **`VendorCheck`** documents gain `author_vendors` and per-role `vendors`;
  `from_document` keeps reading pre-change documents (fields optional on read, required
  on write).

## 4. Testing

TDD, unit only (no provider is called):

- resolution: each provider kind and each cited case — `vibe`/`mistral` same vendor;
  `claude` vs `openrouter` `anthropic/claude-*`; `codex` vs `opencode`
  `openrouter/openai/gpt-*`; `opencode` `openrouter/deepseek/deepseek-v4-pro` vs
  `opencode-go/deepseek-v4-pro`; `openrouter` `z-ai/glm-5.3-flash` → zhipu;
  `openrouter` `moonshotai/kimi-*` → moonshot; `agy` `gemini-*` → google and
  `claude-*` → anthropic; unknown org, empty model, `openai-compat` → unknown;
- the operator file: an added org resolves; a redefined shipped key is refused;
- the rule: same-vendor reviewer refused across different providers; unknown reviewer
  refused; unknown author refuses the review; independent pair passes; legacy
  provenance without `links`; the judge checked (D5);
- `VendorCheck` round-trip, and a pre-change document still reads;
- D2bis: the production codex command for every capability combination passes
  `codex_backend_is_openai`; a command carrying `model_provider`, `model_providers.x`,
  `--profile`, `--oss` or a child env with `OPENAI_BASE_URL` fails it and `run_codex`
  refuses to launch; a `codex` link recorded with a non-`openai` backend resolves to
  `unknown`.

## 5. Non-goals

- Judging independence inside red-rail's reviewer (decision c2c46a2a governs that
  layer; this spec only aligns ha's rule with its principle).
- Detecting distilled or fine-tuned models: a model's vendor is the slug's org.
- A `ha vendors` listing command (a later lot if the refusal message is not enough).

## 6. Success criteria

- `ha review` with an author on `vibe` and a reviewer on `mistral` is refused, exit 2,
  before any reviewer starts; the same with any of the cited cross-router pairs.
- A reviewer whose model the table cannot resolve is refused with a message naming the
  `vendors.toml` key to add.
- Existing reviews with genuinely independent labs pass unchanged; `make ci` and
  `rail check` green.
