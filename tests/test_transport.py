"""Transport layer: failure diagnosis (certificate issuer, DNS-over-HTTPS), proxy scope, CA bundle, headers.

No live network: sockets, DoH and settings are faked.
"""
from doi_hunter_web import transport


def test_tls_failure_names_the_web_filter(monkeypatch):
    monkeypatch.setattr(transport, "peek_certificate", lambda host, port=443, timeout=6: {
        "subject": "CN=FortiClient", "issuer": "CN=support,OU=Certificate Authority,O=Fortinet",
        "not_after": "2038-01-19", "self_signed": False, "expired": False})
    monkeypatch.setattr(transport, "_block_page_title", lambda url: "UNIGE - Site bloqué")
    text = transport.describe_tls_failure("https://sci-hub.fr/x")
    assert "FortiClient web filter" in text and "Site bloqué" in text and "ask IT" in text


def test_tls_failure_self_signed_expired_and_unknown(monkeypatch):
    monkeypatch.setattr(transport, "peek_certificate", lambda host, port=443, timeout=6: {
        "subject": "O=ddos-guard,ST=*,C=EU", "issuer": "O=ddos-guard,ST=*,C=EU", "not_after": "2028-03-25",
        "self_signed": True, "expired": False})
    assert "DDoS-Guard" in transport.describe_tls_failure("https://sci-hub.st/x")
    monkeypatch.setattr(transport, "peek_certificate", lambda host, port=443, timeout=6: {
        "subject": "CN=site", "issuer": "CN=Some CA", "not_after": "2020-01-01", "self_signed": False, "expired": True})
    assert "expired on 2020-01-01" in transport.describe_tls_failure("https://old.example/x")
    monkeypatch.setattr(transport, "peek_certificate", lambda host, port=443, timeout=6: None)
    assert "handshake refused" in transport.describe_tls_failure("https://gone.example/x")


def test_dns_failure_distinguishes_dead_and_filtered(monkeypatch):
    monkeypatch.setattr(transport, "doh_resolves", lambda host: False)
    assert "no longer exists" in transport.describe_dns_failure("https://sci-hub.se/x")
    monkeypatch.setattr(transport, "doh_resolves", lambda host: True)
    assert "blocked by your DNS resolver" in transport.describe_dns_failure("https://annas-archive.li/x")
    monkeypatch.setattr(transport, "doh_resolves", lambda host: None)
    assert "could not check" in transport.describe_dns_failure("https://x.example/x")


def test_describe_failure_by_kind(monkeypatch):
    monkeypatch.setattr(transport, "describe_tls_failure", lambda url: "TLS!")
    monkeypatch.setattr(transport, "describe_dns_failure", lambda url: "DNS!")
    assert transport.describe_failure(transport.TransportError("tls", ""), "https://a") == "TLS!"
    assert transport.describe_failure(transport.TransportError("dns", ""), "https://a") == "DNS!"
    assert "20 s" in transport.describe_failure(transport.TransportError("timeout", ""), "https://a", 20)
    assert "proxy" in transport.describe_failure(transport.TransportError("proxy", ""), "https://a")
    assert "refused" in transport.describe_failure(transport.TransportError("connect", ""), "https://a")
    assert "boom" in transport.describe_failure(transport.TransportError("other", "boom"), "https://a")


def test_proxy_scope(monkeypatch):
    monkeypatch.delenv("REVIEWHUNTER_PROXY", raising=False)
    monkeypatch.delenv("REVIEWHUNTER_PROXY_SCOPE", raising=False)
    monkeypatch.setattr(transport, "_settings", lambda: {"network_proxy": "socks5h://127.0.0.1:9150", "proxy_scope": "shadow"})
    assert transport.proxy_for(shadow=True) == "socks5h://127.0.0.1:9150"
    assert transport.proxy_for(shadow=False) is None
    monkeypatch.setattr(transport, "_settings", lambda: {"network_proxy": "http://p:3128", "proxy_scope": "all"})
    assert transport.proxy_for(shadow=False) == "http://p:3128"
    monkeypatch.setattr(transport, "_settings", lambda: {"network_proxy": ""})
    assert transport.proxy_for(shadow=True) is None
    monkeypatch.setenv("REVIEWHUNTER_PROXY", "http://env:1")
    assert transport.proxy_for(shadow=True) == "http://env:1"


def test_ca_bundle_merges_system_store(library, monkeypatch):
    import certifi

    monkeypatch.setattr(transport, "_settings", lambda: {"use_system_certificates": False})
    assert transport.ca_bundle() == certifi.where()
    monkeypatch.setattr(transport, "_settings", lambda: {"use_system_certificates": True})
    monkeypatch.setattr(transport, "_os_store_certificates", lambda: [b"-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n"])
    path = transport.ca_bundle()
    assert path != certifi.where() and path.endswith("ca-bundle-system.pem")
    data = open(path, "rb").read()
    assert data.startswith(open(certifi.where(), "rb").read()[:64]) and b"AAAA" in data
    assert transport.ca_bundle() == path  # cached


def test_browser_headers_and_pdf_detection():
    h = transport.browser_headers(referer="https://sci-hub.ren/x", accept="application/pdf")
    assert h["Referer"] == "https://sci-hub.ren/x" and h["Accept"] == "application/pdf" and "Chrome/" in h["User-Agent"]
    assert transport.is_pdf_content(b"%PDF-1.4 ...")
    assert transport.is_pdf_content(b"<html>", "application/pdf")
    assert transport.is_pdf_content(b"<html>", "text/html", 'attachment; filename="x.pdf"')
    assert not transport.is_pdf_content(b"<html>", "text/html")
    r = transport.Response(url="https://x", status_code=200, headers={"content-type": "text/html; charset=iso-8859-1"}, content="caf\xe9".encode("latin-1"))
    assert r.text == "café" and r.ok and r.header("Content-Type").startswith("text/html")


def test_tls_failure_after_redirect_names_the_final_host(monkeypatch):
    seen = []

    def fake_peek(host, port=443, timeout=6):
        seen.append(host)
        return {"subject": "CN=FortiClient", "issuer": "O=Fortinet", "not_after": "2038-01-19", "self_signed": False, "expired": False}

    monkeypatch.setattr(transport, "peek_certificate", fake_peek)
    monkeypatch.setattr(transport, "_block_page_title", lambda url: "")
    err = transport.TransportError("tls", "curl: (60) SSL: certificate subject name 'FortiClient' does not match target hostname 'sci-hub.fr'")
    text = transport.describe_failure(err, "https://sci-hub.ru/10.1/x")
    assert seen == ["sci-hub.fr"] and text.startswith("redirects to sci-hub.fr, which is blocked by the FortiClient")
