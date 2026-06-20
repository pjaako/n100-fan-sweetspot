# n100-fan-sweetspot

Find the minimum fan speed at which an ASRock N100DC-ITX is inaudible at 2 meters under a defined sustained load, then derive a BIOS fan curve from that data.

## Hardware

- **Board**: ASRock N100DC-ITX
- **CPU**: Intel N100 (Alder Lake-N), Tj Max = 105°C, TCC offset = 10°C (throttles at 95°C)
- **SuperIO**: NCT6798 — fan2/pwm2
- **Fan control**: sysfs via `/sys/class/hwmon/hwmon2/pwm2`
- **Power measurement**: Intel RAPL via `/sys/devices/virtual/powercap/intel-rapl/intel-rapl:0/`

## Goal

Keep the machine inaudible at 2 meters during normal operation, with a minimal fan ramp reserved for corner-case power spikes.

"Inaudible" = fan noise at 2m ≤ ambient + 3 dB. Two setpoints are needed: one for daytime ambient, one for nighttime. The fan curve stays flat at the inaudible PWM for the time of day and only ramps above it to keep CPU temperature below 90°C (5°C margin below the TCC throttle point at 95°C).

## Method

Four experiments:

**1 & 2 — Find `PWM_day` and `PWM_night`**
With fan off, record baseline dB(A) at 2m. Step PWM up from 0 in increments of 5, dwelling at each step, until noise crosses +6 dB above baseline. `PWM_inaudible` = last step at or below +3 dB. Automated with a mic on the machine; a calibrated noise meter provides the ADC→dB(A) conversion reference. Run once during the day, once at night.

**3 & 4 — T(P) at each setpoint**
Fix fan at `PWM_day` (or `PWM_night`). Run mprime (Small FFTs, FMA3 — peaks at ~17W). Sweep PL1=PL2 from 3W to 18W in steps of 3W, waiting for thermal steady state at each step. This maps T(P) at silent fan speed: the thermal envelope the machine can sustain while remaining inaudible.

**Output**
Two T(P) curves. Where T < 90°C, the machine is safe and silent. Where it would exceed 90°C, the BIOS fan curve ramps PWM just enough to stay within margin.

### Why PL1=PL2

Setting PL1=PL2 eliminates the burst window, making power a genuine independent variable. Without this, actual power draw depends on time since last burst, not just the workload.

## Running a sweep

```bash
# Edit PL1_TARGET in fan_sweep.py, then:
python3 fan_sweep.py
```

Requires root. `mprime` must be installed.

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
