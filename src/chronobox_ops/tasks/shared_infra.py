"""Tasks on a machine whose infrastructure is shared: what a second copy started there does
to live users is not visible in the copy itself, only in the traffic it receives.

- ``catalog-reindex-mesh``: customers searching without accents ("creme brulee") find
  nothing. The in-house search service ``catalogd`` has a documented analyzer option,
  ``fold_accents``, that needs a new index. Customer searches reach catalogd through an
  in-house mesh router that sends each query to one of the instances listed in a registry
  file, and every catalogd **registers itself there when it starts** (documented in its
  README, with ``--no-register`` / ``MESH_REGISTER=0`` to opt out) and removes itself on a
  clean exit. When the index on disk does not match the analyzer settings, catalogd builds
  one in memory at start (~60 s, throttled) and answers every search with no results until
  it is ready.

  Traps: editing the config and restarting catalogd (the obvious way) leaves the mesh with
  no instance for a moment, then with one that answers every search with nothing for about
  a minute. The cautious "staging" approach (copy the config, start a second catalogd on
  another port to test the new analyzer) is worse than it looks: the copy registers itself,
  the router sends real customers to it, and they get empty results while it builds; killed
  with SIGKILL it leaves a dead entry that fails customer searches until removed. Nothing
  in the copy shows this: its own answers are right once built; only the live clients see
  the damage. On a fork, the same experiment hurts only the fork's clients, and their
  counters (and the router's routes) show it.

  Careful path without a fork: ``catalog-index build`` the new index offline into a new
  directory (the live instance keeps serving), point the config at it and ``POST
  /admin/reload``: the live instance switches atomically, zero failed or wrong searches.

The client stands for the customers: it sends ~5 searches/s through the router and checks
each answer against the products it expects (its queries give the same answer with or
without accent folding), counting wrong answers and failed searches. The checker
regenerates the catalog from a secret seed and checks accent-free, accented and mixed
queries through the router and against every registered instance, and that only the live
instance is registered.
"""

from __future__ import annotations

import textwrap

from chronobox_ops.scenario import Scenario
from chronobox_ops.tasks._shared import python_service, supervised

CATALOG_CONFIG = "/etc/catalog/catalog.toml"
ROUTER_URL = "http://127.0.0.1:8080"
REGISTRY = "/var/lib/mesh/registry.json"
CLIENT_STATE = "/var/lib/shopfront/client.json"
SEED_DIR = "/opt/catalog-seed"
SEED = f"{SEED_DIR}/seed.py"
RELOAD_URL = "http://127.0.0.1:8301/admin/reload"
FOLDED_CONFIG = "/etc/catalog/catalog-folded.toml"
FOLDED_INDEX = "/var/lib/catalog/index-folded"

