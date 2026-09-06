"""Compare bundled XPC workers with and without the network client entitlement."""

from pathlib import Path
import plistlib
import subprocess
from tempfile import TemporaryDirectory


def run_variant(root: Path, *, allow_network: bool) -> None:
    name = "Allowed" if allow_network else "Denied"
    app = root / f"{name}.app"
    service = app / "Contents/XPCServices/Worker.xpc"
    identifier = f"org.bob.DictationProbe.{name}"
    sources = Path(__file__).resolve().parent
    for bundle, executable, bundle_id, kind in (
        (app, "Probe", identifier, "APPL"),
        (service, "Worker", identifier + ".Worker", "XPC!"),
    ):
        (bundle / "Contents/MacOS").mkdir(parents=True)
        info = {
            "CFBundleExecutable": executable,
            "CFBundleIdentifier": bundle_id,
            "CFBundlePackageType": kind,
            "CFBundleVersion": "1",
            "ProbeAllowNetwork": allow_network,
        }
        if kind == "XPC!":
            info["XPCService"] = {"ServiceType": "Application"}
        (bundle / "Contents/Info.plist").write_bytes(plistlib.dumps(info))
        subprocess.run(
            [
                "/usr/bin/clang",
                "-fobjc-arc",
                "-fblocks",
                "-framework",
                "Foundation",
                "-framework",
                "Metal",
                str(sources / f"{executable}.m"),
                "-o",
                str(bundle / "Contents/MacOS" / executable),
            ],
            check=True,
        )
    entitlements = {"com.apple.security.app-sandbox": True}
    if allow_network:
        entitlements["com.apple.security.network.client"] = True
    entitlement_path = root / f"{name}.entitlements"
    entitlement_path.write_bytes(plistlib.dumps(entitlements))
    subprocess.run(
        [
            "/usr/bin/codesign",
            "--force",
            "--sign",
            "-",
            "--entitlements",
            str(entitlement_path),
            str(service),
        ],
        check=True,
    )
    subprocess.run(
        ["/usr/bin/codesign", "--force", "--sign", "-", str(app)], check=True
    )
    print(f"Worker network client entitlement: {allow_network}", flush=True)
    subprocess.run([str(app / "Contents/MacOS/Probe")], check=True, timeout=25)


if __name__ == "__main__":
    with TemporaryDirectory(prefix="bob-xpc-probe-") as directory:
        for allowed in (False, True):
            run_variant(Path(directory), allow_network=allowed)
