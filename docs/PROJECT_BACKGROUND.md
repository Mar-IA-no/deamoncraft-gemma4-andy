# Project background

This document gives context for **why** this adapter exists, **where** it fits
in a larger system, and **who** it's for. Technical integration details live in
the other docs in this folder.

---

## The problem

Most AI in games today either lives in the cloud (privacy + cost + latency
issues) or shows up as scripted NPCs (no real agency). For a kids-facing
companion that lives inside a Minecraft world, neither extreme is right:

- Cloud LLMs are expensive per action, send everything off-device, and add
  network latency that breaks the in-game flow.
- Scripted bots can't adapt to children's open-ended ideas ("let's build a
  treehouse for my dog and put torches around it").

We wanted a different point in the design space: a companion that **lives
locally, listens to spoken/written intent, and acts inside the world** — but
without the LLM having to think about every keypress.

That requires splitting the brain. One model handles the conversation,
narrative, mediation, safety — that's the slow, careful part. Another piece
handles the body: turning intent into structured tool calls that a Mineflayer
bot can execute. The body part needs to be fast, predictable, and cheap —
small enough to run on a consumer GPU.

**Gemma-Andy is the body part.** It's a fine-tune of Gemma 4 E4B
specialized for one job: receive a JSON request describing intent + world
state + allowed tools, return a JSON plan with `body_plan`, `checks`,
`tool_calls`, `failure_policy`, and `operational_risk`. JSON only. No prose.
No chitchat. No keypress sequences.

---

## Where it fits — the ecosystem

This adapter is one piece of a larger embodied-companion architecture
sometimes called **DaemonCraft** or **HermesCraft**. The pieces:

```
┌──────────────────────────────────────────────────────────────────┐
│ Conversational agent (Hermes / Pamplinas / your LLM of choice)    │
│ - Talks to the kid                                                │
│ - Maintains story, mood, mediation                                │
│ - Decides WHEN to involve the body                                │
│ - Translates the kid's intent into a clean body request           │
└──────────────────────────────────┬───────────────────────────────┘
                                   │ HTTP /intent
                                   ▼
┌──────────────────────────────────────────────────────────────────┐
│ Embodied service (this adapter's host)                           │
│ - Composes canonical world_state from the bot                    │
│ - Calls Gemma-Andy with the JSON request                         │
│ - Parses the JSON plan                                           │
│ - Dispatches each tool_call to the bot                           │
│ - Runs recovery loops, mitigations, audits                       │
└──────────────────────────────────┬───────────────────────────────┘
                                   │ HTTP /api/chat
                                   ▼
┌──────────────────────────────────────────────────────────────────┐
│ Gemma-Andy (this adapter, served by Ollama)                      │
│ - Receives JSON request                                          │
│ - Returns JSON plan                                              │
│ - That's it — no other I/O                                       │
└──────────────────────────────────────────────────────────────────┘
                                   │
                       (the embodied service then dispatches to)
                                   ▼
┌──────────────────────────────────────────────────────────────────┐
│ Mineflayer bot (e.g. nicoechaniz/DaemonCraft bot)                │
│ - Owns the actual game connection                                │
│ - Exposes tool actions as HTTP endpoints                         │
│ - Lives in Minecraft                                             │
└──────────────────────────────────────────────────────────────────┘
```

This split is **load-bearing**. Generative tasks (story, mediation,
multi-turn dialogue) belong upstream. Body orchestration (single-shot
JSON-in / JSON-out) is what Gemma-Andy was trained for. Don't ask
Gemma-Andy to chat. Don't ask the conversational agent to micro-plan
every block placement.

---

## What's actually demonstrated today

Honest scope of the current state:

- **The adapter ships, runs locally, and responds to JSON requests** in
  ~3-10 s on a single RTX 3090 (Q8_0 GGUF, ~8 GB VRAM).
- **Validated on a focused set of "Tier 1" primitives**: navigation
  (`goto`, `follow`, `move_away`), mining (`mine_block`, `collect_drops`),
  inventory (`get_inventory`, `equip_item`), perception (`scan_nearby`),
  memory (`remember_here`, `goto_remembered_place`), signal
  (`ask_clarification`, `raise_guardian_event`). See
  [`MITIGATION_RESULTS.md`](./MITIGATION_RESULTS.md).
- **The conversational agent (Hermes) is in production use** in our
  setup. The hybrid pattern — Hermes handles what Gemma-Andy doesn't yet
  do reliably; Gemma-Andy executes the primitives that have been
  measured and de-bugged — is the realistic operational model.
- **Field-tested with kids** in earlier iterations of the broader system
  (5 children, ages 8-16, ~7 hours of play). That validation was on the
  cloud-only path; the local Gemma-Andy path is now ready for its own
  field-test cycle.

What the adapter **does not** claim today:

- It does not solve every Minecraft task on its own. Many primitives
  (complex builds, crafting chains, combat strategy) still need either
  upstream agent guidance or human input.
- It does not handle conversation. Asking it for jokes or explanations
  produces undefined behavior. Use the upstream conversational agent
  for those.
- It is not a general-purpose Minecraft AI. It's a body-orchestration
  module for systems that already separate cognition from embodiment.

---

## The companion philosophy

Beyond the technical separation, there's an editorial choice: **screen time
shouldn't be passive consumption**. A companion AI inside Minecraft can help
children sustain their own creative projects — building, exploring, telling
stories with their friends — instead of pulling them into algorithmic feeds.

The architecture above makes that achievable on consumer hardware:

- The conversational agent can run on whatever upstream LLM is appropriate
  for the platform (cloud-hosted commercial models, local models, future
  improvements).
- The body component (this adapter) runs locally on a single consumer
  GPU. Inference cost: ~$0 USD per intent. Inference happens on-device,
  visible, audited.
- The integration is open and inspectable. Anyone can run the same
  setup, see the same JSON contracts, audit the same tool calls.

---

## Acknowledgments

Cross-referenced repositories that made this possible:

- [`nicoechaniz/DaemonCraft`](https://github.com/nicoechaniz/DaemonCraft) —
  the canonical embodied-service implementation and Mineflayer bot. The
  reference deployment that this adapter integrates with.
- The Mineflayer ecosystem (Mineflayer + pathfinder + collectblock + etc.).
- [Voyager](https://arxiv.org/abs/2305.16291) (Wang et al. 2023) for
  inspiration on automatic curriculum and the broader pattern of
  agent-augmented LLMs in Minecraft.
- The Andy / Mindcraft / BlockData / TESS / MineDojo dataset projects
  whose released data informed the SFT training mix.

The fine-tune itself was developed by **Mar-IA-no** (training pipeline,
dataset curation, evaluation harness). Field-test instrumentation and the
[`primitives_lab`](https://github.com/nicoechaniz/DaemonCraft/tree/main/agents/embodied-service/primitives_lab)
suite by Fede654 surfaced the surface-form bias and recovery gaps that
motivated the upstream policy layer.

The 5-layer Hermes mitigation policy documented in
[`HERMES_MITIGATION_V2.md`](./HERMES_MITIGATION_V2.md) and
implemented in [`mitigation/`](../mitigation/) was developed in a
multi-agent collaboration cycle (Codex + Claude code reviewers, four
iterative rounds of plan revision).
