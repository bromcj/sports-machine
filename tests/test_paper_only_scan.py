"""audit.py's paper-only scans, run on every push.

The first checks under SCANNER: PAPER ONLY read the code, not data: nothing
may send anything but a GET (the owner's opt-in phone alert aside), no order
endpoint or order call may appear anywhere, and nothing may call arm(). CI has
no data, so it cannot run audit.py; it runs the same scan here, on this
checkout, and on files planted in a temporary folder. Each plant is a way code
could place an order or arm a strategy. The scan reads one file at a time, so
a plant alone in a folder is caught exactly as it would be among the real
modules.

The plants spell the order path and the SDK call in pieces: the scan reads
tests/ too, and either written whole in this file would be a hit on it.
"""
import pytest

import audit

URL = "https://api.elections.kalshi.com/trade-api/v2" + "/portf" + "olio/ord" + "ers"
HOST = "api.elections.kalshi.com"
SDK_CALL = "create" + "_order"
# The same URL built at run time, so no string in the plant holds the path.
CONCAT = ('BASE = "https://api.elections.kalshi.com/trade-api/v2"\n'
          'URL = BASE + "/port" + "folio/ord" + "ers"\n')

# One per way of sending an order found by the Phase A review (plant_senders).
SHAPES = {
    "requests.post": f'import requests\nrequests.post("{URL}", json={{}})\n',
    "requests.request('POST')":
        f'import requests\nrequests.request("POST", "{URL}", json={{}})\n',
    "Session().post": f'import requests\nrequests.Session().post("{URL}", json={{}})\n',
    "getattr(requests, 'po' + 'st'), literal URL":
        f'import requests\ngetattr(requests, "po" + "st")("{URL}", json={{}})\n',
    "urllib Request(method=POST) + urlopen":
        f'import urllib.request as u\nr = u.Request("{URL}", data=b"{{}}", method="POST")\n'
        'u.urlopen(r)\n',
    "urllib Request + build_opener().open":
        f'import urllib.request as u\nr = u.Request("{URL}", data=b"{{}}", method="POST")\n'
        'u.build_opener().open(r)\n',
    "http.client .request('POST')":
        f'import http.client\nc = http.client.HTTPSConnection("{HOST}")\n'
        'c.request("POST", "/trade-api/v2/port" + "folio/ord" + "ers", body="{}")\n',
    "http.client putrequest/endheaders, built path":
        f'import http.client\nc = http.client.HTTPSConnection("{HOST}")\n'
        'c.putrequest("POST", "/trade-api/v2/port" + "folio/ord" + "ers")\n'
        'c.endheaders(b"{}")\nc.getresponse()\n',
    "httpx.post": f'import httpx\nhttpx.post("{URL}", json={{}})\n',
    "httpx.stream('POST'), built URL":
        'import httpx\n' + CONCAT + 'with httpx.stream("POST", URL, json={}) as r:\n    r.read()\n',
    "subprocess curl -X POST, literal URL":
        f'import subprocess\nsubprocess.run(["curl", "-X", "POST", "{URL}", "-d", "{{}}"])\n',
    "subprocess curl -X POST, built URL":
        'import subprocess\n' + CONCAT + 'subprocess.run(["curl", "-X", "POST", URL, "-d", "{}"])\n',
    "os.system curl, built URL":
        'import os\n' + CONCAT + 'os.system("curl -X POST " + URL + " -d {}")\n',
    "requests.put": f'import requests\nrequests.put("{URL}/abc", json={{}})\n',
    "requests.delete": f'import requests\nrequests.delete("{URL}/abc")\n',
    "requests.post, built URL": 'import requests\n' + CONCAT + 'requests.post(URL, json={})\n',
    "from requests import post as p, built URL":
        'from requests import post as p\n' + CONCAT + 'p(URL, json={})\n',
    "send_it = requests.post, built URL":
        'import requests\n' + CONCAT + 'send_it = requests.post\nsend_it(URL, json={})\n',
    "getattr(requests, 'po' + 'st'), built URL":
        'import requests\n' + CONCAT + 'getattr(requests, "po" + "st")(URL, json={})\n',
    "functools.partial(requests.post), built URL":
        'import functools, requests\n' + CONCAT + 'functools.partial(requests.post, URL)(json={})\n',
    "a Kalshi-SDK-style order call, no URL":
        'from kalshi_python import PortfolioApi\napi = PortfolioApi()\n'
        f'api.{SDK_CALL}(ticker="KXNFL", side="yes", action="buy", count=1, type="limit", yes_price=45)\n',
    "bytes URL + requests.request":
        f'import requests\nrequests.request(b"POST".decode(), b"{URL}".decode(), json={{}})\n',
    "bytes URL + an aliased poster":
        f'from requests import post as p\np(b"{URL}".decode(), json={{}})\n',
    "socket sendall of raw HTTP, built path":
        'import socket, ssl\ns = ssl.create_default_context().wrap_socket('
        f'socket.create_connection(("{HOST}", 443)), server_hostname="{HOST}")\n'
        's.sendall(("POST /trade-api/v2/port" + "folio/ord" + "ers HTTP/1.1\\r\\n\\r\\n").encode())\n',
}

