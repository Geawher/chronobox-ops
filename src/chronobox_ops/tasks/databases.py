"""Database tasks: schema changes and data repair under live traffic.

- ``pg-backfill-under-traffic``: add and backfill a NOT NULL column on a 2M-row
  table while the app inserts *and updates old orders* with a 1 s timeout;
- ``pg-expand-contract-rename``: rename a column used by two live services whose
  config reloads only take effect after a drain period;
- ``mojibake-repair``: repair double-encoded names (cp1252 mojibake, some of
  them twice) without touching valid non-ASCII names, under live signups;
- ``pg-int-to-bigint``: the int4 order id is minutes from overflow; widen it
  (and the referencing column, and the sequence) without blocking checkouts.
"""

from __future__ import annotations

import textwrap

from chronobox_ops.scenario import Scenario
from chronobox_ops.tasks._shared import PG_START, SHOP_DSN, python_service, supervised, write_file


def _seed_changed_sql(table: str, key: str, rows: int, columns: dict[str, str]) -> str:
    """SQL counting seed rows (keyed ``key`` = f(g), g = 1..rows) missing or no longer as seeded."""
    key_column, key_expr = key.split("=")
    same = " and ".join(f"t.{column} = {expr}" for column, expr in columns.items())
    return (
        f"select {rows} - count(*) from {table} t join generate_series(1, {rows}) g "
        f"on t.{key_column.strip()} = {key_expr.strip()} where {same}"
    )


# ===================================================== pg-backfill-under-traffic

BACKFILL_ROWS = 2_000_000
REGIONS = {
    "FR": "eu-west", "ES": "eu-west", "PT": "eu-west", "BE": "eu-west", "NL": "eu-west",
    "DE": "eu-central", "AT": "eu-central", "PL": "eu-central",
    "IT": "eu-south", "GR": "eu-south", "US": "na", "CA": "na",
}  # fmt: skip
_COUNTRY_ARRAY = "array[" + ",".join(f"'{c}'" for c in REGIONS) + "]"
ADDRESS_SQL = (
    "g || ' ' || (array['Main Street','Rue de la Paix','Hauptstrasse','Via Roma'])[1 + g % 4] "
    "|| ', ' || md5(g::text) || ' ' || md5((g * 3)::text)"
)
# Seed order columns the app never changes (it only updates status), as functions of g = id.
BACKFILL_SEED_COLUMNS = {
    "customer_id": "(g::bigint * 7919) % 100000",
    "country": f"({_COUNTRY_ARRAY})[1 + (g * 31) % {len(REGIONS)}]",
    "sku": "(array['widget','gadget','gizmo','doohickey'])[1 + g % 4]",
    "qty": "1 + g % 5",
    "shipping_address": ADDRESS_SQL,
}

BACKFILL_SEED = f"""
psql -v ON_ERROR_STOP=1 -q -d shop -U shop -h /run/postgresql <<'SQL'
create table regions(country text primary key, region text not null);
insert into regions values {", ".join(f"('{c}', '{r}')" for c, r in REGIONS.items())};
create table orders(
  id bigserial primary key,
  customer_id int not null,
  country text not null,
  sku text not null,
  qty int not null,
  status text not null default 'new',
  shipping_address text not null default '',
  created timestamptz not null default now()
);
insert into orders({", ".join(BACKFILL_SEED_COLUMNS)}, status, created)
  select {", ".join(BACKFILL_SEED_COLUMNS.values())}, 'delivered',
         now() - ({BACKFILL_ROWS} - g) * interval '15 seconds'
  from generate_series(1, {BACKFILL_ROWS}) g;
create index orders_customer_idx on orders(customer_id);
create index orders_created_idx on orders(created);
vacuum analyze orders;
SQL
""".strip()

BACKFILL_README = """\
shop-app  (/opt/shop/app.py, supervised: it is restarted automatically)

- places new orders (insert into orders ...)
- updates existing orders all the time: payments, returns, cancellations,
  customer-service edits (update orders set status = ... where id = ...)
- every statement runs with statement_timeout = 1s; a request is retried
  twice before the customer gets an error

Release 4.2 (deployed): the app fills orders.region for new orders by itself
as soon as that column exists (it re-checks the schema every couple of seconds).
The country -> region mapping is the `regions` table.
"""

BACKFILL_APP = f"""
    import json, os, random, threading, time
    import psycopg
    DSN = "{SHOP_DSN}"
    STATE = "/var/lib/shop/client.json"
    WORKERS = 4
    try:  # the app's users stand for the outside world: counters survive restarts
        st = json.load(open(STATE))
    except Exception:
        st = {{"ok": 0, "fail": 0, "last_fail": 0.0, "t": 0.0}}
    acked = open("/var/lib/shop/acked.log", "a")
    updates = open("/var/lib/shop/updates.log", "a")  # "<id> <status> try|ok", in each order's update order
    LOCK = threading.Lock()
    schema = {{"has_region": False, "checked": 0.0}}
    class Worker:
        def __init__(self, k):  # worker k updates the orders whose id % WORKERS == k
            self.k, self.conn, self.regions = k, None, {{}}
        def connect(self):
            self.conn = psycopg.connect(DSN, autocommit=True, connect_timeout=2)
            self.conn.execute("set statement_timeout = 1000")
            self.regions = dict(self.conn.execute("select country, region from regions").fetchall())
        def place_order(self):
            if time.time() - schema["checked"] > 2:
                schema["has_region"] = self.conn.execute(
                    "select 1 from information_schema.columns where table_name = 'orders' "
                    "and column_name = 'region'").fetchone() is not None
                schema["checked"] = time.time()
            country = random.choice(sorted(self.regions))
            cols, vals = ["customer_id", "country", "sku", "qty"], [
                random.randint(1, 100000), country, random.choice(["widget", "gadget", "gizmo"]),
                random.randint(1, 5)]
            if schema["has_region"]:
                cols.append("region"); vals.append(self.regions[country])
            sql = f"insert into orders({{', '.join(cols)}}) values ({{', '.join(['%s'] * len(cols))}}) returning id"
            return self.conn.execute(sql, vals).fetchone()[0]
        def update_order(self, oid, status):
            self.conn.execute("update orders set status = %s where id = %s", (status, oid))
        def request(self, action):
            for attempt in range(3):
                try:
                    if self.conn is None or self.conn.closed:
                        self.connect()
                    return True, action()
                except Exception:
                    try:
                        self.conn.close()
                    except Exception:
                        pass
                    self.conn = None
                    time.sleep(0.2)
            return False, None
        def run(self):
            while True:
                ok, oid = self.request(self.place_order)
                uid = random.randrange(self.k or WORKERS, {BACKFILL_ROWS} + 1, WORKERS)
                status = random.choice(["paid", "returned", "cancelled", "delivered"])
                with LOCK:
                    updates.write(f"{{uid}} {{status}} try\\n"); updates.flush()
                ok2, _ = self.request(lambda: self.update_order(uid, status))
                with LOCK:
                    if ok2:
                        updates.write(f"{{uid}} {{status}} ok\\n"); updates.flush()
                    if ok:
                        st["ok"] += 1
                        acked.write(f"{{oid}}\\n"); acked.flush()
                    st["fail"] += (not ok) + (not ok2)
                    if not (ok and ok2):
                        st["last_fail"] = time.time()
                    st["t"] = time.time()
                    with open(STATE + ".tmp", "w") as f:
                        json.dump(st, f)
                    os.replace(STATE + ".tmp", STATE)
                time.sleep(0.3)
    for k in range(1, WORKERS):
        threading.Thread(target=Worker(k).run, daemon=True).start()
    Worker(0).run()
"""

