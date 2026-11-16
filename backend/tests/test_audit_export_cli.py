import json
import os
import stat

import pytest

from scripts.verify_audit_export import main
from test_audit_export import _signed_valid_chain_document


def _write_inputs(tmp_path, document, keyring):
    document_path = tmp_path / "export.json"
    keyring_path = tmp_path / "keyring.json"
    document_path.write_text(json.dumps(document), encoding="utf-8")
    keyring_path.write_text(json.dumps(keyring), encoding="utf-8")
    if os.name == "posix":
        keyring_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return document_path, keyring_path


def test_cli_accepts_valid_export_and_keyring_file(tmp_path, capsys):
    document, keyring = _signed_valid_chain_document()
    document_path, keyring_path = _write_inputs(tmp_path, document, keyring)

    result = main(["--document", str(document_path), "--keyring-file", str(keyring_path)])

    assert result == 0
    assert "VALID:" in capsys.readouterr().out


def test_cli_returns_invalid_for_tampered_export(tmp_path, capsys):
    document, keyring = _signed_valid_chain_document()
    document["events"][0]["action"] = "tampered"
    document_path, keyring_path = _write_inputs(tmp_path, document, keyring)

    result = main(["--document", str(document_path), "--keyring-file", str(keyring_path)])

    assert result == 1
    assert "INVALID:" in capsys.readouterr().err


def test_cli_rejects_insecure_posix_keyring_permissions(tmp_path, capsys):
    if os.name != "posix":
        pytest.skip("POSIX file permission check")
    document, keyring = _signed_valid_chain_document()
    document_path, keyring_path = _write_inputs(tmp_path, document, keyring)
    keyring_path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP)

    result = main(["--document", str(document_path), "--keyring-file", str(keyring_path)])

    assert result == 2
    assert "permissions are too broad" in capsys.readouterr().err


def test_cli_rejects_invalid_keyring_shape(tmp_path, capsys):
    document, _ = _signed_valid_chain_document()
    document_path, keyring_path = _write_inputs(tmp_path, document, ["not-a-keyring"])

    result = main(["--document", str(document_path), "--keyring-file", str(keyring_path)])

    assert result == 2
    assert "keyring must map" in capsys.readouterr().err
