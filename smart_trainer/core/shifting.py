"""Virtual shifting: gears for a bike that has a single physical cog (e.g. a Zwift Cog).

The gear model
==============

The chain never moves, so a "gear" is a ratio multiplier ``k`` applied to the rider's real
gear: ``k = 1`` is the gear the chain is actually in, ``k = 1.1`` is a 10 % harder gear, and
so on. With the same cadence in gear ``k``, the rear wheel would turn ``k`` times as fast, so
the rider is riding a *virtual road* at ``k`` times the speed the trainer measures. The app
makes the trainer resist like that virtual road, using only the slope command.

Notation (SI units, grades as fractions)::

    v      speed the trainer measures, m/s
    k      gear ratio multiplier (virtual gear / real gear)
    G      grade of the road being ridden (the slider value)
    G'     grade sent to the trainer instead
    m      rider + bike mass, kg
    g      gravity, 9.81 m/s²
    crr    rolling resistance coefficient    (SIM_CRR, sent with every slope command)
    cw     wind resistance coefficient, kg/m (SIM_CW, sent with every slope command)

1. In slope mode an FTMS trainer resists with the force of the road it simulates::

       F_trainer(v, G') = m·g·(crr + G') + cw·v²

2. On the virtual road the rider moves at ``k·v``. Keeping that speed needs the power::

       P_virtual = F_road(k·v)·(k·v) = [m·g·(crr + G) + cw·k²·v²]·k·v

3. The trainer turns at ``v``, so it must resist with ``P_virtual / v`` for the rider to
   put out exactly that power::

       F_needed = k·m·g·(crr + G) + k³·cw·v²

4. Setting ``F_trainer(v, G') = F_needed`` and solving for ``G'``::

       G' = k·(crr + G) − crr + cw·v²·(k³ − 1) / (m·g)

   The first part scales the climbing and rolling force with the gear; the second part is
   the extra air drag of riding ``k`` times as fast. ``k = 1`` gives ``G' = G``.

On the flat at 36 km/h, one gear up (``k = 1.1``) sends about +2 % grade and three gears up
about +8.5 %: on the flat almost all of the effect comes from the air drag part, which is why
the speed is needed. Without a speed reading only the first part applies, so shifting then
mostly changes the feel on climbs.

Assumptions and limits:

- ``m`` is the rider's weight from the settings plus ``BIKE_KG``. FTMS has no way to send the
  rider mass, so the trainer uses its own value; if it differs a lot, the drag part comes out
  too strong or too weak. ``crr`` and ``cw`` are exact: the app sends them itself.
- The result is clamped to what FTMS accepts (``SIM_MAX_GRADE_PCT``), so the hardest gears
  may change nothing at high speed or on steep climbs.
- Flywheel inertia (the feel of accelerating) is not modelled: the trainer keeps its own.
- The model only exists in slope mode: resistance mode has no speed or force model to scale,
  and ERG sets the power directly.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from smart_trainer.ble.parsers.ftms import SIM_CRR, SIM_CW, SIM_MAX_GRADE_PCT

GRAVITY = 9.81  # m/s²
BIKE_KG = 9.0  # bike mass in kg, added to the rider's weight for the virtual road
GEAR_COUNT = 15  # number of virtual gears, numbered 1 (easiest) to GEAR_COUNT (hardest)
NEUTRAL_GEAR = 8  # the gear the chain is really in: ratio multiplier 1
# Ratio multiplier between neighbouring gears: about the jump between cogs of a road
# cassette, giving 1.1**-7 ≈ 0.51× (gear 1) to 1.1**7 ≈ 1.95× (gear 15).
GEAR_STEP = 1.1
# Speed resolution in km/h used by the model: the grade follows the speed, and rounding keeps
# sensor noise from changing the command (and resending it) on every tick.
SPEED_RESOLUTION_KMH = 1.0


def gear_ratio(gear: int) -> float:
    """The ratio multiplier ``k`` of a gear, relative to the real gear.

    Args:
        gear (int): Gear number, 1 to ``GEAR_COUNT``.

    Returns:
        float: ``GEAR_STEP ** (gear - NEUTRAL_GEAR)``; 1 in the neutral gear.
    """
    return GEAR_STEP ** (gear - NEUTRAL_GEAR)


def equivalent_grade(grade_pct: float, ratio: float, speed_mps: float, mass_kg: float) -> float:
    """The grade that makes the trainer resist like the road ridden in a virtual gear.

    Implements ``G' = k·(crr + G) − crr + cw·v²·(k³ − 1) / (m·g)``; see the module docstring
    for the derivation.

    Args:
        grade_pct (float): Grade of the road being ridden, in %.
        ratio (float): Gear ratio multiplier ``k`` (1 = the real gear).
        speed_mps (float): Speed the trainer measures, in m/s (0 if unknown).
        mass_kg (float): Rider + bike mass, in kg.

    Returns:
        float: The grade to send, in %, clamped to ±``SIM_MAX_GRADE_PCT``.
    """
    grade = grade_pct / 100
    climb = ratio * (SIM_CRR + grade) - SIM_CRR
    drag = SIM_CW * speed_mps**2 * (ratio**3 - 1) / (mass_kg * GRAVITY)
    return max(-SIM_MAX_GRADE_PCT, min((climb + drag) * 100, SIM_MAX_GRADE_PCT))


@dataclass
class VirtualGearbox:
    """The virtual gear the rider is in, and the slope command it turns into.

    Attributes:
        rider_kg (Callable[[], float]): Returns the rider's weight in kg; read on every
            command so a weight change in the settings applies right away.
        gear (int): Current gear, 1 (easiest) to ``GEAR_COUNT``; starts in the real gear.
    """

    rider_kg: Callable[[], float]
    gear: int = NEUTRAL_GEAR

    def shift(self, steps: int) -> None:
        """Shift up (positive) or down (negative); stops at the first and last gear.

        Args:
            steps (int): Gears to move, e.g. ``1`` for one gear harder.
        """
        self.gear = max(1, min(self.gear + steps, GEAR_COUNT))

    def grade_for(self, grade_pct: float, speed_kmh: float | None) -> float:
        """The grade to send to the trainer for a road grade in the current gear.

        Args:
            grade_pct (float): Grade of the road being ridden, in %.
            speed_kmh (float | None): Measured speed in km/h; ``None`` if unknown, which
                leaves out the air drag part of the model.

        Returns:
            float: The grade to send, in %.
        """
        speed = round((speed_kmh or 0.0) / SPEED_RESOLUTION_KMH) * SPEED_RESOLUTION_KMH
        mass = self.rider_kg() + BIKE_KG
        return equivalent_grade(grade_pct, gear_ratio(self.gear), speed / 3.6, mass)
