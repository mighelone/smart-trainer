"""A few built-in workouts, handy offline and for trying the app."""

from smart_trainer.core.workout import Step, Workout


def builtin_workouts() -> list[Workout]:
    ss = [Step(600, 0.45, 0.75, text="Warmup")]
    for i in range(3):
        ss += [
            Step(600, 0.90, text=f"Sweet spot {i + 1}/3", cadence=90),
            Step(300, 0.55, text="Recover"),
        ]
    ss.append(Step(600, 0.60, 0.40, text="Cooldown"))

    vo2 = [Step(600, 0.45, 0.75, text="Warmup"), Step(300, 0.60, text="Easy")]
    for i in range(5):
        vo2 += [
            Step(180, 1.15, text=f"VO2 {i + 1}/5", cadence=100),
            Step(180, 0.50, text="Recover"),
        ]
    vo2.append(Step(600, 0.55, 0.40, text="Cooldown"))

    ramp = [Step(60, 0.40 + 0.06 * i, text=f"Ramp step {i + 1}") for i in range(12)]
    return [
        Workout("Sweet Spot 3x10", ss, "3 x 10 min at 90% FTP"),
        Workout("VO2max 5x3", vo2, "5 x 3 min at 115% FTP"),
        Workout("Quick test ramp (12 min)", ramp, "Short ramp to check ERG response"),
    ]
