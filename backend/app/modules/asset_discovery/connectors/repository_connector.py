"""Repository/codebase discovery connector.

Static analysis only: the connector walks a local directory, reads text
files within configured limits, and runs deterministic detection rules
over their contents. It never imports, compiles, installs, or executes
anything from the scanned source, never shells out, and never sends
content anywhere.

Recoverable per-file problems (oversized files, undecodable text,
unreadable entries) are returned as issues so discovery can finish as
PARTIAL; fatal problems (missing/invalid location) raise domain errors.
"""

import os
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from uuid import UUID

from app.modules.asset_discovery.connectors.base import (
    ConnectorIssue,
    DiscoveryConnector,
    DiscoveryConnectorResult,
    DiscoveryObservation,
)
from app.modules.asset_discovery.connectors.detection import detect
from app.modules.asset_discovery.enums import DiscoverySourceType
from app.modules.asset_discovery.errors import (
    DiscoverySourceUnreachableError,
    InvalidDiscoverySourceConfigError,
)
from app.modules.asset_discovery.models import DiscoverySource

MAX_FILE_BYTES = 1_048_576  # 1 MiB; larger files are skipped with an issue
MAX_OBSERVATIONS = 5_000  # hard cap on observations per scan
MAX_ISSUES = 200  # hard cap on recoverable issues per scan
BINARY_SNIFF_BYTES = 8_192

# Deliberately modest defaults: enough to skip VCS, virtualenv, dependency,
# build and cache output without hard-coding an ecosystem-sized list. The
# connector accepts extra entries so exclusions stay extensible.
DEFAULT_EXCLUDED_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        ".tox",
        ".nox",
        ".cache",
        "build",
        "dist",
        "target",
        "coverage",
        "htmlcov",
        "vendor",
        "site-packages",
        ".next",
        ".nuxt",
        ".gradle",
        ".terraform",
    }
)

DEFAULT_EXCLUDED_SUFFIXES = frozenset(
    {
        # images and documents
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".ico",
        ".bmp",
        ".pdf",
        # archives
        ".zip",
        ".tar",
        ".gz",
        ".tgz",
        ".bz2",
        ".xz",
        ".7z",
        ".rar",
        # binaries and object code
        ".exe",
        ".dll",
        ".so",
        ".dylib",
        ".bin",
        ".class",
        ".jar",
        ".war",
        ".o",
        ".lib",
        ".pyc",
        ".pyo",
        ".pyd",
        # fonts and media
        ".woff",
        ".woff2",
        ".ttf",
        ".otf",
        ".eot",
        ".mp3",
        ".mp4",
        ".wav",
        ".avi",
        ".mov",
        ".flac",
        # data stores and serialized/model artifacts
        ".db",
        ".sqlite",
        ".sqlite3",
        ".parquet",
        ".pkl",
        ".pickle",
        ".pt",
        ".onnx",
        ".h5",
        ".hdf5",
        ".npy",
        ".npz",
        ".ckpt",
        ".safetensors",
    }
)

# generated lockfiles: huge, regenerated, and full of transitive noise
DEFAULT_EXCLUDED_FILENAMES = frozenset(
    {
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "poetry.lock",
        "Cargo.lock",
        "Gemfile.lock",
        "uv.lock",
        "composer.lock",
    }
)


def _scan_file(file_path: str, path: Path) -> tuple[str | None, ConnectorIssue | None]:
    """Read one file as UTF-8 text.

    Returns ``(text, None)`` for scannable text, ``(None, None)`` for
    binary files (skipped silently), and ``(None, issue)`` for recoverable
    problems. OSError propagates to the caller, which records it as an
    unreadable-file issue.
    """
    size = path.stat().st_size
    if size > MAX_FILE_BYTES:
        return None, ConnectorIssue(
            file_path,
            "file_too_large",
            f"file exceeds the {MAX_FILE_BYTES} byte scan limit",
        )
    data = path.read_bytes()
    if b"\x00" in data[:BINARY_SNIFF_BYTES]:
        return None, None  # binary content: skip rather than fail
    try:
        return data.decode("utf-8"), None
    except UnicodeDecodeError:
        return None, ConnectorIssue(
            file_path,
            "unsupported_encoding",
            "file is not valid UTF-8 text",
        )


def _iter_files(
    root: Path,
    excluded_dirs: set[str],
    excluded_suffixes: set[str],
    excluded_filenames: set[str],
    onerror: Callable[[OSError], None],
) -> Iterator[Path]:
    """Yield scannable files under ``root`` in deterministic order.

    Excluded directories are pruned before descending; symlinks are never
    followed (neither directory nor file), non-regular files are skipped,
    and every yielded path is verified to resolve inside ``root`` so a
    hostile tree cannot escape the configured location.
    """
    for dirpath, dirnames, filenames in os.walk(
        root, topdown=True, followlinks=False, onerror=onerror
    ):
        kept: list[str] = []
        for dirname in sorted(dirnames):
            if dirname.casefold() in excluded_dirs:
                continue
            if (Path(dirpath) / dirname).is_symlink():
                continue
            kept.append(dirname)
        dirnames[:] = kept

        for filename in sorted(filenames):
            if filename.casefold() in excluded_filenames:
                continue
            if Path(filename).suffix.casefold() in excluded_suffixes:
                continue
            path = Path(dirpath) / filename
            try:
                if path.is_symlink() or not path.is_file():
                    continue
                if not path.resolve().is_relative_to(root):
                    continue
            except OSError:
                continue
            yield path


