"""Operations briefing: plain English written by Claude, grounded in the engine's own facts.

Claude never makes, ranks or changes a decision here. It only rewrites facts this service
has already computed. Every number in its text must match a number in those facts, or the
text is rejected and the deterministic template is shown instead. Without LLM_API_KEY the
template is always used, so the app works offline and in CI.
"""
import asyncio
import json
import os
import re
import ssl
import time

import anthropic

from app import metrics

DEFAULT_MODEL = "claude-opus-5"
MAX_ITEMS = 5
CACHE_SECONDS = 30
# Server-side refusal fallback is only offered for these model families.
FALLBACK_MODELS = ("claude-opus-5", "claude-fable-5")
NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "WARNING": 2}

SYSTEM_PROMPT = """You write the operations briefing for Jalani, the control room of a SIMULATED \
fuel-supply network used in a hackathon. No real infrastructure is involved. An operator reads \
your briefing at a glance before deciding what to review.

Write 3 to 5 short sentences of plain English covering: overall service, the most urgent \
stations and fuels, the shipment proposals waiting for a human, and any active disruption or \
degraded system state. Lead with what needs attention first.

The user message is a JSON object of facts computed by the control room. Use only those facts. \
Every number you write must appear in them. You may round, and you may write a fraction as a \
percentage (0.973 as 97.3%), but do not estimate, add up, subtract or otherwise derive new \
numbers. Do not recommend any action other than the listed proposals. If shipments_blocked is \
true, say that shipments are paused and why. Write plain prose: no headings, lists or markdown."""


class BriefingError(Exception):
    """The generated text cannot be shown; the template is used instead."""


def template(state):
    kpi = state["kpis"]
    return (f"At tick {state['tick']}, service level is {kpi['service_level']:.1%}. "
            f"Unmet demand is {kpi['unmet_liters']:,.0f} L. {kpi['open_alerts']} alerts are open and "
            f"{kpi['pending_recommendations']} allocation proposals await review. "
            f"System mode: {state['mode'].lower()}. All figures describe the simulated environment.")


def facts(state, recommendations, alerts, horizon_hours):
    """The only information Claude sees: already-computed values, bounded in size."""
    names = {x["id"]: x["name"] for x in state["stations"] + state["depots"]}
    kpi = state["kpis"]
    at_risk = sorted(
        ({"station": s["name"], "fuel": fuel, "stockout_risk": round(v["risk"], 3),
          "hours_to_stockout": None if v.get("hours_to_stockout") is None else round(v["hours_to_stockout"], 1),
          "inventory_liters": round(v["inventory"])}
         for s in state["stations"] for fuel, v in s["fuels"].items() if (v.get("risk") or 0) >= 0.2),
        key=lambda item: -item["stockout_risk"])
    proposals = sorted((r for r in recommendations if r["status"] == "PROPOSED"),
                       key=lambda r: -r["impact"]["risk_before"])[:MAX_ITEMS]
    open_alerts = sorted((a for a in alerts if a["status"] != "RESOLVED"),
                         key=lambda a: SEVERITY_ORDER.get(a.get("severity"), 3))[:MAX_ITEMS]
    return {
        "tick": state["tick"], "simulated_time": state["sim_time"], "system_mode": state["mode"],
        "shipments_blocked": state["execution_blocked"], "block_reason": state["reason"] or None,
        "service_level": round(kpi["service_level"], 4), "unmet_demand_liters": round(kpi["unmet_liters"]),
        "fuel_in_transit_liters": round(kpi["in_transit_liters"]), "open_alerts": kpi["open_alerts"],
        "proposals_awaiting_review": kpi["pending_recommendations"], "forecast_horizon_hours": horizon_hours,
        "tanks_at_risk": len(at_risk), "most_at_risk": at_risk[:MAX_ITEMS],
        "top_proposals": [{"station": names.get(r["station_id"], r["station_id"]), "fuel": r["fuel_type"],
                           "quantity_liters": round(r["action"]["quantity"]),
                           "from_depot": names.get(r["action"]["source_depot_id"], r["action"]["source_depot_id"]),
                           "risk_before": round(r["impact"]["risk_before"], 3),
                           "risk_after": round(r["impact"]["risk_after"], 3),
                           "needs_human_review": r["requires_review"]} for r in proposals],
        "open_alert_messages": [a["message"] for a in open_alerts],
    }


