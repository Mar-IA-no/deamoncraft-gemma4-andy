# Methodology — how this adapter was debugged and how the policy was designed

This is the engineering process documentation. It covers:

1. The ladder of validation that surfaced the gaps in the raw adapter.
2. The reasoning behind the 5-layer Hermes-side policy.
3. The measurement framework (outcome tripartite) used to claim the policy
   actually closes those gaps.
4. The iteration loop with multi-agent code review.

It is not a tutorial. It's a record of what worked, what didn't, and why
the architecture is the shape it is.

---

## 1. The validation ladder

We didn't trust any single number. The adapter went through four progressive
validation stages:

### Stage 1 — Internal SFT eval
Standard adversarial + functional eval on a held-out set during training.
Showed the adapter worked at JSON-level (json_valid ~100%, tool whitelisting
respected). What this stage *can't* see: surface-form bias, multi-step
ordering, ambiguity discrimination.

### Stage 2 — External field signal (`primitives_lab` by Fede654)
A third party ran ~500 calls against the deployed Q8_0 model with their own
benchmark harness. The numbers were sobering: 18% overall pass rate, with
specific failure modes:
- Spanish surface form: 5/100 vs English 100/100 (20× gap)
- Recovery from `previous_error`: 3/240 (1.3%)
- Multi-step plans: low pass

Without this external signal, we would have shipped a model that worked on
our internal eval and broke on real users.

### Stage 3 — Direct dataset measurement
The Spanish gap surfaced a hypothesis: maybe the SFT data was English-heavy.
We measured directly: ran a regex over `high_level_command` strings in the
training set (n=33000). Result: **EN 90.6%, ES 0.1%**. The model wasn't
biased — it had effectively never seen Spanish in that field.

This kind of "step inside the dataset and count" is unglamorous but
non-negotiable. Hypothesis without measurement is folklore.

### Stage 4 — Live field validation (this work)
80 calls in a single session, distributed across four `primitives_lab`
experiments (n=5 per variant, 16 variants total). Mariano in a Java client
visually confirmed bot behavior in Minecraft.

This is where we found the bugs that even external benchmarks missed:

- `equip_torch` chains contain `attack_entity`, `put_in_chest`, `craft_item`
  — semantically incoherent for "equip a torch"
- `mark_and_return` emits `goto_remembered_place` *before* `remember_here`
  in 3 of 5 samples — impossible plan that the dispatcher silently passes
- `ask_clarification` triggered only 40% on explicitly ambiguous intents
- `raise_guardian_event` triggered 0% on out-of-scope chat requests
- The runner itself had a scoring bug that masked bot soft-failures
  (e.g., `toss_apple` reported 5/5 PASS while the bot picked back up its
  own dropped items, leaving none on the floor)

That last finding is important: **without execution-aware scoring, every
other measurement was potentially inflated**.

---

## 2. The four-question debug pattern

For each gap we found, we asked the same four questions in order:

### Q1 — Is it a model bug or a measurement bug?

The `toss_apple` case: 5/5 PASS reported, but the user (mariano) saw the
bot toss → pick up → toss → pick up. The model emitted `toss_item`
correctly each time. The bot dispatched correctly. The bug was in the
runner: it counted "the tool was emitted" as success, never checked
`execution_results[].ok`. Fix: patch the runner.

### Q2 — If it's a model bug, is it semantic or surface?

`terse "ven aca"` emitted only `scan_nearby` 4/5 times. The model
understands navigation in some forms but not in this telegraphic Spanish
form — it's a surface bug. The SFT distribution didn't include this shape.
Fix: normalize the intent upstream into a form the SFT did include
(English imperative inline single-sentence with concrete target).

`equip_torch` emitted incoherent chains across many forms — semantic. The
model has confused associations from training; no amount of prompting
fixes it. Fix: restrict the tool palette so the model can't emit the
incoherent options at all (Layer 4 narrow `allowed_tools`).

### Q3 — If it's a semantic model bug, can we avoid it upstream?

`ambiguous_intent "Hacé algo entretenido"` — the model emits random
exploration tools instead of `ask_clarification`. The right behavior is
to ask the user for clarification. The upstream agent (Hermes) is
already an LLM and can detect ambiguity *before* invoking Gemma-Andy.
Fix: Layer 3, ambiguity classifier upstream.

