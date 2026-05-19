"""Solar-aware AC charger pause controller (#163).

Pure decision function: given the current bank state, the charger's
current on/off, the user's config, and the last state-change time,
return one of three outcomes — force_on, force_off, or unchanged.

Hardware interaction lives in OutputsService.toggle(); this module
never touches a transport, which makes it trivially unit-testable
with synthetic state.

The control law has four gates, evaluated in this order:

1. Disabled or never-configured. Return unchanged.
2. Hard floor. SoC below the hard floor forces the charger ON
   regardless of PV. The floor beats every forecast — if the
   weather predictor is wrong, the bank survives.
3. Recover. SoC below recover_soc (and currently paused) forces
   the charger back ON. This is the "wake up" condition during
   a cloudy spell.
4. Pause. SoC above target_soc AND the bank is net-positive AND
   PV is currently producing at least pv_surplus_w. Pauses the
   charger to stop drawing from the grid / generator.

Hysteresis: cooldown_minutes between any two state changes prevents
flapping on a passing cloud.

Manual override: if the user has just toggled the charger manually,
`last_manual_toggle_at` is newer than `last_auto_change_at`, and we
back off — the user knows something we don't.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


Action = Literal["force_on", "force_off", "unchanged"]


@dataclass(frozen=True)
class PauseCfg:
    """User-facing settings. All percentages are SoC 0..100; pv_surplus_w
    is whole watts. Cooldown in minutes. Sensible defaults sized for a
    typical 200 Ah LFP bank on a UK off-grid van."""
    enabled: bool = False
    charger_output_id: str | None = None
    target_soc:       float = 80.0
    recover_soc:      float = 50.0
    hard_floor_soc:   float = 30.0
    pv_surplus_w:     float = 50.0
    cooldown_minutes: int   = 30

    def validate(self) -> str | None:
        """Return an error string if the config is inconsistent. None
        when valid. Order matters: hard_floor < recover < target with
        a healthy gap on each side."""
        if not (0 <= self.hard_floor_soc < self.recover_soc < self.target_soc <= 100):
            return (
                "thresholds must satisfy "
                "0 <= hard_floor_soc < recover_soc < target_soc <= 100"
            )
        if self.target_soc - self.recover_soc < 10:
            return "target_soc must be at least 10 pp above recover_soc to avoid flapping"
        if self.recover_soc - self.hard_floor_soc < 10:
            return "recover_soc must be at least 10 pp above hard_floor_soc"
        if self.cooldown_minutes < 5:
            return "cooldown_minutes must be >= 5 to avoid relay wear"
        if self.pv_surplus_w < 0:
            return "pv_surplus_w must be non-negative"
        return None


@dataclass(frozen=True)
class BankSnapshot:
    soc_pct:      float    # 0..100
    net_w:        float    # +ve = charging
    pv_active_w:  float    # solar input in watts right now


@dataclass(frozen=True)
class ChargerSnapshot:
    on: bool                       # last observed state of the charger
    last_auto_change_at:   int     # unix-seconds; 0 if never auto-changed
    last_manual_toggle_at: int     # unix-seconds; 0 if never manually toggled


@dataclass(frozen=True)
class Decision:
    action: Action
    reason: str


def decide(cfg: PauseCfg, bank: BankSnapshot, charger: ChargerSnapshot,
           *, now_ts: int) -> Decision:
    if not cfg.enabled or cfg.charger_output_id is None:
        return Decision("unchanged", "rule disabled")

    if charger.last_manual_toggle_at > charger.last_auto_change_at:
        return Decision("unchanged", "respecting manual override")

    if bank.soc_pct < cfg.hard_floor_soc:
        return Decision(
            "force_on",
            f"SoC {bank.soc_pct:.1f}% below hard floor {cfg.hard_floor_soc:.0f}%",
        ) if not charger.on else Decision(
            "unchanged",
            f"hard floor satisfied, charger already on",
        )

    cooldown_s = cfg.cooldown_minutes * 60
    since_last = now_ts - charger.last_auto_change_at
    in_cooldown = (charger.last_auto_change_at > 0 and since_last < cooldown_s)

    if not charger.on:
        if bank.soc_pct < cfg.recover_soc:
            return Decision(
                "force_on",
                f"SoC {bank.soc_pct:.1f}% below recover threshold "
                f"{cfg.recover_soc:.0f}%",
            )
        return Decision("unchanged", "paused, bank still above recover threshold")

    if bank.soc_pct < cfg.target_soc:
        return Decision("unchanged", "below target SoC")
    if bank.net_w <= 0:
        return Decision("unchanged", "bank not net-positive")
    if bank.pv_active_w < cfg.pv_surplus_w:
        return Decision("unchanged", "PV not yet covering load")
    if in_cooldown:
        return Decision(
            "unchanged",
            f"within cooldown window ({since_last // 60} min < "
            f"{cfg.cooldown_minutes} min)",
        )
    return Decision(
        "force_off",
        f"SoC {bank.soc_pct:.1f}% >= target {cfg.target_soc:.0f}%, "
        f"PV {bank.pv_active_w:.0f} W covering",
    )
