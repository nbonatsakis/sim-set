"""Thin subprocess layer over `xcrun simctl`. The only module that shells out to it."""
import json
import subprocess


class SimctlError(Exception):
    def __init__(self, args, stderr):
        super().__init__(f"xcrun simctl {' '.join(args)} failed: {stderr.strip()}")
        self.command = list(args)
        self.stderr = stderr


def default_run(args):
    return subprocess.run(["xcrun", "simctl", *args], capture_output=True, text=True)


class Simctl:
    def __init__(self, run=default_run):
        self._run = run

    def _call(self, *args):
        proc = self._run(list(args))
        if proc.returncode != 0:
            raise SimctlError(args, proc.stderr)
        return proc.stdout

    def _json(self, *args):
        return json.loads(self._call(*args, "-j"))

    def list_devices(self):
        devices = []
        for runtime_id, entries in self._json("list", "devices").get("devices", {}).items():
            for entry in entries:
                devices.append({**entry, "runtime": runtime_id})
        return devices

    def list_devicetypes(self):
        return self._json("list", "devicetypes").get("devicetypes", [])

    def list_runtimes(self):
        return self._json("list", "runtimes").get("runtimes", [])

    def create(self, name, devicetype_id, runtime_id):
        return self._call("create", name, devicetype_id, runtime_id).strip()

    def delete(self, udid):
        self._call("delete", udid)

    def boot(self, udid):
        self._call("boot", udid)

    def bootstatus(self, udid):
        """Block until the device has finished booting (SpringBoard is up)."""
        self._call("bootstatus", udid, "-b")

    def shutdown(self, udid):
        self._call("shutdown", udid)

    def erase(self, udid):
        self._call("erase", udid)
