from cryptography.fernet import Fernet
from django.test import TestCase, override_settings

from core.ghl_crypto import TokenCryptoError, decrypt_token, encrypt_token

KEY = Fernet.generate_key().decode()


@override_settings(GHL_TOKEN_ENCRYPTION_KEY=KEY)
class TokenCryptoTests(TestCase):
    def test_round_trip(self):
        self.assertEqual(decrypt_token(encrypt_token("secret-abc")), "secret-abc")

    def test_ciphertext_is_not_plaintext(self):
        self.assertNotEqual(encrypt_token("secret-abc"), "secret-abc")

    def test_empty_values(self):
        self.assertEqual(encrypt_token(""), "")
        self.assertEqual(decrypt_token(""), "")


@override_settings(GHL_TOKEN_ENCRYPTION_KEY="")
class TokenCryptoMissingKeyTests(TestCase):
    def test_encrypt_fails_closed_without_key(self):
        with self.assertRaises(TokenCryptoError):
            encrypt_token("x")
