"""What an add-on runs on, and what that means on this computer.

A manifest (or its catalog entry) says it with two fields:

    "compute": "gpu_preferred",      # cpu | gpu_preferred | gpu_required | remote
    "accelerators": ["cuda"]         # optional: the graphics cards it can use

- ``cpu``: made to run on the processor.
- ``gpu_preferred``: runs on the processor, much faster on a graphics card.
- ``gpu_required``: does not run without a graphics card.
- ``remote``: runs on a server; this computer's hardware does not matter.

``accelerators`` names which cards: ``cuda`` (NVIDIA), ``mps`` (Apple). An
add-on that only uses Apple's GPU is no faster on an NVIDIA card, so the
panel can only say an add-on will use this computer's card when the add-on
names it. Other names are kept, for add-ons ahead of this host, but never
match a card here.

`assess` puts the two together with what `hardware.detect` found. An add-on
without a ``compute`` field gets no verdict: the panel shows nothing rather
than guess.
"""

from __future__ import annotations

from dataclasses import dataclass

from subtitld.modules import hardware as _hardware

CPU = 'cpu'
GPU_PREFERRED = 'gpu_preferred'
GPU_REQUIRED = 'gpu_required'
REMOTE = 'remote'
VALUES = (CPU, GPU_PREFERRED, GPU_REQUIRED, REMOTE)

# Verdicts
RUNS_ON_CPU = 'cpu'              # a CPU add-on
RUNS_REMOTE = 'remote'           # a server does the work
USES_GPU = 'gpu'                 # will use a card this computer has
CPU_FALLBACK = 'cpu_fallback'    # GPU preferred, none here: slower, on the CPU
MISSING = 'missing'              # GPU required, none here
UNCHECKED = 'unchecked'          # cannot tell (not checked yet, or unknown)


def read(source: dict | None) -> tuple[str | None, list[str]]:
    """(compute, accelerators) from a manifest or catalog entry. compute is
    None when missing or not one of `VALUES`."""
    source = source or {}
    compute = source.get('compute')
    compute = compute.strip().lower() if isinstance(compute, str) else None
    if compute not in VALUES:
        compute = None
    accelerators: list[str] = []
    for name in source.get('accelerators') or []:
        if isinstance(name, str):
            name = name.strip().lower()
            if name and name not in accelerators:
                accelerators.append(name)
    return compute, accelerators


@dataclass(frozen=True)
class Verdict:
    kind: str
    compute: str
    accelerators: tuple[str, ...] = ()
    # The accelerator it will use (USES_GPU only).
    accelerator: str | None = None


def assess(compute: str | None, accelerators: list[str] | tuple[str, ...],
           hardware: _hardware.Hardware | None) -> Verdict | None:
    """What an add-on that runs on `compute` will do on this computer.
    `hardware` None means not checked yet."""
    if compute is None:
        return None
    accelerators = tuple(accelerators or ())
    if compute == CPU:
        return Verdict(RUNS_ON_CPU, compute, accelerators)
    if compute == REMOTE:
        return Verdict(RUNS_REMOTE, compute, accelerators)
    if hardware is None:
        return Verdict(UNCHECKED, compute, accelerators)

    without_gpu = CPU_FALLBACK if compute == GPU_PREFERRED else MISSING
    if accelerators:
        for accelerator in accelerators:
            if hardware.available(accelerator) is True:
                return Verdict(USES_GPU, compute, accelerators, accelerator)
        if all(hardware.available(acc) is False for acc in accelerators):
            return Verdict(without_gpu, compute, accelerators)
        return Verdict(UNCHECKED, compute, accelerators)

    # No cards named: which one it would use is not known, but with none
    # of any kind here, it will use none.
    if all(hardware.available(acc) is False for acc in _hardware.ACCELERATORS):
        return Verdict(without_gpu, compute, accelerators)
    return Verdict(UNCHECKED, compute, accelerators)


def runs_here(verdict: Verdict | None) -> bool:
    """False only for an add-on known not to run on this computer."""
    return verdict is None or verdict.kind != MISSING
