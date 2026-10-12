# Sandbox for "full access" Claude Code runs: the agent gets a normal Linux userland,
# internet access and its own empty working directory, but none of the benchmark's
# files (scenarios, checkers, VM images). The Claude Code binary is mounted at run time.
FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates git curl jq less procps python3 python3-venv ripgrep \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -u 1000 agent
USER agent
WORKDIR /work
