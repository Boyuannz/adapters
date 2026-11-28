"""Prepare paired jobs for one agreed parity run without launching either harness."""

import argparse
from pathlib import Path
import re
import shutil
import tomllib
from urllib.parse import urlsplit

import yaml

from .adapter import harbor_id
from .main import PARITY_TASK_IDS
from .verifier_network import prepare_upstream

SMOKE_IDS = (
    "distributed-fs-chfs", "evosuite-test-generation", "navidrome-seg02",
    "numpy-dl-aics-style", "cocos2dx-physics2d-medium",
)


def prepare(source: Path, harbor: Path, output: Path, *, split: str, model: str,
            base_url: str, version: str, binary: Path) -> None:
    endpoint = urlsplit(base_url)
    if (endpoint.scheme != "https" or not endpoint.hostname or endpoint.username is not None
            or endpoint.password is not None or endpoint.query or endpoint.fragment):
        raise ValueError("Supply a container-reachable HTTPS base URL without credentials")
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:-[\w.-]+)?", version):
        raise ValueError("Supply an exact Codex CLI release")
    if not model.startswith("openai/") or not model.removeprefix("openai/").strip():
        raise ValueError("Supply the agreed openai/MODEL name")
    tasks = {"smoke": SMOKE_IDS, "full": PARITY_TASK_IDS}[split]
    output = output.resolve()
    if any(output.is_relative_to(path.resolve()) for path in (source, harbor)):
        raise ValueError("Use a new output directory outside the inputs")
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    try:
        harbor_tasks = []
        for task_id in tasks:
            task = (harbor / task_id).resolve()
            meta = tomllib.loads((task / "task.toml").read_text())["metadata"]
            upstream_id = meta["upstream_task_id"]
            if meta["upstream_bundle"] != "c47e13a" or (
                Path(upstream_id).name != upstream_id or harbor_id(upstream_id) != task_id
            ):
                raise ValueError(f"{task}: unexpected source metadata")
            path = prepare_upstream(source / upstream_id, task, output / task_id,
                                    model_host=endpoint.hostname)
            native = yaml.safe_load(path.read_text())
            native.update(agent="codex", model=model, n_attempts=1, n_concurrent_trials=1,
                          global_timeout_multiplier=1.0, agent_kwargs={
                              "base_url": base_url, "api_key_env": "OPENAI_API_KEY",
                              "codex_binary": str(binary.resolve()), "outer_loop_count": 1,
                          })
            path.write_text(yaml.safe_dump(native, sort_keys=False))
            harbor_tasks.append({"path": str(task)})
        job = {
            "jobs_dir": str(output / "harbor-results"), "n_attempts": 1,
            "n_concurrent_trials": 1, "timeout_multiplier": 1.0, "retry": {"max_retries": 0},
            "environment": {"type": "docker", "force_build": True, "delete": True},
            "agents": [{"name": "codex", "model_name": model, "base_url": base_url,
                        "model_api_key_name": "OPENAI_API_KEY",
                        "extra_allowed_hosts": [endpoint.hostname],
                        "kwargs": {"version": version, "web_search": "disabled",
                                   "config": {"features": {"goals": True}}}}],
            "tasks": harbor_tasks,
        }
        (output / "harbor.yaml").write_text(yaml.safe_dump(job, sort_keys=False))
    except BaseException:
        shutil.rmtree(output)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "harbor", "output", "binary"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--split", choices=("smoke", "full"), required=True)
    for name in ("model", "base-url", "version"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    prepare(**vars(args))


if __name__ == "__main__":
    main()
