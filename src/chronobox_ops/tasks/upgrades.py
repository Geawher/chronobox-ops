"""Realistic upgrades and migrations under live traffic.

- ``lib-upgrade``: a security advisory forces an in-house library upgrade whose
  new major version breaks loudly (renamed module) *and* silently (amounts
  become integer cents behind a friendly ``str``), under live orders;
- ``db-engine-migration``: move a live app from SQLite to PostgreSQL; the data
  has mixed formats and clients keep writing during the cutover;

All offline: wheels are built locally from bundled sources at setup.
"""

from __future__ import annotations

import textwrap

from chronobox_ops.scenario import Scenario
from chronobox_ops.tasks._shared import PG_START, python_service

# ---------------------------------------------------------------- lib-upgrade

PRICING_14 = {
    "pricing/__init__.py": '''
        """pricing 1.4 - shop pricing helpers."""
        from decimal import Decimal, ROUND_HALF_UP
        __version__ = "1.4.0"
        def total(lines, tax):
            """Total of [(unit_price, qty), ...] with tax, in euros (float, 2 decimals)."""
            net = sum(Decimal(str(p)) * q for p, q in lines)
            return float((net * (1 + tax.rate)).quantize(Decimal("0.01"), ROUND_HALF_UP))
    ''',
    "pricing/tax.py": """
        from decimal import Decimal
        class Tax:
            def __init__(self, name, rate):
                self.name, self.rate = name, Decimal(rate)
        VAT = Tax("VAT", "0.20")
    """,
}
PRICING_21 = {
    "pricing/__init__.py": '''
        """pricing 2.1 - shop pricing helpers."""
        from decimal import Decimal, ROUND_HALF_UP
        __version__ = "2.1.0"
        class Money(int):
            """Exact amount in minor units (cents)."""
            def __str__(self):
                return f"{int(self) // 100}.{int(self) % 100:02d} EUR"
            __repr__ = __str__
            @property
            def euros(self):
                return Decimal(int(self)) / 100
        def total(lines, tax):
            """Total of [(unit_price, qty), ...] with tax, as Money."""
            net = sum(Decimal(str(p)) * q for p, q in lines)
            gross = (net * (1 + tax.rate)).quantize(Decimal("0.01"), ROUND_HALF_UP)
            return Money(int(gross * 100))
    ''',
    "pricing/taxes.py": """
        from decimal import Decimal
        class Tax:
            def __init__(self, name, rate):
                self.name, self.rate = name, Decimal(rate)
        VAT = Tax("VAT", "0.20")
    """,
}
CHANGELOG = """\
pricing changelog
=================
2.1.0  security: fixes CVE-2026-31337 (tax rate injection through Tax(name, rate) strings).
2.0.0  `pricing.tax` is now `pricing.taxes`.
       `total()` returns a `Money` value for exact arithmetic.
1.4.0  first public release.
"""
SECURITY = """\
SECURITY ADVISORY SHOP-2026-07
pricing < 2.1 is affected by CVE-2026-31337. Upgrade the shop API to pricing 2.1.
Wheels are in /opt/wheelhouse (this machine has no internet access).
"""
PRICES = {"widget": "4.99", "gadget": "12.50", "gizmo": "0.99", "doohickey": "7.25"}


def _package(root: str, files: dict[str, str], version: str) -> str:
    parts = [f"mkdir -p {root}/pricing"]
    for path, code in files.items():
        parts.append(f"cat > {root}/{path} <<'PY'\n{textwrap.dedent(code).strip()}\nPY")
    parts.append(
        f"cat > {root}/pyproject.toml <<'TOML'\n[build-system]\nrequires = [\"setuptools\"]\n"
        f'build-backend = "setuptools.build_meta"\n[project]\nname = "pricing"\n'
        f'version = "{version}"\n[tool.setuptools]\npackages = ["pricing"]\nTOML'
    )
    return " && ".join(parts[:1]) + "\n" + "\n".join(parts[1:])


