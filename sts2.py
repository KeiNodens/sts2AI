"""STS2_MCP HTTP API の薄いクライアント。

使い方:
    from sts2 import Inst
    host = Inst(15526)
    host.state()                # GET /api/v1/singleplayer (menu 等)
    host.mp_state()             # GET /api/v1/multiplayer
    host.act("menu_select", option="multiplayer")
    host.act("choose_map_node", index=0)
"""
import json
import urllib.request
import urllib.error


class Inst:
    def __init__(self, port: int):
        self.base = f"http://localhost:{port}"

    def _req(self, method: str, path: str, body: dict | None = None):
        url = self.base + path
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            url, data=data, method=method,
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8")
            return {"status": "error", "http": e.code, "body": raw}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"raw": raw}

    def state(self, fmt="json"):
        return self._req("GET", f"/api/v1/singleplayer?format={fmt}")

    def mp_state(self, fmt="json"):
        return self._req("GET", f"/api/v1/multiplayer?format={fmt}")

    def act(self, action, mp=False, **params):
        body = {"action": action, **params}
        ep = "multiplayer" if mp else "singleplayer"
        return self._req("POST", f"/api/v1/{ep}", body)

    def menu(self, option, mp=True, seed=None):
        body = {"action": "menu_select", "option": option}
        if seed is not None:
            body["seed"] = seed
        ep = "multiplayer" if mp else "singleplayer"
        return self._req("POST", f"/api/v1/{ep}", body)
