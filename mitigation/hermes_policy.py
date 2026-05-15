#!/usr/bin/env python3
"""hermes_policy.py — 5-layer Hermes mitigation policy for Gemma-Andy upstream.

Implements general (non-variant-specific) classifiers and orchestrator.

Layers:
  L2 — scope filter   : non-body intents → handled upstream (no embodied call)
  L3 — ambiguity      : vague intents → handled upstream (asks user)
  L5 — decompose      : multi-step intents → split into atomic sub-intents
  L1 — normalize      : per sub-intent, ES→EN imperative inline, canonical names
  L4 — narrow tools   : per sub-intent, classify category, pass narrow allowed_tools

Flow: L2 → L3 → L5 → (for each sub) → L1 → L4 → POST /intent

Outcome tripartite returned in response:
  - policy_handled_upstream  (L2 or L3 cut)
  - embodied_succeeded       (POST went through, all execution_results ok)
  - embodied_failed          (POST went through, at least one execution_result not ok)

Reference: docs/hermes_mitigation_v2.md
"""
from __future__ import annotations

import os
import re
import time
from typing import Optional

try:
    import requests
except ImportError:
    requests = None


class HermesPolicy:
    OUT_OF_SCOPE_REGEX = re.compile(
        r"\b(chiste|joke|cantam|hola|chau|buenas|gracias|de nada|"
        r"qué pensás|que pensas|qué te parece|que te parece|opinión|opinion|"
        r"explicame|explícame|definí|defini|definition|tell me about|"
        r"por qué|por que|why does|how does|qué es|que es|"
        r"sumar|restar|multiplicar|dividir|cuánto es|cuanto es|2\s*\+\s*2)\b",
        re.IGNORECASE,
    )
    AMBIGUITY_TOKENS = re.compile(
        r"\b(algo entretenido|algo bueno|algo divertido|cualquier cosa|"
        r"something good|something fun|whatever|por ahí|por ahi|"
        r"hacé algo|hace algo|do something|haz algo|alrededor sin más|"
        r"andá por|anda por)\b",
        re.IGNORECASE,
    )

    # Orden importa: matches más específicos primero (navigation antes que food para evitar false-positive de "come to";
    # equip antes que inventory_query; etc.).
    # CORRECCIÓN POST-RUN-1: "come " removido de food (matcheaba "Come to" post-normalización de "vení").
    #                       "Find", "approach", "Stop within" agregados a navigation.
    CATEGORY_KEYWORDS = [
        # navigation va PRIMERO ahora — captura "Come to", "Find", "approach", "Move away" antes que food/equip/etc.
        ("navigation",      ["andá", "anda ", "vení", "veni ", "venite", "follow", "go to", "goto", "alejate", "alejame", "flee", "seguime", "acercate", "come to", "come here", "ven aca", "ven acá", "find and approach", "approach", "find the player", "stop within", "stay within", "move away from", "move away", "move_away", "flee from", "flee_from", "get away from"]),
        ("equip",           ["equipá", "equipa", "equip", "ponete", "pongate"]),
        ("toss",            ["tirá", "tira", "toss", "drop", "dejá caer", "deja caer"]),
        ("pickup",          ["recogé", "recoge", "pickup", "agarrá", "agarra", "levantá", "levanta", "pick up"]),
        ("food",            ["comé", "comer", "comelo", "eat ", "drink", "bebé", "bebe", "morder", "ingerir"]),
        ("memory",          ["acordate", "marcá", "marca ", "recordá", "remember", "volvé a", "return to", "olvidá", "forget"]),
        ("mining",          ["minar", "mine ", "conseguí", "consegui", "gather", "dig "]),
        ("build",           ["construí", "construye", "construí ", "pongá", "place ", "build ", "make a "]),
        ("combat",          ["atacá", "ataca", "attack", "defendé", "defend", "raise_shield"]),
        ("inventory_query", ["inventario", "inventory", "decime qué tenés", "decime que tenes", "mostrame el inventario", "what do you have", "show inventory"]),
    ]

    CATEGORY_TOOLS = {
        "navigation":      ["scan_nearby", "goto", "follow", "stop_movement", "move_away"],
        "mining":          ["scan_nearby", "goto", "mine_block", "mine_blocks", "collect_drops", "get_inventory"],
        "equip":           ["get_inventory", "equip_item"],
        "toss":            ["get_inventory", "toss_item"],
        "pickup":          ["scan_nearby", "pickup_item", "get_inventory"],
        "inventory_query": ["get_inventory"],
        "memory":          ["remember_here", "goto_remembered_place", "forget_place", "get_inventory"],
        "food":            ["consume_food", "get_inventory"],
        "build":           ["scan_nearby", "goto", "place_block", "equip_item", "get_inventory"],
        "combat":          ["scan_nearby", "attack_entity", "flee_from", "raise_shield", "consume_food"],
    }
    COMMON_SAFE = ["ask_clarification", "report_execution_error"]
    GUARDIAN_AWARE_CATEGORIES = {"navigation", "combat", "default"}

    DECOMPOSE_CONNECTORS = re.compile(
        r"(\s+después\s+|\s+despues\s+|\s+luego\s+|\s+y después\s+|"
        r"\s+y luego\s+|\s+y después de\s+|\bthen\b|"
        r"(?<=[a-záéíóú])\.\s+(?=[A-ZÁÉÍÓÚ])|^\s*\d+[\.\)])",
        re.IGNORECASE,
    )
    # Sub-intents que SOLO comienzan con estas frases son CONSTRAINTS/modifiers,
    # no nuevas acciones — se mergean con el sub-intent anterior (review correction 2026-05-15).
    # Examples that should NOT be separate sub-intents:
    #   "Stop within 3 blocks." (after "Find and approach the player")
    #   "Avoid hazards along the way."
    #   "Without harming the animals."
    CONSTRAINT_LEAD = re.compile(
        r"^(stop within|stay within|stay near|stay close|stay\s|"
        r"avoid|do not|don't|never|while|during|"
        r"without|keep|be careful|carefully|"
        r"sin (?:hacer|tocar|salir|hurt)|mantente|manténte|"
        r"evitando|cuidando|cuidado con)\b",
        re.IGNORECASE,
    )

    # Verb mapping ES → EN imperative
    VERB_MAP = [
        ("acordate de", "Remember"),
        ("acordate", "Remember"),
        ("recordá", "Remember"),
        ("marcá", "Mark"),
        ("marca ", "Mark "),
        ("volvé a", "Return to"),
        ("volvé", "Return"),
        ("vuelve a", "Return to"),
        ("alejate de", "Move away from"),
        ("alejate", "Move away from"),
        ("alejame", "Move away from"),
        ("andá a", "Go to"),
        ("andá", "Go to"),
        ("anda a", "Go to"),
        ("caminá", "Walk"),
        ("caminar", "Walk"),
        ("camina ", "Walk "),
        ("vení a", "Come to"),
        ("vení", "Come to"),
        ("venite a", "Come to"),
        ("venite", "Come to"),
        ("veni a", "Come to"),
        ("acercate", "Approach"),
        ("comé", "Eat"),
        ("comer ", "Eat "),  # ES infinitive; NOT "come " — conflicts with EN "Come to" post-mapping
        ("minar", "Mine"),
        ("minas", "Mine"),
        ("conseguí", "Get"),
        ("consegui", "Get"),
        ("tirá", "Toss"),
        ("tira ", "Toss "),
        ("equipá", "Equip"),
        ("equipa", "Equip"),
        ("recogé", "Pick up"),
        ("recoge", "Pick up"),
        ("agarrá", "Pick up"),
        ("agarra ", "Pick up "),
        ("construí", "Build"),
        ("construye", "Build"),
        ("pongá", "Place"),
        ("atacá", "Attack"),
        ("ataca ", "Attack "),
        ("defendé", "Defend"),
        ("seguime", "Follow"),
        ("decime qué tenés", "Tell me what you have"),
        ("decime que tenes", "Tell me what you have"),
        ("mostrame el inventario", "Show your inventory"),
        ("hacé", "Do"),
        ("hace ", "Do "),
    ]

    def __init__(self, embodied_service_url: str, player_name: str | None = None, bot_name: str | None = None):
        if requests is None:
            raise RuntimeError("requests package required")
        self.url = embodied_service_url.rstrip("/")
        self.player_name = player_name or os.getenv("HERMES_PLAYER_NAME", "player")
        self.bot_name = bot_name or os.getenv("HERMES_BOT_NAME", "minecraft_bot")

    # ─── Layer 2 ──────────────────────────────────────────────
    def is_out_of_scope(self, intent: str) -> tuple[bool, str | None]:
        if not intent:
            return False, None
        m = self.OUT_OF_SCOPE_REGEX.search(intent)
        return (bool(m), m.group(0) if m else None)

    # ─── Layer 3 ──────────────────────────────────────────────
    def is_ambiguous(self, intent: str) -> tuple[bool, str | None]:
        if not intent:
            return False, None
        m = self.AMBIGUITY_TOKENS.search(intent)
        return (bool(m), m.group(0) if m else None)

    # ─── Layer 4 ──────────────────────────────────────────────
    def classify_category(self, intent: str) -> str:
        low = (intent or "").lower()
        for cat, kws in self.CATEGORY_KEYWORDS:
            for kw in kws:
                if re.search(rf"\b{re.escape(kw)}", low):
                    return cat
        return "default"

    def get_allowed_tools(self, category: str) -> list[str] | None:
        if category == "default":
            return None  # let embodied-service use its own DEFAULT_ALLOWED_TOOLS
        base = list(self.CATEGORY_TOOLS[category]) + list(self.COMMON_SAFE)
        if category in self.GUARDIAN_AWARE_CATEGORIES:
            base.append("raise_guardian_event")
        return base

    # ─── Layer 5 ──────────────────────────────────────────────
    def decompose(self, intent: str) -> list[str]:
        """Split intent into atomic sub-intents.

        Conservative: only split when there are sequential PHYSICAL ACTIONS
        connected by temporal markers ('después', 'luego', etc.) AND the
        following clause starts with a new action verb (not a constraint/modifier).

        If a candidate sub-intent starts with a constraint marker
        (stop within, avoid, do not, etc.), it's MERGED with the previous
        sub-intent rather than separated. (review correction 2026-05-15.)
        """
        if not intent:
            return []
        if not self.DECOMPOSE_CONNECTORS.search(intent):
            return [intent.strip()]
        parts = self.DECOMPOSE_CONNECTORS.split(intent)
        # Known connectors (any of these as the entire stripped part are dropped)
        CONNECTOR_TOKENS = {"después", "despues", "luego", "then", "y después", "y luego", "y", "y después de"}
        atomic_raw = []
        for p in parts:
            if not p:
                continue
            stripped = p.strip(" ,.;")
            if not stripped:
                continue
            low = stripped.lower()
            if low in CONNECTOR_TOKENS:
                continue
            # Numbered list markers like "1." or "1)"
            if re.match(r"^\d+[\.\)]?$", stripped):
                continue
            atomic_raw.append(stripped)
        if len(atomic_raw) <= 1:
            return [intent.strip()]
        # Constraint detection: merge constraint sub-intents back into the previous one
        merged: list[str] = [atomic_raw[0]]
        for sub in atomic_raw[1:]:
            if self.CONSTRAINT_LEAD.match(sub):
                # this is a modifier/constraint — re-attach to previous sub-intent
                merged[-1] = merged[-1].rstrip(" ,.;") + "; " + sub
            else:
                merged.append(sub)
        return merged if len(merged) > 1 else [intent.strip()]

    # ─── Layer 1 ──────────────────────────────────────────────
    def normalize_surface(self, intent: str) -> str:
        n = intent
        # Special case: bare "ven/vení/come here" without specific target → follow player.
        # Matches: "ven", "vení", "ven aca", "vení acá", "come here", "venite", etc.
        # Does NOT match longer constructions like "vení a la posición del jugador".
        bare_come = re.compile(
            r"^\s*(ven[ií]?|venite|come)\s*"
            r"(aca|acá|aqui|aquí|here|por aqui|por aca)?"
            r"[\s,\.!?]*$",
            re.IGNORECASE,
        )
        bare_approach = re.compile(
            r"^\s*acerc[aá]te[\s,\.!?]*$",
            re.IGNORECASE,
        )
        s = n.strip()
        if bare_come.match(s) or bare_approach.match(s):
            return f"Follow the player named {self.player_name} and stay within 3 blocks."
        # Verb mapping
        for es, en in self.VERB_MAP:
            n = re.sub(rf"\b{re.escape(es)}\b", en, n, flags=re.IGNORECASE)
        # Compact whitespace
        n = re.sub(r"\s+", " ", n).strip()
        # Replace pronouns if remaining. Use specific patterns including the trailing
        # player name to avoid duplicates ("of the player named player player").
        # First try "X jugador llamado <name>" / "X jugador <name>" with trailing name capture.
        player_re = re.escape(self.player_name)
        n = re.sub(rf"\bdel jugador(?:\s+llamado)?\s+{player_re}\b", f"of the player named {self.player_name}", n, flags=re.IGNORECASE)
        n = re.sub(rf"\bal jugador(?:\s+llamado)?\s+{player_re}\b", f"to the player named {self.player_name}", n, flags=re.IGNORECASE)
        n = re.sub(rf"\bel jugador(?:\s+llamado)?\s+{player_re}\b", f"the player named {self.player_name}", n, flags=re.IGNORECASE)
        # Then catch bare "X jugador" without trailing name
        n = re.sub(r"\btu posición\b", "your current position", n, flags=re.IGNORECASE)
        n = re.sub(r"\bal jugador\b", f"to the player named {self.player_name}", n, flags=re.IGNORECASE)
        n = re.sub(r"\bel jugador\b", f"the player named {self.player_name}", n, flags=re.IGNORECASE)
        n = re.sub(r"\bdel jugador\b", f"of the player named {self.player_name}", n, flags=re.IGNORECASE)
        # Ensure terminal period
        if not n.endswith("."):
            n = n + "."
        # Capitalize first letter
        if n and n[0].islower():
            n = n[0].upper() + n[1:]
        return n

    # ─── Orchestrator ─────────────────────────────────────────
    def execute(self, user_intent: str, deadline_seconds: int = 30) -> dict:
        t0 = time.time()

        # L2 — scope
        oos, reason = self.is_out_of_scope(user_intent)
        if oos:
            return self._policy_response("scope", f"out_of_scope: matched '{reason}'", t0)

        # L3 — ambiguity
        amb, token = self.is_ambiguous(user_intent)
        if amb:
            return self._policy_response(
                "ambiguity",
                f"ambiguous: matched '{token}'; Hermes would ask user for clarification",
                t0,
            )

        # L5 — decompose
        sub_intents = self.decompose(user_intent)

        # L1 + L4 per sub-intent
        all_exec: list[dict] = []
        all_plans: list[dict | None] = []
        normalized_chain: list[str] = []
        category_chain: list[str] = []
        allowed_chain: list[list[str] | None] = []
        prev_err: dict | None = None

        for sub in sub_intents:
            normalized = self.normalize_surface(sub)             # L1
            normalized_chain.append(normalized)
            category = self.classify_category(normalized)        # L4
            category_chain.append(category)
            allowed = self.get_allowed_tools(category)           # L4
            allowed_chain.append(allowed)

            payload: dict = {"intent": normalized, "deadline_seconds": deadline_seconds}
            if allowed is not None:
                payload["allowed_tools"] = allowed
            if prev_err is not None:
                payload["previous_error"] = prev_err
            # If caller passed extra_payload (e.g. allowed_tools from YAML variant), respect those.
            # Currently extra_payload is not threaded; reserved for future Hermes consumer override.

            try:
                r = requests.post(
                    f"{self.url}/intent",
                    json=payload,
                    timeout=deadline_seconds + 30,
                )
                r.raise_for_status()
                resp = r.json()
            except Exception as e:
                resp = {
                    "ok": False,
                    "execution_results": [{
                        "tool": "n/a",
                        "ok": False,
                        "error_type": "policy_call_failed",
                        "details": str(e),
                    }],
                    "plan": None,
                }

            all_plans.append(resp.get("plan"))
            er = resp.get("execution_results") or []
            all_exec.extend(er)
            failed = [x for x in er if not x.get("ok")]
            prev_err = (
                {
                    "tool": failed[0].get("tool"),
                    "error_type": failed[0].get("error_type"),
                    "details": failed[0].get("details"),
                }
                if failed
                else None
            )

        exec_all_ok = all(er.get("ok") for er in all_exec) if all_exec else None
        elapsed = round(time.time() - t0, 2)
        return {
            "ok": exec_all_ok,
            "outcome": "embodied_succeeded" if exec_all_ok else "embodied_failed",
            "policy_handled": False,
            "policy_layer": None,
            "plan": all_plans[-1] if all_plans else None,
            "execution_results": all_exec,
            "elapsed_seconds": elapsed,
            "mitigation": {
                "sub_intents_count": len(sub_intents),
                "normalized_chain": normalized_chain,
                "category_chain": category_chain,
                "allowed_tools_chain": allowed_chain,
            },
        }

    def _policy_response(self, layer: str, reason: str, t0: float) -> dict:
        return {
            "ok": True,  # "ok" semantically: Hermes handled correctly upstream. NOT bot executed.
            "outcome": "policy_handled_upstream",
            "policy_handled": True,
            "policy_layer": layer,
            "policy_reason": reason,
            "execution_results": [],
            "plan": None,
            "elapsed_seconds": round(time.time() - t0, 2),
        }


