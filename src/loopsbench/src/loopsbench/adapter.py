"""Convert LoopsBench tasks into Harbor tasks.

LoopsBench (https://github.com/microsoft/Loopsbench) ships each task as a
Terminal-Bench-style directory: ``task.yaml``, ``Dockerfile`` plus a ``base/``
build context, ``solution.sh`` with ``gold_patches/``, and ``tests/`` with
``run-tests.sh``. Upstream runs the agent in a ``client`` container, copies its
``/workspace`` into a fresh ``tester`` container built from the same image and
runs ``run-tests.sh`` there. Harbor transfers a workspace archive to a separate
verifier container, with ``tests/test.sh`` reproducing the upstream TestRunner
and result parser.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).parent / "task-template"
DEFAULT_CPUS = 4
DEFAULT_MEMORY_MB = 8192
_TASK_CPU_DEFAULTS = {
    "compiler-rustlike-rv32": 16,
    "ns3-network-simulation": 16,
    "panda3d-collision-hard": 8,
    "panda3d-pgraph-medium": 8,
    "rdma-ns3-evolution": 16,
}
_TASK_MEMORY_DEFAULTS = {
    "ns3-network-simulation": 16384,
    "rdma-ns3-evolution": 16384,
}
_EXCLUDED_TASK_IDS = frozenset({
    "angr-concolic-execution",
    "django-seg11",
    "framework-seg04",
    "jenkins-seg02",
    "jenkins-seg05",
    "llvm-alive-verification",
    "security-labs",
    "typescript-seg01",
})
_CYCLIC_BASE_LINKS = {
    "navidrome-seg02": "tests/fixtures/symlink2dir",
    "node-seg03": "tools/node_modules/eslint/node_modules/eslint",
}
_PANDA3D_BUILD_SETUP = (
    "COPY base/ /workspace/",
    "RUN find /workspace/panda3d -type f ! -newermt @0 -exec touch -m -d @1 {} +\n"
    "ENV PANDA_BUILD_THREADS=6",
)
_DOCKERFILE_STEPS = {
    "db-nanodb": (
        "COPY requirements/ /workspace/requirements/",
        "RUN touch -m -d @1 /workspace/src/test/resources/edu/caltech/test/nanodb/sql/test_sql.props",
    ),
    "node-seg03": (
        "FROM node:18-bullseye",
        "RUN sed -i 's|http://deb.debian.org/debian-security|"
        "https://archive.debian.org/debian-security|' /etc/apt/sources.list",
    ),
    "nodebb-seg05": (
        "RUN cd /workspace && npm install 2>/dev/null || true",
        "RUN npm install --global mocha@9.1.3",
    ),
    "panda3d-collision-hard": _PANDA3D_BUILD_SETUP,
    "panda3d-pgraph-medium": _PANDA3D_BUILD_SETUP,
    "perses-program-reduction": (
        "FROM python:3.12-slim",
        "RUN apt-get update && apt-get install -y --no-install-recommends patch "
        "&& rm -rf /var/lib/apt/lists/*",
    ),
    "spring-course-mgmt-fullstack": (
        "COPY requirements/ /workspace/requirements/",
        "RUN find /workspace/CourseDesignDBMS/src/main/resources "
        "-type f ! -newermt @0 -exec touch -m -d @1 {} +",
    ),
}

# Top-level task files the upstream OracleAgent copies into /task.
_SKIP_TOP_LEVEL = {"tests", "gold_patches", "base", "requirements"}

# Env vars some task composes set on the client container; reproduced in the
# verifier so run-tests.sh sees the same environment.
_EXTRA_ENV_KEYS = ("WORKSPACE",)

# A Dockerfile COPY of gold patches leaks the reference solution into the agent
# image. These lines are dropped (documented in the README).
_GOLD_COPY_RE = re.compile(r"^\s*(COPY|ADD)\b[^\n]*\bgold_patches\b[^\n]*$", re.MULTILINE)


@dataclass(frozen=True)
class Resources:
    cpus: int | None = None
    memory_mb: int | None = None
    storage_mb: int = 30720
    build_timeout_sec: int = 3600


def harbor_id(upstream_id: str) -> str:
    """Stable, registry-safe task id: ``task_ClickHouse_seg07`` -> ``clickhouse-seg07``."""
    name = upstream_id[5:] if upstream_id.startswith("task_") else upstream_id
    name = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return name


def _toml_list(values: list[str]) -> str:
    return json.dumps([str(v) for v in values], ensure_ascii=False)


def _insert_dockerfile_step(task_id: str, dockerfile: str, after: str, step: str) -> str:
    anchor = re.compile(rf"^{re.escape(after)}$", re.MULTILINE)
    if len(anchor.findall(dockerfile)) != 1:
        raise ValueError(f"{task_id}: Dockerfile no longer contains a unique {after!r}")
    return anchor.sub(lambda match: f"{match.group()}\n{step}", dockerfile, count=1)


class LoopsBenchAdapter:
    def __init__(
        self,
        tasks_dir: Path,
        output_dir: Path,
        bundle: str = "unknown",
        task_ids: list[str] | None = None,
        limit: int | None = None,
        overwrite: bool = False,
        resources: Resources = Resources(),
    ) -> None:
        self.tasks_dir = Path(tasks_dir)
        self.output_dir = Path(output_dir)
        self.bundle = bundle
        self.task_ids = task_ids
        self.limit = limit
        self.overwrite = overwrite
        self.resources = resources

    # ------------------------------------------------------------------ select
    def upstream_task_dirs(self) -> list[Path]:
        dirs = sorted(
            p
            for p in self.tasks_dir.iterdir()
            if p.is_dir()
            and not p.name.startswith("_")
            and (p / "task.yaml").is_file()
            and harbor_id(p.name) not in _EXCLUDED_TASK_IDS
        )
        if self.task_ids:
            wanted = set(self.task_ids)
            dirs = [p for p in dirs if p.name in wanted or harbor_id(p.name) in wanted]
            missing = wanted - {p.name for p in dirs} - {harbor_id(p.name) for p in dirs}
            if missing:
                raise ValueError(f"Unknown or excluded task ids: {sorted(missing)}")
        if self.limit is not None:
            dirs = dirs[: self.limit]
        return dirs

    def run(self) -> list[Path]:
        stale = sorted(
            name for name in _EXCLUDED_TASK_IDS
            if (self.output_dir / name).exists() or (self.output_dir / name).is_symlink()
        )
        if stale:
            raise ValueError(
                f"Output directory {self.output_dir} contains excluded tasks {stale}. "
                "Use a new output directory."
            )
        self.output_dir.mkdir(parents=True, exist_ok=True)
        written = []
        for src in self.upstream_task_dirs():
            dst = self.output_dir / harbor_id(src.name)
            if dst.exists():
                if not self.overwrite:
                    logger.info("skip %s (exists; use --overwrite)", dst.name)
                    continue
                shutil.rmtree(dst)
            self.generate_task(src, dst)
            written.append(dst)
            logger.info("wrote %s", dst)
        return written

    # ---------------------------------------------------------------- generate
    def generate_task(self, src: Path, dst: Path) -> None:
        meta = yaml.safe_load((src / "task.yaml").read_text(encoding="utf-8"))
        env = self._compose_env(src)
        dst.mkdir(parents=True)
        self._write_instruction(meta, dst)
        uses_base_assets = self._write_tests(src, dst, meta, env)
        self._write_environment(src, dst)
        self._write_compose_overrides(src, dst)
        self._write_solution(src, dst)
        self._write_task_toml(src, dst, meta)
        if uses_base_assets:
            logger.info("%s: tests read /task-assets/base; bundled into tests/", src.name)

    @staticmethod
    def _client_service(src: Path) -> dict:
        compose = src / "docker-compose.yaml"
        if not compose.is_file():
            return {}
        data = yaml.safe_load(compose.read_text(encoding="utf-8")) or {}
        return (data.get("services") or {}).get("client") or {}

    def _write_compose_overrides(self, src: Path, dst: Path) -> None:
        """Carry container privileges the upstream client/tester services need.

        Only ``cap_add`` is reproduced (one task adds NET_ADMIN for loopback IP
        aliases). Upstream ``network_mode: host`` and custom bridge networks are
        not reproduced: Harbor's phase network policy replaces them.
        """
        client = self._client_service(src)
        overrides = {k: client[k] for k in ("cap_add",) if client.get(k)}
        if not overrides:
            return
        compose = {"services": {"main": overrides}}
        (dst / "environment" / "docker-compose.yaml").write_text(
            yaml.safe_dump(compose, sort_keys=False), encoding="utf-8"
        )
        logger.info("%s: compose override %s", src.name, overrides)

    def _compose_env(self, src: Path) -> dict[str, str]:
        client = self._client_service(src)
        raw = client.get("environment") or []
        items = raw.items() if isinstance(raw, dict) else (
            (e.split("=", 1) + [""])[:2] for e in raw
        )
        return {k: str(v) for k, v in items if k in _EXTRA_ENV_KEYS}

    def _write_instruction(self, meta: dict, dst: Path) -> None:
        (dst / "instruction.md").write_text(meta["instruction"].rstrip() + "\n", encoding="utf-8")

    def _write_environment(self, src: Path, dst: Path) -> None:
        env_dir = dst / "environment"
        env_dir.mkdir()
        for item in src.iterdir():
            # The agent image is built from the upstream build context minus the
            # oracle patches and the upstream harness files.
            if item.name in {"gold_patches", "solution.sh", "docker-compose.yaml"}:
                continue
            target = env_dir / item.name
            if item.is_dir():
                shutil.copytree(item, target, symlinks=True)
            else:
                shutil.copy2(item, target)
        dockerfile = env_dir / "Dockerfile"
        text = dockerfile.read_text(encoding="utf-8")
        stripped = _GOLD_COPY_RE.sub("# (removed by Harbor adapter: gold patch copy)", text)
        if stripped != text:
            logger.warning("%s: removed gold_patches COPY from Dockerfile", src.name)
        adapted = self._restore_cyclic_base_link(src.name, env_dir, stripped)
        if step := _DOCKERFILE_STEPS.get(harbor_id(src.name)):
            adapted = _insert_dockerfile_step(src.name, adapted, *step)
        if adapted != text:
            dockerfile.write_text(adapted, encoding="utf-8")

    @staticmethod
    def _restore_cyclic_base_link(task_id: str, env_dir: Path, dockerfile: str) -> str:
        relative_link = _CYCLIC_BASE_LINKS.get(harbor_id(task_id))
        if relative_link is None:
            return dockerfile
        link = env_dir / "base" / relative_link
        if not link.is_symlink():
            return dockerfile
        target = link.readlink().as_posix()
        if target != "..":
            raise ValueError(f"{task_id}: cyclic link {relative_link!r} no longer points to '..'")
        restore = json.dumps(["ln", "-s", "--", target, f"/workspace/{relative_link}"])
        dockerfile = _insert_dockerfile_step(task_id, dockerfile, "COPY base/ /workspace/", f"RUN {restore}")
        link.unlink()
        return dockerfile

    def _write_solution(self, src: Path, dst: Path) -> None:
        sol = dst / "solution"
        files = sol / "task_files"
        files.mkdir(parents=True)
        for item in src.iterdir():
            if item.is_file():
                shutil.copy2(item, files / item.name)
        if (src / "gold_patches").is_dir():
            shutil.copytree(src / "gold_patches", sol / "gold_patches", symlinks=True)
        shutil.copy2(TEMPLATE_DIR / "solution" / "solve.sh", sol / "solve.sh")
        (sol / "solve.sh").chmod(0o755)

    def _write_tests(self, src: Path, dst: Path, meta: dict, env: dict[str, str]) -> bool:
        tests = dst / "tests"
        if (src / "tests").is_dir():
            shutil.copytree(src / "tests", tests, symlinks=True)
        else:
            tests.mkdir()
        if (tests / "test.sh").exists() or (tests / "_loopsbench").exists():
            raise ValueError(f"{src.name}: tests/ already contains test.sh or _loopsbench")
        harness = tests / "_loopsbench"
        harness.mkdir()
        shutil.copy2(src / "run-tests.sh", harness / "run-tests.sh")
        shutil.copy2(TEMPLATE_DIR / "tests" / "loopsbench_grade.py", harness / "loopsbench_grade.py")
        shutil.copy2(TEMPLATE_DIR / "tests" / "workspace_snapshot.py", harness / "workspace_snapshot.py")
        uses_base_assets = any(
            "task-assets" in p.read_text(encoding="utf-8", errors="ignore")
            for p in [src / "run-tests.sh", *(src / "tests").rglob("*")]
            if p.is_file()
        ) if (src / "tests").is_dir() else False
        if uses_base_assets:
            shutil.copytree(src / "base", harness / "base", symlinks=True)

        extra = "\n".join(f"export {k}={json.dumps(v)}" for k, v in sorted(env.items()))
        test_timeout = int(float(meta.get("max_test_timeout_sec") or 3600))
        script = (TEMPLATE_DIR / "tests" / "test.sh").read_text(encoding="utf-8")
        script = script.replace("{extra_env}", extra).replace("{test_timeout_sec}", str(test_timeout))
        (tests / "test.sh").write_text(script, encoding="utf-8")
        (tests / "test.sh").chmod(0o755)
        return uses_base_assets

    def _write_task_toml(self, src: Path, dst: Path, meta: dict) -> None:
        agent_timeout = float(meta.get("max_agent_timeout_sec") or 1800.0)
        test_timeout = float(meta.get("max_test_timeout_sec") or 3600.0)
        tags = [str(t) for t in (meta.get("tags") or [])]
        r = self.resources
        task_id = harbor_id(src.name)
        cpus = r.cpus if r.cpus is not None else _TASK_CPU_DEFAULTS.get(task_id, DEFAULT_CPUS)
        memory_mb = (
            r.memory_mb
            if r.memory_mb is not None
            else _TASK_MEMORY_DEFAULTS.get(task_id, DEFAULT_MEMORY_MB)
        )
        text = (TEMPLATE_DIR / "task.toml").read_text(encoding="utf-8")
        values = {
            "harbor_id": task_id,
            "upstream_id": src.name,
            "bundle": self.bundle,
            "keywords": _toml_list(["loopsbench", "long-horizon", str(meta.get("category", "general")), *tags]),
            "difficulty": str(meta.get("difficulty", "unknown")),
            "category": str(meta.get("category", "general")),
            "tags": _toml_list(tags),
            "agent_timeout_sec": f"{agent_timeout:.1f}",
            # run-tests.sh is bounded by its own timeout inside test.sh; leave
            # headroom for the workspace snapshot and grading.
            "verifier_timeout_sec": f"{test_timeout + 600:.1f}",
            "build_timeout_sec": f"{float(r.build_timeout_sec):.1f}",
            "cpus": str(cpus),
            "memory_mb": str(memory_mb),
            "storage_mb": str(r.storage_mb),
        }
        for key, value in values.items():
            text = text.replace("{" + key + "}", value)
        (dst / "task.toml").write_text(text, encoding="utf-8")
