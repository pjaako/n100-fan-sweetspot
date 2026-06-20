# CLAUDE.md — n100-fan-sweetspot

## Goal

Find the BIOS fan curve that keeps the machine **inaudible at 2 meters** for normal operation, while handling corner-case power spikes as quietly as possible.

"Inaudible" = fan noise at 2m ≤ ambient + 3 dB (just-noticeable difference). `PWM_inaudible` is the highest PWM that stays at or below that threshold. The noise sweep goes up to +6 dB above baseline before stopping — to gather data beyond the threshold for context. Automated with a mic on the machine; external noise meter provides the ADC→dB(A) calibration reference only.

Two inaudible setpoints are needed — daytime and nighttime — because ambient noise floors differ significantly. The fan curve stays flat at the inaudible PWM for that time of day, and only ramps above it for thermal emergencies.

**Primary metric is dB(A) at 2m, not PWM.** PWM→RPM→dB(A) is nonlinear and fan-specific. PWM is a control input, not the goal.

## Hardware — confirmed values, don't re-derive

- **Board**: ASRock N100DC-ITX
- **CPU**: Intel N100 (Alder Lake-N), 4 Gracemont cores, max single-core turbo 3.4 GHz
- **hwmon1** = coretemp, **hwmon2** = NCT6798
- Fan: `hwmon2/fan2_input` — PWM: `hwmon2/pwm2` — mode: `hwmon2/pwm2_enable` (1=manual, 5=auto)
- **Tj Max** = 105°C (confirmed via `hwmon1/temp1_crit`)
- **TCC offset** = 10°C (confirmed via `rdmsr -f 29:24 0x1a2`), **locked** (bit 31 = 1, not writable)
- **Throttle starts at 95°C** — used as sweep stop condition in `fan_sweep.py`
- Max all-core sustained turbo under stress-ng matrixprod: ~2900 MHz, ~13W hard ceiling — workload-limited (doesn't fully use FMA3/AVX2)
- Max all-core sustained turbo under mprime Small FFTs (FMA3): ~2900 MHz, **~17W peak** — HWP thermal management then settles to ~15.5W steady-state
- At BIOS default PL1=10W, CPU throttles to ~2500 MHz after ~28s (PL2 burst window expires)
- **RAPL PL1 is writable** (tested up to 25W); PL2 = 25W; PL4 = 78W
- TCC MSR is locked — Tj Max and offset cannot be changed in software

## Scripts & data layout

```
fan_sweep.py          — main sweep script; key constants at top
fan_data/
  runs.csv            — index: filename, pl1_w, pl2_w, pwm_start, pwm_step, started_at, notes
  pl1_10w_20260615T2014.csv
  pl1_25w_20260615T2058.csv
  ...
```

Filename auto-generated from `PL1_TARGET` and start time. `runs.csv` gets a row appended on each run.

## Experiment sequence

**Exp 1 — Daytime `PWM_day`**
No load. Fan at PWM=0 (off) — record baseline dB(A). Step PWM up in steps of 5, dwelling at each step, until noise crosses +6 dB above baseline. `PWM_day` = last step that was ≤ baseline + 3 dB. Automated with a mic; noise meter used once to calibrate ADC→dB(A).

**Exp 2 — Nighttime `PWM_night`**
Same procedure at night. Lower ambient → stricter threshold → `PWM_night` ≤ `PWM_day`.

**Exp 3 — T(P) at `PWM_day`**
Fix fan at `PWM_day`. Run mprime. Sweep PL1=PL2 from 3W to 18W in steps of 3W, waiting for thermal steady state at each step. Record steady-state T(P). 18W is the unconstrained ceiling — mprime peaks at ~17W.

**Exp 4 — T(P) at `PWM_night`**
Same as Exp 3 with fan fixed at `PWM_night`.

**Output**
Two T(P) curves. Where T stays below **90°C** (5°C margin below TCC — appropriate for a non-performance-critical home server), the machine is thermally safe at that silent PWM. The BIOS fan curve is: flat at `PWM_day`/`PWM_night` up to the temperature where the margin runs out, then the minimal ramp needed to stay under 90°C.

## Approaches considered — don't re-litigate these

1. **Fix PWM, sweep PL1=PL2** ← chosen for Exp 3 & 4; directly maps T(P) at a known silent fan speed
2. **Sweep PWM at fixed PL1=PL2** ← produces T(pwm) curves but answers the wrong question; the goal is the thermal envelope at a given PWM, not the optimal PWM for a given power
3. **Iso-thermal PID** — hold T constant via PWM feedback, vary PL1 — wrong output space: BIOS fan curves take T as input, not output. Rejected.

## Key technical decisions — don't revisit without reason

**Steady-state detection: half-window mean drift, not min/max spread.**
coretemp rounds to 1°C and bounces 2–4°C under constant load. A 1°C min/max threshold across 36 samples never triggers. Instead: split the 36-sample window in two halves, check `abs(mean_late - mean_early) <= 1.0°C`.

**PL1 ≠ power draw.**
PL1 is a ceiling, not a target. Under mprime the N100 draws up to ~17W; it only throttles when it would exceed the PL1 limit. To make power a genuine independent variable, set **PL1=PL2** to eliminate the burst window.

**Thermal safety limit: 90°C steady-state mean.**
90°C is the design ceiling for Exp 3 & 4 — 5°C margin below the TCC throttle point at 95°C, appropriate for a non-performance-critical home server. The sweep script still uses `TCC_TEMP = 95.0` as a hard abort to protect hardware, but the fan curve is designed around 90°C. Hard safety abort remains at 102°C.

**Standard load: mprime Small FFTs (FMA3), 4 workers.**
Draws ~16W sustained — higher than stress-ng matrixprod's ~13W hard ceiling (matrixprod doesn't fully utilize FMA3/AVX2). mprime config written to `fan_data/mprime_work/prime.txt` at run start.
