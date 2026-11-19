"""Human-readable rendering of a run Summary (FR-006)."""

from morning_brew.domain import Summary


def _section(title: str, items: tuple[str, ...]) -> list[str]:
    if not items:
        return []
    return [f"{title}:", *(f"  {item}" for item in items)]


def render_summary(summary: Summary) -> str:
    lines = [f"Run {summary.run_id} ({summary.quality})"]
    if summary.dead_man:
        lines.append("DEAD-MAN: no successful run within the allowed window")
    lines += _section("Degraded because", summary.degraded_reasons)
    lines += _section("Upgraded", summary.upgraded)
    lines += _section("Failed", summary.failed)
    held = f"Held (decided): {summary.decided_holds}"
    if summary.oldest_decided_hold_days is not None:
        held += f", oldest {summary.oldest_decided_hold_days} days"
    lines.append(held)
    lines.append(f"Blocked by a dependency: {summary.blocked_by_dep}")
    lines.append(f"Transient (will retry): {summary.transient}")
    lines += _section("Run these yourself", summary.self_dep_commands)
    lines += _section("Local flagged, Claude cleared", summary.disagreements)
    lines += _section("Running old binary (restart needed)", summary.live_old_binary)
    lines += _section("Anomalies", summary.anomalies)
    return "\n".join(lines)