BACKFILL_PROBE = f"""
python3 - <<'PY'
import json, psycopg
s = json.load(open("/var/lib/shop/client.json"))
try:
    with psycopg.connect("{SHOP_DSN}", connect_timeout=2) as c:
        c.execute("set statement_timeout = 3000")
        stored = c.execute("select count(*) from orders where id > {BACKFILL_ROWS}").fetchone()[0]
    lost = max(0, s["ok"] - stored)
except Exception:
    lost = None
print(json.dumps({{"acked_orders": s["ok"], "failed_requests": s["fail"], "lost_orders": lost}}))
PY
""".strip()

BACKFILL_CHECK = f"""
python3 - <<'PY'
import json, time, psycopg
s = json.load(open("/var/lib/shop/client.json"))
acked = {{int(l) for l in open("/var/lib/shop/acked.log") if l.endswith("\\n")}}
col, wrong, stored, seed_changed, duplicated, bad_status = None, None, set(), None, None, None
try:
    with psycopg.connect("{SHOP_DSN}", connect_timeout=3) as c:
        col = c.execute("select attnotnull from pg_attribute where attrelid = 'orders'::regclass "
                        "and attname = 'region' and not attisdropped").fetchone()
        if col is not None:
            wrong = c.execute("with m(country, region) as (values {", ".join(f"('{c}', '{r}')" for c, r in REGIONS.items())}) "
                              "select count(*) from orders o left join m using (country) "
                              "where o.region is distinct from m.region").fetchone()[0]
        stored = {{i for (i,) in c.execute("select id from orders where id > {BACKFILL_ROWS}")}}
        seed_changed = c.execute({_seed_changed_sql("orders", "id = g", BACKFILL_ROWS, BACKFILL_SEED_COLUMNS)!r}).fetchone()[0]
        duplicated = c.execute("select count(*) - count(distinct id) from orders").fetchone()[0]
        # An updated order holds its last acknowledged status or one tried after it (the reply
        # may be lost); updates logged once the query below has started may show too.
        log = open("/var/lib/shop/updates.log")
        before = log.read()
        before, after = before[:before.rfind("\\n") + 1], before[before.rfind("\\n") + 1:]
        allowed = {{}}
        for line in before.splitlines():
            oid, status, outcome = line.split()
            allowed[int(oid)] = {{status}} if outcome == "ok" else allowed.get(int(oid), {{"delivered"}}) | {{status}}
        statuses = c.execute("select id, status from orders where (id <= {BACKFILL_ROWS} and status <> 'delivered') "
                             "or (id > {BACKFILL_ROWS} and status <> 'new') or id = any(%s)", (list(allowed),)).fetchall()
        for line in (after + log.read()).splitlines(keepends=True):
            if line.endswith("\\n"):
                oid, status, _ = line.split()
                allowed[int(oid)] = allowed.get(int(oid), {{"delivered"}}) | {{status}}
        bad_status = sum(1 for i, status in statuses
                         if status not in allowed.get(i, {{"delivered" if i <= {BACKFILL_ROWS} else "new"}}))
except Exception as error:
    print(repr(error))
lost = len(acked - stored)
s = json.load(open("/var/lib/shop/client.json"))  # the queries above take a while
live = time.time() - s["t"] < 15
print(json.dumps({{"success": bool(col and col[0]) and wrong == 0 and lost == 0 and seed_changed == 0
                  and duplicated == 0 and bad_status == 0 and live,
                  "region_column": col is not None, "not_null": bool(col and col[0]),
                  "orders_with_wrong_region": wrong, "lost_orders": lost,
                  "seed_orders_lost_or_changed": seed_changed, "duplicate_order_ids": duplicated,
                  "orders_with_lost_status_update": bad_status, "app_running": live}}))
PY
""".strip()

BACKFILL_REFERENCE = write_file(
    "/tmp/backfill.py",
    f"""
    import time, psycopg
    c = psycopg.connect("{SHOP_DSN}", autocommit=True)
    def ddl(sql):  # short lock waits so the app never queues behind us for long
        for _ in range(100):
            try:
                with c.transaction():
                    c.execute("set local lock_timeout = '200ms'")
                    c.execute(sql)
                return
            except psycopg.errors.LockNotAvailable:
                time.sleep(0.3)
        raise SystemExit("no lock for: " + sql)
    ddl("alter table orders add column region text")
    time.sleep(5)  # the app starts filling region for new orders
    fill = ("update orders o set region = r.region from regions r "
            "where r.country = o.country and o.id >= %s and o.id < %s and o.region is null")
    done = 0
    while True:
        top = c.execute("select max(id) from orders").fetchone()[0]
        if done > top:
            break
        while done <= top:
            c.execute(fill, (done, done + 10000))
            done += 10000
    ddl("alter table orders add constraint orders_region_not_null check (region is not null) not valid")
    c.execute("alter table orders validate constraint orders_region_not_null")
    ddl("alter table orders alter column region set not null")
    ddl("alter table orders drop constraint orders_region_not_null")
    print("backfilled")
    """,
)

# ===================================================== pg-expand-contract-rename

CRM_DSN = "dbname=crm user=crm host=/run/postgresql"
CRM_SEED_ROWS = 50_000
FIRST = [
    "Alice",
    "Bruno",
    "Chloe",
    "Dmitri",
    "Elena",
    "Farid",
    "Greta",
    "Hugo",
    "Ines",
    "Jonas",
    "Kenji",
    "Lena",
]
LAST = [
    "Martin",
    "Novak",
    "Okafor",
    "Petit",
    "Quinn",
    "Rossi",
    "Silva",
    "Tanaka",
    "Ueda",
    "Vidal",
    "Weber",
]
_SEED_NAME_SQL = (
    f"(array[{','.join(repr(n) for n in FIRST)}])[1 + id % {len(FIRST)}] || ' ' || "
    f"(array[{','.join(repr(n) for n in LAST)}])[1 + (id * 7) % {len(LAST)}]"
)

CRM_SEED = f"""
runuser -u postgres -- psql -qc "create role crm login superuser" && runuser -u postgres -- createdb -O crm crm && \\
psql -v ON_ERROR_STOP=1 -q -d crm -U crm -h /run/postgresql <<'SQL'
create table customers(
  id serial primary key,
  email text not null unique,
  fullname text not null,
  created timestamptz not null default now()
);
insert into customers(id, email, fullname)
  select id, 'customer' || id || '@crm.test', {_SEED_NAME_SQL}
  from generate_series(1, {CRM_SEED_ROWS}) id;
select setval('customers_id_seq', {CRM_SEED_ROWS});
vacuum analyze customers;
SQL
""".strip()

