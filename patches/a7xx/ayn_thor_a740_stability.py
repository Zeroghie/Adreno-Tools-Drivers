#!/usr/bin/env python3
from pathlib import Path

DEVICE = Path("src/freedreno/vulkan/tu_device.cc")

if DEVICE.exists():
    src = DEVICE.read_text(encoding="utf-8")
    target = "memory_budget_props->heapUsage = p_atomic_read(&tu_process_heap_used);"
    patch = (
        "memory_budget_props->heapUsage = p_atomic_read(&tu_process_heap_used);\n"
        "   if (memory_budget_props->heapBudget > 6ULL * 1024 * 1024 * 1024)\n"
        "      memory_budget_props->heapBudget = 6ULL * 1024 * 1024 * 1024;"
    )
    if target in src and "6ULL * 1024 * 1024 * 1024" not in src:
        DEVICE.write_text(src.replace(target, patch, 1), encoding="utf-8")
        print("src/freedreno/vulkan/tu_device.cc: AYN Thor Pro 6 GiB heapBudget cap applied")
    else:
        print("src/freedreno/vulkan/tu_device.cc: heapBudget cap skipped (anchor not matched)")
