"""Conservative external connectivity checks; no announcement on initial startup."""
import concurrent.futures
import urllib.request


def probe(url, expected):
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.status == 200 and expected in response.read(4096)
    except Exception:
        return False


def reachable():
    targets = [("https://www.apple.com/library/test/success.html", b"Success"),
               ("https://www.msftconnecttest.com/connecttest.txt", b"Microsoft Connect Test")]
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        return any(list(pool.map(lambda args: probe(*args), targets)))


def evaluate(previous, online, now):
    """Require a healthy baseline, >=4 minutes down, then two recovery samples."""
    old = previous if 0 <= now - previous.get("last", -1e12) <= 300 else {}
    state = dict(old, last=now)
    announce = False
    if online:
        state["up"] = old.get("up", 0) + 1
        state["healthy"] = True
        if old.get("outage") and state["up"] >= 2:
            announce = True
            state.pop("outage", None)
        state.pop("downSince", None)
    else:
        state["up"] = 0
        state["downSince"] = old.get("downSince", now)
        if old.get("healthy") and now - state["downSince"] >= 240:
            state["outage"] = True
    return state, announce
