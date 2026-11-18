"""Project-scoped Funnel setup and rollback. Run on the authorized Linux host."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.request import urlopen


class SetupError(RuntimeError):
    pass


def command(*args):
    try:
        return subprocess.run(args, check=True, capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        # CLI output can include account/host details; keep it local.
        raise SetupError(f"{args[0]} command failed; check local service, policy and permissions") from exc


def status():
    try:
        value = json.loads(command("tailscale", "serve", "status", "--json"))
    except ValueError as exc:
        raise SetupError("Cannot parse Tailscale sharing status; no configuration changed") from exc
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise SetupError("Unrecognized Tailscale sharing status")
    # Empty optional fields carry no routes. Any nonempty unknown field blocks us.
    return {k: v for k, v in value.items() if v is not None and v != {}}


def expected_config(host, port, target):
    endpoint = f"{host}:{port}"
    return {
        "TCP": {str(port): {"HTTPS": True}},
        "Web": {endpoint: {"Handlers": {"/": {"Proxy": target}}}},
        "AllowFunnel": {endpoint: True},
    }


def read_state(path):
    try:
        value = json.loads(path.read_text())
        host = value["host"]
        port = value["port"]
        target = value["target"]
        if not re.fullmatch(r"[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)+\.ts\.net", host):
            raise ValueError()
        if port not in (443, 8443, 10000) or not re.fullmatch(r"http://127\.0\.0\.1:[0-9]+", target):
            raise ValueError()
        return value
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SetupError("Invalid local funnel-state.json; have the operator inspect it") from exc


def write_json(path, value):
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as stream:
        os.chmod(temporary, 0o600)
        json.dump(value, stream)
        stream.write("\n")
    temporary.replace(path)


def run(action, app):
    if action not in ("start", "off"):
        raise SetupError("usage: bash tunnel-funnel.sh [start|off]")
    app = Path(app)
    state_path = app / "funnel-state.json"
    public_url = app / "public-url"
    current = status()
    saved = read_state(state_path) if state_path.exists() else None
    owned = expected_config(saved["host"], saved["port"], saved["target"]) if saved else None
    if current and (not saved or current != owned):
        raise SetupError("Existing Tailscale sharing is not exactly the recorded EPICS route; refusing to change it")

    if action == "off":
        if not saved:
            if current:
                raise SetupError("No EPICS ownership record; refusing to disable sharing")
            return
        if current:
            public_url.unlink(missing_ok=True)
            command("tailscale", "funnel", "--bg", f"--https={saved['port']}", saved["target"], "off")
            if status():
                raise SetupError("Sharing remains configured; operator inspection required")
        state_path.unlink(missing_ok=True)
        public_url.unlink(missing_ok=True)
        return

    approval = app / "funnel-approved"
    if not approval.is_file() or approval.read_text().strip() != "yes":
        raise SetupError("Funnel needs owner approval; record it locally in funnel-approved only after approval")
    try:
        backend_port = int(os.environ.get("EPICS_PORT", "8090"))
        https_port = int(os.environ.get("EPICS_FUNNEL_HTTPS_PORT", "443"))
    except ValueError as exc:
        raise SetupError("Ports must be integers") from exc
    if not 1 <= backend_port <= 65535 or https_port not in (443, 8443, 10000):
        raise SetupError("Invalid app port or Funnel HTTPS port (443, 8443 or 10000)")
    target = f"http://127.0.0.1:{backend_port}"
    try:
        node = json.loads(command("tailscale", "status", "--json"))
        host = node["Self"]["DNSName"].rstrip(".")
        if node.get("BackendState") != "Running" or not re.fullmatch(
                r"[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)+\.ts\.net", host):
            raise ValueError()
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise SetupError("Tailscale must be running with a valid node DNS name") from exc
    wanted = expected_config(host, https_port, target)
    if saved and current and wanted != owned:
        raise SetupError("EPICS route settings changed; run off with the old ownership record first")
    try:
        with urlopen(target + "/api/session", timeout=5) as response:
            if json.load(response) != {"authenticated": False}:
                raise ValueError()
    except Exception as exc:
        raise SetupError("Local app health check failed; expected logged-out /api/session response") from exc

    # Journal intent before enabling; retains enough information for scoped recovery
    # if the command succeeds but verification or URL writing subsequently fails.
    public_url.unlink(missing_ok=True)
    write_json(state_path, {"host": host, "port": https_port, "target": target})
    command("tailscale", "funnel", "--bg", f"--https={https_port}", target)
    if status() != wanted:
        raise SetupError("Funnel did not match the EPICS route; no URL published. Inspect locally; do not reset")
    suffix = "" if https_port == 443 else f":{https_port}"
    public_url.write_text(f"https://{host}{suffix}\n")
    # Preserve quick tunnel until Funnel configuration has been verified.
    try:
        if command("docker", "ps", "-a", "--filter", "name=^/epics-tunnel$", "--format", "{{.Names}}").strip():
            command("docker", "rm", "-f", "epics-tunnel")
    except SetupError:
        print("Funnel is configured, but quick-tunnel cleanup failed; retire only epics-tunnel locally.", file=sys.stderr)
    print("EPICS Funnel configured; URL saved locally in public-url.")
    print("Verify external HTTPS and passcode access before calling deployment complete.")


def main():
    try:
        if len(sys.argv) > 2:
            raise SetupError("usage: funnel.py [start|off]")
        run(sys.argv[1] if len(sys.argv) > 1 else "start",
            os.environ.get("EPICS_APP_DIR", str(Path.home() / "epics-app")))
    except (SetupError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
