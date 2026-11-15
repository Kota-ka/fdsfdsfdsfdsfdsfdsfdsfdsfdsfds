# osu! Relax

> External relax cheat for osu! Standard — auto-clicks circles and holds sliders in sync with any beatmap.

![Platform](https://img.shields.io/badge/platform-Windows-blue?style=flat-square)
![Python](https://img.shields.io/badge/python-3.8+-yellow?style=flat-square)
![osu!](https://img.shields.io/badge/osu!-Stable%20(Latest)-pink?style=flat-square)

---

## Features

- **Auto-detection** — finds your currently selected beatmap, no manual path entry
- **Tap to sync** — press Z/X for the first note and the script takes over
- **Persistent monitor** — stays running, detects beatmap changes as you browse
- **Pause / Resume** — press Escape to pause, press again to resume (timing compensated)
- **Fail / Retry detection** — auto-resets when the map ends or you retry
- **Slider support** — holds sliders for the correct duration with proper velocity calculation
- **Alternating keys** — switches between Z and X for natural stream patterns
- **Clean console** — minimal output, debounced beatmap detection

## How It Works

1. Connects to the running `osu!.exe` process
2. Scans your `Songs` folder for the most recently accessed `.osu` file
3. Parses `[TimingPoints]` and `[HitObjects]` to build a timeline with correct BPM and slider velocity
4. Waits for you to press Z or X (your first tap in-game)
5. Anchors the wall clock to the beatmap timeline and auto-clicks every note
6. Monitors for Escape (pause), map completion, fail, retry, and beatmap changes
7. 
## Usage

### Optional tosu mode

Start [tosu](https://tosu.app/) with its local API on port 24050, then run:

```console
python osu.py --tosu
```

This mode uses `/json/v2` for the current beatmap, game time, pause, focus,
failure state and configured keys. It does not open the osu! process or scan
its memory directly. The original mode below remains available.

Only osu! Standard is supported. Ctrl+C exits. Keys are released on pause,
loss of focus, connection errors and exit; retries reset the note sequence.
Attaching or resuming mid-map skips earlier notes. API frames more than 50 ms
late skip overdue notes instead of sending a burst of inputs. HTTP polling
and tosu's update interval limit timing precision; this mode has not been
validated in a live game and makes no claim of being undetectable.

Run integration logic checks without starting the game:

```console
python -m unittest -v test_tosu
```

### Memory addresses

The tosu mode has no offsets of its own: address resolution is handled by
tosu. Keep tosu updated separately. The original mode scans for the time
address at startup and when osu! restarts. If reading an existing address
fails, it rescans automatically, at most once every two seconds.
This rediscovers addresses using the existing signature; it does not download
or generate new signatures for incompatible osu! builds.

### Smooth aim assist (Stable only)

```console
python osu.py --tosu --aim --aim-debug
```

Or use `osu-relax.exe --tosu --aim --aim-debug` in the Windows build.

No manual calibration is required for the normal Stable viewport. The area is
projected from the actual client rectangle, with Stable's eight game-pixel
vertical shift. It updates when the window moves or resizes. The editor's
grid is **not** the gameplay viewport and must not be used for calibration.

With `--aim`, the default profile is **assist**, requiring hand motion and
limiting how far the correction can displace the cursor. Holding Ctrl is not
required. Add `--aim-hold-key "right ctrl"`
if you prefer the previous hold-to-activate behavior. Pause, failure, loss of
focus and API errors stop assistance. `--aim-debug` reports blocking reasons.

Choose a configured profile:

```console
osu-relax.exe --tosu --aim --aim-profile assist --aim-debug
```

| Profile | Strength | Response | Capture radius | Max correction | Max speed |
| --- | --- | --- | --- | --- | --- |
| `assist` (default) | 0.65 | 90 ms | 100 units | 24 units | 300 units/s |
| `strong` | 1.00 | 65 ms | 640 units | 640 units | 16000 units/s |
| `aimbot` (legacy alias) | same as strong | same | same | same | same |

All presets require hand movement. The old aimbot name remains accepted for
existing commands but now uses the same limits as strong assistance.
Individual numeric options override the chosen profile. Speed and acceleration
are bounded, including target changes. Correction runs in its own loop with a
1 ms scheduling target, independent of the HTTP game-state fetch. A stale model,
pause or loss of focus stops it. Startup prints the full effective configuration.
Debug output includes cursor, target, offset and requested/observed Windows
cursor positions, so an input-path failure can be distinguished from low pull.
Each debug interval also reports how many corrections were requested and a
count of blocking/active states, rather than showing only one sampled state.
Verification reads never replace the last commanded position: mouse movement
between a write and its diagnostic read must remain part of the next hand delta.

The hand + offset model estimates physical movement separately from the
generated correction. Fractional offsets survive integer cursor rounding.
Idle hands do not initiate a pull, moving away from a circle target
blocks it, and reaching the hit circle preserves the current offset rather
than dragging the cursor to the centre. Correction is calculated from the
current assisted position, not from an old absolute mouse position. Applied
offset is gradually absorbed into a moving reference without moving the cursor
backward. Missing/invalid targets and manual escape pass the current cursor
through unchanged; they never play an inverse correction back to the hand.
The return-time options now control this reference release, not cursor rewind.
Hand influence scales physical deltas only during eligible active correction;
the default is 1 and the strong preset uses 0.35. Outside correction, input passes
through at full strength. Circle targets advance after arrival and, with Relax
enabled, the click has been sent. Unreached notes expire using the map's Meh
window converted to game milliseconds (50 ms fallback if unavailable), instead of
blocking the map indefinitely. With aim enabled, Relax checks the current cursor
position before clicking; a late but reachable note is not consumed early.
Clicks and key releases share the aim worker's interpolated clock instead of
waiting for the next HTTP response. The clock anchor uses the request midpoint
to reduce receive-delay bias. Arrival is based on observed cursor position,
not the point commanded in that tick. Losing focus or a stale feed releases keys.
An inner-circle margin permits a short early motion handoff while keeping click
ordering tied to each note's timing. Dense streams shorten circle key holds and
prefer an available key to avoid releasing an active slider. Stable-position
observation is capped by the Great window. Decorative curves shrink when the
travel budget is tight.
Sliders remain selected until their full duration ends, and their heads are held
until the click is confirmed. This confirms a Windows keypress, not a game judgement.
Spinner start/end times are parsed from the map. Aim releases the cursor during
spinners and resumes after their end; Relax holds a key for their full remaining
duration, including pause/resume or attaching mid-spinner. Rotation stays manual.
Direction is filtered over a short time window, angular and time weights ease
in gradually, and correction speed is modulated by hand speed when motion is
required. No random jitter is added.

With `--aim-no-require-motion` and nonzero curvature, jumps use timed quintic
curves with zero acceleration at their endpoints. Entry velocity and the next
note shape the curve; position, velocity and acceleration guide the spring.
The destination must be inside the configured capture radius. Large, fast
jumps also need sufficient speed, acceleration and local correction limits;
AR alone does not determine whether those limits can reach the next note.
The motion worker advances through the map's linked targets without waiting
for another HTTP frame, while retaining each slider through its duration.
U-turns carry a transverse tangent rather than forcing zero velocity at the note.
Optional wiggle varies each jump with smooth, fixed-per-segment waves. Its
position, velocity and acceleration contributions vanish at both hit points.
Available speed and acceleration bound its size in fast jumps. It does not
alter slider geometry. Relax checks its own pending head independently of the
aim target and uses a configurable inner click radius to avoid edge clicks.
With idle dance enabled and motion requirement disabled, gameplay gaps use a
smoothly joined ribbon orbit. The next note takes over inside the capture window;
pause, loss of focus and active spinners release control. Curvature sets its size.

The optional automatic timing feedback learns from groups of eight fresh hit
errors reported by osu!, keeping its correction within ±30 game milliseconds.
Very late positional errors are excluded using the Great window. After the
initial calibration, feedback changes in smaller bounded steps to avoid chasing
individual timing outliers.
The correction is blended over time; it is not a guarantee of perfect judgements.
The panel displays applied correction and the most recent median hit error.
The historical per-side angle remains compatible: both 180 and 360 mean full
coverage. Values up to 360 are accepted without rejecting the saved settings.

Sliders remain selected for their full duration. Linear, Bezier, perfect arc
and Catmull paths are sampled, fitted to the declared pixel length and traversed
with the map's duration and repeat count. With motion required, slider correction
remains lateral to the hand movement. With motion disabled, it follows both axes
and uses path velocity and acceleration to reduce spring lag while preserving
physical input. A short tangent window eases corners and repeats; intermediate
slider waypoints no longer zero the spring's momentum. Repeated telemetry clocks
do not restart interpolation or make the sampled path run backward.
Optional slider smoothing rounds corners and repeats using a radius-bounded
window. It preserves head targeting and keeps the stored map path unchanged.
Curve sampling is approximate and does not reproduce every Stable
rounding detail. Malformed geometry skips curve assistance while preserving
otherwise valid click/hold timing.

Stable stacking is applied to circle and slider positions using the map's
format version, stack leniency and approach rate (including HR/EZ). Heads,
slider paths, the next-note curve and click readiness share the same offset.
The raw map geometry remains unchanged; CS-dependent offsets are projected after
Hard Rock mirroring. The MIT reference attribution is in THIRD_PARTY_NOTICES.txt.

The values below are the assist profile's baseline; strong/aimbot override them.

| Option | Assist value | Meaning |
| --- | --- | --- |
| `--aim-strength` | 0.65 | Maximum pull fraction, 0 to 1 |
| `--aim-smooth-ms` | 90 | Pull response time; higher is gentler |
| `--aim-hand-gain` | 1 | Physical mouse contribution during active correction; 0.35 keeps 35% for microadjustments |
| `--aim-curvature` | 0 | Timed jump curves, 0 to 1; requires motion requirement disabled |
| `--aim-wiggle` | 0 | Smooth per-jump path variation, 0 to 1; requires motion requirement disabled |
| `--aim-click-margin` | 0.9 | Safe fraction of a note radius accepted by Relax, 0.5 to 1 |
| `--aim-damping-ratio` | 1 | 1 is critically damped; 0.6 adds elastic response, with more damping near timed notes |
| `--aim-coast-ms` | 0 | Inertial deceleration during gameplay gaps; does not apply after pause/focus loss |
| `--aim-click-settle-ms` | 0 | Continuous real-time observation inside the note before clicking; capped by Great window |
| `--aim-slider-smooth-ms` | 0 | Slider-only corner/repeat smoothing, bounded by circle radius; head targeting is unchanged |
| `--aim-idle-dance` | off | Smooth gameplay-gap loops; requires nonzero curvature and motion requirement disabled |
| `--aim-radius` | 100 | Capture radius in beatmap units |
| `--aim-timing-ms` | 220 | Lookahead window before the note |
| `--aim-cone-deg` | 90 | Maximum angle between hand motion and a circle target |
| `--aim-return-close-ms` | 140 | Reference-release time near a target; no cursor rewind |
| `--aim-return-far-ms` | 180 | Reference-release time without a target; no cursor rewind |
| `--aim-motion-curve` | 1 | Exponent shaping the time-based pull ramp |
| `--aim-max-speed` | 300 | Maximum offset change in beatmap units per second |
| `--aim-max-offset` | 24 | Maximum correction radius around the moving reference |
| `--aim-max-accel` | 1800 | Maximum correction acceleration in beatmap units/s squared |
| `--aim-tablet` | off | Interpret new cursor samples as absolute positions |
| `--aim-no-require-motion` | off | Permit pull with an idle hand |
| `--aim-no-note-hold` | off | Continue pulling inside the hit circle |

Tablet mode is based on observed Windows cursor positions, not driver-level
pen packets. A driver that continually overrides cursor output can still
defeat correction. Raw input is explicitly unsupported and reports a reason
instead of silently doing nothing; disable it for this cursor-based mode.

For custom letterboxing, manually override the automatic projection: during
gameplay or its pause screen, F6 records the top-left of the playfield and F7
records the bottom-right. A bad rectangle restores automatic projection.
A valid manual override must be redone after moving/resizing the window.

Hard Rock's vertical flip is supported for circles and slider paths. Spinner
motion, lazer and Random/Mirror transforms are not implemented.
Correction speed follows the
selected profile or `--aim-max-speed`. Timing still depends on tosu's update interval.
Live-game motion and tablet drivers have not been validated.

The implementation remains Python and uses the existing tosu integration.
The hand/offset and gating design is adapted from
[lame-osu](https://github.com/OnClickEvent/lame-osu); the MIT notice is included
in `THIRD_PARTY_NOTICES.txt` and in the Windows bundle.

Run all checks:

```console
python -m unittest -v test_tosu test_aim test_aim_physics test_slider_path test_offsets
```

### Native tosu configuration + FOV overlay

```powershell
osu-relax.exe --tosu --aim --overlay
```

Start tosu first. Its installation folder is detected from the running process;
use `--tosu-dir "C:\path\to\tosu"` if needed. This installs the local `AimAssist`
overlay, exposes all 26 controls through tosu's native Settings API and adds
one fullscreen layer to the active in-game profile. Existing layers are
preserved; the previous layout is backed up in
`tosu/settings/AimAssist.ingame-backup.json`. Enable the in-game overlay in tosu
if it is off. For a different game resolution, adjust this layer's width/height
to fill the game window using tosu's overlay editor.

Open the visual configuration panel:

[Aim Assist settings](http://127.0.0.1:24050/AimAssist/?configure=1)

Settings can also be edited from the Aim Assist entry in the tosu dashboard.
They persist in `tosu/settings/AimAssist.values.json` and are validated/applied
to the running assist within approximately 200 ms. The panel confirms actual
application or displays the validation error; invalid values preserve the last
valid configuration. **Restaurar padrões** restores all 26 canonical defaults,
including visualization and activation, instead of reusing the last CLI preset.
The note timing window is not overlay latency: setting it to 1 ms severely
limits when notes can be assisted. With `--overlay`, saved tosu values take precedence over
CLI values. Without it, CLI behavior is unchanged.

The panel includes every physics setting, activation, optional hold key,
mouse/tablet mode, console diagnostics, automatic click enable/disable, FOV
color/opacity/width, correction limit, target marker, slider path and status.
All visual controls use the same settings as the assist. The larger circle is
the capture radius around the assisted cursor; the dashed circle is the
local correction limit around the moving reference. This is a radius view,
not a camera field-of-view angle.

The transparent HUD lives at
[Aim Assist overlay](http://127.0.0.1:24050/AimAssist/).
It hides live geometry when paused, unfocused, disconnected or stale. The
configuration page labels its centred example as a preview when no game is
active. Opening a browser panel pauses assistance because osu! loses focus.
Native tosu serves the assets and configuration API. Live geometry now uses
a read-only SSE stream on an automatically assigned **127.0.0.1-only** port.
The browser receives fresh frames directly instead of polling a state file.
The cursor is sampled independently of the game's HTTP request with a 1 ms
scheduling target. Changed positions use compact cursor events; full settings,
target and path data are sent only when the game-state model changes, with
an occasional heartbeat for stationary cursors. Game state older than 150 ms
disables live geometry. Input correction has its own 1 ms target loop, while
note timing and slider metadata still depend on tosu's game-state feed.
The panel reports the received cursor data's age; detailed HUD timing is
available with the existing debug setting. The native tosu render FPS and
game/compositor still contribute to the final visible latency. A 1 ms loop is
not a guarantee of 1 ms end-to-end display latency (144 FPS is about 6.94 ms
between frames). Scheduling, input device polling and CPU load also matter.

The stream permits the local tosu origins and uses a per-run URL token;
it exposes no control/write endpoints. A once-per-second snapshot remains
for older clients. Reconnection discovers the new stream when the program
restarts. Stop old versions before launching the updated executable.

Overlay/controller checks: `python -m unittest -v test_tosu_overlay`.

### Building for Windows

The executable is generated at `dist/osu-relax.exe`. Python is not required
on the destination computer. To use tosu, run `osu-relax.exe --tosu` from a
terminal; Ctrl+C exits.

Rebuild on Windows with the project environment:

```powershell
.\.venv\Scripts\python -m pip install -r requirements.txt pyinstaller==6.22.3
.\.venv\Scripts\python -m PyInstaller osu-relax.spec
```

### Original mode

```bash
python osu.py
```

1. Open osu! and select a song
2. Run the script — it connects and waits
3. Start playing — press **Z** or **X** for the first note
4. The script handles the rest

### Command-line

```bash
python osu.py "path/to/beatmap.osu"
```

Or drag-and-drop a `.osu` file onto the script.

### Controls

| Key | Action |
|-----|--------|
| `Z` / `X` | First tap syncs the script |
| `Escape` | Pause / Resume |
| `Escape` (terminal) | Exit |

## Requirements

- **Windows** only
- **Python 3.8** or newer
- **osu! Stable (Latest)** release

## Disclaimer

This project is for educational purposes only. Using cheats violates the [osu! terms of service](https://osu.ppy.sh/wiki/en/Legal/Terms) and may result in a permanent account ban. Use at your own risk.

## License

MIT © [superangelguy](https://github.com/superangelguy)


## Integração tosu: Stable e lazer

Use `osu-relax.exe --tosu --aim --overlay`. O cliente ativo vem da API do tosu.
O campo padrão é projetado automaticamente nos dois clientes; F6/F7 permitem
substituir a projeção em layouts personalizados. Desative raw input para a correção
do cursor pelo Windows. O suporte ao lazer é experimental: osu!standard e mods
convencionais; mods com transformações adicionais são recusados.

A velocidade `play.mods.rate` é usada no relógio e nas trajetórias, incluindo taxas
personalizadas. Uma troca de taxa refaz a curva restante e reinicia o aprendizado
de timing. Mudanças de BPM no mapa continuam usando os timestamps de cada objeto.

Pontos levemente fora do centro dos círculos diminuem em sequências rápidas.
Sliders compactos evitam perseguir pequenas reversões; curvas maiores usam
suavização e desvio lateral limitado. Nas pausas, qualquer movimento manual
interrompe o trajeto antecipado; ele pode retomar depois de 150 ms sem movimento.
Esses comportamentos não garantem greats ou ausência de slider breaks.

Se o tosu informar teclas vazias no lazer, preencha **Tecla de jogo 1** e
**Tecla de jogo 2** no painel da overlay com as teclas usadas no próprio jogo.
O Relax bloqueia teclas vazias ou duplicadas. Antes de iniciar no lazer,
o motor exige avanço do relógio por ao menos 150 ms; espera novamente no retry.
