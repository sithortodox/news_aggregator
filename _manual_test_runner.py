"""ВНИМАНИЕ: это не часть проекта — временный скрипт для локальной проверки
тестов в песочнице, где недоступен pip install pytest (нет сети).
В реальном окружении пользователя тесты запускаются через `pytest`.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import sys
import traceback


def run_module(module_name: str) -> tuple[int, int]:
    mod = importlib.import_module(module_name)
    passed = 0
    failed = 0
    for name, fn in inspect.getmembers(mod, inspect.isfunction):
        if not name.startswith("test_"):
            continue
        try:
            if inspect.iscoroutinefunction(fn):
                asyncio.run(fn())
            else:
                fn()
            print(f"PASS {module_name}::{name}")
            passed += 1
        except Exception:
            print(f"FAIL {module_name}::{name}")
            traceback.print_exc()
            failed += 1
    return passed, failed


if __name__ == "__main__":
    total_passed = 0
    total_failed = 0
    for mod_name in sys.argv[1:]:
        p, f = run_module(mod_name)
        total_passed += p
        total_failed += f
    print(f"\nTOTAL: {total_passed} passed, {total_failed} failed")
    sys.exit(1 if total_failed else 0)
