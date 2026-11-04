"""Email alerter with rate limiting."""
import logging
import os
import smtplib
import threading
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText

_log = logging.getLogger("alerter")


class EmailAlerter:
    def __init__(self):
        self.to_addr   = os.getenv("ALERT_EMAIL", "")
        self.smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
        self.smtp_port = int(os.getenv("SMTP_PORT", "587"))
        self.smtp_user = os.getenv("SMTP_USER", "")
        self.smtp_pass = os.getenv("SMTP_PASS", "")
        self.use_tls   = os.getenv("SMTP_USE_TLS", "true").lower() == "true"
        self._lock     = threading.Lock()
        self._last_sent: dict[str, datetime] = {}
        self._rate_window = timedelta(minutes=30)
        self._enabled = bool(self.to_addr and self.smtp_user and self.smtp_pass)
        if not self._enabled:
            _log.info("Email alerter disabled (ALERT_EMAIL/SMTP_USER/SMTP_PASS not set)")

    def send_alert(
        self,
        subject: str,
        body: str,
        severity: str = "warning",
        rate_key: str | None = None,
    ) -> None:
        if not self._enabled:
            return
        if rate_key:
            with self._lock:
                last = self._last_sent.get(rate_key)
                if last and (datetime.now(timezone.utc) - last) < self._rate_window:
                    return
                self._last_sent[rate_key] = datetime.now(timezone.utc)
        threading.Thread(target=self._send, args=(subject, body), daemon=True).start()

    def _send(self, subject: str, body: str) -> None:
        try:
            msg = MIMEText(body)
            msg["Subject"] = subject
            msg["From"]    = self.smtp_user
            msg["To"]      = self.to_addr
            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=10) as server:
                if self.use_tls:
                    server.starttls()
                server.login(self.smtp_user, self.smtp_pass)
                server.sendmail(self.smtp_user, self.to_addr, msg.as_string())
            _log.info("Alert email sent: %s", subject)
        except Exception as exc:
            _log.warning("Email send failed: %s", exc)
