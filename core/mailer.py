"""E-posta gonderimi (SMTP).

Yapilandirilmamissa SESSIZCE devre disidir: gonderim simule edilir ve cagiran
bunu `sent=False` olarak gorur. Asistanin "gonderdim" demesi buna baglidir —
gonderilemeyen bir seyi gonderdim demek, hic gondermemekten kotudur.

Varsayilan KAPALI (EMAIL_ENABLED=0): anahtar konmadan yanlislikla posta cikmaz.
"""

from __future__ import annotations

import os
import re
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Optional

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(frozen=True)
class MailResult:
    sent: bool
    reason: str = ""          # gonderilmediyse NEDEN (loglanir, musteriye denmez)
    target: str = ""


class Mailer:
    """Basit SMTP gondericisi.

    Kurulum (.env):
        EMAIL_ENABLED=1
        EMAIL_SMTP_HOST=smtp.gmail.com
        EMAIL_SMTP_PORT=587
        EMAIL_USER=...@gmail.com
        EMAIL_PASSWORD=<uygulama parolasi>     # normal hesap parolasi DEGIL
        EMAIL_FROM_NAME=Moka United
    """

    def __init__(self):
        self.enabled = os.getenv("EMAIL_ENABLED", "0").strip() in ("1", "true", "True")
        self.host = os.getenv("EMAIL_SMTP_HOST", "smtp.gmail.com").strip()
        self.port = self._int_env("EMAIL_SMTP_PORT", 587)
        self.user = os.getenv("EMAIL_USER", "").strip()
        self.password = os.getenv("EMAIL_PASSWORD", "").strip()
        self.from_name = os.getenv("EMAIL_FROM_NAME", "Moka United").strip()
        self.timeout = self._int_env("EMAIL_TIMEOUT_S", 20)

    @staticmethod
    def _int_env(name: str, default: int) -> int:
        try:
            return int(os.getenv(name, str(default)))
        except ValueError:
            return default

    def is_configured(self) -> bool:
        return bool(self.enabled and self.host and self.user and self.password)

    def send(self, to_address: str, subject: str, body: str) -> MailResult:
        address = (to_address or "").strip()

        if not self.is_configured():
            return MailResult(False, "e-posta yapilandirilmamis", address)
        if not _EMAIL_RE.match(address):
            return MailResult(False, "gecersiz adres", address)

        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = f"{self.from_name} <{self.user}>" if self.from_name else self.user
        message["To"] = address
        message.set_content(body)

        try:
            context = ssl.create_default_context()
            if self.port == 465:
                with smtplib.SMTP_SSL(self.host, self.port, timeout=self.timeout,
                                      context=context) as server:
                    server.login(self.user, self.password)
                    server.send_message(message)
            else:
                with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as server:
                    server.starttls(context=context)
                    server.login(self.user, self.password)
                    server.send_message(message)
        except Exception as error:
            # Gonderilemedi: cagiran bunu BILMELI ki asistan "gonderdim" demesin.
            print(f"E-posta gonderilemedi ({address}): {error}")
            return MailResult(False, str(error), address)

        return MailResult(True, "", address)


if __name__ == "__main__":
    # Kurulum dogrulamasi:  python3 -m core.mailer kendi@adresin.com
    # Demo oncesi BIR KEZ calistirin — anahtarlar dogru mu, posta gidiyor mu.
    import sys
    from dotenv import load_dotenv

    load_dotenv()
    target = sys.argv[1] if len(sys.argv) > 1 else ""
    mailer = Mailer()

    if not target:
        print("Kullanim: python3 -m core.mailer kendi@adresin.com")
        raise SystemExit(2)
    if not mailer.is_configured():
        print("E-posta KAPALI. .env'de EMAIL_ENABLED=1, EMAIL_USER ve "
              "EMAIL_PASSWORD (Gmail uygulama parolasi) doldurulmali.")
        raise SystemExit(1)

    outcome = mailer.send(target, "Moka Sesli Asistan — test",
                          "Bu bir kurulum testidir. Bunu gorduyseniz "
                          "e-posta gonderimi calisiyor demektir.")
    if outcome.sent:
        print(f"Gonderildi -> {outcome.target}  (gelen kutusunu ve spam'i kontrol edin)")
        raise SystemExit(0)
    print(f"GONDERILEMEDI: {outcome.reason}")
    raise SystemExit(1)