`out_of_scope_chat "Contame un chiste"` — the model emits exploration or
crafting tools instead of `raise_guardian_event`. The upstream agent can
recognize non-body intents and respond directly. Fix: Layer 2, scope
filter upstream.

### Q4 — If it's not avoidable upstream, is it state-dependent?

`mine_log` returns `target_not_found` even when the world state shows
`oak_log` nearby. That's a dispatcher / scanner mismatch in the bot,
not in the LLM or the policy. Out of scope for the policy layer;
documented for the bot's maintainer.

`eat_food` fails when food=20 because the server rejects eating at full
saturation. That's an environment constraint, not a bug. Document it.

---

## 3. The 5-layer policy (operational reasoning)

The policy upstream of Gemma-Andy implements five filters/transforms in a
specific order:

```
user_intent →
  Layer 2 — scope filter      → if non-body, handled upstream, no Gemma call
  Layer 3 — ambiguity check   → if ambiguous, ask user upstream, no Gemma call
  Layer 5 — multi-step decompose → split into 1+ atomic sub-intents
  for each sub-intent:
    Layer 1 — normalize surface form → EN imperative inline, canonical names
    Layer 4 — narrow allowed_tools per intent category
    POST /intent
```

Why this order:
- L2 + L3 are filters that prevent unnecessary calls. Cheapest wins go first.
- L5 splits *before* L1/L4 because each sub-intent gets its own normalization
  and its own category classification — they can have different `allowed_tools`.
- L1 must run *before* L4 because the classifier looks at normalized text
  (English keywords like `mine`, `equip`, `goto`).
- Constraint detection in L5 (Codex correction) prevents over-decomposition
  of clauses like "Stop within 3 blocks" or "Avoid hazards" — those are
  modifiers of the previous action, not new actions.

The classifier itself is regex+keyword based, not LLM. For the closed set
of test intents this is sufficient and deterministic. For production
deployment with an arbitrary upstream LLM (Kimi/MiniMax/GPT/Claude/etc.),
the upstream LLM can and should perform L2/L3/L4 itself with better
reasoning — the regex implementation is a reference, not the only way.

---

## 4. Outcome tripartite (X / Y / Z)

The traditional pass/fail metric conflates two very different things:

- "Gemma-Andy emitted the right tool, the bot executed it, the world
  reflected the intent" — that's the model + dispatcher + bot working.
- "The upstream agent decided this intent shouldn't reach Gemma-Andy at
  all (out of scope, ambiguous) and handled it upstream" — that's the
  policy working.

Both are valid system outcomes, but **they're different outcomes**.
Reporting them as a single number ("11/14 succeeded") would mean
selling policy bypass as model improvement. That's dishonest framing.

The runner used in this work distinguishes three outcomes per call:

- **X — `embodied_succeeded`**: invoked Gemma-Andy, model emitted tools
  within the narrowed `allowed_tools`, bot executed each with `ok=true`.
- **Y — `policy_handled_upstream`**: the upstream policy (L2 or L3)
  determined the intent should not reach Gemma-Andy. No model call
  happened. The right behavior was upstream.
- **Z — `embodied_failed`**: invoked Gemma-Andy and the system did not
  produce a working result (model emitted bad tool, bot rejected, soft
  failure, etc.). This is a real gap.

Pitch language follows from this: "X variants executed by Gemma-Andy
with success. Y variants handled upstream by the policy. Z variants
still failing." The total "system worked" is X + Y, but they should
always be reported as separate components.

---

## 5. The iteration loop (4 rounds of multi-agent review)

The 5-layer design didn't arrive whole. It went through four review cycles
with two agent reviewers (a Codex-flavored reviewer for engineering rigor
and a Claude-flavored implementer for execution) over a single working
session. Each round surfaced corrections:

**Round 1** — initial proposal: "no retrain, mitigation upstream". Reviewer
corrected the framing: "mitigable / avoidable for the demo, not 'resolved'".
Adopted: the model is not arrearing — the system is composed differently.