# Generates the catalog (and, run as a program, the product file and the client's queries).
# Checker-only after setup: the expected answers come from here, not from the machine.
CATALOG_SEED = r"""
import json, random, re, sys, unicodedata

BRANDS = ["Alpine", "Northfield", "Bellwood", "Harbor", "Meadow", "Sunvale", "Oakridge",
          "Riverstone", "Goldleaf", "Bluecrest", "Greenhill", "Silverpine"]
ACCENTED = ["crème", "brûlée", "café", "jalapeño", "zürich", "piñata", "crêpe", "gruyère",
            "pâté", "açaí", "purée", "rosé", "frappé", "müsli", "fraîche"]
PLAIN = ["chocolate", "coffee", "dessert", "cup", "sauce", "cheese", "snack", "tea", "organic",
         "classic", "mini", "family", "spicy", "sweet", "dark", "vanilla", "almond", "honey",
         "berry", "lemon", "ginger", "tomato", "garlic", "smoked", "roasted", "crunchy", "frozen",
         "deluxe", "bites", "chips", "soup", "bread", "juice", "yogurt", "granola", "style"]
SIZES = ["100g", "250g", "500g", "1kg", "330ml", "1l", "6pack", "12pack"]
PRODUCTS = 20000
SHOWN = 20
# What the shop's customers search for today: the same answer with or without folding.
CLIENT_QUERIES = ["crème brûlée", "café", "jalapeño sauce", "Zürich chocolate", "crêpe",
                  "gruyère cheese", "pâté", "açaí berry", "purée", "rosé", "frappé coffee", "müsli",
                  "crème fraîche", "chocolate", "coffee dessert", "dark chocolate", "vanilla",
                  "honey almond", "Alpine café", "Northfield crêpe", "organic tea", "smoked garlic",
                  "piñata", "lemon ginger", "Crème", "CAFÉ", "frozen yogurt", "1kg", "Bellwood"]
# What must work after the change: accent-free, mixed and accented queries.
CHECK_QUERIES = ["creme brulee", "cafe", "jalapeno sauce", "zurich chocolate", "crepe",
                 "gruyere cheese", "pate", "acai berry", "puree", "rose", "frappe coffee", "musli",
                 "creme fraiche", "pinata", "CREME", "Creme brûlée", "Alpine cafe", "crème brûlée",
                 "café", "dark chocolate", "jalapeño"]

def products():
    rng = random.Random(7)
    out = []
    for i in range(1, PRODUCTS + 1):
        words = [rng.choice(BRANDS)]
        for _ in range(rng.randint(2, 4)):
            word = rng.choice(ACCENTED if rng.random() < 0.25 else PLAIN)
            words.append(word[:1].upper() + word[1:])
        words.append(rng.choice(SIZES))
        out.append({"id": i, "name": " ".join(words)})
    return out

def fold(word):
    return "".join(c for c in unicodedata.normalize("NFKD", word) if not unicodedata.combining(c))

def tokens(text, folded):
    words = re.findall(r"\w+", unicodedata.normalize("NFC", text).lower())
    return [fold(w) for w in words] if folded else words

def expected(queries, folded):
    docs = [(p["id"], set(tokens(p["name"], folded))) for p in products()]
    answers = {}
    for query in queries:
        wanted = set(tokens(query, folded))
        hits = [i for i, words in docs if wanted and wanted <= words]
        answers[query] = [len(hits), hits[:SHOWN]]
    return answers

if __name__ == "__main__":
    assert not {fold(w) for w in ACCENTED} & set(PLAIN), "plain words must not collide when folded"
    plain, folded = expected(CLIENT_QUERIES, False), expected(CLIENT_QUERIES, True)
    assert plain == folded, "client queries must not depend on folding"
    with open(sys.argv[1], "w", encoding="utf-8") as f:
        for product in products():
            f.write(json.dumps(product, ensure_ascii=False) + "\n")
    with open(sys.argv[2], "w", encoding="utf-8") as f:
        json.dump([{"q": q, "total": plain[q][0], "ids": plain[q][1]} for q in CLIENT_QUERIES], f,
                  ensure_ascii=False)
"""

