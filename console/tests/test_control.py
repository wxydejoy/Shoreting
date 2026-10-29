from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from dock_console.server import serve
from dock_console.services import (
    Controller,
    parse_launchctl_print,
    plan_action,
    service_specs,
)


ROOT = Path(__file__).resolve().parents[2]


class ParsePrintTest(unittest.TestCase):
    def test_running_pid(self) -> None:
        text = """
gui/501/cn.weiekko.helm-mini = {
	state = running
	pid = 62684
}
"""
        state, pid = parse_launchctl_print(text)
        self.assertEqual(state, "running")
        self.assertEqual(pid, 62684)

    def test_later_child_state_does_not_replace_job_state(self) -> None:
        text = """
user/501/cn.weiekko.dock-hub = {
	state = running
	pid = 62617
		state = active
		state = active
	job state = running
}
"""
        state, pid = parse_launchctl_print(text)
        self.assertEqual(state, "running")
        self.assertEqual(pid, 62617)

    def test_missing_service(self) -> None:
        state, pid = parse_launchctl_print(
            'Could not find service "cn.weiekko.dock-hub" in domain for user gui: 501'
        )
        self.assertIsNone(state)
        self.assertIsNone(pid)


class PlanTest(unittest.TestCase):
    def test_stop_unloads_loaded_job(self) -> None:
        plan = plan_action(
            "stop",
            loaded=True,
            running=True,
            domain="user/501",
            label="cn.weiekko.dock-hub",
            plist=Path("/tmp/cn.weiekko.dock-hub.plist"),
            installed=True,
        )
        self.assertTrue(plan.ok)
        self.assertEqual(plan.commands, [["launchctl", "bootout", "user/501/cn.weiekko.dock-hub"]])

    def test_start_bootstraps_preferred_domain(self) -> None:
        plist = Path("/tmp/cn.weiekko.helm-mini.plist")
        plan = plan_action(
            "start",
            loaded=False,
            running=False,
            domain="gui/501",
            label="cn.weiekko.helm-mini",
            plist=plist,
            installed=True,
        )
        self.assertTrue(plan.ok)
        self.assertEqual(
            plan.commands,
            [
                ["launchctl", "bootstrap", "gui/501", str(plist)],
                ["launchctl", "enable", "gui/501/cn.weiekko.helm-mini"],
            ],
        )

    def test_start_is_noop_when_already_running(self) -> None:
        plan = plan_action(
            "start",
            loaded=True,
            running=True,
            domain="gui/501",
            label="cn.weiekko.helm-mini",
            plist=Path("/tmp/x.plist"),
            installed=True,
        )
        self.assertTrue(plan.ok)
        self.assertEqual(plan.commands, [])

    def test_restart_kicks_loaded_job(self) -> None:
        plan = plan_action(
            "restart",
            loaded=True,
            running=True,
            domain="gui/501",
            label="cn.weiekko.companion-tts",
            plist=Path("/tmp/x.plist"),
            installed=True,
        )
        self.assertEqual(
            plan.commands,
            [["launchctl", "kickstart", "-k", "gui/501/cn.weiekko.companion-tts"]],
        )

    def test_restart_when_stopped_is_a_start(self) -> None:
        plist = Path("/tmp/cn.weiekko.dock-hub.plist")
        plan = plan_action(
            "restart",
            loaded=False,
            running=False,
            domain="user/501",
            label="cn.weiekko.dock-hub",
            plist=plist,
            installed=True,
        )
        self.assertEqual(plan.commands[0][:3], ["launchctl", "bootstrap", "user/501"])

    def test_missing_plist_cannot_start(self) -> None:
        plan = plan_action(
            "start",
            loaded=False,
            running=False,
            domain="user/501",
            label="cn.weiekko.dock-hub",
            plist=Path("/tmp/missing.plist"),
            installed=False,
        )
        self.assertFalse(plan.ok)
        self.assertEqual(plan.commands, [])
        self.assertIn("install-launchd", plan.message)


