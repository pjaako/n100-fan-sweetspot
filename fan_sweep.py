#!/usr/bin/env python3
"""
Fan curve sweep for ASRock N100DC-ITX (Intel N100, NCT6798).
Sweeps fan2 PWM 255→0 step -15, collecting sensor data every 5s.
Advances to next PWM step after 180s of thermal steady state (pkg_temp ±1°C).
Aborts if package temp reaches 102°C and restores automatic fan control.
Load: mprime Small FFTs (FMA3), 4 workers — draws ~16W vs stress-ng matrixprod's ~13W ceiling.
"""

import os
import sys
import csv
import time
import signal
import argparse
import subprocess
from datetime import datetime
from collections import deque

# --- sysfs paths ---
HWMON_CT  = "/sys/class/hwmon/hwmon1"   # coretemp
HWMON_NCT = "/sys/class/hwmon/hwmon2"   # nct6798
RAPL_PKG  = "/sys/devices/virtual/powercap/intel-rapl/intel-rapl:0/energy_uj"
RAPL_PL1  = "/sys/devices/virtual/powercap/intel-rapl/intel-rapl:0/constraint_0_power_limit_uw"
RAPL_PL2  = "/sys/devices/virtual/powercap/intel-rapl/intel-rapl:0/constraint_1_power_limit_uw"
PWM_PATH  = f"{HWMON_NCT}/pwm2"
PWM_EN    = f"{HWMON_NCT}/pwm2_enable"
FAN_PATH  = f"{HWMON_NCT}/fan2_input"
CPU_FREQ  = "/sys/devices/system/cpu/cpu{i}/cpufreq/scaling_cur_freq"

DATA_DIR   = "/root/fan_data"
RUNS_INDEX = f"{DATA_DIR}/runs.csv"
INDEX_FIELDS = ["filename", "pl1_w", "pl2_w", "pwm_start", "pwm_step", "started_at", "ambient_c", "notes"]

FIELDS = [
    "timestamp", "pwm", "fan2_rpm", "pkg_power_w",
    "core0_mhz", "core1_mhz", "core2_mhz", "core3_mhz",
    "pkg_temp_c", "core0_temp_c", "core1_temp_c", "core2_temp_c", "core3_temp_c",
    "systin_c", "peci_c",
]

PWM_STEPS      = range(255, -1, -15)   # 255, 240, ..., 15, 0
INTERVAL       = 5                     # seconds between samples
STEADY_WINDOW  = 180                   # seconds to confirm steady state
STEADY_SAMPLES = STEADY_WINDOW // INTERVAL  # 36 samples
STEADY_DELTA   = 1.0                   # °C max drift between first/second half means
SAFETY_TEMP    = 102.0                 # °C hard abort threshold
TCC_TEMP       = 95.0                  # °C steady-state stop (TCC activation = Tj Max - 10)

PL1_TARGET   = 25_000_000              # µW — set on start, restored on exit

_stress      = None
_orig_enable = None
_orig_pl1    = None
_orig_pl2    = None
_outfile     = None


def rd(path):
    with open(path) as f:
        return f.read().strip()


def wr(path, val):
    with open(path, "w") as f:
        f.write(str(val) + "\n")


def set_pl1_pl2(uw):
    wr(RAPL_PL1, uw)
    wr(RAPL_PL2, uw)


def register_run(filename, pl1_w, ambient_c=None, notes=""):
    os.makedirs(DATA_DIR, exist_ok=True)
    write_header = not os.path.exists(RUNS_INDEX)
    with open(RUNS_INDEX, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=INDEX_FIELDS)
        if write_header:
            w.writeheader()
        w.writerow({
            "filename":   filename,
            "pl1_w":      pl1_w,
            "pl2_w":      pl1_w,
            "pwm_start":  PWM_STEPS.start,
            "pwm_step":   PWM_STEPS.step,
            "started_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "ambient_c":  ambient_c if ambient_c is not None else "",
            "notes":      notes,
        })