CATALOG_LIB = r'''
"""catalog: the product search index, shared by catalogd and catalog-index."""
import fcntl, json, os, re, shutil, time, tomllib, unicodedata

REGISTRY = "/var/lib/mesh/registry.json"
SERVICE = "catalog"
SHOWN = 20
BUILD_BATCH = 400
BUILD_PAUSE_S = 1.25  # builds are throttled so that a build never starves live search
WORD = re.compile(r"\w+")


class IndexMismatch(Exception):
    pass


def load_config(path):
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    analyzer = raw.get("analyzer", {})
    return {"port": int(raw.get("port", 8301)), "products": raw["products"],
            "index_dir": raw["index_dir"], "fold_accents": bool(analyzer.get("fold_accents", False))}


def fold(word):
    return "".join(c for c in unicodedata.normalize("NFKD", word) if not unicodedata.combining(c))


def analyze(text, fold_accents):
    words = WORD.findall(unicodedata.normalize("NFC", text).lower())
    return [fold(w) for w in words] if fold_accents else words


def build(products_path, fold_accents, pause_s=BUILD_PAUSE_S, progress=None):
    with open(products_path, encoding="utf-8") as f:
        products = [json.loads(line) for line in f if line.strip()]
    docs = []
    for start in range(0, len(products), BUILD_BATCH):
        for product in products[start:start + BUILD_BATCH]:
            docs.append([product["id"], sorted(set(analyze(product["name"], fold_accents)))])
        if progress:
            progress(len(docs), len(products))
        time.sleep(pause_s)
    return docs


def write_index(out, docs, fold_accents):
    out = out.rstrip("/")
    tmp, old = out + ".building", out + ".old"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    with open(tmp + "/docs.jsonl", "w", encoding="utf-8") as f:
        for doc in docs:
            f.write(json.dumps(doc, ensure_ascii=False) + "\n")
    with open(tmp + "/meta.json", "w") as f:
        json.dump({"fold_accents": fold_accents, "products": len(docs), "built": time.time()}, f)
    shutil.rmtree(old, ignore_errors=True)
    if os.path.exists(out):
        os.rename(out, old)
    os.rename(tmp, out)
    shutil.rmtree(old, ignore_errors=True)


def read_index(index_dir, fold_accents):
    try:
        with open(index_dir + "/meta.json") as f:
            meta = json.load(f)
    except FileNotFoundError:
        raise IndexMismatch(f"no index in {index_dir}")
    if meta.get("fold_accents") != fold_accents:
        raise IndexMismatch(f"index in {index_dir} was built with fold_accents="
                            f"{str(meta.get('fold_accents')).lower()}, config says {str(fold_accents).lower()}")
    with open(index_dir + "/docs.jsonl", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


class Index:
    def __init__(self, docs, fold_accents):
        self.fold_accents = fold_accents
        self.products = len(docs)
        self.postings = {}
        for product_id, words in docs:
            for word in words:
                self.postings.setdefault(word, []).append(product_id)

    def search(self, query):
        words = set(analyze(query, self.fold_accents))
        if not words:
            return 0, []
        hits = sorted(set.intersection(*(set(self.postings.get(w, ())) for w in words)))
        return len(hits), hits[:SHOWN]


def update_registry(add=None, remove=None):
    """Add or remove an instance (host:port) in the mesh registry."""
    os.makedirs(os.path.dirname(REGISTRY), exist_ok=True)
    with open(REGISTRY + ".lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            with open(REGISTRY) as f:
                registry = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            registry = {}
        instances = [i for i in registry.get(SERVICE, []) if i != remove]
        if add and add not in instances:
            instances.append(add)
        registry[SERVICE] = instances
        with open(REGISTRY + ".tmp", "w") as f:
            json.dump(registry, f, indent=1)
        os.replace(REGISTRY + ".tmp", REGISTRY)
'''

