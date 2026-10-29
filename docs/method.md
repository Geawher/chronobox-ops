# Method and limitations

What the results can and cannot tell you. The numbers themselves, with confidence intervals and
per-task tables, are in [results/](../results/README.md).

- **Our tasks, built around hazards like these.** The tasks were written by chronobox's authors,
  and chronobox is designed for exactly this kind of trap. Every task passes `validate` (its
  reference succeeds with zero harm, its naive fix fails, harm to one piece of data is caught), but
  tasks written by other people are the best test of the benchmark itself.
- **The tasks are public.** The repository, checks included, has been public since 2026-10-12, and
  the leaderboard ran on 2026-10-10/13. The agents had internet access in their sandbox; none of
  the 1,081 runs used web search or fetch (each run's tool counts are in `results.json`). The
  checkers never reach the agent's machine or container. Future models may have seen these tasks
  in training: compare them with care.
- **Small samples.** 3 seeds per task (6 for the follow-up), one host, one week. The intervals are
  wide; where we say "outside chance", the 95% bootstrap interval excludes zero.
- **One agent, one model family.** Only Claude models, only through Claude Code.
- **The CTF tasks measure safeguards.** On them, Opus 5.5 and Sonnet 5.5 stop with a safeguard
  refusal, so they are reported apart. The harness disables Claude Code's silent fallback to
  another model, so every run is the named model's own work.
- **The agent is root on the machine.** Checker data never touches its filesystem and tampering
  with client logs is detected, but a determined agent could still try to fool a checker. Runs
  flagged as tampered fail, and every transcript is kept for audit.
- **Linux + KVM only** (the machines are Firecracker microVMs).
