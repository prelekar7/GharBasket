import shutil
import subprocess
import sys
from importlib import import_module
from pathlib import Path

import pytest
from fastapi import APIRouter

from gharbasket.main import MODULE_ROUTERS
from gharbasket.modules import MODULES
from gharbasket.platform.db import SCHEMAS

API_DIR = Path(__file__).resolve().parents[1]
MODULES_DIR = API_DIR / "src" / "gharbasket" / "modules"
MODULE_FILES = (
    "__init__.py",
    "api.py",
    "router.py",
    "service.py",
    "schemas.py",
    "models.py",
    "events.py",
)


def test_every_module_folder_is_registered() -> None:
    folders = {p.name for p in MODULES_DIR.iterdir() if p.is_dir() and p.name != "__pycache__"}

    assert folders == set(MODULES)
    assert len(MODULES) == 13


@pytest.mark.parametrize("module", MODULES)
def test_module_has_the_standard_files(module: str) -> None:
    present = {p.name for p in (MODULES_DIR / module).iterdir()}

    assert set(MODULE_FILES) <= present


@pytest.mark.parametrize("module", MODULES)
def test_module_docstring_names_its_spec(module: str) -> None:
    doc = import_module(f"gharbasket.modules.{module}").__doc__ or ""

    assert doc.startswith("M") and "docs/modules/" in doc


def test_every_module_owns_one_schema_from_the_baseline() -> None:
    schemas = [import_module(f"gharbasket.modules.{m}.models").SCHEMA for m in MODULES]

    assert len(set(schemas)) == len(MODULES)
    assert set(schemas) == set(SCHEMAS) - {"platform"}


def test_every_module_router_is_mounted_in_order() -> None:
    assert len(MODULE_ROUTERS) == len(MODULES)
    for module, router in zip(MODULES, MODULE_ROUTERS, strict=True):
        assert isinstance(router, APIRouter)
        assert router.tags == [module]


def _lint_imports_with(tmp_path: Path, edits: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run lint-imports on a copy of the package with some files changed."""
    src = tmp_path / "src"
    shutil.copytree(API_DIR / "src" / "gharbasket", src / "gharbasket")
    for relative, content in edits.items():
        (src / "gharbasket" / relative).write_text(content)
    config = tmp_path / "pyproject.toml"
    shutil.copy(API_DIR / "pyproject.toml", config)
    lint_imports = Path(sys.executable).with_name("lint-imports")
    return subprocess.run(
        [str(lint_imports), "--config", str(config), "--no-cache"],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env={"PYTHONPATH": str(src), "PATH": str(lint_imports.parent)},
        check=False,
    )


def test_a_module_may_use_another_modules_facade(tmp_path: Path) -> None:
    result = _lint_imports_with(
        tmp_path, {"modules/orders/service.py": "from gharbasket.modules.billing import api\n"}
    )

    assert result.returncode == 0, result.stdout


def test_a_module_may_not_reach_into_another_module(tmp_path: Path) -> None:
    result = _lint_imports_with(
        tmp_path, {"modules/orders/service.py": "from gharbasket.modules.billing import models\n"}
    )

    assert result.returncode == 1
    assert "Modules talk to each other only through api.py BROKEN" in result.stdout


def test_platform_may_not_depend_on_a_module(tmp_path: Path) -> None:
    result = _lint_imports_with(
        tmp_path, {"platform/health.py": "from gharbasket.modules.orders import api\n"}
    )

    assert result.returncode == 1
    assert "BROKEN" in result.stdout
