"""A few built-in workouts, handy offline and for trying the app."""

from smart_trainer.core.workout import Step, Workout


def builtin_workouts() -> list[Workout]:
    """Build the list of built-in workouts.

    Returns:
        list[Workout]: Sweet spot, VO2max and a short test ramp workout.
    """
    ss = [Step(600, 0.45, 0.75, text="Warmup")]
    for number in range(3):
        ss += [
            Step(600, 0.90, text=f"Sweet spot {number + 1}/3", cadence=90),
            Step(300, 0.55, text="Recover"),
        ]
    ss.append(Step(600, 0.60, 0.40, text="Cooldown"))

    vo2 = [Step(600, 0.45, 0.75, text="Warmup"), Step(300, 0.60, text="Easy")]
    for number in range(5):
        vo2 += [
            Step(180, 1.15, text=f"VO2 {number + 1}/5", cadence=100),
            Step(180, 0.50, text="Recover"),
        ]
    vo2.append(Step(600, 0.55, 0.40, text="Cooldown"))

    ramp = [Step(60, 0.40 + 0.06 * number, text=f"Ramp step {number + 1}") for number in range(12)]
    return [
        Workout("Sweet Spot 3x10", ss, "3 x 10 min at 90% FTP"),
        Workout("VO2max 5x3", vo2, "5 x 3 min at 115% FTP"),
        Workout("Quick test ramp (12 min)", ramp, "Short ramp to check ERG response"),
    ]
