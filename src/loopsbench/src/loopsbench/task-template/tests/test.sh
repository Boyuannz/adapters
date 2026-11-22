#!/bin/bash
# Mirrors LoopsBench's TestRunner (loopsbench/harness/test_runner.py): the agent's
# /workspace arrives here as a Harbor artifact in a fresh copy of the task image,
# the task's tests are at /tests and run-tests.sh is staged at /task-assets.
set -u

mkdir -p /logs/verifier /task-assets
# Stage harness files outside /tests so /tests holds exactly the upstream tests.
mv /tests/_loopsbench /task-assets/_loopsbench
cp /task-assets/_loopsbench/run-tests.sh /task-assets/run-tests.sh
if [ -d /task-assets/_loopsbench/base ]; then
  mv /task-assets/_loopsbench/base /task-assets/base
fi

mkdir -p /workspace
cd /workspace
(git init -q 2>/dev/null; git add -A 2>/dev/null; \
  git -c core.hooksPath=/dev/null -c user.email=b@b -c user.name=b commit -q -m snap --allow-empty >/dev/null 2>&1) || true

export REPO_DIR=/workspace
export TEST_DIR=/tests
export UNIT_TEST_DIR=/tests
export REPO_DIR_OVERRIDE=/workspace
export SEGPIPE_BASE_ROOT=/task-assets/base
{extra_env}
timeout --signal=TERM --kill-after=5s {test_timeout_sec} bash /task-assets/run-tests.sh \
  > /logs/verifier/post-test.txt 2>&1
code=$?
tail -n 60 /logs/verifier/post-test.txt

if command -v python3 >/dev/null 2>&1; then
  python3 /task-assets/_loopsbench/loopsbench_grade.py /logs/verifier/post-test.txt "$code" /logs/verifier
else
  echo "python3 is not available in the verifier image; cannot grade" >&2
  echo 0 > /logs/verifier/reward.txt
fi
exit 0
