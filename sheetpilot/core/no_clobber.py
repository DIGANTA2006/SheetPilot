"""Race-safe publication of a staged file without replacing an existing path."""

from __future__ import annotations

import os
from pathlib import Path


def publish_new_file(source: Path, destination: Path) -> None:
    """Atomically publish *source* and fail if *destination* already exists.

    Windows rename is no-clobber. On POSIX, a same-directory hard link provides
    the equivalent atomic create-if-absent guarantee before the staging name is
    removed. Callers deliberately fail closed on filesystems without either
    guarantee.
    """
    if source.parent.resolve() != destination.parent.resolve():
        raise OSError("Safe publication requires same-directory staging.")
    if os.name == "nt":
        source.rename(destination)
        return
    os.link(source, destination)
    try:
        source.unlink()
    except OSError:
        try:
            destination.unlink()
        finally:
            raise
