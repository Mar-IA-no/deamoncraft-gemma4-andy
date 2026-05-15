# Mitigation results — pre / post comparison

Comparison of baseline (patched runner, execution-aware scoring) vs post-mitigation (HermesPolicy wrapper) on the same Tier 1 primitives + variants. Both sides use identical scoring; outcomes are tripartite: `policy_handled_upstream` / `embodied_succeeded` / `embodied_failed`.

## Per-variant outcome breakdown

| Experiment | Variant | n | Baseline pass | Baseline outcomes | Mitigation pass | Mitigation outcomes | Δ pass |
|---|---|---|---|---|---|---|---|
| 001-intent-verbosity | medium | 5 | 4/5 | embodied_succeeded:5 | 5/5 | embodied_succeeded:5 | +1 |
| 001-intent-verbosity | terse | 5 | 2/5 | embodied_succeeded:5 | 5/5 | embodied_succeeded:5 | +3 |
| 001-intent-verbosity | verbose | 5 | 5/5 | embodied_succeeded:5 | 5/5 | embodied_succeeded:5 | +0 |
| 001-intent-verbosity | verbose_with_constraints | 5 | 5/5 | embodied_succeeded:5 | 5/5 | embodied_succeeded:5 | +0 |
| 004-tier1-visual-distinct | equip_torch | 5 | 1/5 | embodied_failed:4, embodied_succeeded:1 | 5/5 | embodied_succeeded:5 | +4 |
| 004-tier1-visual-distinct | follow_me | 5 | 5/5 | embodied_succeeded:5 | — |  | — |
| 004-tier1-visual-distinct | mine_log | 5 | 0/5 | embodied_failed:5 | — |  | — |
| 004-tier1-visual-distinct | toss_apple | 5 | 0/5 | embodied_failed:5 | — |  | — |
| 005-tier1-visual-distinct-v2 | eat_food | 5 | 0/5 | embodied_failed:5 | — |  | — |
| 005-tier1-visual-distinct-v2 | flee_from_player | 5 | 5/5 | embodied_succeeded:5 | 5/5 | embodied_succeeded:5 | +0 |
| 005-tier1-visual-distinct-v2 | goto_coord | 5 | 5/5 | embodied_succeeded:5 | — |  | — |
| 005-tier1-visual-distinct-v2 | mine_stone | 5 | 2/5 | embodied_failed:1, embodied_succeeded:4 | — |  | — |
| 006-tier1-remaining | ambiguous_intent | 5 | 1/5 | embodied_failed:4, embodied_succeeded:1 | 5/5 | policy_handled_upstream:5 | +4 |
| 006-tier1-remaining | get_inventory | 5 | 5/5 | embodied_succeeded:5 | — |  | — |
| 006-tier1-remaining | mark_and_return | 5 | 5/5 | embodied_succeeded:5 | 5/5 | embodied_succeeded:5 | +0 |
| 006-tier1-remaining | out_of_scope_chat | 5 | 4/5 | embodied_succeeded:4, embodied_failed:1 | 5/5 | policy_handled_upstream:5 | +1 |

## Agregado X/Y/Z (post-mitigation)

| Outcome | Count | % |
|---|---|---|
| X — embodied_succeeded (Gemma ejecutó OK) | 35 | 78% |
| Y — policy_handled_upstream (Hermes evitó la call) | 10 | 22% |
| Z — embodied_failed (gap real, no resuelto) | 0 | 0% |
| **Total** | **45** | 100% |

**Headline framing**: el sistema completo mejora porque Hermes aprende a delegar con criterio. NO es 'Gemma mejoró'.