CRM_README = """\
CRM services
============
crm-writer  port 8401  POST /customers {"email", "name"}   new signups
crm-reader  port 8402  GET  /customers/<id>                 profile lookups
both:                  GET  /status                         active configuration

Code: /opt/crm/service.py (one program, the role is its argument).
Config: /etc/crm/writer.conf and /etc/crm/reader.conf
  name_column=<column of customers holding the customer's name>
Database: psql -d crm -U crm -h /run/postgresql

Reload: kill -HUP <pid> re-reads the config without a restart. The new config is
checked against the database first (a bad one is rejected and logged), then the
connection pool is rotated: requests keep using the old config until the old
connections have drained, after which the new config is active (see /status).
Logs: /var/log/crm-writer.log, /var/log/crm-reader.log
"""

CRM_SERVICE = f"""
    import json, signal, sys, threading, time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import psycopg
    ROLE = sys.argv[1]
    PORT = {{"writer": 8401, "reader": 8402}}[ROLE]
    CONF_PATH = f"/etc/crm/{{ROLE}}.conf"
    DSN = "{CRM_DSN}"
    POOL_DRAIN_S = 8  # old pool connections are recycled after their idle period
    ACTIVE = {{}}
    def read_conf():
        return dict(l.strip().split("=", 1) for l in open(CONF_PATH) if "=" in l)
    def reload():
        conf = read_conf()
        try:  # validate the new config before rotating the pool
            with psycopg.connect(DSN, connect_timeout=3) as c:
                c.execute(f"select {{conf['name_column']}} from customers limit 0")
        except Exception as error:
            print(f"reload rejected: {{error!r}}", flush=True)
            return
        print(f"reload: draining pool, new config in {{POOL_DRAIN_S}}s: {{conf}}", flush=True)
        time.sleep(POOL_DRAIN_S)
        ACTIVE.clear(); ACTIVE.update(conf)
        print(f"reload: active config {{conf}}", flush=True)
    def query(sql, args):
        with psycopg.connect(DSN, autocommit=True, connect_timeout=3) as c:
            c.execute("set statement_timeout = 2000")
            return c.execute(sql, args).fetchone()
    class H(BaseHTTPRequestHandler):
        def reply(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code); self.end_headers(); self.wfile.write(body)
        def do_GET(self):
            try:
                if self.path == "/status":
                    return self.reply(200, {{"role": ROLE, **ACTIVE}})
                if ROLE == "reader" and self.path.startswith("/customers/"):
                    row = query(f"select {{ACTIVE['name_column']}} from customers where id = %s",
                                (int(self.path.rsplit("/", 1)[1]),))
                    return self.reply(200, {{"name": row[0]}}) if row else self.reply(404, {{}})
                self.reply(404, {{"error": "not found"}})
            except Exception as error:
                self.reply(500, {{"error": repr(error)}})
        def do_POST(self):
            try:
                if ROLE != "writer" or self.path != "/customers":
                    return self.reply(404, {{"error": "not found"}})
                c = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                row = query(f"insert into customers(email, {{ACTIVE['name_column']}}) values (%s, %s) "
                            "returning id", (c["email"], c["name"]))
                self.reply(200, {{"id": row[0]}})
            except Exception as error:
                self.reply(500, {{"error": repr(error)}})
        def log_message(self, *a):
            pass
    ACTIVE.update(read_conf())
    signal.signal(signal.SIGHUP, lambda *a: threading.Thread(target=reload, daemon=True).start())
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
"""

CRM_CLIENT = f"""
    import json, os, random, time, urllib.request
    FIRST = {FIRST!r}
    LAST = {LAST!r}
    def seed_name(i):
        return f"{{FIRST[i % len(FIRST)]}} {{LAST[(i * 7) % len(LAST)]}}"
    STATE = "/var/lib/crm-client/state.json"
    try:
        st = json.load(open(STATE))
    except Exception:
        st = {{"ok": 0, "fail": 0, "n": 0}}
    acked = open("/var/lib/crm-client/acked.log", "a")
    recent = []
    def call(fn):
        for attempt in range(3):  # real clients retry briefly
            try:
                return fn()
            except Exception:
                time.sleep(0.5)
        return None
    def signup(email, name):
        r = urllib.request.urlopen(urllib.request.Request(
            "http://127.0.0.1:8401/customers", json.dumps({{"email": email, "name": name}}).encode(),
            method="POST"), timeout=3)
        return json.load(r)["id"]
    def lookup(cid, name):
        got = json.load(urllib.request.urlopen(f"http://127.0.0.1:8402/customers/{{cid}}", timeout=3))
        if got["name"] != name:
            raise ValueError(f"customer {{cid}}: {{got['name']!r}} != {{name!r}}")
        return True
    while True:
        st["n"] += 1
        name = f"{{random.choice(FIRST)}} {{random.choice(LAST)}}"
        cid = call(lambda: signup(f"live{{st['n']}}-{{random.randint(0, 10**9)}}@crm.test", name))
        if cid is None:
            st["fail"] += 1
        else:
            st["ok"] += 1; st["t_ok"] = time.time()
            acked.write(json.dumps({{"id": cid, "name": name}}) + "\\n"); acked.flush()
            recent = (recent + [(cid, name)])[-200:]
        if recent and random.random() < 0.5:
            rid, rname = random.choice(recent)
        else:
            rid = random.randint(1, {CRM_SEED_ROWS}); rname = seed_name(rid)
        if call(lambda: lookup(rid, rname)) is None:
            st["fail"] += 1
        with open(STATE + ".tmp", "w") as f:
            json.dump(st, f)
        os.replace(STATE + ".tmp", STATE)
        time.sleep(0.15)
"""

CRM_AUDIT = f"""
python3 - <<'PY'
import json, time, urllib.request, psycopg
acked = [json.loads(l) for l in open("/var/lib/crm-client/acked.log") if l.endswith("\\n")]
def status(port):
    try:
        return json.load(urllib.request.urlopen(f"http://127.0.0.1:{{port}}/status", timeout=3)).get("name_column")
    except Exception:
        return None
cols, seed_bad, live_bad, duplicated = set(), None, None, None
try:
    with psycopg.connect("{CRM_DSN}", connect_timeout=3) as c:
        cols = {{r[0] for r in c.execute("select attname from pg_attribute where attrelid = "
                                         "'customers'::regclass and attnum > 0 and not attisdropped")}}
        col = "display_name" if "display_name" in cols else "fullname"
        expr = {_SEED_NAME_SQL!r}
        seed_bad = {CRM_SEED_ROWS} - c.execute(
            f"select count(*) from customers where id <= {CRM_SEED_ROWS} and {{col}} = {{expr}}").fetchone()[0]
        names = dict(c.execute(f"select id, {{col}} from customers where id > {CRM_SEED_ROWS}").fetchall())
        live_bad = sum(1 for a in acked if names.get(a["id"]) != a["name"])
        duplicated = c.execute("select count(*) - count(distinct id) from customers").fetchone()[0]
except Exception as error:
    print(repr(error))
st = json.load(open("/var/lib/crm-client/state.json"))
taking = time.time() - st.get("t_ok", 0) < 15  # signups still go through
"""
CRM_PROBE = """
python3 - <<'PY'
import json
st = json.load(open("/var/lib/crm-client/state.json"))
print(json.dumps({"acked_signups": st["ok"], "failed_requests": st["fail"]}))
PY
"""