SHOP_APP = f"""
    import json, sqlite3, sys
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import pricing
    from pricing.tax import VAT
    PRICES = {PRICES!r}
    DB = "/srv/shop/orders.db"
    def order_total(items):
        return round(float(pricing.total([(PRICES[i["sku"]], i["qty"]) for i in items], VAT)), 2)
    class H(BaseHTTPRequestHandler):
        def reply(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code); self.end_headers(); self.wfile.write(body)
        def do_POST(self):
            try:
                o = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                total = order_total(o["items"])
                db = sqlite3.connect(DB, timeout=3)
                db.execute("insert into orders(ref, total) values (?, ?)", (o["ref"], total))
                db.commit(); db.close()
                self.reply(200, {{"ref": o["ref"], "total": total}})
            except Exception as error:
                self.reply(500, {{"error": repr(error)}})
        def do_GET(self):
            self.reply(200, {{"ok": True, "pricing": pricing.__version__}})
        def log_message(self, *a):
            pass
    if __name__ == "__main__":
        ThreadingHTTPServer(("127.0.0.1", 8200), H).serve_forever()
"""

SHOP_TEST = """
    import sys
    sys.path.insert(0, "/opt/shop")
    from app import order_total
    def test_basket_total():
        items = [{"sku": "widget", "qty": 3}, {"sku": "gizmo", "qty": 2}]
        assert order_total(items) == 20.34
    def test_single_item():
        assert order_total([{"sku": "gadget", "qty": 1}]) == 15.0
"""

SHOP_CLIENT = f"""
    import json, os, random, time, urllib.request
    from decimal import Decimal, ROUND_HALF_UP
    PRICES = {PRICES!r}
    STATE = "/var/lib/shop-client/state.json"
    try:
        st = json.load(open(STATE))
    except Exception:
        st = {{"ok": 0, "fail": 0, "n": 0}}
    acked = open("/var/lib/shop-client/acked.log", "a")
    while True:
        st["n"] += 1
        ref = f"o{{st['n']}}"
        items = [{{"sku": random.choice(list(PRICES)), "qty": random.randint(1, 4)}}
                 for _ in range(random.randint(1, 3))]
        net = sum(Decimal(PRICES[i["sku"]]) * i["qty"] for i in items)
        expected = float((net * Decimal("1.20")).quantize(Decimal("0.01"), ROUND_HALF_UP))
        body = json.dumps({{"ref": ref, "items": items}}).encode()
        for attempt in range(3):  # real clients retry briefly
            try:
                r = urllib.request.urlopen(urllib.request.Request(
                    "http://127.0.0.1:8200/order", body, method="POST"), timeout=3)
                if r.status == 200:
                    st["ok"] += 1
                    acked.write(json.dumps({{"ref": ref, "expected": expected}}) + "\\n")
                    acked.flush()
                    break
            except Exception:
                time.sleep(1)
        else:
            st["fail"] += 1
        with open(STATE + ".tmp", "w") as f:
            json.dump(st, f)
        os.replace(STATE + ".tmp", STATE)
        time.sleep(0.3)
"""

# Every acknowledged order must be stored with the expected total. ``None`` when the
# database cannot be read (the check then fails; the probe skips the reading).
SHOP_AUDIT = """
python3 - <<'PY'
import json, sqlite3
st = json.load(open("/var/lib/shop-client/state.json"))
# only complete lines: the client may be appending one right now
acked = [json.loads(l) for l in open("/var/lib/shop-client/acked.log") if l.endswith("\\n")]
try:
    db = sqlite3.connect("file:/srv/shop/orders.db?mode=ro", uri=True, timeout=5)
    stored = dict(db.execute("select ref, total from orders"))
    wrong = sum(1 for a in acked if a["ref"] in stored and abs(stored[a["ref"]] - a["expected"]) > 0.001)
    missing = sum(1 for a in acked if a["ref"] not in stored)
except Exception:
    wrong = missing = None
"""
# Validation only: one acknowledged order is charged differently afterwards.
SHOP_CORRUPT = (
    'ref=$(head -n1 /var/lib/shop-client/acked.log | jq -r .ref) && python3 -c "import sqlite3, sys; '
    "db = sqlite3.connect('/srv/shop/orders.db', timeout=10); "
    "n = db.execute('update orders set total = total + 1 where ref = ?', (sys.argv[1],)).rowcount; "
    'db.commit(); sys.exit(n != 1)" "$ref"'
)

# ------------------------------------------------------- db-engine-migration

