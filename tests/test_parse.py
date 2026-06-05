from app.mail_parse import html_to_text, make_snippet, normalize_whitespace, parse_email


def test_parse_plain(sample_eml):
    msg = parse_email(sample_eml)
    assert msg.from_addr == "notices@insurance.example.com"
    assert msg.to_addrs == ["user@proton.me"]
    assert msg.subject == "Your insurance renewal notice"
    assert msg.date_utc == "2026-06-03T10:12:00Z"
    assert "policy renewal" in msg.body_text
    assert msg.has_attachments is False


def test_parse_html_strips_scripts(html_eml):
    msg = parse_email(html_eml)
    assert "alert" not in msg.body_text
    assert "ignore me" not in msg.body_text
    assert "shipping" in msg.body_text
    # link target preserved
    assert "track.example.com" in msg.body_text


def test_html_to_text_strips_style():
    text = html_to_text("<style>.x{}</style><p>hello <b>world</b></p>")
    assert "hello" in text and "world" in text
    assert ".x" not in text


def test_normalize_whitespace():
    assert normalize_whitespace("a\r\n\r\n\r\nb   c") == "a\n\nb c"


def test_snippet_truncates():
    s = make_snippet("x" * 500, limit=100)
    assert len(s) <= 101
    assert s.endswith("…")
