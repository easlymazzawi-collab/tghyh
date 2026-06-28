#!/usr/bin/env python3
"""Run the Login Tester server."""

from __future__ import annotations

import importlib.util
import subprocess
import sys


REQUIRED_PACKAGES = (
    ("fastapi", "fastapi"),
    ("uvicorn", "uvicorn[standard]"),
    ("httpx", "httpx"),
    ("pydantic", "pydantic"),
    ("pydantic_settings", "pydantic-settings"),
    ("bs4", "beautifulsoup4"),
    ("multipart", "python-multipart"),
)


def _missing_packages() -> list[tuple[str, str]]:
    missing: list[tuple[str, str]] = []
    for module_name, pip_name in REQUIRED_PACKAGES:
        if importlib.util.find_spec(module_name) is None:
            missing.append((module_name, pip_name))
    return missing


def _print_install_help(missing: list[tuple[str, str]]) -> None:
    names = " ".join(pip_name for _, pip_name in missing)
    print("Thieu thu vien Python. Chay lenh sau trong thu muc project:\n")
    print(f"    {sys.executable} -m pip install -r requirements.txt\n")
    print("Hoac cai rieng:")
    print(f"    {sys.executable} -m pip install {names}\n")
    print("Windows: double-click install.bat hoac chay start.bat")


def ensure_dependencies(auto_install: bool = False) -> None:
    missing = _missing_packages()
    if not missing:
        return

    if auto_install:
        print("Dang cai dat dependencies...")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"],
        )
        missing = _missing_packages()

    if missing:
        _print_install_help(missing)
        raise SystemExit(1)


def main() -> None:
    import uvicorn

    from app.config import settings

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
        use_colors=False,
    )


if __name__ == "__main__":
    auto = "--install" in sys.argv
    ensure_dependencies(auto_install=auto)
    main()
