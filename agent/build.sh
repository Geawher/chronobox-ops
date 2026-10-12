#!/bin/sh
# Build the sandbox image for full-access setups (Claude Code with its own tools).
# The Claude Code binary is mounted at run time, not baked in.
set -e
cd "$(dirname "$0")"
docker build -q -t chronobox-ops-agent:latest .
