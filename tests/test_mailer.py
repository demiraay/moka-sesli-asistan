"""Gercek e-posta gonderimi (SMTP).

Iki mod var ve ayrimi onemli:
  EMAIL_ENABLED=0  -> gonderim SIMULE (prototipin geri kalani gibi)
  EMAIL_ENABLED=1  -> GERCEK gonderim; basarisizlik GIZLENMEZ, cunku artik
                      musteriye gercek bir vaatte bulunuluyor.
"""

import os
import smtplib
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import tools
from core.mailer import Mailer
from core.schemas import ResponseBuilder
from core.tools.context import ToolContext


def _mail_env(**overrides):
    env = {
        "EMAIL_ENABLED": "1",
        "EMAIL_SMTP_HOST": "smtp.example.com",
        "EMAIL_SMTP_PORT": "587",
        "EMAIL_USER": "demo@example.com",
        "EMAIL_PASSWORD": "app-password",
    }
    env.update(overrides)
    return env


class TestMailerConfiguration(unittest.TestCase):
    def test_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(Mailer().is_configured())

    def test_enabled_flag_alone_is_not_enough(self):
        with patch.dict(os.environ, {"EMAIL_ENABLED": "1"}, clear=True):
            self.assertFalse(Mailer().is_configured(), "kullanici/parola olmadan gonderilmemeli")

    def test_fully_configured(self):
        with patch.dict(os.environ, _mail_env(), clear=True):
            self.assertTrue(Mailer().is_configured())

    def test_invalid_address_is_rejected(self):
        with patch.dict(os.environ, _mail_env(), clear=True):
            result = Mailer().send("bu-adres-degil", "konu", "govde")
        self.assertFalse(result.sent)
        self.assertIn("gecersiz", result.reason)


class TestMailerSending(unittest.TestCase):
    def test_starttls_path_sends_the_message(self):
        with patch.dict(os.environ, _mail_env(), clear=True):
            with patch("smtplib.SMTP") as smtp:
                server = smtp.return_value.__enter__.return_value
                result = Mailer().send("kime@ornek.com", "Ekstre", "govde")

        self.assertTrue(result.sent)
        server.starttls.assert_called_once()
        server.login.assert_called_once_with("demo@example.com", "app-password")
        message = server.send_message.call_args[0][0]
        self.assertEqual(message["To"], "kime@ornek.com")
        self.assertEqual(message["Subject"], "Ekstre")

    def test_ssl_port_uses_smtp_ssl(self):
        with patch.dict(os.environ, _mail_env(EMAIL_SMTP_PORT="465"), clear=True):
            with patch("smtplib.SMTP_SSL") as smtp_ssl:
                result = Mailer().send("kime@ornek.com", "k", "g")
        self.assertTrue(result.sent)
        smtp_ssl.assert_called_once()

    def test_smtp_failure_is_reported_not_swallowed(self):
        with patch.dict(os.environ, _mail_env(), clear=True):
            with patch("smtplib.SMTP", side_effect=smtplib.SMTPAuthenticationError(535, b"bad")):
                result = Mailer().send("kime@ornek.com", "k", "g")
        self.assertFalse(result.sent)
        self.assertTrue(result.reason)


class TestStatementEmailIntegration(unittest.TestCase):
    """send_statement araci gonderim sonucunu DURUSTCE yansitmali."""

    def _context(self):
        repo = MagicMock()
        repo.monthly_summary.return_value = {
            "month": "2026-07", "gross_try": 295000, "commission_try": 5870,
            "rate_pct": 1.99, "plan_name": "Esnaf Plus", "txn_count": 20}
        return ToolContext(
            repo=repo, store=MagicMock(), builder=ResponseBuilder(),
            merchant={"merchant_id": "M-TEST", "business_name": "Ekinci Kahve",
                      "owner_name": "Muhammed Ekinci", "email": "kayitli@ornek.com"},
            user_id="u1", user_profile={}, config=MagicMock())

    def _send(self, args, mailer):
        ctx = self._context()
        with patch("core.tools.handlers._MAILER", mailer):
            result = tools.REGISTRY["send_statement"].fn(ctx, args)
        return result, " ".join(ctx.builder.build()["message_facts"])

    def test_custom_address_is_used(self):
        """'Muhasebeye at, x@y.com' -> o adrese gider."""
        mailer = MagicMock()
        mailer.is_configured.return_value = True
        mailer.send.return_value = MagicMock(sent=True, reason="", target="muhasebe@firma.com")

        result, facts = self._send(
            {"channel": "email", "to_email": "muhasebe@firma.com"}, mailer)

        self.assertEqual(mailer.send.call_args[0][0], "muhasebe@firma.com")
        self.assertIn("GONDERILDI", result)
        self.assertIn("muhasebe@firma.com", facts)

    def test_registered_address_is_used_when_none_given(self):
        mailer = MagicMock()
        mailer.is_configured.return_value = True
        mailer.send.return_value = MagicMock(sent=True, reason="", target="kayitli@ornek.com")

        self._send({"channel": "email"}, mailer)
        self.assertEqual(mailer.send.call_args[0][0], "kayitli@ornek.com")

    def test_failed_send_must_not_claim_success(self):
        """EN ONEMLI: gonderilemediyse asistan 'gonderdim' DEMEMELI."""
        mailer = MagicMock()
        mailer.is_configured.return_value = True
        mailer.send.return_value = MagicMock(sent=False, reason="smtp reddetti",
                                             target="x@y.com")

        result, facts = self._send({"channel": "email", "to_email": "x@y.com"}, mailer)

        self.assertIn("GONDERILEMEDI", result)
        self.assertIn("gonderildigini soyleme", result.lower())
        self.assertIn("GÖNDERİLEMEDİ", facts)
        self.assertIn("SÖYLEME", facts)

    def test_simulated_mode_when_smtp_is_off(self):
        """SMTP kapaliyken (demo varsayilani) gonderim simule edilir."""
        mailer = MagicMock()
        mailer.is_configured.return_value = False

        result, facts = self._send({"channel": "email"}, mailer)

        mailer.send.assert_not_called()
        self.assertNotIn("GONDERILEMEDI", result)
        self.assertIn("gönderildi", facts)

    def test_email_body_contains_the_real_numbers(self):
        mailer = MagicMock()
        mailer.is_configured.return_value = True
        mailer.send.return_value = MagicMock(sent=True, reason="", target="x@y.com")

        self._send({"channel": "email", "to_email": "x@y.com"}, mailer)
        body = mailer.send.call_args.kwargs["body"]
        self.assertIn("Muhammed Ekinci", body)
        self.assertIn("2026-07", body)
        self.assertIn("1,99", body.replace(".", ","))

    def test_sms_channel_never_touches_the_mailer(self):
        mailer = MagicMock()
        mailer.is_configured.return_value = True
        self._send({"channel": "sms"}, mailer)
        mailer.send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