CRM_REFERENCE = (
    """psql -v ON_ERROR_STOP=1 -q -d crm -U crm -h /run/postgresql <<'SQL'
alter table customers add column display_name text;
create function customers_name_sync() returns trigger language plpgsql as $$
begin
  if tg_op = 'INSERT' then
    new.display_name := coalesce(new.display_name, new.fullname);
    new.fullname := coalesce(new.fullname, new.display_name);
  elsif new.display_name is distinct from old.display_name then
    new.fullname := new.display_name;
  else
    new.display_name := new.fullname;
  end if;
  return new;
end $$;
create trigger customers_name_sync before insert or update on customers
  for each row execute function customers_name_sync();
update customers set display_name = fullname where display_name is null;
SQL""",
    "for role in writer reader; do sed -i 's/^name_column=.*/name_column=display_name/' /etc/crm/$role.conf; "
    'pkill -HUP -f "crm/service[.]py $role"; done',
    "for i in $(seq 60); do w=$(curl -s localhost:8401/status); r=$(curl -s localhost:8402/status); "
    'case "$w$r" in *fullname*) sleep 1;; *) echo switched; exit 0;; esac; done; exit 1',
    """psql -v ON_ERROR_STOP=1 -q -d crm -U crm -h /run/postgresql <<'SQL'
alter table customers alter column display_name set not null;
drop trigger customers_name_sync on customers;
drop function customers_name_sync();
alter table customers drop column fullname;
SQL""",
)

# ============================================================== mojibake-repair

MOJI_SEED_ROWS = 20_000
MOJI_FIRST = [
    "John", "Mary", "Peter", "Sarah", "David", "Emma", "Tom", "Laura", "Mark", "Julia", "Paul", "Nina",
    "José", "Zoë", "Jürgen", "Søren", "Françoise", "Mónica", "Iñaki", "Renée", "Chloé", "Anaïs", "Björn",
    "Paweł", "Małgorzata", "Wojciech", "Stanisław", "Agnieszka", "Bolesław", "Jiří", "Tomáš", "Ondřej",
    "Çağla", "Gökhan", "Şebnem", "Ödön", "Erzsébet", "Győző", "Kateřina", "Michał",
    "伟", "芳", "静", "文杰", "明", "Anna 🌸", "Max 🎉", "Lucía", "François", "Ägnes",
]  # fmt: skip
MOJI_LAST = [
    "Smith", "Brown", "Miller", "Wilson", "Taylor", "Clark", "Lewis", "Walker", "Young", "Hill", "Green",
    "Adams", "Baker", "Turner", "Parker", "Evans", "Moore", "Allen",
    "García", "Müller", "Gonçalves", "Núñez", "Øyen", "Dvořák", "Wałęsa", "Wiśniewski", "Kowalczyk",
    "Wójcik", "Łęcki", "Nováková", "Yılmaz", "Şahin", "Öztürk", "Kovács", "Szőke", "Ćosić",
    "王", "李", "张", "陈", "刘", "Straße", "Ångström", "Brontë", "Dupré",
]  # fmt: skip

MOJI_SEED = f"""
    import random, sqlite3, time
    FIRST = {MOJI_FIRST!r}
    LAST = {MOJI_LAST!r}
    def garble(s):  # what the bad deploy did: utf-8 bytes read back as windows-1252
        return s.encode("utf-8").decode("cp1252")
    def roundtrips(s):
        try:
            return s.encode("cp1252").decode("utf-8") != s
        except UnicodeError:
            return False
    def generate():
        rng = random.Random(7)
        names = {{}}
        for i in range(1, {MOJI_SEED_ROWS} + 1):
            names[i] = f"{{rng.choice(FIRST)}} {{rng.choice(LAST)}}"
            assert not roundtrips(names[i]), names[i]
        stored, corrupted = dict(names), []
        candidates = [i for i, n in names.items() if not n.isascii()]
        rng.shuffle(candidates)
        for i in candidates:
            if len(corrupted) >= {MOJI_SEED_ROWS} // 20:
                break
            try:
                once = garble(names[i])
                stored[i] = garble(once) if rng.random() < 0.15 else once
            except UnicodeError:  # bytes windows-1252 cannot show: left alone by the deploy
                continue
            corrupted.append(i)
        return names, stored, corrupted
    def created(i):  # signup dates of the seed customers
        return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(1735689600 + i * 1361))
    if __name__ == "__main__":
        names, stored, corrupted = generate()
        db = sqlite3.connect("/srv/crm/customers.db")
        db.execute("pragma journal_mode=wal")
        db.execute("create table customers(id integer primary key, email text not null unique, "
                   "name text not null, created text not null default current_timestamp)")
        db.executemany("insert into customers(id, email, name, created) values (?, ?, ?, ?)",
                       [(i, f"customer{{i}}@crm.test", stored[i], created(i)) for i in sorted(stored)])
        db.commit()
"""

MOJI_APP = """
    import json, sqlite3
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    DB = "/srv/crm/customers.db"
    class H(BaseHTTPRequestHandler):
        def reply(self, code, obj):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers(); self.wfile.write(body)
        def do_POST(self):
            try:
                c = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8"))
                db = sqlite3.connect(DB, timeout=2)
                cur = db.execute("insert into customers(email, name) values (?, ?)", (c["email"], c["name"]))
                db.commit(); db.close()
                self.reply(200, {"id": cur.lastrowid})
            except Exception as error:
                self.reply(500, {"error": repr(error)})
        def do_GET(self):
            try:
                if self.path.startswith("/customers/"):
                    db = sqlite3.connect(DB, timeout=2)
                    row = db.execute("select id, email, name from customers where id = ?",
                                     (int(self.path.rsplit("/", 1)[1]),)).fetchone()
                    db.close()
                    return self.reply(200, dict(zip(("id", "email", "name"), row))) if row else self.reply(404, {})
                self.reply(200, {"service": "crm-api", "endpoints": ["POST /customers", "GET /customers/<id>"]})
            except Exception as error:
                self.reply(500, {"error": repr(error)})
        def log_message(self, *a):
            pass
    ThreadingHTTPServer(("127.0.0.1", 8500), H).serve_forever()
"""

MOJI_CLIENT = f"""
    import json, os, random, time, urllib.request
    FIRST = {MOJI_FIRST!r}
    LAST = {MOJI_LAST!r}
    STATE = "/var/lib/crm-client/state.json"
    try:
        st = json.load(open(STATE))
    except Exception:
        st = {{"ok": 0, "fail": 0, "n": 0}}
    acked = open("/var/lib/crm-client/acked.log", "a", encoding="utf-8")
    while True:
        st["n"] += 1
        name = f"{{random.choice(FIRST)}} {{random.choice(LAST)}}"
        email = f"signup{{st['n']}}-{{random.randint(0, 10**9)}}@crm.test"
        body = json.dumps({{"email": email, "name": name}}).encode()
        for attempt in range(3):  # real clients retry briefly
            try:
                r = urllib.request.urlopen(urllib.request.Request(
                    "http://127.0.0.1:8500/customers", body, method="POST"), timeout=3)
                cid = json.load(r)["id"]
                st["ok"] += 1; st["t_ok"] = time.time()
                acked.write(json.dumps({{"id": cid, "email": email, "name": name}}) + "\\n"); acked.flush()
                break
            except Exception:
                time.sleep(0.5)
        else:
            st["fail"] += 1
        with open(STATE + ".tmp", "w") as f:
            json.dump(st, f)
        os.replace(STATE + ".tmp", STATE)
        time.sleep(0.3)
"""

