# Smart Trainer: project rules

<!--
Rules for anyone changing this code, including Claude (which reads this file at the start of every
session). Keep each rule short, specific and checkable. A rule a tool can enforce (ruff, pyrefly,
pre-commit, a test) belongs in that tool's config instead, and this file just points to it.
-->

## What the app is (and isn't)

Smart Trainer controls a Bluetooth smart trainer (FTMS) during indoor rides, records them
crash-safe and uploads them to training platforms.

- **In scope:** riding (devices, trainer control, workouts, live screen), clean recording, FIT
  export, upload.
- **Out of scope (handled by the platforms, e.g. intervals.icu, Strava):** training metrics
  (NP/IF/TSS), post-ride analysis, workout authoring, FTP tests.

## Architecture

| Package | Responsibility |
|---|---|
| `smart_trainer/ble/` | Bluetooth devices: scanning, FTMS / Cycling Power / HR parsing, fake devices |
| `smart_trainer/core/` | App logic, UI-agnostic: `Engine`, `Ride`, `TrainerController`, `SessionStore`, recorder, workouts |
| `smart_trainer/export/` | FIT export |
| `smart_trainer/sync/` | Connections to platforms (intervals.icu, Strava) |
| `smart_trainer/ui/` | NiceGUI pages and widgets |

Dependency rules:
- `core`, `ble`, `export` and `sync` never import `ui` (checked by `tests/test_architecture.py`).

## State

- **One source of truth.** Every piece of state has exactly one owner. Never keep a copy that
  something has to keep in sync by hand.
- **Settings** (persisted): any page may receive `Settings` and read it freely, but never
  assigns its fields or calls `save()`. Changes go through a method that validates, saves and
  applies them:
  - `settings.update(...)` for a value that is only read when needed (FTP, cues, power matching);
  - `connection.update_settings(...)` for a connection's switches and options;
  - a component method when the change must also act on running objects
    (`engine.set_spiral_guard(on)`).
- **Ride and device state** (ride, controller, devices, snapshot, and the workout selected for
  the next ride): owned by the `core` components. The UI reads it freely but changes it only
  through methods (`ride.set_erg(...)`, `controller.set_manual(mode, value)`), never by assigning
  attributes. All clients (Mac, iPad over `--lan`) share it.
- **UI-only state** (selected page, open dialogs, window geometry, "Load more" limit): lives in
  the page or widget object that uses it, not in a module-level global.
- **Derived state** (status lines, zones, alerts) is computed from the owner's state when needed,
  not stored and updated separately.
- Pages receive the components they use (`ride`, `devices`, `store`, …) and settings through
  their constructor, never the whole `Engine` (see Dependency injection). `register_pages`
  (`ui/pages/__init__.py`) is the only place that wires them; `AppContext` (`ui/context.py`)
  carries the engine and settings there.

## Design: dependency injection and SOLID

**Dependency injection**
- A class receives its collaborators (devices, store, controller, clock, settings, connections)
  through its constructor. It doesn't create them, look them up in a global, or import a singleton.
- Objects are wired in one place: `Engine.from_settings` for the core, `main.py` for the app.
- Optional behaviour is an optional injected object (`None` = off), not a boolean flag plus
  internal state. Example: `TrainerController(cfg, guard: SpiralGuard | None)`.
- Time is injected (`now` / `now_mono` arguments), never read inside logic, so it can be tested.
- Use dataclasses for data and for classes that are mostly state. If a dataclass or injection
  isn't possible, say why in the docstring or the PR.

**SOLID**
- **Single responsibility:** one reason to change per class and per module. If a class's
  docstring needs "and" to describe it, split it.
- **Open/closed:** new behaviour is a new class plugged in (a new connection, device or guard),
  not another `if` branch in existing code. `sync/` connections are the model to follow.
- **Liskov substitution:** every implementation of a base class or `Protocol` works wherever the
  base is expected, fakes included (`ble/fake.py` must behave like real devices).
- **Interface segregation:** depend on the narrowest type that does the job, for example a
  `Protocol` with the two methods you call, not the whole `Engine`.
- **Dependency inversion:** `core` depends on abstractions (`Device`, `Connection`, `Protocol`s),
  never on `ui` or on concrete BLE classes.

**Balance with Simplicity:** inject concrete classes by default. Add a `Protocol` or base class
only when there is a second implementation, and a test fake counts as one.

## Code style