# Where a plant can sit. Only data, history and tooling folders are skipped.
PLACES = ["scanner/zz_plant.py", "zz_plant.py", "research/zz_plant.py",
          "tests/zz_plant.py", "dataflow.py", "datasets/zz_plant.py",
          # archive/ is tracked history, but a module can sit there and be
          # imported; a .pyw file imports and runs like a .py one.
          "archive/zz_plant.py", "scanner/zz_plant.pyw"]

# The scheduled jobs and workflows are code too.
SCRIPTS = {
    "zz_job.bat": f'@echo off\ncurl -X POST "{URL}" -H "Content-Type: application/json" -d "{{}}"\n',
    "zz_job.ps1": f'Invoke-RestMethod -Uri "{URL}" -Method Post -Body "{{}}"\n',
    ".github/workflows/zz.yml":
        f'jobs:\n  go:\n    steps:\n      - run: curl -X POST "{URL}" -d "{{}}"\n',
}


# Ways code could arm a strategy, which must stay a person's act (gate 3).
# tests/ is not read for these: the gate tests arm throwaway files on purpose.
V = "from model import validation\n\n\ndef go():\n"
ARMS = {
    "validation.arm('x')": ("scanner/zz_arm.py", V + "    validation.arm('x')\n"),
    "from ... import arm; arm('x')":
        ("scanner/zz_arm.py", "from model.validation import arm\n\n\ndef go():\n    arm('x')\n"),
    "from ... import arm as switch_on":
        ("scanner/zz_arm.py",
         "from model.validation import arm as switch_on\n\n\ndef go():\n    switch_on('x')\n"),
    "getattr(validation, 'arm')": ("scanner/zz_arm.py", V + "    getattr(validation, 'arm')('x')\n"),
    "a bound name: f = validation.arm":("scanner/zz_arm.py", V + "    f = validation.arm\n    f('x')\n"),
    "functools.partial(validation.arm)":
        ("scanner/zz_arm.py", "import functools\n" + V + "    functools.partial(validation.arm, 'x')()\n"),
    "python -c from subprocess":
        ("scanner/zz_arm.py", 'import subprocess\nsubprocess.run(["python", "-c",'
                              ' "from model.validation import arm; arm(\'x\')"])\n'),
    "a top-level module named data*": ("data_tools.py", V + "    validation.arm('x')\n"),
    "a scheduled .bat job":
        ("zz_job.bat", '@echo off\npython -c "from model import validation; validation.arm(\'x\')"\n'),
    "a workflow step":
        (".github/workflows/zz.yml",
         "jobs:\n  go:\n    steps:\n      - run: python -c \"import model.validation as v; v.arm('x')\"\n"),
}


def _plant(root, rel, body):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


def _caught(root):
    senders, orders, _ = audit.paper_only_scan(root)
    return senders + orders


def test_this_checkout_is_paper_only():
    senders, orders, arms = audit.paper_only_scan()
    assert senders == [], senders
    assert orders == [], orders
    assert arms == [], arms


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_every_way_of_sending_an_order_is_caught(tmp_path, shape):
    _plant(tmp_path, "scanner/zz_plant.py", SHAPES[shape])
    assert _caught(tmp_path), shape


@pytest.mark.parametrize("rel", PLACES)
def test_every_folder_that_can_hold_code_is_read(tmp_path, rel):
    _plant(tmp_path, rel, SHAPES["requests.post"])
    assert _caught(tmp_path), rel


@pytest.mark.parametrize("rel", sorted(SCRIPTS))
def test_the_scheduled_jobs_and_workflows_are_read(tmp_path, rel):
    _plant(tmp_path, rel, SCRIPTS[rel])
    senders, orders, _ = audit.paper_only_scan(tmp_path)
    assert senders and orders, (senders, orders)


def test_the_phone_alert_is_the_only_post_allowed(tmp_path):
    body = ('import requests\n\n\ndef {}():\n'
            '    requests.post("https://ntfy.sh/topic", data=b"x")\n')
    _plant(tmp_path, "notify.py", body.format("_ntfy"))
    assert audit.paper_only_scan(tmp_path)[0] == []
    _plant(tmp_path, "notify.py", body.format("deliver"))
    senders = audit.paper_only_scan(tmp_path)[0]
    assert len(senders) == 1 and senders[0].startswith("notify.py:5 "), senders


@pytest.mark.parametrize("shape", sorted(ARMS))
def test_every_way_of_calling_arm_is_caught(tmp_path, shape):
    rel, body = ARMS[shape]
    _plant(tmp_path, rel, body)
    assert audit.paper_only_scan(tmp_path)[2], shape


def test_only_the_audits_own_probe_may_call_arm(tmp_path):
    guard = "def betting_guard(v, probe):\n    v.arm(probe)\n"
    _plant(tmp_path, "audit.py", guard)
    assert audit.paper_only_scan(tmp_path)[2] == []
    _plant(tmp_path, "audit.py", guard + "\n\ndef later(v):\n    v.arm('kalshi_mlb_ml')\n")
    assert audit.paper_only_scan(tmp_path)[2], "an arm() anywhere else in audit.py"
