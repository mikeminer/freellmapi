"""Exercise Tailscale launch from the actual APK on an isolated Android emulator.

Does not sign in, connect a VPN, or modify a physical phone. Optional baseline
APK demonstrates the pre-fix package-visibility failure on the same emulator.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PACKAGE = "it.freellmapi.remote"
TAILSCALE = "com.tailscale.ipn"
ADB = Path(os.environ["LOCALAPPDATA"]) / "Android/Sdk/platform-tools/adb.exe"


def verify(serial, tailscale_apk, output, before_apk=None):
    if not serial.startswith("emulator-"):
        raise ValueError("This destructive test is restricted to an emulator.")
    output.mkdir(parents=True, exist_ok=True)
    commands, observations = [], {}

    def adb(*args, raw=False):
        result = subprocess.run([str(ADB), "-s", serial, *map(str, args)],
                                capture_output=True, timeout=45)
        commands.append({"args": list(map(str, args)), "exit_code": result.returncode})
        if result.returncode:
            raise RuntimeError(result.stderr.decode(errors="replace") + result.stdout.decode(errors="replace"))
        return result.stdout if raw else result.stdout.decode(errors="replace")

    def wait(check, timeout=25):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            value = check()
            if value is not None and value is not False:
                return value
            time.sleep(.4)
        raise TimeoutError("Android launch condition not reached")

    def nodes():
        adb("shell", "uiautomator", "dump", "/sdcard/freellmapi-launch-test.xml")
        return list(ET.fromstring(adb("shell", "cat", "/sdcard/freellmapi-launch-test.xml")).iter("node"))

    def find(label):
        return next((n for n in nodes() if n.get("text", "").casefold() == label.casefold()), None)

    def tap(node):
        if node is None:
            raise AssertionError("UI control not found")
        x1, y1, x2, y2 = map(int, re.findall(r"\d+", node.get("bounds")))
        adb("shell", "input", "tap", (x1 + x2) // 2, (y1 + y2) // 2)

    def resumed():
        return "\n".join(line.strip() for line in adb("shell", "dumpsys", "activity", "activities").splitlines()
                         if "mResumedActivity" in line or "topResumedActivity" in line)

    def open_button():
        adb("shell", "am", "force-stop", PACKAGE)
        adb("shell", "am", "start", "-n", PACKAGE + "/.MainActivity")
        tap(wait(lambda: find("Apri Tailscale · accesso Google")))

    disabled = False
    apk = ROOT / "build/freellmapi-remote.apk"
    try:
        if adb("shell", "getprop", "ro.kernel.qemu").strip() != "1":
            raise ValueError("Target is not an Android emulator")
        wait(lambda: adb("shell", "getprop", "sys.boot_completed").strip() == "1")
        observations["android_api"] = int(adb("shell", "getprop", "ro.build.version.sdk").strip())
        if observations["android_api"] < 30:
            raise ValueError("Package visibility regression requires Android 11 or later")
        adb("install", "-r", tailscale_apk)
        if before_apk:
            adb("install", "-r", "-d", before_apk)
            adb("shell", "pm", "clear", PACKAGE)
            open_button()
            baseline = wait(lambda: (value if PACKAGE not in (value := resumed()) and value else None))
            observations["baseline_resumed"] = baseline
            observations["baseline_opened_tailscale"] = TAILSCALE in baseline
            (output / "before.png").write_bytes(adb("exec-out", "screencap", "-p", raw=True))
        adb("install", "-r", apk)
        adb("shell", "pm", "clear", PACKAGE)
        open_button()
        current = wait(lambda: (value if TAILSCALE in (value := resumed()) else None))
        observations["installed_opens_tailscale"] = True
        observations["fixed_resumed"] = current
        (output / "tailscale-opened.png").write_bytes(adb("exec-out", "screencap", "-p", raw=True))
        adb("shell", "am", "force-stop", TAILSCALE)
        disabled = True
        adb("shell", "pm", "disable-user", "--user", "0", TAILSCALE)
        open_button()
        wait(lambda: find("Tailscale non disponibile"))
        observations["unavailable_shows_dialog_in_app"] = PACKAGE in resumed()
        (output / "unavailable-dialog.png").write_bytes(adb("exec-out", "screencap", "-p", raw=True))
        tap(wait(lambda: find("Annulla")))
        observations["cancel_stays_in_app"] = PACKAGE in resumed() and find("Tailscale non disponibile") is None
        adb("shell", "pm", "enable", TAILSCALE)
        disabled = False
        open_button()
        wait(lambda: TAILSCALE in resumed())
        observations["reenabled_opens_tailscale"] = True
        observations["no_app_crash"] = ("Process: " + PACKAGE) not in adb("logcat", "-d", "-b", "crash")
        checks = ["installed_opens_tailscale", "unavailable_shows_dialog_in_app",
                  "cancel_stays_in_app", "reenabled_opens_tailscale", "no_app_crash"]
        observations["passed"] = all(observations.get(key) for key in checks)
        return observations
    finally:
        if disabled:
            adb("shell", "pm", "enable", TAILSCALE)
        result = {"scope": "Real signed APK and official Tailscale, Android emulator only; no login or VPN activation",
                  "observations": observations, "commands": commands,
                  "apk_sha256": hashlib.sha256(apk.read_bytes()).hexdigest()}
        (output / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(observations, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--serial", default="emulator-5554")
    parser.add_argument("--tailscale-apk", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--before-apk", type=Path)
    args = parser.parse_args()
    if not verify(args.serial, args.tailscale_apk.resolve(), args.output.resolve(),
                  args.before_apk.resolve() if args.before_apk else None).get("passed"):
        raise SystemExit(1)