def _collect(value, out):
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, (int, float)):
        out.add(float(value))
        if 0 <= value <= 1:
            out.add(value * 100)
    elif isinstance(value, str):
        out.update(float(token.replace(",", "")) for token in NUMBER.findall(value))
    elif isinstance(value, dict):
        for item in value.values():
            _collect(item, out)
    elif isinstance(value, list):
        for item in value:
            _collect(item, out)


def unverified_numbers(text, grounded):
    """Numbers in the text that no fact supports, allowing ordinary rounding."""
    allowed = set()
    _collect(grounded, allowed)
    return [token for token in NUMBER.findall(text)
            if not any(abs(float(token.replace(",", "")) - a) <= max(0.051, abs(a) * 0.005) for a in allowed)]


class Briefing:
    def __init__(self, config, generate=None):
        self.config = config
        # Deployment templates pass an unset LLM_MODEL through as an empty string.
        self.model = config.llm_model or DEFAULT_MODEL
        self.client = None
        self._generate = generate
        if generate is None and config.llm_api_key:
            options = {"base_url": config.llm_base_url} if config.llm_base_url else {}
            # Behind TLS-inspecting antivirus or proxies, trust their root in addition to the defaults.
            if config.llm_ca_file and os.path.isfile(config.llm_ca_file) and os.path.getsize(config.llm_ca_file):
                context = ssl.create_default_context()
                context.load_verify_locations(cafile=config.llm_ca_file)
                options["http_client"] = anthropic.DefaultAsyncHttpxClient(verify=context)
            self.client = anthropic.AsyncAnthropic(api_key=config.llm_api_key, timeout=config.llm_timeout_seconds,
                                                   max_retries=1, **options)
            self._generate = self._claude
        self.lock = asyncio.Lock()
        self.cache = {}
        self.last_error = None

    @property
    def enabled(self):
        return self._generate is not None

    def status(self):
        if not self.enabled:
            return {"status": "disabled", "explanations": "grounded templates"}
        return {"status": "degraded" if self.last_error else "healthy", "model": self.model,
                "explanations": "Claude briefing, numbers checked; template fallback",
                "last_error": self.last_error}

    async def _claude(self, grounded):
        options = {}
        if self.model.startswith(FALLBACK_MODELS):
            options = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
        response = await self.client.beta.messages.create(
            model=self.model, max_tokens=2048, system=SYSTEM_PROMPT,
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": json.dumps(grounded, sort_keys=True)}],
            **options)
        if response.stop_reason != "end_turn":
            raise BriefingError(f"Claude stopped early ({response.stop_reason})")
        return " ".join(block.text for block in response.content if block.type == "text").strip(), response.model

    async def summarize(self, state, recommendations, alerts):
        fallback = {"text": template(state), "source": "template"}
        if not self.enabled:
            return fallback
        kpi = state["kpis"]
        key = (state["run_id"], state["tick"], state["mode"], kpi["pending_recommendations"], kpi["open_alerts"])
        async with self.lock:
            cached = self.cache.get(key)
            if cached and cached[0] > time.monotonic():
                return cached[1]
            grounded = facts(state, recommendations, alerts, self.config.horizon_hours)
            try:
                text, model = await self._generate(grounded)
                if not text:
                    raise BriefingError("Claude returned no text")
                unsupported = unverified_numbers(text, grounded)
                if unsupported:
                    raise BriefingError("Claude used numbers not in the data: " + ", ".join(unsupported[:5]))
                result = {"text": text, "source": "llm", "model": model}
                self.last_error = None
                metrics.briefings.labels("llm").inc()
            except anthropic.APIStatusError as exc:
                result = self._failed(fallback, f"Claude API error {exc.status_code}")
            except anthropic.APIConnectionError:
                result = self._failed(fallback, "Claude API unreachable or timed out")
            except BriefingError as exc:
                result = self._failed(fallback, str(exc))
            # One entry: the key changes every tick, and a failure is not retried for 30 s.
            self.cache = {key: (time.monotonic() + CACHE_SECONDS, result)}
            return result

    def _failed(self, fallback, reason):
        self.last_error = reason[:200]
        metrics.briefings.labels("fallback").inc()
        return {**fallback, "reason": self.last_error}
