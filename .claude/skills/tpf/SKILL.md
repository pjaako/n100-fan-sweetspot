---
name: tpf
description: Check current thermals, power (RAPL), and fan state on the N100DC-ITX box. Use when asked to check temps, power draw, fan speed/RPM/PWM, or whether a stress run / orphaned load process is still active. Scoped to /root/n100-fan-sweetspot.
user-invocable: true
allowed-tools:
  - Bash(sensors *)
  - Bash(cat /sys/devices/virtual/powercap/*)
  - Bash(ps *)
  - Bash(pstree *)
---

# /n100-fan-sweetspot:tpf — Thermal/Power/Fan status check

Arguments passed: `$ARGUMENTS` (usually none).

## 1. Sensors snapshot

```
sensors | grep -E "fan2|pwm2|Package id 0|Core [0-3]|PECI|SYSTIN"
```

Report: `pwm2` (mode + manual/auto), `fan2` RPM, package temp, per-core temps,
PECI Agent 0, SYSTIN. Flag if package temp >= 90°C (design ceiling, see
project CLAUDE.md) or >= 95°C (TCC activation — imminent throttle).

## 2. Power (RAPL)

```
cat /sys/devices/virtual/powercap/intel-rapl/intel-rapl:0/constraint_0_power_limit_uw
cat /sys/devices/virtual/powercap/intel-rapl/intel-rapl:0/constraint_1_power_limit_uw
```
(PL1, PL2 in µW — divide by 1e6 for W.)

Instant power draw — sample `energy_uj` twice 1s apart and diff:
```
e1=$(cat /sys/devices/virtual/powercap/intel-rapl/intel-rapl:0/energy_uj); sleep 1
e2=$(cat /sys/devices/virtual/powercap/intel-rapl/intel-rapl:0/energy_uj)
echo "scale=2; ($e2-$e1)/1000000" | bc
```

Flag if PL1 != PL2 — this means power isn't a controlled variable for any
sweep currently running (see project CLAUDE.md, "PL1 ≠ power draw").

## 3. Orphaned load processes

```
ps -ef | grep -E "mprime|stress-ng" | grep -v grep
```

For any hit, check `ps -o pid,ppid,lstart,etimes,cmd -p <pid>`. **PPID 1 with
a multi-hour/day `etimes` means it's orphaned** — its parent (presumably a
`fan_sweep.py` run) died without running `cleanup()`, most likely from an
uncaught signal (e.g. a dropped SSH session sending SIGHUP — `fan_sweep.py`
now traps SIGHUP too, but older orphans predate that fix). An orphaned load
process explains sustained high temps/fan speed with no sweep visibly
running.

## 4. Report

One paragraph: current temp/fan/power state, whether everything is
nominal/idle, and whether step 3 found an orphan that needs killing (ask
before killing — it's a destructive action on a possibly-intentional long
run).
