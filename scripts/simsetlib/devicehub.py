"""Reclaim a simulator's input surface after Xcode 27's Device Hub attaches to it.

Device Hub runs a guest HID daemon (dtuhidd) on every booted simulator and publishes the notify
state below. On iOS 27, backboardd then disconnects the legacy input services AXe, MobileBuildMCP,
and baguette inject into, so hardware buttons stop working and taps can silently drop. Clearing
the state and restarting backboardd, in that order, brings them back; SpringBoard restarts with
it, so running apps are killed. Same repair as tddworks/baguette `heal` (issue #77).
"""
import time

STATE_KEY = "com.apple.coredevice.dtuhidd.active"
BACKBOARDD = "system/com.apple.backboardd"
SPRINGBOARD = "com.apple.SpringBoard"


def attached(simctl, udid):
    try:
        output = simctl.spawn(udid, "notifyutil", "-g", STATE_KEY)
    except Exception:
        return False
    return any(line.split() == [STATE_KEY, "1"] for line in output.splitlines())


def springboard_pid(simctl, udid):
    for line in simctl.spawn(udid, "launchctl", "list").splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[2] == SPRINGBOARD and parts[0].isdigit():
            return parts[0]
    return None


def heal(simctl, udid, sleep=time.sleep, timeout=20.0, poll=0.5, settle=2.0):
    """Return True if the device was shadowed and has been reclaimed, False if there was nothing to do."""
    if not attached(simctl, udid):
        return False
    before = springboard_pid(simctl, udid)
    simctl.spawn(udid, "notifyutil", "-s", STATE_KEY, "0")
    simctl.spawn(udid, "launchctl", "kickstart", "-k", BACKBOARDD)
    waited = 0.0
    while waited < timeout:
        sleep(poll)
        waited += poll
        current = springboard_pid(simctl, udid)
        if current and current != before:
            sleep(settle)
            return True
    raise HealError(f"SpringBoard did not come back on {udid} after restarting backboardd; reboot the simulator")


class HealError(Exception):
    pass
