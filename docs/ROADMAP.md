# Roadmap

What's done, what's next, what's parking-lot. Honest scope, no marketing.

---

## Done (as of 2026-05-15)

- **Adapter trained** (`gemma-andy:e4b-v2-2-3-q8_0`): SFT on ~33k records,
  4125 training steps, JSON-only contract validated on internal eval.
- **Public release** of the LoRA adapter + integration docs + reference
  examples.
- **Field validation** on a focused subset of 9 critical Tier 1 variants
  (45 calls, n=5 per variant) — see
  [`MITIGATION_RESULTS.md`](./MITIGATION_RESULTS.md).
- **5-layer Hermes-side policy** (scope filter, ambiguity detection,
  surface normalization, narrow allowed_tools, multi-step decomposition)
  — see [`HERMES_MITIGATION_V2.md`](./HERMES_MITIGATION_V2.md) and
  [`mitigation/`](../mitigation/) for reference impl.
- **Reference deployment** with Ollama (Q8_0 GGUF, ~8 GB VRAM), tested
  on RTX 3090.
- **Schema v2** (68 canonical tools, 42 executor-supported in the
  reference Mineflayer bot).

---

## In progress

- **Field-test gate with end-users**: the broader system has a separate
  field-test cycle pending where actual children interact through
  Hermes ↔ Gemma-Andy ↔ Mineflayer end-to-end. Until that signal is
  collected, the policy is validated on synthetic + instrumented runs only.
- **Curated training data**: working on a `unified-test-dataset` that
  spans the full schema v2 surface (~500-1000 records) for cross-version
  comparison post-retrain.

---

## Next iteration (post-field-test)

### v2.2.4 — dataset rebalance
The current SFT distribution measured **EN 90.6% / ES 0.1%** on
`high_level_command`. That's the root cause of the surface-form bias the
upstream policy currently mitigates. Next training cycle:

- Mirror the dataset to Spanish (target 50/50 EN/ES).
- Add a narrative-prose slice (current: 0.3% — explains why polite
  phrasings collapse).
- Add an explicit recovery slice with verified cause-effect pairs
  (current `previous_error` is populated but the model doesn't learn
  to act on it; needs supervised pairs of failed-call → correct-recovery).
- Re-evaluate against Fede's `iter4-e` suite as held-out external benchmark.

If v2.2.4 closes the upstream policy gaps at the model level, the
upstream policy can shrink (or be retired) for those cases — but the
upstream policy's composition pattern remains useful for whatever new
gaps the next model exposes.

### Tier 2 primitives
The current "Tier 1 reliable" set is ~12 primitives (movement, mining,
inventory_query, memory, signals, eat, scan). Tier 2 candidates:

- `place_block` (currently fails ~50% in basic tests due to coordinate
  resolution issues — needs dispatcher work first).
- `craft_item` chain (requires proximity to `crafting_table`; currently
  works only when bot is already there).
- `attack_entity` and combat (high-risk; needs guardian policies + visual
  validation cycle).
- `build_blueprint` (architectural decision pending — schema v2 marks
  it `executor_supported: true` but reference bots may not have a
  blueprint executor; currently gated as `consumer_overrides`).

### Better executor
Several gaps are bot-side, not model-side:

- The reference bot's `pickup` action filters by `e.name === 'item'`
  which doesn't match Mineflayer 4.23 dropped-item entities. Needs a
  one-line filter fix.
- The reference bot's pathfinder times out at 15 s without progress
  signal — long-distance `goto` reports false failures. Either move to
  background pathfinding or expose progress.
- `place_block` against Mineflayer's `bot.placeBlock` has timing-finicky
  behavior (server `blockUpdate` ack timeouts); pillaring up requires a
  custom skill rather than the default action.

These belong in
[`nicoechaniz/DaemonCraft`](https://github.com/nicoechaniz/DaemonCraft)
rather than in this adapter, but the adapter's downstream reliability
depends on them.

---

## Parking lot (lower priority, not planned)

- **`pillar_up` action** in the reference bot — initial implementation
  attempted but `bot.placeBlock` timing made it unreliable.
- **Semantic checks** in the runner — current execution-aware scoring
  catches "the tool was dispatched" but not "the world reflects the
  intent" (e.g., did the apple really land on the floor or did the bot
  pick it back up?).
- **LLM-based classifiers** for the policy upstream — current regex+keyword
  is sufficient for closed test sets but a deployed Hermes can use its
  own LLM for L2/L3/L4 classification with better generalization.
- **Multi-bot multi-agent scenarios** — current focus is a single bot
  with a single human. Multi-bot worlds raise interesting policy
  questions (whose intent does the bot serve?) that aren't addressed.

---

## How to suggest changes

This adapter has a public release license. Issues, PRs, and field
reports are welcome on the GitHub repository. For integration-specific
questions, the integration docs (
[`INTEGRATION_GUIDE.md`](./INTEGRATION_GUIDE.md),
[`OLLAMA_USAGE.md`](./OLLAMA_USAGE.md),
[`HERMES_MITIGATION_V2.md`](./HERMES_MITIGATION_V2.md)
) are the entry points.

Field-test signal from real deployments is the most valuable input —
the existing measurement framework
([`MITIGATION_RESULTS.md`](./MITIGATION_RESULTS.md),
[`RESULT_MANIFEST.md`](./RESULT_MANIFEST.md)) is designed to be
reproducible by anyone running the same stack.
