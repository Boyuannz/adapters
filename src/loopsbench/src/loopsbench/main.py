from __future__ import annotations

import argparse
import logging
from pathlib import Path

from .adapter import DEFAULT_CPUS, DEFAULT_MEMORY_MB, LoopsBenchAdapter, Resources

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

DEFAULT_OUTPUT_DIR = Path("datasets/loopsbench")


def main() -> None:
    p = argparse.ArgumentParser(description="Generate Harbor tasks from LoopsBench")
    p.add_argument(
        "--tasks-dir",
        type=Path,
        required=True,
        help="Extracted LoopsBench task bundle (the tasks/ directory from "
        "huggingface.co/datasets/LoopsBench/LoopsBench)",
    )
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    p.add_argument("--task-ids", nargs="+", default=None,
                   help="Upstream ids (task_foo) or Harbor ids (foo)")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--overwrite", action="store_true")
    p.add_argument("--bundle", default="c47e13a",
                   help="Upstream bundle revision recorded in task metadata")
    p.add_argument("--cpus", type=int,
                   help=f"Override per-task CPU allocation (normally {DEFAULT_CPUS})")
    p.add_argument("--memory-mb", type=int,
                   help=f"Override per-task memory in MiB (normally {DEFAULT_MEMORY_MB})")
    p.add_argument("--storage-mb", type=int, default=Resources.storage_mb)
    args = p.parse_args()

    written = LoopsBenchAdapter(
        tasks_dir=args.tasks_dir,
        output_dir=args.output_dir,
        bundle=args.bundle,
        task_ids=args.task_ids,
        limit=args.limit,
        overwrite=args.overwrite,
        resources=Resources(cpus=args.cpus, memory_mb=args.memory_mb, storage_mb=args.storage_mb),
    ).run()
    logging.info("Generated %d task(s) in %s", len(written), args.output_dir)


if __name__ == "__main__":
    main()
