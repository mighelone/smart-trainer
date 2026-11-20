"""Interval cues: a beep and a toast shortly before each step change."""

from __future__ import annotations

from collections.abc import Callable

from nicegui import Client, ui

from smart_trainer.core.ride import Ride
from smart_trainer.ui.widgets.text import next_step_text

# Defines window.smartTrainerBeep(count) once per page: short beeps made with the Web Audio
# API (no sound file). Browsers only allow audio after a user gesture, so the first click or
# key press on the page unlocks it (pressing Start does).
BEEP_JS = """
window.smartTrainerBeep ??= (() => {
  let ctx = null;
  const audio = () => (ctx ??= new (window.AudioContext || window.webkitAudioContext)());
  const unlock = () => { if (audio().state === "suspended") audio().resume(); };
  for (const ev of ["pointerdown", "keydown"]) {
    document.addEventListener(ev, unlock, {capture: true});
  }
  return (count = 2) => {
    const c = audio();
    if (c.state === "suspended") c.resume();
    const t0 = c.currentTime + 0.05;
    for (let i = 0; i < count; i++) {
      const osc = c.createOscillator(), gain = c.createGain();
      const t = t0 + i * 0.3;
      osc.type = "square";  // rich in harmonics: much louder than a sine at the same level
      osc.frequency.value = 880;
      gain.gain.setValueAtTime(0.0001, t);
      gain.gain.exponentialRampToValueAtTime(0.8, t + 0.01);
      gain.gain.setValueAtTime(0.8, t + 0.15);
      gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.2);
      osc.connect(gain).connect(c.destination);
      osc.start(t);
      osc.stop(t + 0.21);
    }
  };
})();
"""


class CuePlayer:
    """Plays a beep and shows the next step when the ride announces a step change.

    Create it while building a page: cues raised before the page opened aren't played.

    Attributes:
        BEEPS (int): Beeps per cue.
    """

    BEEPS = 2

    def __init__(self, ride: Ride, enabled: Callable[[], bool]) -> None:
        """Install the beep in the page and remember the cues already raised.

        Args:
            ride (Ride): The ride whose cues are played.
            enabled (Callable[[], bool]): Whether cues are on (read at each cue, so
                the Settings switch applies right away).
        """
        self.ride = ride
        self.enabled = enabled
        self.client = ui.context.client
        self.seen = ride.cue.seq if ride.cue else 0
        self.install()

    @staticmethod
    def install() -> None:
        """Define the beep in the page being built."""
        ui.add_head_html(f"<script>{BEEP_JS}</script>")

    def check(self) -> None:
        """Play the latest cue if it's new (and cues are on)."""
        cue, runner = self.ride.cue, self.ride.runner
        if cue is None or cue.seq <= self.seen:
            return
        self.seen = cue.seq
        if not self.enabled() or runner is None:
            return
        self.beep(self.client, self.BEEPS)
        text = next_step_text(cue.next_step, runner.ftp, runner.intensity)
        ui.notify(f"In {cue.in_s:.0f} s · {text}", position="top", timeout=cue.in_s * 1000)

    @staticmethod
    def beep(client: Client, count: int = 2) -> None:
        """Play the beeps in a page.

        Args:
            client (Client): The page's client.
            count (int): Number of beeps.
        """
        client.run_javascript(f"window.smartTrainerBeep?.({count})")