Enforced by tools (don't repeat them here, change the config):
- ruff (`pyproject.toml`): line length 100, complexity ≤ 10, branches ≤ 10, args ≤ 8.
- pyrefly type checking.

Conventions:
- Google-style docstrings with `Args:` / `Returns:` and types, on every function and class
  (ruff's `D` rules check public ones and every `Args:` list).
- Size: split a module past ~400 lines and a function past ~40 lines of code (its docstring
  doesn't count). A long function is usually several steps that want their own names.
- Use a `Protocol` to define the contract when a class has more than one implementation (a
  test fake counts).
- Use meaningful names, no names with one letter
- Comments explain **why**, not what: the reason, the constraint, the trade-off. If a comment
  restates the code, delete it, or rename things until it isn't needed.
- Always comment:
  - constants and thresholds: what they mean and their unit
    (`SPIRAL_RELEASE_RPM = 50  # below this the trainer is let go`);
  - protocol details: BLE byte offsets and flags, FIT scale factors, with the spec section;
  - workarounds for a library, device or platform quirk, with a link to the issue or doc;
  - non-obvious choices ("monotonic clock: wall time jumps on sleep/wake").
- Docstrings say what a function does and its contract. Comments are for how and why inside it.
- No commented-out code (ruff `ERA`), and no history ("changed in #29", "old version"): git
  keeps both.
- No bare `TODO`: write `TODO(#N): …` with an open issue (ruff `TD`), or don't write it.
- Update or delete a comment in the same change that makes it wrong.

Errors and logging:
- Every module that logs does it through `log = logging.getLogger(__name__)`; no `print`.
- A ride must never stop because of a recoverable failure (a BLE command, an upload, a sync):
  catch it at the boundary where it happens, log a warning with the reason, and carry on.
- Catch the narrowest exception you can handle. A broad `except Exception` is allowed only at
  those boundaries, and it always logs.
- Errors the rider should see go to the UI as a short message; details go to the log.
- Never log secrets (API keys, tokens, Strava client secret).

NiceGUI:
- One class per page, built by its `@ui.page` function, which passes in its components and settings.
- No ride or device logic in UI code: callbacks call a `core` method and update the view.
- Refresh timers only read state and update widgets; they never change engine state.
- Blocking work (file I/O, SQLite, FIT export) runs in `asyncio.to_thread`, never in a callback
  or timer on the event loop.
- Build widgets once and update them in place; don't `clear()` and rebuild on every refresh.
- Elements that tests look up get a `.mark("…")`.

## Simplicity

- Prefer the smallest change that works; no abstraction without a second user.
- No new dependency without asking
- Delete code instead of keeping it 'just in case'.

## Testing

- `uv run pytest` must pass; every change comes with tests.
- Coverage must not go down: CI fails under `fail_under` in `pyproject.toml`. Raise it as
  coverage grows.
- UI tests use NiceGUI's simulated `User` (`tests/test_ui.py`); BLE is simulated with
  `smart_trainer/ble/fake.py`.
- A bug fix comes with a test that fails before the fix and passes after.
- Test behaviour through public methods, not private attributes or call counts on internals.
- Inject time (`now` / `now_mono`) instead of sleeping. `asyncio.sleep` is only for letting fake
  devices react, kept short.
- Prefer the fakes (`ble/fake.py`, test doubles that implement the same `Protocol`) over
  `monkeypatch`/mocks.
- Use `@pytest.mark.parametrize` to test several scenarios of the same functionality.
- Use `@pytest.fixture` to share code and objects between tests.
- Tests never touch the network, the real keychain or the user's settings and session folders.
  `tests/conftest.py` gives every test its own app folder and an in-memory keyring; use its
  `settings` fixture and `tmp_path`.

## Workflow

- One GitHub issue → one branch from an up-to-date `main` → one **draft PR** (`Closes #N`).
  Wait for review and merge before starting the next one.
- Plan before non-trivial changes (the `plan-first` skill): goal, files, approach, risks, tests;
  get approval, then implement.
- Every user-visible change gets an entry in `CHANGELOG.md` under `[Unreleased]`.
- Before pushing: `uv run pytest`, `uv run ruff check`, `uv run ruff format --check`,
  `uv run pyrefly check` (pre-commit runs ruff and pyrefly too).
- Never commit `.claude/settings.json` or `.claude/skills/`.
- Commit messages: imperative subject in sentence case, no prefix, ≤ 72 characters
  ("Record the mean power of each second"); a body says why when it isn't obvious.
- Keep PRs reviewable: one concern each, ideally under ~400 changed lines excluding tests. Split
  anything bigger before opening it.
- Refactor PRs don't change behaviour: no CHANGELOG entry unless users notice, existing tests pass
  unchanged (apart from moved imports), and new tests pin the behaviour before code moves.

## Commands

```bash
uv run smart-trainer --fake        # demo mode with simulated devices
uv run pytest                      # tests
uv run ruff check && uv run ruff format --check
uv run pyrefly check
```

Releasing: see [README.md → Releasing](README.md#releasing).
