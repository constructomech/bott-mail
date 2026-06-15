from app.imap_client import ImapClient, ImapMutationError


class FakeListClient:
    def __init__(self, names):
        self._names = names

    def list_folders(self):
        return [((), "/", name) for name in self._names]


def test_resolve_label_mailbox_accepts_exact_or_common_prefix():
    assert ImapClient._resolve_label_mailbox(
        FakeListClient(["INBOX", "Labels/Political"]), "Political"
    ) == "Labels/Political"
    assert ImapClient._resolve_label_mailbox(
        FakeListClient(["INBOX", "travel"]), "Travel"
    ) == "travel"


def test_resolve_label_mailbox_reports_available_names():
    try:
        ImapClient._resolve_label_mailbox(FakeListClient(["INBOX"]), "Political")
    except ImapMutationError as exc:
        text = str(exc)
        assert "Political" in text
        assert "INBOX" in text
    else:
        raise AssertionError("expected label lookup to fail")
