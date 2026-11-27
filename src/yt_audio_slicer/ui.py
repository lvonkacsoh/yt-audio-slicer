"""Shared console and formatting helpers."""

from __future__ import annotations

from pathlib import Path

from rich.console import Console

console = Console()
err_console = Console(stderr=True)


def rel(p: Path, root: Path) -> str:
    """p relative to root for display, or p itself when it is not below root."""
    try:
        return str(p.relative_to(root))
    except ValueError:
        return str(p)


def fmt_time(s: float | None) -> str:
    if s is None:
        return "-"
    s = round(s)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def fmt_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def ranges(nums) -> str:
    """[1, 2, 3, 5, 7, 8] -> '1-3, 5, 7-8'."""
    nums = sorted(set(nums))
    out, i = [], 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        out.append(str(nums[i]) if i == j else f"{nums[i]}-{nums[j]}")
        i = j + 1
    return ", ".join(out)
