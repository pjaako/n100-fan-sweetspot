# n100-fan-sweetspot

Find the minimum fan speed at which an ASRock N100DC-ITX is inaudible at 2 meters under a defined sustained load, then derive a BIOS fan curve from that data.

## Hardware

- **Board**: ASRock N100DC-ITX
- **CPU**: Intel N100 (Alder Lake-N), Tj Max = 105°C, TCC offset = 10°C (throttles at 95°C)
- **SuperIO**: NCT6798 — fan2/pwm2
- **Fan control**: sysfs via `/sys/class/hwmon/hwmon2/pwm2`
- **Power measurement**: Intel RAPL via `/sys/devices/virtual/powercap/intel-rapl/intel-rapl:0/`

## Goal

> Inaudible at 2 meters while at standard (TBD) load.

"Inaudible" = fan noise at 2m ≤ ambient noise floor + 3 dB (just-noticeable difference). Measured with a noise meter. Ambient is measured first with fan off.

## Method

Run `fan_sweep.py` at several `PL1=PL2` values (e.g. 6W, 10W, 15W, 20W, 25W). Each run produces one CSV sweeping PWM from 255→0 in steps of 15, waiting for thermal steady state at each step. Together the runs produce a family of T(pwm) curves at different sustained power levels.

Separately, measure dB(A) at 2m for each PWM step. Combined, the data maps:

```
PWM → RPM → dB(A) @ 2m
PWM → steady T @ known power
```

From these, derive the BIOS fan curve: `PWM = f(T)` that stays below the noise threshold under the target load.

### Why PL1=PL2

Setting PL1=PL2 eliminates the burst window, making power a genuine independent variable. Without this, power is a consequence of temperature (which depends on PWM), not a free parameter.

### Why not iso-thermal PID control

Holding T constant via feedback and varying PWM is elegant but produces output in the wrong space — BIOS fan curves take temperature as input, not output.

## Running a sweep

```bash
# Edit PL1_TARGET in fan_sweep.py, then:
python3 fan_sweep.py
```

Requires root. `stress-ng` must be installed.

The sweep stops early if steady-state mean temperature reaches 95°C (TCC threshold).

## Data layout

```
fan_data/
  runs.csv                        # index: filename, pl1_w, pl2_w, pwm_start, pwm_step, started_at, notes
  pl1_10w_20260615T2014.csv
  pl1_25w_20260615T2100.csv
  ...
```

### CSV columns

`timestamp, pwm, fan2_rpm, pkg_power_w, core0..3_mhz, pkg_temp_c, core0..3_temp_c, systin_c, peci_c`
