"""Offline verifier for a downloaded LedgerGate audit-export JSON document.

Run from the backend directory:
    python -m scripts.verify_audit_export --document export.json --keyring-file keyring.json

The keyring file is secret material. Restrict it to the operator account (mode 0600
on POSIX systems); never commit it or include key values in command-line arguments.
"""
import argparse
import json
import os
import stat
import sys
from pathlib import Path
from typing import Sequence

from app.services.audit_export import verify_audit_export_document


MAX_EXPORT_FILE_BYTES = 10 * 1024 * 1024
MAX_KEYRING_FILE_BYTES = 1024 * 1024


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _reject_nonstandard_constant(value):
    raise ValueError(f"non-standard JSON numeric constant: {value}")


def _read_json(path: Path, *, secret: bool = False):
    if path.is_symlink() or not path.is_file():
        raise ValueError("verifier input must be a regular file, not a symbolic link")
    if secret and os.name == "posix":
        file_stat = path.stat()
        mode = stat.S_IMODE(file_stat.st_mode)
        if file_stat.st_uid != os.getuid():
            raise ValueError("keyring file must be owned by the current operator account")
        if mode & 0o077:
            raise ValueError("keyring file permissions are too broad; run chmod 600 on the file")
    maximum = MAX_KEYRING_FILE_BYTES if secret else MAX_EXPORT_FILE_BYTES
    with path.open("rb") as stream:
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError(f"verifier input exceeds the {maximum}-byte file limit")
    try:
        decoded = raw.decode("utf-8")
        return json.loads(
            decoded,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonstandard_constant,
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("verifier input is not valid UTF-8 JSON") from exc


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify a LedgerGate signed audit export and its event hash chain.")
    parser.add_argument("--document", required=True, type=Path, help="Downloaded audit export JSON file")
    parser.add_argument("--keyring-file", required=True, type=Path, help="JSON object mapping signing key IDs to secret keys")
    args = parser.parse_args(argv)

    try:
        document = _read_json(args.document)
        keyring = _read_json(args.keyring_file, secret=True)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        print(f"ERROR: could not read verifier input: {exc}", file=sys.stderr)
        return 2

    if not isinstance(keyring, dict) or not keyring or any(
        not isinstance(key_id, str) or not key_id or len(key_id) > 128 or key_id.strip() != key_id
        or not isinstance(key, str) or not 32 <= len(key) <= 4096
        for key_id, key in keyring.items()
    ):
        print(
            "ERROR: keyring must map bounded, non-empty key IDs to keys of 32-4096 characters",
            file=sys.stderr,
        )
        return 2

    if verify_audit_export_document(document, keyring):
        print("VALID: signature, export metadata, tenant consistency, and event hash chain verified")
        return 0

    print("INVALID: signature, export metadata, or event hash chain verification failed", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
