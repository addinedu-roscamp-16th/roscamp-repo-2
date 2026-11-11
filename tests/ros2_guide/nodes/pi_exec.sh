#!/usr/bin/env bash
# Usage: pi_exec.sh [KEY=VAL ...] -- command [args ...]
# Sources ROS, exports KEY=VAL pairs, prints "PID <n>", then execs the command.
# Run on the Raspberry Pi (home-relative paths, no `set -u`: ROS setup scripts need it off).
# shellcheck disable=SC1091
source /opt/ros/jazzy/setup.bash
while [ "$#" -gt 0 ] && [ "$1" != "--" ]; do
  export "$1"
  shift
done
[ "$1" = "--" ] && shift
echo "PID $$"
exec "$@"
