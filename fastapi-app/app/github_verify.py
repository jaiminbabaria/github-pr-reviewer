# Validates the X-Hub-Signature-256 header GitHub sends with every webhook.
#
# GitHub hashes the raw request body with our webhook secret (HMAC-SHA256)
# and sends it as "sha256=<hex>". We just redo the same hash and compare.
# Has to be the raw bytes - if you json.loads() then re-dump it before
# hashing, the signature won't match anymore.
import hashlib
import hmac


def verify_signature(payload_body: bytes, secret: str, signature_header: str | None) -> bool:
    # payload_body must be the raw request bytes, not parsed/re-serialized JSON
    if not signature_header:
        return False
    if not signature_header.startswith("sha256="):
        return False

    expected = hmac.new(
        key=secret.encode("utf-8"),
        msg=payload_body,
        digestmod=hashlib.sha256,
    ).hexdigest()

    received = signature_header.split("=", 1)[1]
    # compare_digest is constant-time, preventing timing attacks.
    return hmac.compare_digest(expected, received)