# ─── CLI / unit tests ─────────────────────────────────────────
def _unit_tests():
    p = HermesPolicy("http://localhost:7790", player_name="player", bot_name="minecraft_bot")

    assert p.is_out_of_scope("Contame un chiste corto")[0] is True
    assert p.is_out_of_scope("Hola, ¿cómo estás?")[0] is True
    assert p.is_out_of_scope("Mine 1 oak_log")[0] is False
    print("L2 OK")

    assert p.is_ambiguous("Hacé algo entretenido")[0] is True
    assert p.is_ambiguous("Andá por ahí")[0] is True
    assert p.is_ambiguous("Mine 1 oak_log")[0] is False
    assert p.is_ambiguous("Andá a (302, 67, 200)")[0] is False
    print("L3 OK")

    assert p.classify_category("Equipá una torch") == "equip", p.classify_category("Equipá una torch")
    assert p.classify_category("Mine 1 cobblestone") == "mining"
    assert p.classify_category("Tirá 1 apple") == "toss"
    assert p.classify_category("Recogé los items") == "pickup"
    assert p.classify_category("Decime qué tenés en el inventario") == "inventory_query"
    assert p.classify_category("Acordate de esta posición") == "memory"
    assert p.classify_category("Andá al jugador") == "navigation"
    print("L4 OK")

    tools = p.get_allowed_tools("equip")
    assert tools == ["get_inventory", "equip_item", "ask_clarification", "report_execution_error"], tools
    nav_tools = p.get_allowed_tools("navigation")
    assert "raise_guardian_event" in nav_tools
    eq_tools = p.get_allowed_tools("equip")
    assert "raise_guardian_event" not in eq_tools, "equip should NOT include raise_guardian_event"
    print("L4 allowed_tools OK")

    sub = p.decompose("Acordate de esta posición como home, después caminá 8 bloques al oeste, después volvé a home")
    assert len(sub) >= 3, f"expected >=3 sub-intents, got {len(sub)}: {sub}"
    print(f"L5 decompose OK ({len(sub)} sub-intents)")

    norm = p.normalize_surface("Acordate de tu posición")
    assert "Remember" in norm, norm
    assert norm.endswith("."), norm
    print(f"L1 normalize OK: {norm!r}")

    norm2 = p.normalize_surface("ven aca")
    assert "Follow the player named player" in norm2, norm2
    print(f"L1 bare-come OK: {norm2!r}")

    print()
    print("ALL UNIT TESTS PASSED")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        _unit_tests()
    else:
        print(__doc__)