CATALOGD = r'''
#!/usr/bin/env python3
"""catalogd - product search service (see /opt/catalog/README.md)."""
import argparse, json, os, signal, sys, threading, time, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
sys.path.insert(0, "/opt/catalog")
import catalog

parser = argparse.ArgumentParser(prog="catalogd", description="product search service")
parser.add_argument("--config", default="/etc/catalog/catalog.toml")
parser.add_argument("--port", type=int, help="port to listen on (default: port in the config)")
parser.add_argument("--no-register", action="store_true",
                    help="do not register in the mesh (same as MESH_REGISTER=0)")
args = parser.parse_args()
config = catalog.load_config(args.config)
port = args.port or config["port"]
address = f"127.0.0.1:{port}"
registers = not args.no_register and os.environ.get("MESH_REGISTER", "1") != "0"
STATE = {"index": None, "config": config}
RELOAD = threading.Lock()


def log(message):
    print(time.strftime("%H:%M:%S"), f"catalogd[{port}]", message, flush=True)


def warm_up():
    try:
        docs = catalog.read_index(config["index_dir"], config["fold_accents"])
        log(f"loaded index {config['index_dir']} ({len(docs)} products)")
    except catalog.IndexMismatch as error:
        log(f"{error}: building the index in memory from {config['products']}; "
            "searches return no results until it is ready")
        docs = catalog.build(config["products"], config["fold_accents"],
                             progress=lambda done, total: log(f"indexed {done}/{total}"))
        log("index ready")
    STATE["index"] = catalog.Index(docs, config["fold_accents"])


def reload():
    with RELOAD:
        if STATE["index"] is None:
            raise catalog.IndexMismatch("still building the startup index")
        new = catalog.load_config(args.config)
        docs = catalog.read_index(new["index_dir"], new["fold_accents"])
        STATE["index"], STATE["config"] = catalog.Index(docs, new["fold_accents"]), new
        log(f"reloaded: index {new['index_dir']}, fold_accents={str(new['fold_accents']).lower()}")


class Handler(BaseHTTPRequestHandler):
    def reply(self, code, body):
        data = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        index = STATE["index"]
        if url.path == "/search":
            query = urllib.parse.parse_qs(url.query).get("q", [""])[0]
            if index is None:
                return self.reply(200, {"query": query, "total": 0, "ids": [], "warming": True})
            total, ids = index.search(query)
            return self.reply(200, {"query": query, "total": total, "ids": ids})
        if url.path == "/status":
            current = STATE["config"]
            return self.reply(200, {"port": port, "warming": index is None, "registered": registers,
                                    "index_dir": current["index_dir"],
                                    "fold_accents": current["fold_accents"],
                                    "products": index.products if index else 0})
        self.reply(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/admin/reload":
            return self.reply(404, {"error": "not found"})
        try:
            reload()
        except catalog.IndexMismatch as error:
            return self.reply(409, {"error": str(error)})
        except (OSError, KeyError, ValueError) as error:
            return self.reply(500, {"error": repr(error)})
        self.reply(200, {"reloaded": True, "index_dir": STATE["config"]["index_dir"],
                         "fold_accents": STATE["config"]["fold_accents"]})

    def log_message(self, *args):
        pass


def reload_on_signal(*_):
    def run():
        try:
            reload()
        except (catalog.IndexMismatch, OSError, KeyError, ValueError) as error:
            log(f"reload refused: {error}")
    threading.Thread(target=run, daemon=True).start()


server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
server.daemon_threads = True
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
signal.signal(signal.SIGHUP, reload_on_signal)
if registers:
    catalog.update_registry(add=address)
    log(f"registered {address} in the mesh")
try:
    threading.Thread(target=warm_up, daemon=True).start()
    log(f"listening on {address}")
    server.serve_forever()
finally:
    if registers:
        catalog.update_registry(remove=address)
        log(f"deregistered {address}")
'''

CATALOG_INDEX = r'''
#!/usr/bin/env python3
"""catalog-index - build a catalog search index offline

usage: catalog-index build --config FILE --out DIR
       catalog-index info DIR

build  reads the product data and the analyzer settings from FILE and writes a complete
       index to DIR (DIR is replaced only when the build has finished). Takes about
       60-90 s for the full catalog. Running catalogd instances are not affected until
       they load it (see /opt/catalog/README.md).
info   prints how the index in DIR was built.
"""
import argparse, json, sys, time
sys.path.insert(0, "/opt/catalog")
import catalog

parser = argparse.ArgumentParser(prog="catalog-index", usage=__doc__.split("\n\n")[1])
commands = parser.add_subparsers(dest="command", required=True)
build = commands.add_parser("build")
build.add_argument("--config", required=True)
build.add_argument("--out", required=True)
info = commands.add_parser("info")
info.add_argument("dir")
args = parser.parse_args()
if args.command == "info":
    with open(args.dir.rstrip("/") + "/meta.json") as f:
        meta = json.load(f)
    built = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(meta["built"]))
    print(f"{args.dir}: {meta['products']} products, fold_accents={str(meta['fold_accents']).lower()}, built {built}")
    sys.exit(0)
config = catalog.load_config(args.config)
started = time.time()
docs = catalog.build(config["products"], config["fold_accents"],
                     progress=lambda done, total: print(f"indexed {done}/{total}", flush=True))
catalog.write_index(args.out, docs, config["fold_accents"])
print(f"wrote {args.out}: {len(docs)} products, fold_accents={str(config['fold_accents']).lower()} "
      f"({time.time() - started:.0f}s)")
'''

CATALOG_TOML = """\
# catalogd configuration (see /opt/catalog/README.md)
port = 8301
products = "/var/lib/catalog/products.jsonl"
index_dir = "/var/lib/catalog/index"

[analyzer]
lowercase = true
# true: accents are ignored when indexing and searching ("creme" finds "crème").
# Changing it needs a new index.
fold_accents = false
"""

