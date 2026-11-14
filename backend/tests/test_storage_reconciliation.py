from app.reconcile_storage import reconcile


def test_reconciliation_reports_orphans_and_missing_objects(monkeypatch):
    referenced = {"tenant-a/" + "a" * 32 + ".pdf", "tenant-a/" + "b" * 32 + ".txt"}
    stored = {"tenant-a/" + "a" * 32 + ".pdf", "tenant-b/" + "c" * 32 + ".txt"}
    monkeypatch.setattr("app.reconcile_storage.SessionLocal", lambda: _Session(referenced))
    monkeypatch.setattr("app.reconcile_storage.list_document_keys", lambda: stored)

    orphans, missing, deleted = reconcile()

    assert orphans == {"tenant-b/" + "c" * 32 + ".txt"}
    assert missing == {"tenant-a/" + "b" * 32 + ".txt"}
    assert deleted == set()


def test_reconciliation_deletes_only_reported_orphans(monkeypatch):
    orphan = "tenant-b/" + "c" * 32 + ".txt"
    deleted = []
    monkeypatch.setattr("app.reconcile_storage.SessionLocal", lambda: _Session(set()))
    monkeypatch.setattr("app.reconcile_storage.list_document_keys", lambda: {orphan})
    monkeypatch.setattr("app.reconcile_storage.delete_document", lambda key, tenant: deleted.append((key, tenant)))

    _, _, deleted_keys = reconcile(delete=True)

    assert deleted_keys == {orphan}
    assert deleted == [(orphan, "tenant-b")]


class _Session:
    def __init__(self, keys):
        self.keys = keys

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def scalars(self, statement):
        return _Result(self.keys)


class _Result:
    def __init__(self, keys):
        self.keys = keys

    def all(self):
        return list(self.keys)