NOTES_SEED = """
    import random, sqlite3, time
    rng = random.Random(11)
    db = sqlite3.connect("/srv/notes/notes.db")
    db.execute("pragma journal_mode=wal")
    db.execute("create table notes(id integer primary key, author text, body text, "
               "pinned, created)")
    base = 1767225600  # 2026-01-01
    for i in range(1, 5001):
        t = base + rng.randint(0, 200 * 86400)
        # three generations of the app wrote dates and flags differently
        gen = rng.random()
        if gen < 0.6:
            created, pinned = t, rng.choice([0, 1])
        elif gen < 0.9:
            created = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(t))
            pinned = rng.choice(["yes", "no"])
        else:
            created = time.strftime("%d/%m/%Y %H:%M", time.gmtime(t - t % 60))
            pinned = rng.choice(["Y", "N", ""])
        body = rng.choice(["call back", "renew TLS cert", "check backups", "ship order",
                           "it's done", "50% off \\u2014 promo", "line1\\nline2"]) + f" #{i}"
        db.execute("insert into notes values (?,?,?,?,?)", (i, f"user{rng.randint(1, 40)}",
                   body, pinned, created))
    db.commit()
"""

NOTES_APP = """
    import json, signal, sqlite3, time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import psycopg
    CONF = {}
    def load(*_):
        conf = dict(l.strip().split("=", 1) for l in open("/etc/notes.conf") if "=" in l)
        CONF.clear(); CONF.update(conf)
    def insert(author, body, pinned):
        now = int(time.time())
        if CONF["backend"] == "sqlite":
            db = sqlite3.connect(CONF["path"], timeout=3)
            cur = db.execute("insert into notes(author, body, pinned, created) values (?,?,?,?)",
                             (author, body, 1 if pinned else 0, now))
            db.commit(); db.close()
            return cur.lastrowid
        with psycopg.connect(CONF["dsn"], connect_timeout=3) as c:
            return c.execute("insert into notes(author, body, pinned, created) values "
                             "(%s,%s,%s,to_timestamp(%s)) returning id",
                             (author, body, bool(pinned), now)).fetchone()[0]
    class H(BaseHTTPRequestHandler):
        def reply(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code); self.end_headers(); self.wfile.write(body)
        def do_POST(self):
            try:
                n = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                self.reply(200, {"id": insert(n["author"], n["body"], n["pinned"])})
            except Exception as error:
                self.reply(500, {"error": repr(error)})
        def do_GET(self):
            self.reply(200, {"backend": CONF.get("backend")})
        def log_message(self, *a):
            pass
    load()
    signal.signal(signal.SIGHUP, load)
    ThreadingHTTPServer(("127.0.0.1", 8300), H).serve_forever()
"""

NOTES_README = """\
notes - team notes service (port 8300)
config: /etc/notes.conf
  backend=sqlite  path=/srv/notes/notes.db
  backend=postgres  dsn=dbname=notes user=notes host=/run/postgresql
kill -HUP <pid> re-reads the config without a restart (in-flight requests finish).
Target schema for PostgreSQL: /opt/notes/schema.sql (already applied to database `notes`).
"""

NOTES_SCHEMA = """\
create table notes(
  id bigserial primary key,
  author text not null,
  body text not null,
  pinned boolean not null default false,
  created timestamptz not null
);
"""

NOTES_CLIENT = """
    import json, os, random, time, urllib.request
    STATE = "/var/lib/notes-client/state.json"
    try:
        st = json.load(open(STATE))
    except Exception:
        st = {"ok": 0, "fail": 0, "n": 0}
    acked = open("/var/lib/notes-client/acked.log", "a")
    while True:
        st["n"] += 1
        note = {"author": f"user{random.randint(1, 40)}", "body": f"live note {st['n']}",
                "pinned": random.random() < 0.3}
        for attempt in range(3):
            try:
                r = urllib.request.urlopen(urllib.request.Request(
                    "http://127.0.0.1:8300/", json.dumps(note).encode(), method="POST"), timeout=3)
                if r.status == 200:
                    st["ok"] += 1
                    acked.write(json.dumps(note) + "\\n"); acked.flush()
                    break
            except Exception:
                time.sleep(1)
        else:
            st["fail"] += 1
        with open(STATE + ".tmp", "w") as f:
            json.dump(st, f)
        os.replace(STATE + ".tmp", STATE)
        time.sleep(0.25)
"""

