from __future__ import annotations

import csv
import gzip
import hashlib
import io
import os
import re
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


MAX_COMPRESSED_BYTES = 16 * 1024 * 1024
MAX_DECOMPRESSED_BYTES = 64 * 1024 * 1024
MAX_ZIP_MEMBERS = 8
MAX_CSV_ROWS = 250_000
SAFE_FILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")


class ArtifactValidationError(ValueError):
    pass


class ArtifactSchemaDriftError(ArtifactValidationError):
    pass


@dataclass(frozen=True, slots=True)
class ArtifactBytes:
    file_name: str
    content: bytes
    source_locator: str

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()


def validate_file_name(file_name: str) -> str:
    if Path(file_name).name != file_name or not SAFE_FILE_NAME.fullmatch(file_name):
        raise ArtifactValidationError("Artifact file name is unsafe")
    return file_name


def _bounded_gzip(content: bytes) -> bytes:
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(content), mode="rb") as compressed:
            result = compressed.read(MAX_DECOMPRESSED_BYTES + 1)
    except (EOFError, OSError) as exc:
        raise ArtifactValidationError("Malformed GZIP artifact") from exc
    if len(result) > MAX_DECOMPRESSED_BYTES:
        raise ArtifactValidationError("GZIP artifact exceeds decompression ceiling")
    return result


def _bounded_zip(content: bytes) -> tuple[str, bytes]:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            members = [item for item in archive.infolist() if not item.is_dir()]
            if not members or len(members) > MAX_ZIP_MEMBERS:
                raise ArtifactValidationError("ZIP has an invalid member count")
            for member in members:
                path = PurePosixPath(member.filename.replace("\\", "/"))
                if path.is_absolute() or ".." in path.parts or len(path.parts) != 1:
                    raise ArtifactValidationError("ZIP member path is unsafe")
                if member.file_size > MAX_DECOMPRESSED_BYTES:
                    raise ArtifactValidationError("ZIP member exceeds decompression ceiling")
            csv_members = [item for item in members if item.filename.casefold().endswith(".csv")]
            if len(csv_members) != 1:
                raise ArtifactValidationError("ZIP must contain exactly one CSV member")
            member = csv_members[0]
            with archive.open(member) as stream:
                extracted = stream.read(MAX_DECOMPRESSED_BYTES + 1)
    except zipfile.BadZipFile as exc:
        raise ArtifactValidationError("Malformed ZIP artifact") from exc
    if len(extracted) > MAX_DECOMPRESSED_BYTES:
        raise ArtifactValidationError("ZIP artifact exceeds decompression ceiling")
    return PurePosixPath(member.filename).name, extracted


def extract_csv(artifact: ArtifactBytes) -> tuple[str, bytes]:
    validate_file_name(artifact.file_name)
    if not artifact.content or len(artifact.content) > MAX_COMPRESSED_BYTES:
        raise ArtifactValidationError("Artifact is empty or exceeds the compressed-size ceiling")
    name = artifact.file_name.casefold()
    if name.endswith(".zip"):
        member_name, content = _bounded_zip(artifact.content)
    elif name.endswith(".gz"):
        member_name = artifact.file_name[:-3]
        content = _bounded_gzip(artifact.content)
    elif name.endswith(".csv"):
        member_name, content = artifact.file_name, artifact.content
    else:
        raise ArtifactValidationError("Only CSV, CSV.GZ, and CSV.ZIP artifacts are supported")
    if b"\x00" in content:
        raise ArtifactValidationError("CSV contains binary NUL bytes")
    return member_name, content


def csv_records(artifact: ArtifactBytes) -> tuple[list[str], list[tuple[int, dict[str, str]]]]:
    _, content = extract_csv(artifact)
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ArtifactValidationError("CSV is not valid UTF-8") from exc
    try:
        reader = csv.DictReader(io.StringIO(text, newline=""))
        if not reader.fieldnames or any(name is None or not name.strip() for name in reader.fieldnames):
            raise ArtifactSchemaDriftError("CSV header is missing or malformed")
        headers = [name.strip() for name in reader.fieldnames]
        folded_headers = [name.casefold() for name in headers]
        if len(folded_headers) != len(set(folded_headers)):
            raise ArtifactSchemaDriftError("CSV header contains duplicate column names")
        rows: list[tuple[int, dict[str, str]]] = []
        for row_number, row in enumerate(reader, start=2):
            if row_number - 1 > MAX_CSV_ROWS:
                raise ArtifactValidationError("CSV row-count ceiling exceeded")
            if None in row:
                raise ArtifactValidationError(f"Malformed CSV row at line {row_number}")
            rows.append(
                (
                    row_number,
                    {str(key).strip(): (value or "").strip() for key, value in row.items()},
                )
            )
    except csv.Error as exc:
        raise ArtifactValidationError("Malformed CSV artifact") from exc
    return headers, rows


class NseArtifactStore:
    def __init__(self, root: Path | None = None) -> None:
        project_root = Path(__file__).resolve().parents[4]
        self.root = (root or project_root / "data" / "nse").resolve()
        self.inbox = self.root / "inbox"
        self.raw = self.root / "raw"
        self.rejected = self.root / "rejected"

    def ensure(self) -> None:
        for directory in (self.inbox, self.raw, self.rejected):
            directory.mkdir(parents=True, exist_ok=True)

    def resolve_inbox_file(self, value: str | Path) -> Path:
        self.ensure()
        path = Path(value)
        if not path.is_absolute():
            path = self.inbox / path
        resolved = path.resolve()
        if resolved.parent != self.inbox.resolve():
            raise ArtifactValidationError("Local imports must be direct files in data/nse/inbox")
        if not resolved.is_file():
            raise ArtifactValidationError("Local artifact was not found in data/nse/inbox")
        validate_file_name(resolved.name)
        return resolved

    def read_inbox(self, value: str | Path) -> ArtifactBytes:
        path = self.resolve_inbox_file(value)
        content = path.read_bytes()
        if len(content) > MAX_COMPRESSED_BYTES:
            raise ArtifactValidationError("Artifact exceeds the compressed-size ceiling")
        return ArtifactBytes(
            file_name=path.name,
            content=content,
            source_locator=f"local-inbox:{path.name}",
        )

    def _retain_to(self, artifact: ArtifactBytes, directory: Path, prefix: str) -> str:
        self.ensure()
        safe_name = validate_file_name(artifact.file_name)
        storage_name = f"{artifact.sha256[:16]}-{safe_name}"
        destination = (directory / storage_name).resolve()
        if destination.parent != directory.resolve():
            raise ArtifactValidationError("Artifact destination escaped storage root")
        if not destination.exists():
            handle, temporary_name = tempfile.mkstemp(prefix=".partial-", dir=directory)
            try:
                with os.fdopen(handle, "wb") as stream:
                    stream.write(artifact.content)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary_name, destination)
            finally:
                Path(temporary_name).unlink(missing_ok=True)
        return f"{prefix}/{storage_name}"

    def retain(self, artifact: ArtifactBytes) -> str:
        return self._retain_to(artifact, self.raw, "raw")

    def retain_rejected(self, artifact: ArtifactBytes) -> str:
        return self._retain_to(artifact, self.rejected, "rejected")
