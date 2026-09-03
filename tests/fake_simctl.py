"""In-memory stand-in for Simctl used by every CLI and planning test."""
import sys
import uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from simsetlib.simctl import SimctlError

IPHONE_17_PRO = "com.apple.CoreSimulator.SimDeviceType.iPhone-17-Pro"
IPHONE_17_PRO_MAX = "com.apple.CoreSimulator.SimDeviceType.iPhone-17-Pro-Max"
IPHONE_16E = "com.apple.CoreSimulator.SimDeviceType.iPhone-16e"
IPAD_PRO_13 = "com.apple.CoreSimulator.SimDeviceType.iPad-Pro-13-inch-M5-12GB"
IOS_26_3 = "com.apple.CoreSimulator.SimRuntime.iOS-26-3"
IOS_18_4 = "com.apple.CoreSimulator.SimRuntime.iOS-18-4"

DEFAULT_DEVICETYPES = [
    {"identifier": IPHONE_17_PRO, "name": "iPhone 17 Pro"},
    {"identifier": IPHONE_17_PRO_MAX, "name": "iPhone 17 Pro Max"},
    {"identifier": IPHONE_16E, "name": "iPhone 16e"},
    {"identifier": IPAD_PRO_13, "name": "iPad Pro 13-inch (M5)"},
]

DEFAULT_RUNTIMES = [
    {"identifier": IOS_26_3, "version": "26.3", "isAvailable": True, "platform": "iOS", "name": "iOS 26.3"},
    {"identifier": IOS_18_4, "version": "18.4", "isAvailable": True, "platform": "iOS", "name": "iOS 18.4"},
    {"identifier": "com.apple.CoreSimulator.SimRuntime.tvOS-26-2", "version": "26.2",
     "isAvailable": True, "platform": "tvOS", "name": "tvOS 26.2"},
]

TYPE_IDS_BY_NAME = {dt["name"]: dt["identifier"] for dt in DEFAULT_DEVICETYPES}


def make_device(name, udid=None, state="Shutdown", devicetype_id=IPHONE_17_PRO, runtime=IOS_26_3, available=True):
    return {
        "udid": udid or str(uuid.uuid4()).upper(),
        "name": name,
        "state": state,
        "deviceTypeIdentifier": devicetype_id,
        "isAvailable": available,
        "runtime": runtime,
        "dataPath": "",
        "logPath": "",
    }


class FakeSimctl:
    def __init__(self, devices=None, devicetypes=None, runtimes=None):
        self.devices = [dict(d) for d in (devices or [])]
        self.devicetypes = list(devicetypes or DEFAULT_DEVICETYPES)
        self.runtimes = list(runtimes or DEFAULT_RUNTIMES)
        self.calls = []

    def list_devices(self):
        return [dict(d) for d in self.devices]

    def list_devicetypes(self):
        return list(self.devicetypes)

    def list_runtimes(self):
        return list(self.runtimes)

    def create(self, name, devicetype_id, runtime_id):
        device = make_device(name, devicetype_id=devicetype_id, runtime=runtime_id)
        self.devices.append(device)
        self.calls.append(("create", name))
        return device["udid"]

    def _find(self, udid):
        for device in self.devices:
            if device["udid"] == udid:
                return device
        raise SimctlError(["<fake>", udid], f"Invalid device: {udid}")

    def delete(self, udid):
        self.devices.remove(self._find(udid))
        self.calls.append(("delete", udid))

    def boot(self, udid):
        self._find(udid)["state"] = "Booted"
        self.calls.append(("boot", udid))

    def bootstatus(self, udid):
        self._find(udid)
        self.calls.append(("bootstatus", udid))

    def shutdown(self, udid):
        self._find(udid)["state"] = "Shutdown"
        self.calls.append(("shutdown", udid))

    def erase(self, udid):
        self._find(udid)
        self.calls.append(("erase", udid))

    def names(self):
        return sorted(d["name"] for d in self.devices)
