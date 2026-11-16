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


def _read_json(path: Path, *, secret: bool = False):
    if secret and os.name == "posix":
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & 0o077:
            raise ValueError("keyring file permissions are too broad; run chmod 600 on the file")
    with path.open("r", encoding="utf-8") as stream:
        return json.load(stream)


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
        not isinstance(key_id, str) or not key_id or not isinstance(key, str) or len(key) < 32
        for key_id, key in keyring.items()
    ):
        print("ERROR: keyring must map non-empty key IDs to keys of at least 32 characters", file=sys.stderr)
        return 2

    if verify_audit_export_document(document, keyring):
        print("VALID: signature, export metadata, tenant consistency, and event hash chain verified")
        return 0

    print("INVALID: signature, export metadata, or event hash chain verification failed", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
