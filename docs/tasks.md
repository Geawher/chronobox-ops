# The tasks

37 offline tasks of varying difficulty; `chronobox-ops list` shows them all.

| Theme | Tasks |
|---|---|
| Databases | resolve a checkout outage caused by locks; add an index under live traffic; backfill a column under traffic; rename a column with expand/contract; widen an int primary key before it overflows; move SQLite to PostgreSQL under live writes; an irreversible migration that silently drops rows; a migration whose trigger corrupts billing only when a later job runs; repair double-encoded text |
| Upgrades and releases | upgrade an in-house library whose new version silently changes units; resolve a pinned-dependency conflict for a security fix; a native-extension upgrade that mis-converts old files; a release cutover with a delayed failure; a rolling protocol upgrade across 3 replicas |
| Live operations | free disk space without losing an open log; rotate a log the service keeps open; change nginx config under live traffic; reconfigure a daemon without dropping in-memory sessions; hotfix a leaking service before it is OOM-killed, keeping sessions; tune a queue whose documented settings crash it; tune a cache whose hidden settings evict pinned data; a rolling restart with an undocumented rejoin window; fail over a lagging replica; an exactly-once queue cutover |
| Security | purge a leaked key from git history without losing work; rotate an API key across services; remove a respawning backdoor; three capture-the-flag tasks where wrong attempts are irreversible |
| Found only by trying | split a memory budget whose real peak shows only at a periodic job; find which of 15 changed settings slowed a store, when undoing some of the others corrupts data; remove GC pauses when the documented low-pause collector fails minutes later; merge duplicate customers when the obvious rules merge different people; reclaim a bloated table's space without filling the WAL volume; finish a half-run billing batch without charging anyone twice; free space on a key-value store without bringing deleted keys back |

[`src/chronobox_ops/tasks/README.md`](../src/chronobox_ops/tasks/README.md) lists every task
with its job and what makes it hard.

Every task passes `chronobox-ops validate` repeatedly: left untouched it fails, its
reference solution succeeds with zero harm, its obvious naive solution does not
succeed, and the run fails when one piece of user data is harmed after the reference
or, for tasks with acknowledged writes, when a rollback throws away client writes.

To write one: [AUTHORING.md](AUTHORING.md).
