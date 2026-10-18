"""In-memory stand-in for a device, so tests never need adb."""

from tvlean.device import Device

STATE_CODES = {"default": 0, "enabled": 1, "disabled": 2, "disabled-user": 3}


class FakeDevice(Device):
    def __init__(self, packages=None, settings=None, model="onn. Streaming Device 4K pro"):
        super().__init__("fake:5555")
        self.packages = dict(packages or {})  # name -> state
        self.settings = dict(settings or {})  # "ns/key" -> value
        self._model = model
        self.home_breakers: set[str] = set()  # disabling any of these breaks home
        self.fail_on: str | None = None  # package whose disable raises

    def model(self):
        return self._model

    def installed_packages(self):
        return set(self.packages)

    def package_state(self, pkg):
        return self.packages.get(pkg)

    def disable(self, pkg):
        if pkg == self.fail_on:
            raise RuntimeError(f"boom disabling {pkg}")
        self.packages[pkg] = "disabled-user"

    def enable(self, pkg):
        self.packages[pkg] = "enabled"

    def get_setting(self, ns, key):
        return self.settings.get(f"{ns}/{key}")

    def put_setting(self, ns, key, value):
        if value is None:
            self.settings.pop(f"{ns}/{key}", None)
        else:
            self.settings[f"{ns}/{key}"] = value

    def home_activity(self):
        if any(self.packages.get(p) == "disabled-user" for p in self.home_breakers):
            return "com.google.android.tungsten.setupwraith/.RecoveryActivity"
        return "com.spocky.projengmenu/.ui.home.MainActivity"
