"""Offline Funnel lifecycle tests. No real Tailscale, Docker or network access."""
import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

spec = importlib.util.spec_from_file_location(
    "epics_funnel", Path(__file__).parents[1] / "deploy/spark/funnel.py")
funnel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(funnel)
HOST = "epics-test.unit-test.ts.net"


@pytest.fixture
def env(tmp_path, monkeypatch):
    state = {"config": {}, "calls": [], "fail": False, "verify": False}
    (tmp_path / "funnel-approved").write_text("yes")
    monkeypatch.setenv("EPICS_PORT", "8090")
    monkeypatch.setenv("EPICS_FUNNEL_HTTPS_PORT", "443")

    def command(*args):
        state["calls"].append(args)
        if args == ("tailscale", "serve", "status", "--json"):
            return json.dumps(state["config"])
        if args == ("tailscale", "status", "--json"):
            return json.dumps({"BackendState": "Running", "Self": {"DNSName": HOST + "."}})
        if args[:2] == ("tailscale", "funnel"):
            if state["fail"]:
                raise funnel.SetupError("policy denied")
            if args[-1] == "off":
                state["config"] = {}
            elif not state["verify"]:
                state["config"] = funnel.expected_config(HOST, int(args[3].split("=")[1]), args[4])
            return ""
        if args[:2] == ("docker", "ps"):
            return "epics-tunnel\n"
        if args == ("docker", "rm", "-f", "epics-tunnel"):
            return ""
        raise AssertionError(args)

    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.read.return_value = b'{"authenticated":false}'
    monkeypatch.setattr(funnel, "command", command)
    monkeypatch.setattr(funnel, "urlopen", Mock(return_value=response))
    return tmp_path, state


def mutations(state):
    return [c for c in state["calls"] if c[:2] == ("tailscale", "funnel") or c[:2] == ("docker", "rm")]


def test_start_two_restarts_and_scoped_rollback(env):
    app, state = env
    for _ in range(3):
        funnel.run("start", app)
        assert (app / "public-url").read_text() == f"https://{HOST}\n"
    assert len([c for c in mutations(state) if c[:2] == ("tailscale", "funnel")]) == 3
    funnel.run("off", app)
    assert not (app / "funnel-state.json").exists()
    assert not (app / "public-url").exists()
    assert not state["config"]
    assert mutations(state)[-1] == (
        "tailscale", "funnel", "--bg", "--https=443", "http://127.0.0.1:8090", "off")


def test_no_lab_approval_no_mutation(env):
    app, state = env
    (app / "funnel-approved").unlink()
    with pytest.raises(funnel.SetupError, match="approval"):
        funnel.run("start", app)
    assert not mutations(state)


@pytest.mark.parametrize("config", [
    {"TCP": {"443": {"TCPForward": "127.0.0.1:22"}}},
    {"Web": {"other.unit-test.ts.net:443": {"Handlers": {"/": {"Text": "lab service"}}}}},
    {"Foreground": {"123": {"TCP": {"8443": {"HTTPS": True}}}}},
    {"Services": {"svc:lab": {"Tun": True}}},
])
def test_unowned_shared_configuration_is_untouched(env, config):
    app, state = env
    state["config"] = config
    for action in ("start", "off"):
        with pytest.raises(funnel.SetupError, match="refusing"):
            funnel.run(action, app)
    assert not mutations(state)
    assert state["config"] == config


def test_modified_owned_configuration_is_untouched(env):
    app, state = env
    funnel.run("start", app)
    state["config"]["Web"][f"{HOST}:443"]["Handlers"]["/lab"] = {"Text": "other"}
    state["calls"].clear()
    with pytest.raises(funnel.SetupError, match="refusing"):
        funnel.run("off", app)
    assert not mutations(state)


@pytest.mark.parametrize("mode", ["fail", "verify"])
def test_failed_activation_keeps_quick_tunnel_and_journals_recovery(env, mode):
    app, state = env
    state[mode] = True
    (app / "public-url").write_text("previous URL")
    with pytest.raises(funnel.SetupError):
        funnel.run("start", app)
    assert not (app / "public-url").exists()
    assert (app / "funnel-state.json").exists()
    assert not any(c[:2] == ("docker", "rm") for c in state["calls"])


def test_unhealthy_app_is_not_exposed(env, monkeypatch):
    app, state = env
    monkeypatch.setattr(funnel, "urlopen", Mock(side_effect=OSError("not listening")))
    with pytest.raises(funnel.SetupError, match="health check"):
        funnel.run("start", app)
    assert not mutations(state)


@pytest.mark.parametrize("variable,value", [
    ("EPICS_PORT", "0"), ("EPICS_PORT", "65536"), ("EPICS_PORT", "bad"),
    ("EPICS_FUNNEL_HTTPS_PORT", "8090"),
])
def test_invalid_port_does_not_change_routes(env, monkeypatch, variable, value):
    app, state = env
    monkeypatch.setenv(variable, value)
    with pytest.raises(funnel.SetupError, match="[Pp]ort"):
        funnel.run("start", app)
    assert not mutations(state)


def test_changed_route_requires_explicit_shutdown(env, monkeypatch):
    app, state = env
    funnel.run("start", app)
    state["calls"].clear()
    monkeypatch.setenv("EPICS_PORT", "8091")
    with pytest.raises(funnel.SetupError, match="settings changed"):
        funnel.run("start", app)
    assert not mutations(state)


def test_no_owned_route_off_is_noop(env):
    app, state = env
    funnel.run("off", app)
    assert not mutations(state)


def test_malformed_status_refuses(env):
    app, _ = env
    original = funnel.command
    def malformed(*args):
        if args == ("tailscale", "serve", "status", "--json"):
            return "not JSON"
        return original(*args)
    from unittest.mock import patch
    with patch.object(funnel, "command", malformed), pytest.raises(funnel.SetupError, match="parse"):
        funnel.run("start", app)



def test_nondefault_port_url_and_rollback(env, monkeypatch):
    app, state = env
    monkeypatch.setenv("EPICS_FUNNEL_HTTPS_PORT", "8443")
    funnel.run("start", app)
    assert (app / "public-url").read_text() == f"https://{HOST}:8443\n"
    funnel.run("off", app)
    assert mutations(state)[-1][3] == "--https=8443"


def test_no_quick_container_is_ok(env, monkeypatch):
    app, state = env
    original = funnel.command
    def no_container(*args):
        if args[:2] == ("docker", "ps"):
            return ""
        return original(*args)
    monkeypatch.setattr(funnel, "command", no_container)
    funnel.run("start", app)
    assert not any(c[:2] == ("docker", "rm") for c in state["calls"])


def test_docker_cleanup_failure_does_not_undo_funnel(env, monkeypatch, capsys):
    app, state = env
    original = funnel.command
    def failed_cleanup(*args):
        if args[:2] == ("docker", "ps"):
            raise funnel.SetupError("Docker unavailable")
        return original(*args)
    monkeypatch.setattr(funnel, "command", failed_cleanup)
    funnel.run("start", app)
    assert (app / "public-url").exists()
    assert "cleanup failed" in capsys.readouterr().err