class ControllerTest(unittest.TestCase):
    def test_status_uses_the_domain_that_is_loaded(self) -> None:
        home = Path(self.id().replace(".", "_"))  # unused; controller gets explicit paths
        del home
        calls: list[list[str]] = []

        def run(args: list[str], timeout: float = 20):
            calls.append(args)
            target = args[2]
            if target.endswith("cn.weiekko.dock-hub") and target.startswith("user/"):
                return _done(0, "state = running\npid = 42\n")
            return _done(1, 'Could not find service "x"')

        ctl = Controller(
            repo=ROOT,
            home=Path("/Users/weiekko"),
            uid=501,
            run=run,
            probe=lambda url: (True, {"ok": True, "service": "dock-hub"}, None),
        )
        rows = {row["id"]: row for row in ctl.snapshot()}
        self.assertTrue(rows["hub"]["loaded"])
        self.assertEqual(rows["hub"]["domain"], "user/501")
        self.assertEqual(rows["hub"]["pid"], 42)
        self.assertTrue(rows["hub"]["health_ok"])
        self.assertIn(["launchctl", "print", "user/501/cn.weiekko.dock-hub"], calls)

    def test_stop_issues_bootout(self) -> None:
        calls: list[list[str]] = []

        def run(args: list[str], timeout: float = 20):
            calls.append(args)
            if args[1] == "print" and args[2] == "gui/501/cn.weiekko.helm-mini":
                return _done(0, "state = running\npid = 7\n")
            if args[1] == "print":
                return _done(1, "Could not find service")
            return _done(0, "")

        ctl = Controller(
            repo=ROOT,
            home=Path("/Users/weiekko"),
            uid=501,
            run=run,
            probe=lambda url: (False, None, "down"),
        )
        result = ctl.apply("mini", "stop")
        self.assertTrue(result["ok"])
        self.assertIn(["launchctl", "bootout", "gui/501/cn.weiekko.helm-mini"], calls)


class ServerTest(unittest.TestCase):
    def test_page_and_actions(self) -> None:
        class Fake:
            def snapshot(self):
                return [{"id": "hub", "name": "Hub", "state_label": "运行中"}]

            def apply(self, service_id: str, action: str):
                if service_id != "hub":
                    raise KeyError(service_id)
                return {"ok": True, "id": service_id, "action": action, "message": "已关闭"}

            def apply_all(self, action: str):
                return {"ok": True, "action": action, "results": []}

        httpd = serve(Fake(), host="127.0.0.1", port=0)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(httpd.shutdown)
        self.addCleanup(httpd.server_close)
        port = httpd.server_address[1]
        base = f"http://127.0.0.1:{port}"

        page = urllib.request.urlopen(base + "/", timeout=2).read().decode()
        self.assertIn("启动", page)
        self.assertIn("关闭", page)
        self.assertIn("重启", page)

        status = json.loads(urllib.request.urlopen(base + "/api/status", timeout=2).read())
        self.assertEqual(status["services"][0]["id"], "hub")

        req = urllib.request.Request(base + "/api/services/hub/stop", method="POST", data=b"")
        body = json.loads(urllib.request.urlopen(req, timeout=2).read())
        self.assertEqual(body["message"], "已关闭")

        bad = urllib.request.Request(base + "/api/services/nope/stop", method="POST", data=b"")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(bad, timeout=2)
        self.assertEqual(caught.exception.code, 404)
        caught.exception.close()


class CatalogTest(unittest.TestCase):
    def test_three_local_services(self) -> None:
        specs = {spec.id: spec for spec in service_specs(ROOT)}
        self.assertEqual(set(specs), {"hub", "mini", "tts"})
        self.assertEqual(specs["hub"].label, "cn.weiekko.dock-hub")
        self.assertEqual(specs["hub"].prefer, ("user", "gui"))
        self.assertEqual(specs["mini"].port, 17891)
        self.assertEqual(specs["tts"].port, 18100)
        self.assertEqual(specs["tts"].prefer, ("gui", "user"))


def _done(code: int, out: str):
    class Result:
        returncode = code
        stdout = out
        stderr = "" if code == 0 else out

    return Result()


if __name__ == "__main__":
    unittest.main()