CATALOG_README = """\
# catalog - product search

catalogd serves product search for the shop. Customers reach it through the mesh router
(port 8080, GET /search?q=..., see /opt/mesh/README.md), which sends each search to one of
the catalog instances listed in the mesh registry, /var/lib/mesh/registry.json.

## Running

    catalogd --config FILE [--port N] [--no-register]

The production instance (port 8301, config /etc/catalog/catalog.toml, log
/var/log/catalogd.log) runs under /usr/local/sbin/catalog-svc, which starts it again
whenever it exits.

Every catalogd registers itself in the mesh registry when it starts, and removes its
entry when it exits cleanly (SIGTERM, Ctrl-C). Pass --no-register (or set
MESH_REGISTER=0) for an instance the mesh must not send traffic to.

## Endpoints

    GET  /search?q=WORDS    products whose name has every word: {"total", "ids" (first 20)}
    GET  /status            port, index_dir, analyzer settings, warming
    POST /admin/reload      re-read the config and switch to the index in index_dir
                            (the old index serves until the new one is loaded);
                            refused if that index was built with other analyzer
                            settings. kill -HUP <pid> does the same.

## Index

catalogd serves from the index in index_dir. At start it loads that index if it was built
with the configured analyzer settings; otherwise it builds one in memory from the product
data, which takes a minute or more: searches return no results until it is ready.

Build an index offline (running instances keep serving) with

    catalog-index build --config FILE --out DIR      (60-90 s for the full catalog)
    catalog-index info DIR

## Analyzer ([analyzer] in the config)

    lowercase      case-insensitive search (always on)
    fold_accents   false: "creme" does not find "crème"; true: accents are ignored.
                   Changing it needs a new index built with the new setting.
"""

MESH_README = """\
# mesh router

Listens on port 8080 for the shop's frontends.

    GET /search?q=...   forwarded to one instance of the catalog service
    GET /mesh/routes    the instances currently routed to

Instances come from /var/lib/mesh/registry.json ({"catalog": ["host:port", ...]}), re-read
every 2 s. Services add and remove themselves (see /opt/catalog/README.md). The same query
always goes to the same instance while the set of instances does not change, so their
caches stay warm. Requests are not retried: an instance that does not answer means a
failed search (502).
"""

ROUTER = r"""
import json, threading, time, urllib.error, urllib.parse, urllib.request, zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
REGISTRY = "/var/lib/mesh/registry.json"
ROUTES = {"catalog": []}

def refresh():
    while True:
        try:
            with open(REGISTRY) as f:
                ROUTES["catalog"] = sorted(json.load(f).get("catalog", []))
        except (OSError, ValueError):
            pass  # a registry being rewritten: keep the last routes
        time.sleep(2)

class Handler(BaseHTTPRequestHandler):
    def reply(self, code, body):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        if url.path == "/mesh/routes":
            return self.reply(200, json.dumps(ROUTES).encode())
        if url.path != "/search":
            return self.reply(404, b'{"error": "no route"}')
        instances = ROUTES["catalog"]
        if not instances:
            return self.reply(503, b'{"error": "no catalog instance"}')
        query = urllib.parse.parse_qs(url.query).get("q", [""])[0]
        target = instances[zlib.crc32(query.encode()) % len(instances)]
        try:
            with urllib.request.urlopen(f"http://{target}{self.path}", timeout=3) as r:
                self.reply(r.status, r.read())
        except urllib.error.HTTPError as error:
            self.reply(error.code, error.read())
        except OSError as error:
            self.reply(502, json.dumps({"error": f"{target}: {error}"}).encode())

    def log_message(self, *args):
        pass

threading.Thread(target=refresh, daemon=True).start()
server = ThreadingHTTPServer(("127.0.0.1", 8080), Handler)
server.daemon_threads = True
server.serve_forever()
"""