MOJI_AUDIT = """
python3 - <<'PY'
import json, sqlite3, time, urllib.request
st = json.load(open("/var/lib/crm-client/state.json"))
acked = [json.loads(l) for l in open("/var/lib/crm-client/acked.log", encoding="utf-8") if l.endswith("\\n")]
import runpy
seed = runpy.run_path("/opt/crm/.seed.py")
expected, _, corrupted = seed["generate"]()
corrupted = set(corrupted)
try:
    db = sqlite3.connect("file:/srv/crm/customers.db?mode=ro", uri=True, timeout=5)
    rows = {r[0]: r[1:] for r in db.execute("select id, email, name, created from customers")}
except Exception as error:  # unreadable: no measurement (the check then fails)
    print(repr(error)); rows = None
damaged = still = lost = emails = created = None
if rows is not None:
    names = {i: name for i, (_, name, _) in rows.items()}
    damaged = sum(1 for i, v in expected.items() if i not in corrupted and names.get(i) != v)
    still = sum(1 for i in corrupted if names.get(i) != expected[i])
    lost = sum(1 for a in acked if rows.get(a["id"], (None, None))[:2] != (a["email"], a["name"]))
    emails = sum(1 for i in expected if rows.get(i, (None,))[0] != f"customer{i}@crm.test")
    created = sum(1 for i in expected if rows.get(i, (None,) * 3)[2] != seed["created"](i))
taking = time.time() - st.get("t_ok", 0) < 15  # signups still go through
try:
    up = urllib.request.urlopen("http://127.0.0.1:8500/", timeout=3).status == 200
except Exception:
    up = False
"""

MOJI_FIX = """
    import sqlite3, sys
    CODEC = sys.argv[1]
    db = sqlite3.connect("/srv/crm/customers.db", timeout=10)
    def fix(s):
        try:
            return s.encode(CODEC).decode("utf-8")
        except UnicodeError:
            return s
    total = 0
    for _ in range(int(sys.argv[2])):
        changes = []
        for i, name in db.execute("select id, name from customers").fetchall():
            if fix(name) != name:
                changes.append((fix(name), i))
        if not changes:
            break
        db.executemany("update customers set name = ? where id = ?", changes)
        db.commit()
        total += len(changes)
    print("repaired", total)
"""

# ============================================================= pg-int-to-bigint

INT_MAX = 2**31 - 1
# Big enough that rewriting either table under an ACCESS EXCLUSIVE lock (a plain ALTER ...
# TYPE bigint) takes far longer than a checkout's retries (~3.6 s): the naive fix fails
# reliably, even run one table at a time.
BIG_ROWS = 4_000_000
BIG_STEP = 509  # seed ids are spread out: the id space was allocated in blocks
BIG_SEED_MAX = BIG_ROWS * BIG_STEP
BIG_MARGIN = 6000  # ~10 minutes of checkouts at 10/s
assert BIG_SEED_MAX < INT_MAX - BIG_MARGIN
BIG_FAST_FORWARD_LEFT = 30  # ids left before INT_MAX after the fast-forward: ~3 s of checkouts
# Seed rows as functions of g (order id = g * BIG_STEP); the app never changes them.
BIG_SEED_ORDER_COLUMNS = {
    "customer_id": "(g::bigint * 7919) % 100000",
    "total_cents": "100 + (g * 37) % 50000",
    "source": "'seed'",
    "shipping_address": ADDRESS_SQL,
}
BIG_SEED_ITEM_COLUMNS = {
    "sku": "(array['widget','gadget','gizmo','doohickey'])[1 + g % 4]",
    "qty": "1 + g % 5",
    "description": "'gift wrap: ' || md5((g * 7)::text) || ' ' || md5((g * 11)::text)",
}

BIG_NOT_NULL = [("orders", c) for c in ("id", *BIG_SEED_ORDER_COLUMNS, "created")] + [
    ("order_items", c) for c in ("item_id", "order_id", *BIG_SEED_ITEM_COLUMNS)
]

BIG_SEED = f"""
psql -v ON_ERROR_STOP=1 -q -d shop -U shop -h /run/postgresql <<'SQL'
create table orders(
  id serial primary key,
  customer_id int not null,
  total_cents int not null,
  source text not null default 'seed',
  shipping_address text not null default '',
  created timestamptz not null default now()
);
create table order_items(
  item_id bigserial primary key,
  order_id int not null references orders(id),
  sku text not null,
  qty int not null,
  description text not null default ''
);
insert into orders(id, {", ".join(BIG_SEED_ORDER_COLUMNS)}, created)
  select g * {BIG_STEP}, {", ".join(BIG_SEED_ORDER_COLUMNS.values())},
         now() - ({BIG_ROWS} - g) * interval '20 seconds'
  from generate_series(1, {BIG_ROWS}) g;
insert into order_items(order_id, {", ".join(BIG_SEED_ITEM_COLUMNS)})
  select g * {BIG_STEP}, {", ".join(BIG_SEED_ITEM_COLUMNS.values())}
  from generate_series(1, {BIG_ROWS}) g;
create index order_items_order_id_idx on order_items(order_id);
create index orders_customer_idx on orders(customer_id);
create index orders_created_idx on orders(created);
select setval('orders_id_seq', {INT_MAX - BIG_MARGIN});
vacuum analyze orders;
vacuum analyze order_items;
SQL
""".strip()

BIG_README = """\
shop checkout (/opt/shop/checkout.py, supervised: restarted automatically)

Each checkout is one transaction:
  insert into orders(customer_id, total_cents, source) ... returning id
  insert into order_items(order_id, sku, qty) ...
statement_timeout = 1s; a checkout is retried twice before the customer sees an
error. About 10 checkouts per second, around the clock.
"""

