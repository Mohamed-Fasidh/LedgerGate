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



def test_cli_rejects_duplicate_json_keys_in_export(tmp_path, capsys):
    _, keyring = _signed_valid_chain_document()
    document_path = tmp_path / "duplicate-export.json"
    document_path.write_text('{"format":"first","format":"second"}', encoding="utf-8")
    keyring_path = tmp_path / "keyring.json"
    keyring_path.write_text(json.dumps(keyring), encoding="utf-8")
    if os.name == "posix":
        keyring_path.chmod(stat.S_IRUSR | stat.S_IWUSR)

    result = main(["--document", str(document_path), "--keyring-file", str(keyring_path)])

    assert result == 2
    assert "duplicate JSON object key" in capsys.readouterr().err


def test_cli_rejects_nonstandard_json_constants_in_keyring(tmp_path, capsys):
    document, _ = _signed_valid_chain_document()
    document_path, keyring_path = _write_inputs(tmp_path, document, {})
    keyring_path.write_text('{"key": NaN}', encoding="utf-8")
    if os.name == "posix":
        keyring_path.chmod(stat.S_IRUSR | stat.S_IWUSR)

    result = main(["--document", str(document_path), "--keyring-file", str(keyring_path)])

    assert result == 2
    assert "non-standard JSON numeric constant" in capsys.readouterr().err


def test_cli_rejects_export_file_over_size_limit(tmp_path, capsys):
    from scripts.verify_audit_export import MAX_EXPORT_FILE_BYTES

    document_path = tmp_path / "oversized-export.json"
    document_path.write_bytes(b" " * (MAX_EXPORT_FILE_BYTES + 1))
    keyring_path = tmp_path / "keyring.json"
    keyring_path.write_text("{}", encoding="utf-8")
    if os.name == "posix":
        keyring_path.chmod(stat.S_IRUSR | stat.S_IWUSR)

    result = main(["--document", str(document_path), "--keyring-file", str(keyring_path)])

    assert result == 2
    assert "file limit" in capsys.readouterr().err


def test_cli_rejects_symbolic_link_input(tmp_path, capsys):
    if not hasattr(os, "symlink"):
        pytest.skip("symbolic links unavailable")
    document, keyring = _signed_valid_chain_document()
    real_document, keyring_path = _write_inputs(tmp_path, document, keyring)
    linked_document = tmp_path / "linked-export.json"
    try:
        linked_document.symlink_to(real_document)
    except OSError:
        pytest.skip("symbolic link creation is not permitted")

    result = main(["--document", str(linked_document), "--keyring-file", str(keyring_path)])

    assert result == 2
    assert "symbolic link" in capsys.readouterr().err



@pytest.mark.parametrize(
    "keyring",
    [
        {"": "x" * 32},
        {"x" * 129: "x" * 32},
        {"valid-key": "x" * 4097},
        {" valid-key": "x" * 32},
    ],
)
def test_cli_rejects_unbounded_keyring_entries(tmp_path, capsys, keyring):
    document, _ = _signed_valid_chain_document()
    document_path, keyring_path = _write_inputs(tmp_path, document, keyring)

    result = main(["--document", str(document_path), "--keyring-file", str(keyring_path)])

    assert result == 2
    assert "32-4096 characters" in capsys.readouterr().err


def test_cli_rejects_non_regular_export_input(tmp_path, capsys):
    document, keyring = _signed_valid_chain_document()
    keyring_path = tmp_path / "keyring.json"
    keyring_path.write_text(json.dumps(keyring), encoding="utf-8")
    if os.name == "posix":
        keyring_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    directory_path = tmp_path / "export-directory"
    directory_path.mkdir()

    result = main(["--document", str(directory_path), "--keyring-file", str(keyring_path)])

    assert result == 2
    assert "regular file" in capsys.readouterr().err


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="named pipes are unavailable")
def test_cli_rejects_fifo_input_without_blocking(tmp_path, capsys):
    _, keyring = _signed_valid_chain_document()
    keyring_path = tmp_path / "keyring.json"
    keyring_path.write_text(json.dumps(keyring), encoding="utf-8")
    keyring_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    fifo_path = tmp_path / "export-pipe"
    os.mkfifo(fifo_path)

    result = main(["--document", str(fifo_path), "--keyring-file", str(keyring_path)])

    assert result == 2
    assert "regular file" in capsys.readouterr().err