# Every seed note and every acknowledged live note must be in PostgreSQL, unchanged. A
# body may appear twice only as identical copies (a client retry after a timeout).
NOTES_AUDIT = """
python3 - <<'PY'
import calendar, json, sqlite3, time
import psycopg
acked = [json.loads(l) for l in open("/var/lib/notes-client/acked.log") if l.endswith("\\n")]
try:
    conf = dict(l.strip().split("=", 1) for l in open("/etc/notes.conf") if "=" in l)
except Exception:
    conf = {}
def seed_expected():
    exec(open("/opt/notes/.seed.py").read().replace("sqlite3.connect(\\"/srv/notes/notes.db\\")",
         "sqlite3.connect(\\":memory:\\")").replace("db.commit()", "SEED.extend(db.execute('select * from notes'))"),
         {"SEED": SEED})
SEED = []
seed_expected()
def ts(v):
    if isinstance(v, int):
        return v
    for fmt in ("%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M"):
        try:
            return calendar.timegm(time.strptime(v, fmt))
        except ValueError:
            pass
    raise ValueError(v)
def flag(v):
    return v in (1, "1", "yes", "Y")
try:
    with psycopg.connect("dbname=notes user=notes host=/run/postgresql", connect_timeout=3) as c:
        rows = c.execute("select id, author, body, pinned, extract(epoch from created)::bigint from notes").fetchall()
    pg_ok = True
except Exception:
    rows, pg_ok = [], False
copies = {}
for r in rows:
    copies.setdefault(r[2], set()).add(r[1:])
seed_bad = sum(1 for (i, a, b, p, cr) in SEED if copies.get(b) != {(a, b, flag(p), ts(cr))})
live_bad = sum(1 for n in acked
               if (n["author"], n["body"], n["pinned"]) not in {r[:3] for r in copies.get(n["body"], ())})
"""
NOTES_PROBE = """
python3 - <<'PY'
import json
st = json.load(open("/var/lib/notes-client/state.json"))
print(json.dumps({"acked_notes": st["ok"], "failed_requests": st["fail"]}))
PY
"""
# Validation only: one migrated seed note is altered (its pinned flag flipped).
NOTES_CORRUPT = 'psql -q -v ON_ERROR_STOP=1 -d notes -U notes -h /run/postgresql -c "update notes set pinned = not pinned where id = 1"'


