"""Building blocks shared by tasks: PostgreSQL bootstrap, the shop workload, service files."""

from __future__ import annotations

import textwrap

# ---------------------------------------------------------------- shared pieces

PG_START = (
    "sed -i -E 's/(peer|scram-sha-256|md5)$/trust/' /etc/postgresql/15/main/pg_hba.conf && "
    "pg_ctlcluster 15 main start && "
    "runuser -u postgres -- psql -qc \"create role shop login superuser password 'shop'\" && "
    "runuser -u postgres -- createdb -O shop shop"
)

# Shop app workload: one order every 0.1 s with a 2 s statement timeout.
# Counts successes / failures; the probe compares successes with stored rows.
SHOP_LOADGEN = textwrap.dedent("""
    mkdir -p /opt/shop && cat > /opt/shop/loadgen.py <<'PY'
    import json, os, random, time
    import psycopg
    STATS = "/var/lib/shop/stats.json"
    try:  # the app stands for the outside world: its counters survive restarts
        s = json.load(open(STATS))
        ok, fail, last_fail = s["ok"], s["fail"], s["last_fail"]
    except Exception:
        ok = fail = 0
        last_fail = 0.0
    conn = None
    while True:
        try:
            if conn is None or conn.closed:
                conn = psycopg.connect("dbname=shop user=shop host=/run/postgresql",
                                       autocommit=True, connect_timeout=2)
                conn.execute("set statement_timeout = 2000")
            cid = random.randint(1, 100000)
            conn.execute(
                "insert into orders(customer_id, customer_email, item, qty, source) "
                "values (%s, %s, %s, %s, 'app')",
                (cid, f"user{cid}@shop.test", "widget", random.randint(1, 5)))
            ok += 1
        except Exception:
            fail += 1
            last_fail = time.time()
            try:
                conn.close()
            except Exception:
                pass
            conn = None
        tmp = STATS + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"ok": ok, "fail": fail, "last_fail": last_fail, "t": time.time()}, f)
        os.replace(tmp, STATS)
        time.sleep(0.1)
    PY
    mkdir -p /var/lib/shop && nohup sh -c 'while true; do python3 /opt/shop/loadgen.py; sleep 1; done' >/var/log/shop-loadgen.log 2>&1 &
""").strip()

SHOP_SCHEMA = (
    'runuser -u postgres -- psql -q -d shop -c "create table orders(id bigserial primary key, '
    "customer_id int, customer_email text, item text, qty int, source text default 'seed', "
    'created timestamptz default now()); alter table orders owner to shop;"'
)


def python_service(path: str, code: str) -> str:
    return f"mkdir -p {path.rsplit('/', 1)[0]} && cat > {path} <<'PY'\n{textwrap.dedent(code).strip()}\nPY"
