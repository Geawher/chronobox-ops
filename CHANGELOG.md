# Changelog

## 0.2.0 (2026-10)

### Results
- Six-seed follow-up: Sonnet 5.5 with chronobox against Opus 4.8 alone ([#4]).
- Hand-off mode results: alone live, chronobox live and chronobox hand-off, normal Claude Code
  everywhere ([#6]).
- Every published file rebuilds from the published runs, checked by a test; the 90 hand-off runs
  are published; the commits the runs used are tagged ([#9]).

### Benchmark
- Hand-off mode (`chronobox-ops handoff`): the agent works on a staging copy and hands over a
  script, graded on fresh productions: an exact copy, a drifted one, and a drifted one with the
  script killed halfway ([#5], [#6]).
- `report --skip-tasks`; `handoff validate` exits 1 on an invalid task; `handoff run` resumes by
  default (`--force` reruns), like `run` ([#9]).
- Two task checks fixed ([#3]).

### Docs
- The README: results first, how a task works, a quick start; the details in [docs/](docs/README.md)
  (running, tasks, grading, method and limitations); CONTRIBUTING.md, templates, and this file
  ([#11], [#12]).

### CI
- Tests run against chronobox's latest `main` ([#8]).

## 0.1.0 (2026-10)

First public version: 37 tasks, scoring of success and harm, the live harness with six setups,
and results for five Claude models ([#1], [#2]).

[#1]: https://github.com/Geawher/chronobox-ops/pull/1
[#2]: https://github.com/Geawher/chronobox-ops/pull/2
[#3]: https://github.com/Geawher/chronobox-ops/pull/3
[#4]: https://github.com/Geawher/chronobox-ops/pull/4
[#5]: https://github.com/Geawher/chronobox-ops/pull/5
[#6]: https://github.com/Geawher/chronobox-ops/pull/6
[#8]: https://github.com/Geawher/chronobox-ops/pull/8
[#9]: https://github.com/Geawher/chronobox-ops/pull/9
[#11]: https://github.com/Geawher/chronobox-ops/pull/11
[#12]: https://github.com/Geawher/chronobox-ops/pull/12
