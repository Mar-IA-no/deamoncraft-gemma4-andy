# Result manifest — sprint Hermes Mitigation v3 (2026-05-15)

Trazabilidad reproducible per-(experiment, variant). Los JSONs raw viven en `onairam-agent:~/agents/hermes-daemoncraft/daemoncraft/agents/embodied-service/primitives_lab/results/`. SHA256 calculado al momento de generar este manifest.

## Schema

- **kind**: baseline (post-runner-patch) o mitigation_v3 (post 5-layer policy)
- **JSON**: path en remoto (relativo a `primitives_lab/results/`)
- **sha256[16]**: primer 16 chars del hash, suficiente para verificación rápida

## Manifest

| Kind | Experiment | Variant | JSON | sha256 | n_samples | outcome_summary |
|---|---|---|---|---|---|---|
| baseline | 001-intent-verbosity | terse | `001-intent-verbosity_20260515_184153.json` | `d4c67a845a02853e…` | 5 | embodied_succeeded:5 |
| baseline | 001-intent-verbosity | medium | `001-intent-verbosity_20260515_184153.json` | `d4c67a845a02853e…` | 5 | embodied_succeeded:5 |
| baseline | 001-intent-verbosity | verbose | `001-intent-verbosity_20260515_184153.json` | `d4c67a845a02853e…` | 5 | embodied_succeeded:5 |
| baseline | 001-intent-verbosity | verbose_with_constraints | `001-intent-verbosity_20260515_184153.json` | `d4c67a845a02853e…` | 5 | embodied_succeeded:5 |
| baseline | 004-tier1-visual-distinct | follow_me | `004-tier1-visual-distinct_20260515_184351.json` | `6a7d0690520082ca…` | 5 | embodied_succeeded:5 |
| baseline | 004-tier1-visual-distinct | mine_log | `004-tier1-visual-distinct_20260515_184351.json` | `6a7d0690520082ca…` | 5 | embodied_failed:5 |
| baseline | 004-tier1-visual-distinct | toss_apple | `004-tier1-visual-distinct_20260515_184351.json` | `6a7d0690520082ca…` | 5 | embodied_failed:5 |
| baseline | 004-tier1-visual-distinct | equip_torch | `004-tier1-visual-distinct_20260515_184351.json` | `6a7d0690520082ca…` | 5 | embodied_succeeded:1, embodied_failed:4 |
| baseline | 005-tier1-visual-distinct-v2 | eat_food | `005-tier1-visual-distinct-v2_20260515_184716.json` | `5f65c38773f5f1fc…` | 5 | embodied_failed:5 |
| baseline | 005-tier1-visual-distinct-v2 | goto_coord | `005-tier1-visual-distinct-v2_20260515_184716.json` | `5f65c38773f5f1fc…` | 5 | embodied_succeeded:5 |
| baseline | 005-tier1-visual-distinct-v2 | mine_stone | `005-tier1-visual-distinct-v2_20260515_184716.json` | `5f65c38773f5f1fc…` | 5 | embodied_succeeded:4, embodied_failed:1 |
| baseline | 005-tier1-visual-distinct-v2 | flee_from_player | `005-tier1-visual-distinct-v2_20260515_184716.json` | `5f65c38773f5f1fc…` | 5 | embodied_succeeded:5 |
| baseline | 006-tier1-remaining | get_inventory | `006-tier1-remaining_20260515_185001.json` | `5bb033e46ce9270d…` | 5 | embodied_succeeded:5 |
| baseline | 006-tier1-remaining | mark_and_return | `006-tier1-remaining_20260515_185001.json` | `5bb033e46ce9270d…` | 5 | embodied_succeeded:5 |
| baseline | 006-tier1-remaining | ambiguous_intent | `006-tier1-remaining_20260515_185001.json` | `5bb033e46ce9270d…` | 5 | embodied_succeeded:1, embodied_failed:4 |
| baseline | 006-tier1-remaining | out_of_scope_chat | `006-tier1-remaining_20260515_185001.json` | `5bb033e46ce9270d…` | 5 | embodied_succeeded:4, embodied_failed:1 |
| mitigation_v3 | 001-intent-verbosity | terse | `001-intent-verbosity_20260515_193936_MITIGATED.json` | `22506b75e6b1f14f…` | 5 | embodied_succeeded:5 |
| mitigation_v3 | 001-intent-verbosity | medium | `001-intent-verbosity_20260515_193955_MITIGATED.json` | `71fb6140de29eb23…` | 5 | embodied_succeeded:5 |
| mitigation_v3 | 001-intent-verbosity | verbose | `001-intent-verbosity_20260515_194010_MITIGATED.json` | `b8bd6f64bdb50bad…` | 5 | embodied_succeeded:5 |
| mitigation_v3 | 001-intent-verbosity | verbose_with_constraints | `001-intent-verbosity_20260515_194024_MITIGATED.json` | `71c14e58ca68e99e…` | 5 | embodied_succeeded:5 |
| mitigation_v3 | 004-tier1-visual-distinct | equip_torch | `004-tier1-visual-distinct_20260515_194042_MITIGATED.json` | `31c670cdd691e9ea…` | 5 | embodied_succeeded:5 |
| mitigation_v3 | 005-tier1-visual-distinct-v2 | flee_from_player | `005-tier1-visual-distinct-v2_20260515_194056_MITIGATED.json` | `6a6bfc2d466e54de…` | 5 | embodied_succeeded:5 |
| mitigation_v3 | 006-tier1-remaining | mark_and_return | `006-tier1-remaining_20260515_195018_MITIGATED.json` | `75b26711900882a3…` | 5 | embodied_succeeded:5 |
| mitigation_v3 | 006-tier1-remaining | ambiguous_intent | `006-tier1-remaining_20260515_194639_MITIGATED.json` | `d6a7f435dd0746df…` | 5 | policy_handled_upstream:5 |
| mitigation_v3 | 006-tier1-remaining | out_of_scope_chat | `006-tier1-remaining_20260515_194641_MITIGATED.json` | `74baf5a7671d4a54…` | 5 | policy_handled_upstream:5 |
