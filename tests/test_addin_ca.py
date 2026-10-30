"""The Word add-in's local CA can only vouch for localhost, even if its key leaked (X.509 name constraints).

Verified with a real TLS handshake (OpenSSL through Python's ssl module): the localhost certificate is
accepted, a certificate for a real website forged with the same CA key is rejected.
"""
import datetime as dt
import socket
import ssl
import threading

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def _serve_once(cert_file, key_file):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert_file, key_file)
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def run():
        try:
            conn, _ = srv.accept()
            with ctx.wrap_socket(conn, server_side=True) as s:
                s.recv(1)
        except (OSError, ssl.SSLError):
            pass
        finally:
            srv.close()

    threading.Thread(target=run, daemon=True).start()
    return srv.getsockname()[1]


def _handshake(port, ca_file, server_name):
    ctx = ssl.create_default_context(cafile=str(ca_file))
    ctx.check_hostname = False          # isolate chain validation (name constraints), not hostname matching
    with socket.create_connection(("127.0.0.1", port), timeout=5) as raw:
        with ctx.wrap_socket(raw, server_hostname=server_name) as s:
            s.sendall(b"x")


def test_ca_is_constrained_to_localhost(library, tmp_path):
    from doi_hunter_web import addin

    p = addin.ensure_certificates()
    ca = x509.load_pem_x509_certificate(p["ca_cert"].read_bytes())
    nc = ca.extensions.get_extension_for_class(x509.NameConstraints)
    assert nc.critical and x509.DNSName("localhost") in nc.value.permitted_subtrees

    # 1. the real localhost certificate is accepted
    _handshake(_serve_once(str(p["cert"]), str(p["key"])), p["ca_cert"], "localhost")

    # 2. a certificate for a real site, forged with the CA key, is refused by the chain validation
    ca_key = serialization.load_pem_private_key(p["ca_key"].read_bytes(), password=None)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = dt.datetime.now(dt.timezone.utc)
    forged = (x509.CertificateBuilder()
              .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "www.example.com")]))
              .issuer_name(ca.subject).public_key(key.public_key()).serial_number(x509.random_serial_number())
              .not_valid_before(now - dt.timedelta(minutes=5)).not_valid_after(now + dt.timedelta(days=30))
              .add_extension(x509.SubjectAlternativeName([x509.DNSName("www.example.com")]), critical=False)
              .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
              # Otherwise flawless (same extensions as the real leaf): only the name constraint may reject it.
              .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
              .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
              .sign(ca_key, hashes.SHA256()))
    cert_file, key_file = tmp_path / "forged.crt", tmp_path / "forged.key"
    cert_file.write_bytes(forged.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    port = _serve_once(str(cert_file), str(key_file))
    try:
        _handshake(port, p["ca_cert"], "www.example.com")
        raise AssertionError("a certificate for www.example.com signed by the local CA was accepted")
    except ssl.SSLCertVerificationError as e:
        assert "constraint" in str(e).lower() or "permitted" in str(e).lower(), e


def test_unconstrained_ca_from_older_versions_is_replaced(library):
    from doi_hunter_web import addin

    p = addin.ensure_certificates()
    first = p["ca_cert"].read_bytes()
    # Simulate a pre-2.11 CA: same paths, no NameConstraints extension.
    key = serialization.load_pem_private_key(p["ca_key"].read_bytes(), password=None)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, addin.CA_NAME)])
    now = dt.datetime.now(dt.timezone.utc)
    old = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
           .serial_number(x509.random_serial_number()).not_valid_before(now).not_valid_after(now + dt.timedelta(days=3000))
           .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True).sign(key, hashes.SHA256()))
    p["ca_cert"].write_bytes(old.public_bytes(serialization.Encoding.PEM))
    addin.ensure_certificates()
    new = x509.load_pem_x509_certificate(p["ca_cert"].read_bytes())
    assert new.extensions.get_extension_for_class(x509.NameConstraints)
    assert p["ca_cert"].read_bytes() not in (first, old.public_bytes(serialization.Encoding.PEM))