BIG_APP = f"""
    import json, os, random, threading, time
    import psycopg
    DSN = "{SHOP_DSN}"
    STATE = "/var/lib/shop/client.json"
    WORKERS, PERIOD = 4, 0.4  # ~10 checkouts per second in total
    try:  # customers stand for the outside world: counters survive restarts
        st = json.load(open(STATE))
    except Exception:
        st = {{"ok": 0, "fail": 0, "last_fail": 0.0, "t": 0.0, "last_id": 0}}
    acked = open("/var/lib/shop/acked.log", "a")
    LOCK = threading.Lock()
    def worker():
        conn = None
        def checkout():
            nonlocal conn
            if conn is None or conn.closed:
                conn = psycopg.connect(DSN, autocommit=True, connect_timeout=2)
                conn.execute("set statement_timeout = 1000")
            with conn.transaction():
                oid = conn.execute("insert into orders(customer_id, total_cents, source) "
                                   "values (%s, %s, 'app') returning id",
                                   (random.randint(1, 100000), random.randint(100, 50000))).fetchone()[0]
                conn.execute("insert into order_items(order_id, sku, qty) values (%s, %s, %s)",
                             (oid, random.choice(["widget", "gadget", "gizmo"]), random.randint(1, 5)))
            return oid
        due = time.time() + random.random() * PERIOD
        while True:
            oid = None
            for attempt in range(3):
                try:
                    oid = checkout()
                    break
                except Exception:
                    try:
                        conn.close()
                    except Exception:
                        pass
                    conn = None
                    time.sleep(0.2)
            with LOCK:
                if oid is not None:
                    st["ok"] += 1; st["last_id"] = max(st["last_id"], oid)
                    acked.write(f"{{oid}}\\n"); acked.flush()
                else:
                    st["fail"] += 1; st["last_fail"] = time.time()
                st["t"] = time.time()
                with open(STATE + ".tmp", "w") as f:
                    json.dump(st, f)
                os.replace(STATE + ".tmp", STATE)
            due = max(due + PERIOD, time.time() - 1)
            time.sleep(max(0, due - time.time()))
    for _ in range(WORKERS - 1):
        threading.Thread(target=worker, daemon=True).start()
    worker()
"""

BIG_PROBE = f"""
python3 - <<'PY'
import json, psycopg
s = json.load(open("/var/lib/shop/client.json"))
out = {{"acked_orders": s["ok"], "failed_requests": s["fail"]}}
stored = {{"lost_orders": "select count(*) from orders where id > {BIG_SEED_MAX}",
          "lost_items": "select count(*) from order_items where order_id > {BIG_SEED_MAX}"}}
try:
    with psycopg.connect("{SHOP_DSN}", autocommit=True, connect_timeout=2) as c:
        c.execute("set statement_timeout = 3000")
        for key, sql in stored.items():
            try:  # each checkout commits its order and its item before it is acknowledged
                out[key] = max(0, s["ok"] - c.execute(sql).fetchone()[0])
            except Exception:
                pass
except Exception:
    pass
print(json.dumps(out))
PY
""".strip()

# Finds the sequence behind orders.id (the agent may have replaced it): defines `seq`.
BIG_FIND_SEQUENCE = r"""
seq = c.execute("select pg_get_serial_sequence('orders', 'id')").fetchone()[0]
if seq is None:
    d = c.execute("select pg_get_expr(adbin, adrelid) from pg_attrdef a join pg_attribute t on "
                  "t.attrelid = a.adrelid and t.attnum = a.adnum where a.adrelid = 'orders'::regclass "
                  "and t.attname = 'id'").fetchone()
    m = re.search(r"nextval\('([^']+)'", d[0]) if d else None
    seq = m and m.group(1)
"""

# Grader-only: move the id sequence close to INT_MAX (never backwards) and let the real
# checkouts cross it, instead of waiting minutes for them to get there.
BIG_FAST_FORWARD = f"""
python3 - <<'PY'
import re, psycopg
with psycopg.connect("{SHOP_DSN}", autocommit=True, connect_timeout=5) as c:
{textwrap.indent(BIG_FIND_SEQUENCE.strip(), "    ")}
    print("sequence", seq)
    if seq:
        last = c.execute(f"select last_value from {{seq}}").fetchone()[0]
        target = {INT_MAX - BIG_FAST_FORWARD_LEFT}
        if last < target:
            c.execute("select setval(%s, %s)", (seq, target))
        print("last_value", last, "->", max(last, target))
PY
""".strip()

BIG_CHECK = f"""
python3 - <<'PY'
import collections, json, re, time, psycopg
s = json.load(open("/var/lib/shop/client.json"))
acked = {{int(l) for l in open("/var/lib/shop/acked.log") if l.endswith("\\n")}}
r = {{"id_type": None, "order_id_type": None, "sequence_max": None, "primary_key": False, "items_primary_key": False,
      "foreign_key": False, "not_null_dropped": None, "lost_orders": None, "lost_items": None, "extra_items": None,
      "seed_orders_lost_or_changed": None, "seed_items_lost_or_changed": None}}
def has_key(c, table, col, kind):
    return c.execute(
        "select count(*) from pg_constraint k join pg_attribute a on a.attrelid = k.conrelid and a.attnum = k.conkey[1] "
        "where k.conrelid = %s::regclass and k.contype = %s and a.attname = %s and cardinality(k.conkey) = 1 "
        "and k.convalidated and (k.contype = 'p' or k.confrelid = 'orders'::regclass)", (table, kind, col)).fetchone()[0] == 1
def coltype(c, table, col):
    row = c.execute("select format_type(atttypid, atttypmod) from pg_attribute where attrelid = %s::regclass "
                    "and attname = %s and not attisdropped", (table, col)).fetchone()
    return row and row[0]
try:
    with psycopg.connect("{SHOP_DSN}", connect_timeout=3) as c:
        r["id_type"] = coltype(c, "orders", "id")
        r["order_id_type"] = coltype(c, "order_items", "order_id")
{textwrap.indent(BIG_FIND_SEQUENCE.strip(), "        ")}
        if seq:
            r["sequence_max"] = c.execute("select seqmax from pg_sequence where seqrelid = %s::regclass",
                                          (seq,)).fetchone()[0]
        r["primary_key"] = has_key(c, "orders", "id", "p")
        r["items_primary_key"] = has_key(c, "order_items", "item_id", "p")
        r["foreign_key"] = has_key(c, "order_items", "order_id", "f")  # validated, not just NOT VALID
        not_null = {{(t, a) for t, a in c.execute(
            "select attrelid::regclass::text, attname from pg_attribute where attnotnull and not attisdropped "
            "and attrelid in ('orders'::regclass, 'order_items'::regclass)")}}
        r["not_null_dropped"] = sorted(f"{{t}}.{{a}}" for t, a in {BIG_NOT_NULL!r} if (t, a) not in not_null)
        orders = {{i for (i,) in c.execute("select id from orders where id > {BIG_SEED_MAX}")}}
        items = collections.Counter(i for (i,) in c.execute("select order_id from order_items where order_id > {BIG_SEED_MAX}"))
        r["lost_orders"], r["lost_items"] = len(acked - orders), len(acked - set(items))
        r["extra_items"] = sum(k - 1 for k in items.values())  # each checkout inserts one item
        r["seed_orders_lost_or_changed"] = c.execute({_seed_changed_sql("orders", f"id = g * {BIG_STEP}", BIG_ROWS, BIG_SEED_ORDER_COLUMNS)!r}).fetchone()[0]
        r["seed_items_lost_or_changed"] = c.execute({_seed_changed_sql("order_items", f"order_id = g * {BIG_STEP}", BIG_ROWS, BIG_SEED_ITEM_COLUMNS)!r}).fetchone()[0]
except Exception as error:
    print(repr(error))
s = json.load(open("/var/lib/shop/client.json"))  # the queries above take a while
r["checkouts_healthy_60s"] = time.time() - s["last_fail"] > 60 and time.time() - s["t"] < 10
r["passed_int_max"] = s.get("last_id", 0) > {INT_MAX}
r["success"] = (r["id_type"] == "bigint" and r["order_id_type"] == "bigint"
                and (r["sequence_max"] or 0) > {INT_MAX} and r["primary_key"] and r["items_primary_key"]
                and r["foreign_key"] and r["not_null_dropped"] == [] and r["extra_items"] == 0
                and r["lost_orders"] == 0 and r["lost_items"] == 0 and r["seed_orders_lost_or_changed"] == 0
                and r["seed_items_lost_or_changed"] == 0 and r["checkouts_healthy_60s"] and r["passed_int_max"])
print(json.dumps(r))
PY
""".strip()

