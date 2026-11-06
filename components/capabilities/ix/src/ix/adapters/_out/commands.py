"""The ``command`` probe, and the ``exit-status`` sensor that reads what it leaves.

**A command is a check of the subject.** Applying the probe runs its ``command`` once, with
the subject up: its options are filled as any probe's are (a factor's level, an option or a
facet of the subject, ``{url}``), and it runs in ``cwd``, which the run resolves against the
experiment's directory, in a process group of its own that ends when it finishes or
overruns. It leaves the facet ``output``: ``{exit_code, stdout, stderr, truncated}``, each
stream the last :data:`OUTPUT_LIMIT` bytes it wrote, as text, and ``truncated`` true when
either wrote more. The ``exit-status`` sensor reads ``exit_code`` against its ``code``.

**Whose failure.** A command that cannot be started (its program is not there) and one that
runs past its ``timeout`` are the harness's: the check showed nothing, so a ``check`` reads
the trial as inconclusive, never as the subject failing. A command that runs and exits, with
any code, is an observation: what the code means is the sensor's to say.
"""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING, Any

from ix.adapters._out.processes import startable
from ix.extension import HarnessError, Sensed, duration_of

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ix.extension import Interaction, Launcher, Requirement, Target

#: The most of each stream a trial keeps, in bytes: its end, where a check says what failed.
OUTPUT_LIMIT = 64 * 1024

#: How long past a command's ``timeout`` the run waits for the probe to give up, before it
#: records a harness timeout of its own.
BACKSTOP_S = 10.0

#: How long a command that has finished, or overrun, has to end with its group.
GRACE_S = 1.0


async def _tail(stream: asyncio.StreamReader | None, limit: int) -> tuple[bytes, bool]:
    """The last ``limit`` bytes of ``stream``, read to its end, and whether it held more."""
    if stream is None:
        return b"", False
    kept = bytearray()
    cut = False
    while chunk := await stream.read(65536):
        kept += chunk
        if len(kept) > limit:
            del kept[: len(kept) - limit]
            cut = True
    return bytes(kept), cut


def _limit(options: Mapping[str, Any]) -> float:
    """How long the command may run: its ``timeout``. Raises :class:`HarnessError`
    (``option``) for one a cell filled with what is not a duration."""
    try:
        return duration_of(options, "timeout")
    except ValueError as error:
        raise HarnessError("option", str(error)) from error


class CommandProbe:
    """Runs a command once against the subject: see the module docstring. A
    :class:`~ix.extension.Preflight`: its program can be started here."""

    def __init__(self, launcher: Launcher) -> None:
        self._launcher = launcher

    def requirements(self, options: Mapping[str, Any], *, base: str) -> list[Requirement]:
        """That its program can be started here."""
        command = options.get("command")
        if not isinstance(command, list | tuple) or not command:
            return []  # the kind's declaration reports it
        found = startable(str(command[0]), base=base, cwd=str(options["cwd"]))
        return [] if found is None else [found]

    def budget_s(self, options: Mapping[str, Any]) -> float | None:
        return _limit(options) + BACKSTOP_S

    async def apply(
        self, options: Mapping[str, Any], target: Target, *, seed: int
    ) -> Mapping[str, Mapping[str, Any]]:
        argv = [str(part) for part in options["command"]]
        limit = _limit(options)
        added = {str(key): str(value) for key, value in (options.get("env") or {}).items()}
        try:
            process = await self._launcher.start(
                argv,
                cwd=str(options["cwd"]),
                env={**os.environ, **added},
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as error:
            raise HarnessError(
                "not_started", f"{argv[0]!r} could not be started: {error}"
            ) from error
        try:
            async with asyncio.timeout(limit):
                (out, out_cut), (err, err_cut) = await asyncio.gather(
                    _tail(process.stdout, OUTPUT_LIMIT), _tail(process.stderr, OUTPUT_LIMIT)
                )
                code = await process.wait()
        except TimeoutError:
            raise HarnessError(
                "timeout", f"{argv[0]!r} ran past its timeout of {limit:g}s"
            ) from None
        finally:
            await self._launcher.stop(process, grace_s=GRACE_S)
        return {
            "output": {
                "exit_code": code,
                "stdout": out.decode("utf-8", errors="replace"),
                "stderr": err.decode("utf-8", errors="replace"),
                "truncated": out_cut or err_cut,
            }
        }


class ExitStatusSensor:
    """Whether the command a probe ran exited with ``code``. Its detail keeps the exit code
    and the end of what the command wrote to stderr."""

    def watch(self, target: Target, options: Mapping[str, Any]) -> None:
        return None

    async def sense(
        self, interaction: Interaction, *, truth: Mapping[str, Any], options: Mapping[str, Any]
    ) -> Sensed:
        output = interaction.observations["output"]
        code = int(output["exit_code"])
        return Sensed(
            values={"exited_ok": code == int(options["code"])},
            detail={"exit_code": code, "stderr": str(output.get("stderr", ""))[-400:]},
        )
