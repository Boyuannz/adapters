# LoopsBench → Harbor Adapter

## Overview

This adapter converts LoopsBench tasks into Harbor tasks. The upstream manifest for `loopsbench-tasks-c47e13a.tar.zst` lists **112 upstream tasks**. The adapter selects **104 tasks** after eight exclusions. All 104 have a passing Harbor oracle result across the baseline and follow-up runs. Local execution uses a reviewed task subset.

The draft uses the upstream LoopsBench checkout at `f44331c` and task bundle revision `c47e13a`. The upstream repository is MIT-licensed. [Upstream repository](https://github.com/microsoft/Loopsbench)

## What is LoopsBench?

LoopsBench evaluates coding agents on long-horizon terminal tasks. An agent works in a task-specific workspace where it builds, modifies, or completes software. The upstream harness runs task-specific tests against the resulting workspace and uses the terminal test output to decide whether the task is resolved. [LoopsBench paper](https://arxiv.org/abs/2608.00267)

The source covers real-world project maintenance and coursework in domains such as databases, compilers, operating systems, graphics, and machine learning.

## Adapter Features

- Converts selected tasks from a local input directory, carrying over their instructions and mapping upstream IDs to stable `loopsbench/<task-id>` names.
- Packages the Docker build context, reference solution, and test assets for Harbor. The oracle runs the packaged upstream solution.
- Evaluates tasks with the packaged upstream test runner and a standard-library port of the upstream result parser. Harbor records a binary reward in `/logs/verifier/reward.txt`.

## Generated Task Structure

```text
datasets/loopsbench-reviewed/
└── <task-id>/
    ├── task.toml
    ├── instruction.md
    ├── environment/
    │   └── Dockerfile
    ├── solution/
    │   └── solve.sh
    └── tests/
        ├── test.sh
        └── _loopsbench/
```

The environment, solution, and test directories also contain the assets needed by each task. Tasks that require Compose capability overrides include `environment/docker-compose.yaml`.

## Run Evaluation / Harness

From the adapters repository root, use the reference config after reviewing the generated subset in `datasets/loopsbench-reviewed`. The config runs the oracle agent with Docker and reads only that directory. This is the local oracle workflow for the reviewed subset.

```bash
harbor run -c src/loopsbench/run_loopsbench.yaml
```

Job results are written to `jobs/loopsbench-reviewed/`.

## Usage: Create Task Directories

Prepare a separate input directory containing only the upstream task directories selected for local review. Review each task’s instructions, Docker build context, dependencies, reference solution, and verifier code before including it. The local subset excludes offensive and security-lab tasks. Before running Harbor, review the generated directories too, including their environment settings and bundled scripts.

From the adapters repository root, set `REVIEWED_TASKS_DIR` to the absolute path of that reviewed input directory. The output directory must be new for this example so it contains only tasks from this generation.

```bash
cd src/loopsbench
REVIEWED_TASKS_DIR=/absolute/path/to/reviewed-loopsbench-inputs
test -d "$REVIEWED_TASKS_DIR" &&
  test ! -e ../../datasets/loopsbench-reviewed &&
  uv run loopsbench --tasks-dir "$REVIEWED_TASKS_DIR" \
    --output-dir ../../datasets/loopsbench-reviewed
```

`--tasks-dir` is required. `--task-ids` narrows selection within the reviewed input and accepts upstream or Harbor IDs. `--limit` caps the number of generated tasks, and `--overwrite` replaces existing selected tasks. A directory containing an excluded task is rejected. Use a new output directory when migrating an older dataset. `--bundle` records the source bundle revision. `--cpus`, `--memory-mb`, and `--storage-mb` override the generated resource settings.

## Comparison with Original Benchmark (Parity)

Parity is pending coordination with the Harbor team on the agent, model, reviewed task subset, and run settings. No parity claims are made.

| Agent | Model | Metric | Number of Runs | Dataset Size | Original Benchmark Performance | Harbor Adapter Performance |
| --- | --- | --- | --- | --- | --- | --- |
| Pending | Pending | Resolved rate | — | — | — | — |

Both sides will use the same reviewed tasks and matched settings, with at least two runs per side. The reported scores will use mean ± sample SEM from the raw runs in `parity_experiment.json`, with matching assessed by overlap of the run-score ranges.

Before agent parity, compare the oracle outcomes on the reviewed tasks. Run the Harbor reference config from this repository. From the pinned upstream checkout, set `REVIEWED_TASKS_DIR` to the reviewed source directory and `REVIEWED_UPSTREAM_TASK_ID` to one of its upstream IDs.

```bash
.venv/bin/loopsbench run -p "$REVIEWED_TASKS_DIR" -t "$REVIEWED_UPSTREAM_TASK_ID" \
  -a oracle --docker-image-strategy local-build --output-path runs
```

## Notes & Caveats

- `angr-concolic-execution` and `security-labs` are excluded from task generation. They require vulnerability-trigger generation or attack implementation.
- `django-seg11`, `llvm-alive-verification`, and `typescript-seg01` are excluded because the reference solution fails the same tests in Harbor and the upstream harness.
- `framework-seg04` is excluded after Composer blocked its Laravel requirement on security advisories. `jenkins-seg02` and `jenkins-seg05` are excluded after their Harbor builds failed during Maven archive extraction. All three upstream comparisons ended at the Docker build time bound.
- The adapter removes Dockerfile instructions that copy reference patches into the agent image for upstream tasks `mlir_circuit_ir` and `mlsys_autodiff_pa1`. The reference patches remain in the oracle solution assets. A parity comparison needs the same correction on both sides.
- Harbor snapshots `/workspace` after the agent phase, then restores it in a fresh verifier container from the same image. The archive preserves ignored build outputs, file timestamps, and workspace links. Verifier assets and reward files are created outside the restored workspace. The generated verifier uses public networking because some upstream test scripts install dependencies at verification time.
- The adapter carries over Compose capabilities where needed. Upstream host networking and custom bridge networks are omitted.
- The Dockerfiles for `navidrome-seg02` and `node-seg03` restore the original cyclic symlinks inside the image. This lets Harbor fingerprint each task directory while preserving its in-container layout.
- The `node-seg03` Dockerfile uses the official Debian security archive for its Bullseye security packages after the original mirror began returning HTTP 404. APT signature verification stays enabled.
- The `db-nanodb` image sets a one-second mtime on the packaged `test_sql.props` resource. Maven copies it to the test classpath with this timestamp, and the Harbor oracle passes all 18 tests.
- The `nodebb-seg05` image supplies Mocha 9.1.3 for the verifier's `npx mocha` command. This version supports the task's Node 14 runtime and keeps the workspace's dependencies intact.
- The `perses-program-reduction` image installs `patch` for the upstream oracle script.
- The `spring-course-mgmt-fullstack` image normalizes epoch-zero Maven resource mtimes so the existing build copies the packaged configuration files.
- The two Panda3D images normalize epoch-zero source mtimes because `makepanda` treats those timestamps as missing files. They default to 8 CPUs and limit `makepanda` to 6 threads.
- `compiler-rustlike-rv32` defaults to 16 CPUs. `ns3-network-simulation` and `rdma-ns3-evolution` default to 16 CPUs and 16 GiB. All three complete within their upstream test limits with those allocations. The standard allocation is 4 CPUs and 8 GiB. Explicit `--cpus` and `--memory-mb` values override task defaults.

## Installation / Prerequisites

- Python 3.12 or later and `uv` for the adapter package. Run `uv sync` from `src/loopsbench` to install the locked dependencies.
- A separate Harbor CLI installation with support for the generated task configuration. The development setup uses Harbor `0.26.0`.
- A running Docker engine, adequate disk space for task images and build caches, and a prepared local directory containing the reviewed upstream task subset.

## Troubleshooting

- **Dataset path missing.** Run the reference config from the adapters repository root and confirm the reviewed output exists at `datasets/loopsbench-reviewed`.
- **Symlink recursion.** Regenerate older outputs for `navidrome-seg02` and `node-seg03` in a new directory so Harbor can fingerprint them with the updated Dockerfiles.
- **Build or verification failure.** Check the Harbor job logs for Docker build errors, dependency installation failures, or verifier timeouts. Record any confirmed upstream failure and its eventual disposition before finalizing the adapted task count.

## Citation

The title and author list below follow the upstream `CITATION.cff`.

```bibtex
@article{li2026loopsbench,
  title = {LoopsBench: From Harness Engineering to Loop Engineering in Coding Agent Evaluation},
  author = {Li, Han and Fang, Zhemin and Feng, Rili and Zhao, Yingqi and Liu, Jiaheng and Gao, Pengfei and Ye, He and Lin, Dayi and Lin, Qingwei and Rajmohan, Saravan and Zhang, Dongmei},
  journal = {arXiv preprint arXiv:2608.00267},
  year = {2026},
  url = {https://arxiv.org/abs/2608.00267}
}
```

## Authors & Contributions

LoopsBench was authored by Han Li, Zhemin Fang, Rili Feng, Yingqi Zhao, Jiaheng Liu, Pengfei Gao, He Ye, Dayi Lin, Qingwei Lin, Saravan Rajmohan, and Dongmei Zhang. Boyuan Zheng contributes the Harbor adapter. [Upstream citation metadata](https://github.com/microsoft/Loopsbench/blob/f44331c/CITATION.cff)