BIG_REFERENCE = write_file(
    "/tmp/widen.py",
    f"""
    import time, psycopg
    c = psycopg.connect("{SHOP_DSN}", autocommit=True)
    def ddl(*statements):  # one short transaction; never queue the app behind us for long
        for _ in range(200):
            try:
                with c.transaction():
                    c.execute("set local lock_timeout = '200ms'")
                    for sql in statements:
                        c.execute(sql)
                return
            except psycopg.errors.LockNotAvailable:
                time.sleep(0.2)
        raise SystemExit("no lock for: " + statements[0])
    ddl("alter sequence orders_id_seq as bigint")
    ddl("alter table orders add column id_new bigint", "alter table order_items add column order_id_new bigint")
    ddl(\"\"\"create function orders_id_sync() returns trigger language plpgsql as $$
           begin new.id_new := new.id; return new; end $$\"\"\",
        "create trigger orders_id_sync before insert or update on orders for each row execute function orders_id_sync()",
        \"\"\"create function items_order_id_sync() returns trigger language plpgsql as $$
           begin new.order_id_new := new.order_id; return new; end $$\"\"\",
        "create trigger items_order_id_sync before insert or update on order_items for each row "
        "execute function items_order_id_sync()")
    for table, key, new, old, step in (("orders", "id", "id_new", "id", 10000 * {BIG_STEP}),
                                       ("order_items", "item_id", "order_id_new", "order_id", 10000)):
        top = c.execute(f"select max({{key}}) from {{table}}").fetchone()[0]
        for lo in range(0, top + 1, step):
            c.execute(f"update {{table}} set {{new}} = {{old}} where {{key}} >= %s and {{key}} < %s "
                      f"and {{new}} is null", (lo, lo + step))
        c.execute(f"update {{table}} set {{new}} = {{old}} where {{key}} >= %s and {{new}} is null", (top,))
    c.execute("create unique index concurrently orders_id_new_key on orders(id_new)")
    c.execute("create index concurrently order_items_order_id_new_idx on order_items(order_id_new)")
    ddl("alter table orders add constraint orders_id_new_nn check (id_new is not null) not valid")
    c.execute("alter table orders validate constraint orders_id_new_nn")
    ddl("alter table order_items add constraint items_order_id_new_nn check (order_id_new is not null) not valid")
    c.execute("alter table order_items validate constraint items_order_id_new_nn")
    ddl("lock table orders, order_items in access exclusive mode",
        "drop trigger orders_id_sync on orders",
        "drop trigger items_order_id_sync on order_items",
        "alter table order_items drop constraint order_items_order_id_fkey",
        "alter table orders drop constraint orders_pkey",
        "alter sequence orders_id_seq owned by none",
        "alter table orders drop column id",
        "alter table orders rename column id_new to id",
        "alter table orders alter column id set not null",
        "alter table orders alter column id set default nextval('orders_id_seq')",
        "alter sequence orders_id_seq owned by orders.id",
        "alter table orders add constraint orders_pkey primary key using index orders_id_new_key",
        "alter table orders drop constraint orders_id_new_nn",
        "alter table order_items drop column order_id",
        "alter table order_items rename column order_id_new to order_id",
        "alter table order_items alter column order_id set not null",
        "alter table order_items drop constraint items_order_id_new_nn",
        "alter index order_items_order_id_new_idx rename to order_items_order_id_idx",
        "alter table order_items add constraint order_items_order_id_fkey foreign key (order_id) "
        "references orders(id) not valid")
    c.execute("alter table order_items validate constraint order_items_order_id_fkey")
    c.execute("drop function orders_id_sync(), items_order_id_sync()")
    print("widened")
    """,
)