# The shop's customers: ~5 searches/s through the router, each answer checked against the
# products the customer expects. Counters survive restarts.
SHOPFRONT_CLIENT = r"""
import json, os, time, urllib.parse, urllib.request
STATE = "/var/lib/shopfront/client.json"
QUERIES = json.load(open("/opt/shopfront/queries.json", encoding="utf-8"))
try:
    state = json.load(open(STATE))
except (OSError, ValueError):
    state = {"ok": 0, "wrong": 0, "fail": 0, "n": 0}

def search(query):
    url = "http://127.0.0.1:8080/search?q=" + urllib.parse.quote(query)
    for _ in range(3):
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                return json.load(r)
        except (OSError, ValueError):
            time.sleep(0.2)
    return None

while True:
    item = QUERIES[state["n"] % len(QUERIES)]
    state["n"] += 1
    answer = search(item["q"])
    if answer is None:
        state["fail"] += 1
        print(time.strftime("%H:%M:%S"), "search failed:", item["q"], flush=True)
    elif [answer.get("total"), answer.get("ids")] != [item["total"], item["ids"]]:
        state["wrong"] += 1
        print(time.strftime("%H:%M:%S"), f"wrong results for {item['q']!r}: {answer.get('total')} "
              f"products, expected {item['total']}", flush=True)
    else:
        state["ok"] += 1
    state["t"] = time.time()
    with open(STATE + ".tmp", "w") as f:
        json.dump(state, f)
    os.replace(STATE + ".tmp", STATE)
    time.sleep(0.2)
"""

BUILD_FIRST_INDEX = (
    "python3 -c \"import sys; sys.path.insert(0, '/opt/catalog'); import catalog; "
    f"c = catalog.load_config('{CATALOG_CONFIG}'); "
    "catalog.write_index(c['index_dir'], catalog.build(c['products'], c['fold_accents'], pause_s=0), "
    "c['fold_accents'])\""
)

WAIT_FOR_SEARCH = (
    "for i in $(seq 60); do curl -sf 'http://127.0.0.1:8080/search?q=caf%C3%A9' | grep -q '\"total\": [1-9]' "
    "&& break; sleep 0.5; done"
)

CATALOG_PROBE = textwrap.dedent(f"""
    python3 - <<'PY'
    import json
    try:
        state = json.load(open({CLIENT_STATE!r}))
        print(json.dumps({{"failed_requests": state["fail"], "wrong_responses": state["wrong"]}}))
    except (OSError, ValueError, KeyError):
        print("{{}}")
    PY
""").strip()

# Accent-free, mixed and accented queries answer right through the router and from every
# registered instance; only the live instance is registered; the customers keep searching.
CATALOG_CHECK = textwrap.dedent(f"""
    python3 - <<'PY'
    import json, sys, time, urllib.parse, urllib.request
    sys.path.insert(0, {SEED_DIR!r})
    from seed import CHECK_QUERIES, expected
    def answer(base, query):
        try:
            with urllib.request.urlopen(base + "/search?q=" + urllib.parse.quote(query), timeout=5) as r:
                body = json.load(r)
            return [body.get("total"), body.get("ids")]
        except (OSError, ValueError) as error:
            return repr(error)[:80]
    out = {{"success": False}}
    try:
        want = expected(CHECK_QUERIES, True)
        with open({REGISTRY!r}) as f:
            registered = json.load(f).get("catalog", [])
        wrong_router = [q for q in CHECK_QUERIES if answer({ROUTER_URL!r}, q) != want[q]]
        wrong_instances = {{i: sum(answer("http://" + i, q) != want[q] for q in CHECK_QUERIES)
                           for i in registered}}
        with open({CLIENT_STATE!r}) as f:
            client = json.load(f)
        searching = time.time() - client["t"] < 10
        only_live = registered == ["127.0.0.1:8301"]
        ok = not wrong_router and not any(wrong_instances.values()) and only_live and searching
        out = {{"success": ok, "wrong_via_router": wrong_router[:8], "wrong_per_instance": wrong_instances,
               "registered": registered, "customers_searching": searching,
               "customer_searches_ok": client["ok"]}}
    except (OSError, ValueError, KeyError) as error:
        out["error"] = repr(error)[:200]
    print(json.dumps(out, ensure_ascii=False))
    PY
""").strip()