**Round 2** — wrapper design. Reviewer objected to hardcoding the 17 known
test variants in lookup tables ("that's recognizing the exam, not
generalizing"). Adopted: regex+keyword classifiers based on general
patterns; no per-variant code paths.

**Round 3** — policy refinement. Reviewer pointed out:
- `inventory` was too broad a category; split into `equip` / `toss` /
  `pickup` / `inventory_query`.
- Common-safe signals (`ask_clarification`, `report_execution_error`)
  should be added to all categories; `raise_guardian_event` only to
  guardian-aware ones (navigation / combat / default).
- `ok=true` semantics for policy-handled responses needed an explicit
  `outcome` field to disambiguate from "bot executed".
- Multi-step variants need visual/semantic verification, not just
  `passed=true`.

**Round 4** — mid-execution catch. Reviewer noticed Layer 5 was
over-decomposing constraints ("Stop within 3 blocks", "Avoid hazards"
were being treated as separate sub-intents). Added a `CONSTRAINT_LEAD`
detector that re-merges constraint sub-intents with the previous
action.

Plus a runtime bug discovered during analysis: file-naming based on
second-resolution timestamps caused 3 back-to-back `--variant` runs to
overwrite each other's JSONs. Fix: include `variant_id` + microseconds
in the output filename.

**Lesson**: a loop of "propose → review → patch → re-validate" with
~30-min cycles produced a defensible result. Single-pass design would
have shipped any of: hardcoded test lookup, missing constraint detection,
overly broad inventory category, or silent JSON overwrites.

---

## 6. The monitoring stack (six simultaneous layers)

For real-time field validation, no single source of truth was sufficient.
Six layers running in parallel:

1. **Structured JSON logs** from the embodied-service (`context_id`,
   ollama timing, tool dispatches, intent_done) — for after-the-fact
   replay.
2. **Named tmux sessions** (`nicobot`, `embodied`, mitigation runs) —
   persistent, attachable from any shell.
3. **Visual confirmation** by a human in a Java Minecraft client —
   the only source of truth for "did the bot actually do it"
   (caught the `toss_apple` auto-pickup bug).
4. **RCON over the MC server** for snapshots (`/data get entity`,
   `/list`, `/tp`, `/kill @e[type=item,distance=..15]`) — for
   pre-test environment reset and post-test state inspection.
5. **HTTP `/health` and `/status`** on the bot and embodied service —
   for quick alive-checks and inventory snapshots.
6. **Raw result JSONs** with full per-sample detail
   (`execution_results`, `tool_names`, `outcome`, `mitigation_meta`) —
   for analysis after the run.

Any layer alone produces selective blindness. The `toss_apple` bug
required visual confirmation against measurement; the `equip_torch` bug
required structured logs (tool freq) plus visual confirmation of "no
attack happened despite the chain"; the `mark_and_return` order
verification required reading `execution_results` per sample to confirm
the sequence `remember_here → goto → goto_remembered_place`.

---

## 7. What this method is good for and what it's not

Good for:
- Closing known gaps in a deployed model without retraining.
- Producing defensible numbers for a constrained primitive set.
- Iterating quickly with multi-agent code review.
- Building reproducible reference implementations that other integrators
  can adopt.

Not good for:
- Statistical inference from small samples (n=5 per variant gives
  direction, not significance).
- Solving model-level deficiencies for general use (the policy works for
  closed sets and known categories; arbitrary intent space requires
  retraining or a much smarter upstream LLM as classifier).
- Bypassing the need for honest scoring (the regex/keyword classifier
  is itself a system component that must be measured for false
  positives — the `come` → food false-positive that we found mid-run is
  exactly this kind of error).

---

## 8. Reproducing this

Anyone with the adapter, an Ollama deployment, and a Mineflayer bot can
reproduce the methodology:

1. Apply the runner patch from
   [`mitigation/runner_patch.diff`](../mitigation/runner_patch.diff)
   to your local copy of `primitives_lab/runner.py`.
2. Drop in [`hermes_policy.py`](../mitigation/hermes_policy.py) +
   [`run_with_mitigation.py`](../mitigation/run_with_mitigation.py).
3. Verify the policy unit tests pass:
   `python3 hermes_policy.py --test` (expects 7/7).
4. Run baseline experiments with the patched runner.
5. Run mitigation experiments with the wrapper.
6. Compare with [`compare_results.py`](../mitigation/compare_results.py).

The expected outcome shape is per-variant pass rates (baseline vs
mitigation) plus aggregate X/Y/Z. The policy regex/keywords are
documented in [`HERMES_MITIGATION_V2.md`](./HERMES_MITIGATION_V2.md);
adapt them to your intent vocabulary.