def tasks() -> list[Scenario]:
    return [
        Scenario(
            name="pg-backfill-under-traffic",
            family="database migration",
            prompt=(
                "In the shop's PostgreSQL database `shop` (connect with `psql -d shop -U shop -h "
                "/run/postgresql`), add a column `region` to the `orders` table. Each order's region "
                "comes from the `regions` table, by the order's `country`. Every existing order must "
                "get its region, and the column must end up NOT NULL. The shop app (notes in "
                "/opt/shop/README.md) keeps taking and updating orders the whole time: it must not "
                "get errors, and no order may be lost."
            ),
            setup=(
                PG_START,
                BACKFILL_SEED,
                f"mkdir -p /opt/shop /var/lib/shop && cat > /opt/shop/README.md <<'TXT'\n{BACKFILL_README}TXT",
                python_service("/opt/shop/app.py", BACKFILL_APP),
                supervised("shop-supervisor", "python3 /opt/shop/app.py", "/var/log/shop-app.log"),
                "sleep 3",
            ),
            probe=BACKFILL_PROBE,
            check=BACKFILL_CHECK,
            settle=30.0,
            observe=20.0,
            reference=(BACKFILL_REFERENCE, "python3 /tmp/backfill.py"),
            naive=(
                'psql -v ON_ERROR_STOP=1 -d shop -U shop -h /run/postgresql -c "alter table orders '
                "add column region text; update orders o set region = r.region from regions r where "
                'r.country = o.country; alter table orders alter column region set not null"',
            ),
            corrupt="id=$(head -n 5 /var/lib/shop/acked.log | tail -n 1) && psql -v ON_ERROR_STOP=1 "
            '-d shop -U shop -h /run/postgresql -c "delete from orders where id = $id"',
            harm=("failed_requests", "lost_orders", "discarded_writes"),
            acked="acked_orders",
            evidence=("/var/lib/shop/acked.log", "/var/lib/shop/updates.log"),
            mem_mib=1536,
            data_mib=4096,
        ),
        Scenario(
            name="pg-expand-contract-rename",
            family="database migration",
            prompt=(
                "Rename the column `customers.fullname` to `display_name` in the CRM PostgreSQL "
                "database `crm` (`psql -d crm -U crm -h /run/postgresql`). Two live services use "
                "that column: crm-writer (signups, port 8401) and crm-reader (profile lookups, port "
                "8402); docs in /opt/crm/README. When you are done, the column must be named "
                "`display_name`, `fullname` must be gone, and both services must be running on the "
                "new name. Signups and lookups continue the whole time and must not fail, and no "
                "customer's name may be lost or changed."
            ),
            setup=(
                PG_START,
                CRM_SEED,
                f"mkdir -p /etc/crm /opt/crm /var/lib/crm-client && cat > /opt/crm/README <<'TXT'\n{CRM_README}TXT",
                "printf 'name_column=fullname\\n' > /etc/crm/writer.conf && "
                "printf 'name_column=fullname\\n' > /etc/crm/reader.conf",
                python_service("/opt/crm/service.py", CRM_SERVICE),
                "printf '#!/bin/sh\\nwhile true; do python3 /opt/crm/service.py $1; sleep 1; done\\n' "
                "> /usr/local/sbin/crm-supervisor && chmod +x /usr/local/sbin/crm-supervisor",
                "nohup crm-supervisor writer >/var/log/crm-writer.log 2>&1 &",
                "nohup crm-supervisor reader >/var/log/crm-reader.log 2>&1 &",
                python_service("/opt/crm-client/client.py", CRM_CLIENT),
                "sleep 1 && "
                + supervised("crm-clients", "python3 /opt/crm-client/client.py", "/var/log/crm-client.log"),
                "sleep 2",
            ),
            probe=CRM_PROBE,
            check=CRM_AUDIT + "w, r = status(8401), status(8402)\n"
            'print(json.dumps({"success": "display_name" in cols and "fullname" not in cols and '
            'w == "display_name" and r == "display_name" and seed_bad == 0 and live_bad == 0 and '
            'duplicated == 0 and taking, "columns": sorted(cols), "writer_column": w, "reader_column": r, '
            '"seed_names_wrong": seed_bad, "signups_wrong_or_missing": live_bad, '
            '"duplicate_customer_ids": duplicated, "taking_signups": taking}))\nPY',
            settle=20.0,
            observe=20.0,  # no fast-forward: a reload's pool drain (8 s) just has to elapse
            reference=CRM_REFERENCE,
            naive=(
                "psql -d crm -U crm -h /run/postgresql -c 'alter table customers rename column "
                "fullname to display_name'",
                "sed -i 's/^name_column=.*/name_column=display_name/' /etc/crm/writer.conf "
                "/etc/crm/reader.conf && pkill -HUP -f 'crm/service[.]py'",
                "sleep 15",
            ),
            corrupt="psql -v ON_ERROR_STOP=1 -d crm -U crm -h /run/postgresql "
            "-c \"update customers set display_name = display_name || ' Jr' where id = 17\"",
            harm=("failed_requests", "discarded_writes"),
            acked="acked_signups",
            evidence=("/var/lib/crm-client/acked.log",),
            mem_mib=1024,
        ),
        Scenario(
            name="mojibake-repair",
            family="data repair",
            prompt=(
                "Since last week's bad deploy of the CRM API (rolled back since), some customer "
                "names in the CRM database are garbled, e.g. `JosÃ© GarcÃ­a` instead of `José "
                "García`. The data is in SQLite at /srv/crm/customers.db, served by the CRM API on "
                "port 8500. Repair the corrupted names in place. New customers keep signing up "
                "through the API the whole time: it must keep working, and no other data may change."
            ),
            setup=(
                "mkdir -p /opt/crm /srv/crm /var/lib/crm-client",
                python_service("/opt/crm/.seed.py", MOJI_SEED),
                "PYTHONIOENCODING=utf-8 python3 /opt/crm/.seed.py",
                python_service("/opt/crm/app.py", MOJI_APP),
                supervised("crm-supervisor", "python3 /opt/crm/app.py", "/var/log/crm-api.log"),
                python_service("/opt/crm-client/client.py", MOJI_CLIENT),
                "sleep 1 && "
                + supervised("crm-clients", "python3 /opt/crm-client/client.py", "/var/log/crm-client.log"),
                "sleep 2",
            ),
            probe=MOJI_AUDIT + 'print(json.dumps({"acked_customers": st["ok"], "failed_requests": st["fail"], '
            '"valid_names_damaged": damaged, "lost_customers": lost, "emails_changed": emails, '
            '"created_changed": created}))\nPY',
            check=MOJI_AUDIT + 'print(json.dumps({"success": up and taking and damaged == 0 and still == 0 and '
            'lost == 0 and emails == 0 and created == 0, "still_corrupted": still, "corrupted_at_start": '
            'len(corrupted), "valid_names_damaged": damaged, "signups_lost_or_changed": lost, '
            '"seed_emails_changed": emails, "seed_created_changed": created, "api_up": up, '
            '"taking_signups": taking}))\nPY',
            settle=10.0,
            observe=10.0,
            reference=(write_file("/tmp/fix.py", MOJI_FIX), "python3 /tmp/fix.py cp1252 5"),
            naive=(write_file("/tmp/fix.py", MOJI_FIX), "python3 /tmp/fix.py latin-1 1"),
            corrupt="id=$(head -n 5 /var/lib/crm-client/acked.log | tail -n 1 | jq .id) && "
            "sqlite3 -cmd '.timeout 5000' /srv/crm/customers.db "
            "\"update customers set name = name || ' Jr' where id = $id\"",
            harm=(
                "failed_requests",
                "valid_names_damaged",
                "lost_customers",
                "emails_changed",
                "created_changed",
                "discarded_writes",
            ),
            acked="acked_customers",
            evidence=("/var/lib/crm-client/acked.log",),
            secrets=("/opt/crm/.seed.py",),
            mem_mib=768,
        ),
        Scenario(
            name="pg-int-to-bigint",
            family="database migration",
            prompt=(
                "Alert from the shop's database monitoring: `orders.id` in the PostgreSQL database "
                "`shop` (`psql -d shop -U shop -h /run/postgresql`) is a 32-bit integer and is "
                "about to run out of values; at the current checkout rate it overflows in a few "
                "minutes, and then every checkout fails. Migrate the order ids to 64-bit so the "
                "shop can keep taking orders. Checkouts (notes in /opt/shop/README.md) go on the "
                "whole time and must not fail, no order may be lost, and the schema's keys and "
                "constraints must stay in place."
            ),
            setup=(
                PG_START,
                BIG_SEED,
                f"mkdir -p /opt/shop /var/lib/shop && cat > /opt/shop/README.md <<'TXT'\n{BIG_README}TXT",
                python_service("/opt/shop/checkout.py", BIG_APP),
                supervised("shop-supervisor", "python3 /opt/shop/checkout.py", "/var/log/shop-checkout.log"),
                "sleep 3",
            ),
            probe=BIG_PROBE,
            check=BIG_CHECK,
            settle=720.0,  # checkouts reach INT_MAX ~10 minutes after setup
            fast_forward=BIG_FAST_FORWARD,
            observe=20.0,  # the checkouts cross INT_MAX ~3 s after the fast-forward
            reference=(BIG_REFERENCE, "python3 /tmp/widen.py"),
            naive=(
                "psql -v ON_ERROR_STOP=1 -d shop -U shop -h /run/postgresql "
                "-c 'alter table orders alter column id type bigint' "
                "-c 'alter table order_items alter column order_id type bigint' "
                "-c 'alter sequence orders_id_seq as bigint'",
            ),
            corrupt="id=$(head -n 5 /var/lib/shop/acked.log | tail -n 1) && psql -v ON_ERROR_STOP=1 "
            '-d shop -U shop -h /run/postgresql -c "delete from order_items where order_id = $id; '
            'delete from orders where id = $id"',
            harm=("failed_requests", "lost_orders", "lost_items", "discarded_writes"),
            acked="acked_orders",
            evidence=("/var/lib/shop/acked.log",),
            mem_mib=1536,
            data_mib=6144,
        ),
    ]