def cleanup(sig=None, frame=None):
    print("\n[cleanup] Restoring fan control and stopping stress-ng...", flush=True)
    try:
        if _stress and _stress.poll() is None:
            _stress.terminate()
            _stress.wait()
    except Exception as e:
        print(f"[cleanup] stress-ng: {e}")
    try:
        if _orig_pl1 is not None:
            wr(RAPL_PL1, _orig_pl1)
            print(f"[cleanup] PL1 restored to {int(_orig_pl1)//1_000_000}W", flush=True)
    except Exception as e:
        print(f"[cleanup] PL1 restore failed: {e}")
    try:
        if _orig_pl2 is not None:
            wr(RAPL_PL2, _orig_pl2)
            print(f"[cleanup] PL2 restored to {int(_orig_pl2)//1_000_000}W", flush=True)
    except Exception as e:
        print(f"[cleanup] PL2 restore failed: {e}")
    try:
        if _orig_enable is not None:
            wr(PWM_EN, _orig_enable)
            print(f"[cleanup] pwm2_enable restored to {_orig_enable}", flush=True)
    except Exception as e:
        print(f"[cleanup] PWM restore failed: {e}")
    if _outfile:
        _outfile.close()
    if sig is not None:
        sys.exit(0)


signal.signal(signal.SIGINT,  cleanup)
signal.signal(signal.SIGTERM, cleanup)
signal.signal(signal.SIGHUP,  cleanup)