def tasks() -> list[Scenario]:
    return [
        Scenario(
            name="lib-upgrade",
            difficulty="medium",
            evidence=("/var/lib/shop-client/acked.log",),
            reference=(
                "cd /opt/shop && uv pip install -q --offline --python .venv/bin/python --find-links /opt/wheelhouse pricing==2.1.0 && python3 -c \"p='/opt/shop/app.py'; s=open(p).read().replace('from pricing.tax import', 'from pricing.taxes import').replace(', VAT)), 2)', ', VAT).euros), 2)'); open(p,'w').write(s)\" && .venv/bin/python -m pytest -q tests && pkill -f 'shop/.venv/bin/python app.py'",
            ),
            naive=(
                "cd /opt/shop && uv pip install -q --offline --python .venv/bin/python --find-links /opt/wheelhouse pricing==2.1.0 && sed -i 's/from pricing.tax import/from pricing.taxes import/' app.py && pkill -f 'shop/.venv/bin/python app.py'",
            ),
            family="discovery: dependency upgrade",
            prompt=(
                "Security advisory (see /opt/shop/SECURITY.md): the shop API in /opt/shop "
                "(port 8200, virtualenv /opt/shop/.venv, tests in /opt/shop/tests) must be "
                "upgraded to the in-house `pricing` library 2.1; wheels are in /opt/wheelhouse "
                "and there is no internet. Customers keep placing orders: the shop must keep "
                "taking orders, and every order must be charged correctly, during and after "
                "the upgrade."
            ),
            setup=(
                _package("/tmp/src/pricing-1.4", PRICING_14, "1.4.0"),
                _package("/tmp/src/pricing-2.1", PRICING_21, "2.1.0"),
                "mkdir -p /opt/wheelhouse && cd /tmp/src && for d in pricing-1.4 pricing-2.1; do "
                "python3 -m pip wheel -q --no-index --no-build-isolation -w /opt/wheelhouse ./$d "
                ">/dev/null; done && rm -rf /tmp/src",
                f"cat > /opt/wheelhouse/CHANGELOG.md <<'TXT'\n{CHANGELOG}TXT",
                "mkdir -p /opt/shop/tests /srv/shop /var/lib/shop-client && "
                f"cat > /opt/shop/SECURITY.md <<'TXT'\n{SECURITY}TXT",
                "cd /opt/shop && uv venv -q --system-site-packages .venv && "
                "uv pip install -q --offline --python .venv/bin/python --find-links /opt/wheelhouse "
                "pricing==1.4.0",
                python_service("/opt/shop/app.py", SHOP_APP),
                python_service("/opt/shop/tests/test_pricing.py", SHOP_TEST),
                "python3 -c \"import sqlite3; db=sqlite3.connect('/srv/shop/orders.db'); "
                "db.execute('pragma journal_mode=wal'); db.execute('create table orders(id integer "
                "primary key, ref text unique, total real)'); db.commit()\"",
                "printf '#!/bin/sh\\ncd /opt/shop\\nwhile true; do /opt/shop/.venv/bin/python "
                "app.py; sleep 1; done\\n' > /usr/local/sbin/shop-supervisor && chmod +x "
                "/usr/local/sbin/shop-supervisor && nohup shop-supervisor >/var/log/shop-api.log 2>&1 &",
                python_service("/opt/shop-client/client.py", SHOP_CLIENT),
                "sleep 1 && nohup sh -c 'while true; do python3 /opt/shop-client/client.py; sleep 1; "
                "done' >/var/log/shop-client.log 2>&1 &",
                "sleep 2",
            ),
            probe=SHOP_AUDIT + 'print(json.dumps({"acked_orders": st["ok"], "failed_requests": st["fail"], '
            '"wrong_orders": wrong, "lost_orders": missing}))\nPY',
            check=SHOP_AUDIT + "import subprocess, urllib.request\n"
            "v = subprocess.run(['/opt/shop/.venv/bin/python', '-c', 'import pricing; print(pricing.__version__)'], "
            "capture_output=True, text=True).stdout.strip()\n"
            "try:\n    up = json.load(urllib.request.urlopen('http://127.0.0.1:8200/', timeout=3))['pricing'] == v\n"
            "except Exception:\n    up = False\n"
            'print(json.dumps({"success": v == "2.1.0" and up and wrong == 0 and missing == 0, '
            '"pricing": v, "serving_new_version": up, "wrong_orders": wrong, "lost_orders": missing}))\nPY',
            settle=20.0,
            observe=20.0,  # no delayed effect: watch live orders for as long as natural grading
            corrupt=SHOP_CORRUPT,
            mem_mib=768,
            harm=("wrong_orders", "lost_orders", "failed_requests", "discarded_writes"),
            acked="acked_orders",
        ),
        Scenario(
            name="db-engine-migration",
            difficulty="hard",
            secrets=("/opt/notes/.seed.py",),
            evidence=("/var/lib/notes-client/acked.log",),
            reference=(
                'cat > /tmp/m.py <<\'PYX\'\nimport calendar, sqlite3, subprocess, time, psycopg\ndef ts(v):\n    if isinstance(v, int): return v\n    for fmt in ("%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M"):\n        try: return calendar.timegm(time.strptime(v, fmt))\n        except ValueError: pass\n    raise ValueError(v)\nflag = lambda v: v in (1, "1", "yes", "Y")\nsrc = sqlite3.connect("/srv/notes/notes.db", timeout=5)\npg = psycopg.connect("dbname=notes user=notes host=/run/postgresql", autocommit=True)\ndef copy(after):\n    rows = src.execute("select id, author, body, pinned, created from notes where id > ? order by id", (after,)).fetchall()\n    with pg.cursor() as c:\n        for i, a, b, p, cr in rows:\n            c.execute("insert into notes(id, author, body, pinned, created) values (%s,%s,%s,%s,to_timestamp(%s)) on conflict (id) do nothing", (i, a, b, flag(p), ts(cr)))\n    return rows[-1][0] if rows else after\nlast = copy(0)\npg.execute("select setval(\'notes_id_seq\', %s)", (last + 10000,))\nopen("/etc/notes.conf", "w").write("backend=postgres\\ndsn=dbname=notes user=notes host=/run/postgresql\\n")\nsubprocess.run("pkill -HUP -f \'python3 /opt/notes/app.py\'", shell=True)\ntime.sleep(2)\ncopy(last)\nprint("migrated")\nPYX',
                "python3 /tmp/m.py",
            ),
            naive=(
                'cat > /tmp/m.py <<\'PYX\'\nimport sqlite3, subprocess, psycopg\nsrc = sqlite3.connect("/srv/notes/notes.db", timeout=5)\npg = psycopg.connect("dbname=notes user=notes host=/run/postgresql", autocommit=True)\nfor i, a, b, p, cr in src.execute("select * from notes").fetchall():\n    try:\n        pg.execute("insert into notes(author, body, pinned, created) values (%s,%s,%s,%s)",\n                   (a, b, bool(p), cr if isinstance(cr, str) else __import__(\'datetime\').datetime.utcfromtimestamp(cr)))\n    except Exception:\n        pass\nopen("/etc/notes.conf", "w").write("backend=postgres\\ndsn=dbname=notes user=notes host=/run/postgresql\\n")\nsubprocess.run("pkill -HUP -f \'python3 /opt/notes/app.py\'", shell=True)\nPYX',
                "python3 /tmp/m.py",
            ),
            family="discovery: database engine migration",
            prompt=(
                "Move the `notes` service (port 8300, docs in /opt/notes/README) from SQLite "
                "(/srv/notes/notes.db) to the PostgreSQL database `notes` (connect with `psql -d "
                "notes -U notes -h /run/postgresql`; the target schema is already applied) and "
                "switch the service over to it. Clients keep writing notes the whole time: no "
                "note may be lost or altered, existing ones included."
            ),
            setup=(
                PG_START,
                'runuser -u postgres -- psql -qc "create role notes login superuser" && '
                "runuser -u postgres -- createdb -O notes notes",
                "mkdir -p /opt/notes /srv/notes /var/lib/notes-client && "
                f"cat > /opt/notes/README <<'TXT'\n{NOTES_README}TXT",
                f"cat > /opt/notes/schema.sql <<'SQL'\n{NOTES_SCHEMA}SQL",
                "psql -q -d notes -U notes -h /run/postgresql -f /opt/notes/schema.sql",
                python_service("/opt/notes/seed.py", NOTES_SEED),
                "python3 /opt/notes/seed.py && mv /opt/notes/seed.py /opt/notes/.seed.py",
                "printf 'backend=sqlite\\npath=/srv/notes/notes.db\\n' > /etc/notes.conf",
                python_service("/opt/notes/app.py", NOTES_APP),
                "printf '#!/bin/sh\\nwhile true; do python3 /opt/notes/app.py; sleep 1; done\\n' "
                "> /usr/local/sbin/notes-supervisor && chmod +x /usr/local/sbin/notes-supervisor && "
                "nohup notes-supervisor >/var/log/notes.log 2>&1 &",
                python_service("/opt/notes-client/client.py", NOTES_CLIENT),
                "sleep 1 && nohup sh -c 'while true; do python3 /opt/notes-client/client.py; "
                "sleep 1; done' >/var/log/notes-client.log 2>&1 &",
                "sleep 2",
            ),
            probe=NOTES_PROBE,
            check=NOTES_AUDIT + 'print(json.dumps({"success": pg_ok and conf.get("backend") == "postgres" and '
            'seed_bad == 0 and live_bad == 0, "backend": conf.get("backend"), '
            '"seed_notes_wrong_or_missing": seed_bad, "live_notes_wrong_or_missing": live_bad, '
            '"postgres_rows": len(rows)}))\nPY',
            settle=15.0,
            observe=15.0,  # no delayed effect: watch live notes for as long as natural grading
            corrupt=NOTES_CORRUPT,
            mem_mib=1024,
            harm=("failed_requests", "discarded_writes"),
            # the prompt forbids lost or altered notes, not failed requests
            tolerated=("failed_requests",),
            acked="acked_notes",
        ),
    ]
