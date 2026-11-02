"""Agent selection, headroom routing gate and routing decisions."""

from __future__ import annotations

from time import time as wall_clock

from ..agent_run import AgentRunStatus
from ..failure import (
    Failure,
    parse_retry_after_seconds,
)
from ..headroom import REJECT, GateVerdict, HeadroomGate, format_utc, merge_verdicts
from ..events import Event, EventType
from ..registry import DetectedAgent
from ..tasks import Task


class RoutingMixin:
    """WorkflowEngine methods: agent selection, headroom routing gate and routing decisions."""

    def _historical_performance(self, workflow_id: str) -> dict[str, float]:
        """Return deterministic recent success rates by agent identifier."""
        try:
            runs = self.state.list_agent_runs(workflow_id, limit=200)
        except Exception:
            return {}
        totals: dict[str, int] = {}
        successes: dict[str, int] = {}
        try:
            for run in runs:
                if not run.agent:
                    continue
                totals[run.agent] = totals.get(run.agent, 0) + 1
                if run.status is AgentRunStatus.COMPLETED and (
                    run.exit_code is None or run.exit_code == 0
                ):
                    successes[run.agent] = successes.get(run.agent, 0) + 1
        except Exception:
            return {}
        return {agent: successes.get(agent, 0) / total for agent, total in totals.items() if total}

    def _repository_characteristics(self, working_directory: object) -> dict[str, object]:
        """Bounded repository hints for the router.

        Inspection is scoring input only, so any failure degrades to `{}` and
        selection still proceeds through the preference/fallback path.
        """
        if not working_directory:
            return {}
        try:
            return self.repo_inspector.routing_flags(working_directory)
        except Exception:
            return {}

    def _select_agent(
        self,
        task: Task,
        excluded: set[str],
        working_directory: object = None,
        repository_characteristics: dict[str, object] | None = None,
        routing_gate: tuple[dict[str, GateVerdict], list[str]] | None = None,
    ) -> DetectedAgent | None:
        """Select through the router when available, otherwise use legacy selection.

        ``repository_characteristics`` lets async callers pass flags computed
        off the event loop; when omitted they are computed here.  The same
        holds for ``routing_gate`` (headroom verdicts and cooldowns).
        """
        router = getattr(self, "router", None)
        if router is not None:
            try:
                profiles = self.registry.profiles()
            except Exception:
                profiles = None
            if isinstance(profiles, dict):
                gate_verdicts, gate_notes = (
                    routing_gate if routing_gate is not None else self._routing_gate(task)
                )
                gate_rejected = {name for name, verdict in gate_verdicts.items() if verdict.rejects}
                self._gate_rejections.pop(task.id, None)
                try:
                    decision = router.route(
                        role=task.role,
                        task_description=task.description,
                        repository_characteristics=(
                            repository_characteristics
                            if repository_characteristics is not None
                            else self._repository_characteristics(working_directory)
                        ),
                        # No hard capability filter here: the router still scores
                        # role/task fit, while role gates, availability, explicit
                        # user preferences, and exclusions preserve the legacy path.
                        required_capabilities=(),
                        available_agents=tuple(profiles.values()),
                        user_preferences=self.config.role_preferences.get(task.role, ()),
                        historical_performance=self._historical_performance(task.workflow_id),
                        excluded=excluded,
                        **(
                            {"gate_verdicts": gate_verdicts, "gate_notes": gate_notes}
                            if gate_verdicts or gate_notes
                            else {}
                        ),
                    )
                except Exception:
                    return self.registry.select(task.role, set(excluded) | gate_rejected)
                selected = getattr(decision, "selected_agent", None)
                identifier = getattr(selected, "identifier", None)
                if not identifier and gate_rejected:
                    self._gate_rejections[task.id] = tuple(
                        f"{name}: {'; '.join(gate_verdicts[name].reasons)}"
                        for name in sorted(gate_rejected)
                    )
                if identifier:
                    try:
                        agent = self.registry.get(identifier)
                        self._record_routing_decision(task, decision, agent.config.name)
                        return agent
                    except (KeyError, TypeError):
                        # A stale router/registry mapping must not silently
                        # execute a different agent than the persisted decision.
                        fallback = self.registry.select(task.role, set(excluded) | gate_rejected)
                        executed = fallback.config.name if fallback is not None else None
                        self._record_routing_decision(task, decision, executed)
                        return fallback
                self._record_routing_decision(task, decision, None)
                return None
        return self.registry.select(task.role, excluded)

    def _routing_gate_active(self) -> bool:
        return (
            getattr(self, "headroom_gate", None) is not None
            or getattr(self.config, "usage_limits", None) is not None
        )

    def _routing_gate(self, task: Task) -> tuple[dict[str, GateVerdict], list[str]]:
        """Opt-in verdicts from the headroom gate and usage-limit cooldowns.

        Both sources fail open: an unusable headroom document or an unreadable
        cooldown table yields no verdicts (routing unchanged) and is logged.
        """
        notes: list[str] = []
        headroom_verdicts: dict[str, GateVerdict] = {}
        cooldown_verdicts: dict[str, GateVerdict] = {}
        if not self._routing_gate_active():
            return {}, notes
        try:
            now = float(self.clock())
        except Exception:
            now = wall_clock()
        gate = getattr(self, "headroom_gate", None)
        if gate is not None:
            headroom_verdicts, snapshot = gate.check(now)
            notes.append(HeadroomGate.note(snapshot))
            if snapshot.usable:
                self._headroom_last_unavailable = None
            else:
                key = f"{snapshot.status}: {snapshot.detail}"
                if key != self._headroom_last_unavailable:
                    # Logged once per distinct cause; every routing decision
                    # still carries the note in its constraints.
                    self._headroom_last_unavailable = key
                    try:
                        self.state.event(
                            task.workflow_id,
                            task.id,
                            "headroom-unavailable",
                            f"Headroom gate inactive ({key}); routing unchanged.",
                        )
                    except Exception as error:
                        self.degradation.record(
                            "event.headroom_unavailable",
                            error,
                            workflow_id=task.workflow_id,
                            task_id=task.id,
                        )
        if getattr(self.config, "usage_limits", None) is not None:
            try:
                cooldowns = self.state.active_agent_cooldowns(now)
            except Exception as error:
                self.degradation.record(
                    "state.agent_cooldowns",
                    error,
                    workflow_id=task.workflow_id,
                    task_id=task.id,
                )
                cooldowns = {}
            for agent, row in cooldowns.items():
                reason = row.get("reason")
                text = f"usage limit cooldown until {format_utc(row.get('until_epoch'))}"
                if reason:
                    text += f" ({reason})"
                cooldown_verdicts[agent] = GateVerdict(REJECT, (text,))
            if cooldowns:
                notes.append("usage-limit cooldowns: " + ", ".join(sorted(cooldowns)))
        return merge_verdicts(headroom_verdicts, cooldown_verdicts), notes

    def _record_usage_limit_cooldown(self, task: Task, failure: Failure | None, text: str) -> None:
        """Persist the failed agent's cooldown so it survives a restart."""
        limits = getattr(self.config, "usage_limits", None)
        agent = task.assigned_agent
        if limits is None or not agent:
            return
        try:
            now = float(self.clock())
        except Exception:
            now = wall_clock()
        stated = parse_retry_after_seconds(text)
        wait = stated if stated is not None else limits.default_cooldown_seconds
        wait = min(max(wait, 1.0), limits.max_cooldown_seconds)
        basis = "stated by the agent" if stated is not None else "default cooldown"
        until = now + wait
        try:
            self.state.set_agent_cooldown(
                agent,
                until,
                f"usage limit hit; {basis}",
                failure_id=getattr(failure, "id", None),
                workflow_id=task.workflow_id,
                task_id=task.id,
            )
            self.state.event(
                task.workflow_id,
                task.id,
                "agent-cooldown",
                f"{agent} hit a usage limit; not routed until {format_utc(until)} ({basis}).",
            )
        except Exception as error:
            self.degradation.record(
                "state.agent_cooldowns",
                error,
                workflow_id=task.workflow_id,
                task_id=task.id,
            )

    def _record_routing_decision(
        self, task: Task, decision, executed_agent: str | None = None
    ) -> None:
        """Persist an explainable routing decision without leaking metadata."""
        try:
            payload = decision.to_dict()
            selected_profile = payload.get("selected_profile")
            if isinstance(selected_profile, dict):
                selected_profile["metadata"] = {}
            payload["executed_agent"] = executed_agent
            selected = payload.get("selected_agent") or "no agent"
            detail = (
                selected
                if executed_agent in (None, selected)
                else f"{selected} (executed {executed_agent})"
            )
            self.state.record_typed_event(
                Event(
                    workflow_id=task.workflow_id,
                    task_id=task.id,
                    type=EventType.ROUTING_DECISION,
                    message=f"Routed {task.role} to {detail}",
                    payload=payload,
                )
            )
        except Exception as error:
            # A8 SAFE_TO_DEGRADE: selection already happened and the run must
            # proceed. The explainability record is reported as degraded.
            self.degradation.record(
                "event.routing_decision",
                error,
                workflow_id=task.workflow_id,
                task_id=task.id,
            )