class RepositoryDiscoveryConnector(DiscoveryConnector):
    """Static scanner for local repository/codebase discovery sources."""

    name = DiscoverySourceType.REPOSITORY.value

    def __init__(
        self,
        *,
        extra_excluded_dirs: Iterable[str] = (),
        extra_excluded_suffixes: Iterable[str] = (),
        extra_excluded_filenames: Iterable[str] = (),
    ) -> None:
        self.excluded_dirs = {
            entry.casefold() for entry in DEFAULT_EXCLUDED_DIRS | set(extra_excluded_dirs)
        }
        self.excluded_suffixes = {
            entry.casefold() for entry in DEFAULT_EXCLUDED_SUFFIXES | set(extra_excluded_suffixes)
        }
        self.excluded_filenames = {
            entry.casefold() for entry in DEFAULT_EXCLUDED_FILENAMES | set(extra_excluded_filenames)
        }

    @staticmethod
    def _resolve_root(source: DiscoverySource) -> Path:
        """Validate and resolve the configured location (fatal on failure).

        Error messages intentionally never include the configured path so
        filesystem layout is not leaked through API responses or logs.
        """
        config = source.config if isinstance(source.config, dict) else {}
        location = config.get("location")
        if not isinstance(location, str) or not location.strip():
            raise InvalidDiscoverySourceConfigError(
                "config.location is required and must be a non-empty string "
                "for source type 'repository'"
            )
        if len(location) > 1024 or "\x00" in location:
            raise InvalidDiscoverySourceConfigError("config.location is invalid")
        try:
            root = Path(location).resolve()
        except (OSError, ValueError):
            raise DiscoverySourceUnreachableError(
                "discovery source location is not accessible"
            ) from None
        if not root.exists():
            raise DiscoverySourceUnreachableError("discovery source location does not exist")
        if not root.is_dir():
            raise DiscoverySourceUnreachableError("discovery source location is not a directory")
        if not os.access(root, os.R_OK):
            raise DiscoverySourceUnreachableError("discovery source location is not readable")
        return root

    def discover(self, source: DiscoverySource, run_id: UUID) -> DiscoveryConnectorResult:
        root = self._resolve_root(source)
        issues: list[ConnectorIssue] = []
        observations: list[DiscoveryObservation] = []
        files_scanned = 0
        stop = False

        def record(issue: ConnectorIssue) -> bool:
            """Append an issue; False once the issue cap means stop scanning."""
            nonlocal stop
            if stop:
                return False
            if len(issues) >= MAX_ISSUES - 1:
                issues.append(
                    ConnectorIssue(
                        None,
                        "error_limit",
                        f"error limit of {MAX_ISSUES} reached; scan stopped",
                    )
                )
                stop = True
                return False
            issues.append(issue)
            return True

        def on_walk_error(exc: OSError) -> None:
            relative: str | None = None
            if exc.filename:
                try:
                    relative = Path(exc.filename).resolve().relative_to(root).as_posix()
                except (OSError, ValueError):
                    relative = None
            record(ConnectorIssue(relative, "unreadable", "directory could not be read"))

        files = _iter_files(
            root,
            self.excluded_dirs,
            self.excluded_suffixes,
            self.excluded_filenames,
            on_walk_error,
        )
        for path in files:
            if stop:
                break
            if len(observations) >= MAX_OBSERVATIONS:
                record(
                    ConnectorIssue(
                        None,
                        "observation_limit",
                        f"observation limit of {MAX_OBSERVATIONS} reached; scan stopped",
                    )
                )
                break

            file_path = path.relative_to(root).as_posix()
            try:
                text, issue = _scan_file(file_path, path)
            except OSError:
                text, issue = (
                    None,
                    ConnectorIssue(file_path, "unreadable", "file could not be read"),
                )
            if issue is not None:
                if not record(issue):
                    break
                continue
            if text is None:
                continue  # binary file, skipped silently

            files_scanned += 1
            remaining = MAX_OBSERVATIONS - len(observations)
            observations.extend(detect(source.id, run_id, file_path, text, limit=remaining))

        observations.sort(
            key=lambda obs: (obs.file_path, obs.line_number or 0, obs.detection_rule, obs.name)
        )
        return DiscoveryConnectorResult(
            connector=self.name,
            files_scanned=files_scanned,
            observations=tuple(observations),
            issues=tuple(issues),
        )