def sample(prev_uj, prev_t):
    now = time.time()
    ts  = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    uj      = int(rd(RAPL_PKG))
    power_w = (uj - prev_uj) / 1e6 / (now - prev_t)

    pkg_temp   = int(rd(f"{HWMON_CT}/temp1_input")) / 1000
    core_temps = [int(rd(f"{HWMON_CT}/temp{i}_input")) / 1000 for i in range(2, 6)]
    systin     = int(rd(f"{HWMON_NCT}/temp1_input")) / 1000
    peci       = int(rd(f"{HWMON_NCT}/temp7_input")) / 1000

    core_mhz = [int(rd(CPU_FREQ.format(i=i))) // 1000 for i in range(4)]
    fan_rpm  = int(rd(FAN_PATH))

    return dict(
        ts=ts, now=now, uj=uj,
        fan_rpm=fan_rpm,
        power_w=round(power_w, 3),
        pkg_temp=pkg_temp,
        core_temps=core_temps,
        systin=systin,
        peci=peci,
        core_mhz=core_mhz,
    )


def main():
    global _stress, _orig_enable, _orig_pl1, _outfile

    parser = argparse.ArgumentParser()
    parser.add_argument("--ambient-c", type=float, default=None,
                         help="Room ambient temperature in °C at run start (no on-board sensor for this)")
    args = parser.parse_args()

    _orig_pl1 = rd(RAPL_PL1)
    _orig_pl2 = rd(RAPL_PL2)
    set_pl1_pl2(PL1_TARGET)
    pl1_w = PL1_TARGET // 1_000_000
    print(f"[*] PL1=PL2 set to {pl1_w}W (was PL1={int(_orig_pl1)//1_000_000}W PL2={int(_orig_pl2)//1_000_000}W)", flush=True)

    os.makedirs(DATA_DIR, exist_ok=True)
    ts_tag  = datetime.now().strftime("%Y%m%dT%H%M")
    filename = f"pl1_{pl1_w}w_{ts_tag}.csv"
    output   = f"{DATA_DIR}/{filename}"

    register_run(filename, pl1_w, ambient_c=args.ambient_c)
    print(f"[*] Run registered in {RUNS_INDEX}", flush=True)

    _orig_enable = rd(PWM_EN)

    _outfile = open(output, "w", newline="")
    writer = csv.DictWriter(_outfile, fieldnames=FIELDS)
    writer.writeheader()
    _outfile.flush()

    mprime_dir = f"{DATA_DIR}/mprime_work"
    os.makedirs(mprime_dir, exist_ok=True)
    with open(f"{mprime_dir}/prime.txt", "w") as f:
        f.write(
            "V24OptionsConverted=1\n"
            "CpuSupportsAVX=1\n"
            "CpuSupportsAVX2=1\n"
            "TortureTest=1\n"   # Small FFTs — FMA3, max FPU power
            "TortureTime=1\n"
            "NumCPUs=4\n"
            "CpuNumHyperthreads=1\n"
        )
    wr(PWM_EN, "1")
    wr(PWM_PATH, "255")
    print("[*] Fan2 in manual mode at PWM=255. Waiting 10s for fan to ramp up...", flush=True)
    time.sleep(10)

    print("[*] Starting mprime (4 workers, Small FFTs / FMA3)...", flush=True)
    _stress = subprocess.Popen(
        ["mprime", "-t", f"-W{mprime_dir}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    print(f"[*] Output → {output}\n", flush=True)

    # Bootstrap: get a valid initial energy reading before first sample
    prev_uj = int(rd(RAPL_PKG))
    prev_t  = time.time()
    time.sleep(INTERVAL)

    for pwm in PWM_STEPS:
        wr(PWM_PATH, str(pwm))
        print(
            f"\n{'='*70}\n"
            f"  PWM = {pwm:3d}   (steady = {STEADY_SAMPLES} samples, half-mean drift ≤{STEADY_DELTA}°C)\n"
            f"{'='*70}",
            flush=True,
        )

        window = deque(maxlen=STEADY_SAMPLES)
        steady = False

        while not steady:
            d = sample(prev_uj, prev_t)
            prev_uj, prev_t = d["uj"], d["now"]

            # Safety
            if d["pkg_temp"] >= SAFETY_TEMP:
                print(
                    f"\n[!!!] SAFETY TRIP: pkg_temp={d['pkg_temp']}°C ≥ {SAFETY_TEMP}°C\n"
                    f"      Setting fan to full speed and aborting.",
                    flush=True,
                )
                wr(PWM_PATH, "255")
                cleanup()
                sys.exit(1)

            # Steady-state window
            window.append(d["pkg_temp"])
            if len(window) == STEADY_SAMPLES:
                half = STEADY_SAMPLES // 2
                wlist = list(window)
                mean_early = sum(wlist[:half]) / half
                mean_late  = sum(wlist[half:]) / half
                if abs(mean_late - mean_early) <= STEADY_DELTA:
                    steady = True
                    if mean_late >= TCC_TEMP:
                        print(
                            f"\n[*] Steady-state mean {mean_late:.1f}°C ≥ {TCC_TEMP}°C (TCC threshold)."
                            f" Stopping sweep.",
                            flush=True,
                        )
                        cleanup()
                        sys.exit(0)

            # Write row
            writer.writerow({
                "timestamp":    d["ts"],
                "pwm":          pwm,
                "fan2_rpm":     d["fan_rpm"],
                "pkg_power_w":  d["power_w"],
                "core0_mhz":    d["core_mhz"][0],
                "core1_mhz":    d["core_mhz"][1],
                "core2_mhz":    d["core_mhz"][2],
                "core3_mhz":    d["core_mhz"][3],
                "pkg_temp_c":   d["pkg_temp"],
                "core0_temp_c": d["core_temps"][0],
                "core1_temp_c": d["core_temps"][1],
                "core2_temp_c": d["core_temps"][2],
                "core3_temp_c": d["core_temps"][3],
                "systin_c":     d["systin"],
                "peci_c":       d["peci"],
            })
            _outfile.flush()

            if len(window) >= 2:
                wlist = list(window)
                half = len(wlist) // 2
                drift = abs(sum(wlist[half:]) / len(wlist[half:]) - sum(wlist[:half]) / half)
                drift_str = f"drift={drift:.2f}°C"
            else:
                drift_str = "drift=--"
            status = "STEADY → next PWM" if steady else f"{len(window):2d}/{STEADY_SAMPLES}"
            cores_str = " ".join(f"{t:.0f}" for t in d["core_temps"])
            freqs_str = " ".join(str(f) for f in d["core_mhz"])
            print(
                f"  {d['ts']}  pwm={pwm:3d}  rpm={d['fan_rpm']:4d}  "
                f"pwr={d['power_w']:5.2f}W  pkg={d['pkg_temp']:4.1f}°C  "
                f"cores=[{cores_str}]°C  freqs=[{freqs_str}]MHz  {drift_str}  [{status}]",
                flush=True,
            )

            if not steady:
                time.sleep(INTERVAL)

    print("\n[*] Sweep complete.", flush=True)
    cleanup()


if __name__ == "__main__":
    main()
