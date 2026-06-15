# CLAUDE.md — n100-fan-sweetspot

## Goal

Find the minimum fan PWM at which the machine is **inaudible at 2 meters** under a defined sustained load (TBD), then encode that as a BIOS fan curve: `PWM = f(T)`.

"Inaudible" = fan noise at 2m ≤ ambient noise floor + 3 dB (just-noticeable difference). The user has a noise meter. Always measure ambient first with fan off.

**Primary metric is dB(A) at 2m, not PWM.** PWM→RPM→dB(A) is nonlinear and fan-specific. PWM is a control input, not the goal.

## Hardware — confirmed values, don't re-derive

- **Board**: ASRock N100DC-ITX
- **CPU**: Intel N100 (Alder Lake-N), 4 Gracemont cores, max single-core turbo 3.4 GHz
- **hwmon1** = coretemp, **hwmon2** = NCT6798
- Fan: `hwmon2/fan2_input` — PWM: `hwmon2/pwm2` — mode: `hwmon2/pwm2_enable` (1=manual, 5=auto)
- **Tj Max** = 105°C (confirmed via `hwmon1/temp1_crit`)
- **TCC offset** = 10°C (confirmed via `rdmsr -f 29:24 0x1a2`), **locked** (bit 31 = 1, not writable)
- **Throttle starts at 95°C** — used as sweep stop condition in `fan_sweep.py`
- Max all-core sustained turbo under stress-ng matrixprod: ~2900 MHz, ~13W
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

## Planned experiment

Run `fan_sweep.py` at several `PL1=PL2` values: **6W, 10W, 15W, 20W, 25W**. One CSV per run. Together they produce a family of T(pwm) curves at different sustained power levels. Separately, measure dB(A) at 2m for each PWM step (noise meter). Combined, the data maps `PWM → dB(A)` and `PWM → steady T @ power`, which is enough to derive the BIOS fan curve.

## Approaches considered — don't re-litigate these

1. **Sweep PWM at fixed PL1=PL2** ← chosen; produces T(pwm) at known sustained power
2. **Fixed PWM, sweep PL1** ← valid but less efficient; same information, more runs
3. **Set PL1=PL2 only** ← necessary condition for (1); not an approach on its own
4. **Iso-thermal PID** — hold T constant via PWM feedback, vary PL1 — clever but **wrong output space**: BIOS fan curves take T as input, not output. Rejected.

## Key technical decisions — don't revisit without reason

**Steady-state detection: half-window mean drift, not min/max spread.**
coretemp rounds to 1°C and bounces 2–4°C under constant load. A 1°C min/max threshold across 36 samples never triggers. Instead: split the 36-sample window in two halves, check `abs(mean_late - mean_early) <= 1.0°C`.

**PL1 ≠ power draw.**
PL1 is a ceiling, not a target. At full turbo the N100 draws ~13W regardless of whether PL1 is 25W or 15W — it only throttles when it would exceed the limit. To make power a genuine independent variable, set **PL1=PL2** to eliminate the burst window.

**Sweep stops at 95°C steady-state mean.**
Above 95°C the TCC throttles frequency to hold temperature — further PWM reduction degrades performance without revealing new thermal behavior. Implemented as `TCC_TEMP = 95.0` in `fan_sweep.py`. Hard safety abort remains at 102°C.

**Standard load: stress-ng, 4 cores, matrixprod.**
Chosen for reproducibility. "Standard load" for the fan curve purpose (i.e. real workload) is still TBD.
