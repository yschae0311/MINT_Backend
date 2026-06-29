import ssl
import unittest

from app.search.es_client import build_es_ssl_context


class EsSslContextTests(unittest.TestCase):
    def test_verify_mode_disabled_when_not_verifying(self) -> None:
        ctx = build_es_ssl_context(verify_certs=False)
        self.assertEqual(ctx.verify_mode, ssl.CERT_NONE)
        self.assertFalse(ctx.check_hostname)

    def test_clears_strict_flag_when_verifying(self) -> None:
        strict = getattr(ssl, "VERIFY_X509_STRICT", 0)
        if not strict:
            self.skipTest("VERIFY_X509_STRICT not available on this Python build")

        ctx = build_es_ssl_context(verify_certs=True)
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
        self.assertEqual(ctx.verify_flags & strict, 0)


if __name__ == "__main__":
    unittest.main()