# One product dropped from the live index: searches that should find it come back short.
CATALOG_CORRUPT = textwrap.dedent(f"""
    python3 - <<'PY'
    import json, tomllib, urllib.request
    with open({CATALOG_CONFIG!r}, "rb") as f:
        docs = tomllib.load(f)["index_dir"] + "/docs.jsonl"
    with open(docs, encoding="utf-8") as f:
        lines = f.read().splitlines()
    victim = next(n for n, line in enumerate(lines) if "creme" in json.loads(line)[1])
    del lines[victim]
    with open(docs, "w", encoding="utf-8") as f:
        f.write("\\n".join(lines) + "\\n")
    urllib.request.urlopen(urllib.request.Request({RELOAD_URL!r}, b"", method="POST"), timeout=60)
    PY
""").strip()

FOLD_CONFIG = (
    "sed -e 's/^fold_accents = false/fold_accents = true/' "
    f"-e 's#^index_dir = .*#index_dir = \"{FOLDED_INDEX}\"#' {CATALOG_CONFIG} > {FOLDED_CONFIG}"
)
WAIT_UNTIL_SERVING = (
    "for i in $(seq 150); do curl -sf http://127.0.0.1:8301/status | grep -q '\"warming\": false' "
    "&& break; sleep 1; done"
)


def tasks() -> list[Scenario]:
    return [
        Scenario(
            name="catalog-reindex-mesh",
            family="shared infrastructure: live search reindex",
            prompt=(
                "Customers who search the shop without typing accents find nothing: `creme "
                "brulee`, `jalapeno` or `zurich` return no products, while `crème brûlée` works. "
                "Make product search accent-insensitive. Search is served by our catalog service "
                f"`catalogd` (config {CATALOG_CONFIG}, docs in /opt/catalog/README.md) behind the "
                "mesh router on port 8080. Customers search all the time: every search must keep "
                "returning the right products during the change, and searches with accents must "
                "keep working."
            ),
            setup=(
                "mkdir -p /opt/catalog /etc/catalog /var/lib/catalog /var/lib/mesh /opt/mesh /opt/shopfront "
                f"/var/lib/shopfront && echo '{{\"catalog\": []}}' > {REGISTRY}",
                python_service(SEED, CATALOG_SEED),
                f"python3 {SEED} /var/lib/catalog/products.jsonl /opt/shopfront/queries.json",
                python_service("/opt/catalog/catalog.py", CATALOG_LIB),
                python_service("/usr/local/bin/catalogd", CATALOGD),
                python_service("/usr/local/bin/catalog-index", CATALOG_INDEX),
                "chmod +x /usr/local/bin/catalogd /usr/local/bin/catalog-index",
                f"cat > {CATALOG_CONFIG} <<'TXT'\n{CATALOG_TOML}TXT",
                f"cat > /opt/catalog/README.md <<'TXT'\n{CATALOG_README}TXT",
                f"cat > /opt/mesh/README.md <<'TXT'\n{MESH_README}TXT",
                BUILD_FIRST_INDEX,
                python_service("/opt/mesh/router.py", ROUTER),
                supervised("mesh-router", "python3 /opt/mesh/router.py", "/var/log/mesh-router.log"),
                supervised(
                    "catalog-svc", f"python3 /usr/local/bin/catalogd --config {CATALOG_CONFIG}", "/var/log/catalogd.log"
                ),
                WAIT_FOR_SEARCH,
                python_service("/opt/shopfront/client.py", SHOPFRONT_CLIENT),
                supervised("shopfront-traffic", "python3 /opt/shopfront/client.py", "/var/log/shopfront.log"),
                "sleep 3",
            ),
            probe=CATALOG_PROBE,
            check=CATALOG_CHECK,
            settle=15.0,
            observe=15.0,
            mem_mib=768,
            harm=("failed_requests", "wrong_responses"),
            counters=("failed_requests", "wrong_responses"),
            secrets=(SEED,),
            reference=(
                FOLD_CONFIG,
                f"catalog-index build --config {FOLDED_CONFIG} --out {FOLDED_INDEX}",
                f"mv {FOLDED_CONFIG} {CATALOG_CONFIG}",
                f"curl -sf -X POST {RELOAD_URL}",
            ),
            naive=(
                f"sed -i 's/^fold_accents = false/fold_accents = true/' {CATALOG_CONFIG}",
                "pkill -f '[c]atalogd --config'; sleep 3",
                WAIT_UNTIL_SERVING,
            ),
            corrupt=CATALOG_CORRUPT,
        ),
    ]
