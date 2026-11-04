"""Configure the local SMTP password privately, without putting it in chat."""
import getpass
import os
from pathlib import Path
import smtplib
import ssl

from dotenv import load_dotenv, set_key

ROOT = Path(__file__).resolve().parents[1]


def main():
    load_dotenv(ROOT / ".env")
    sender = os.getenv("SMTP_USER", "").strip()
    if not sender:
        sender = input("Sender email address: ").strip()
    if not sender or "@" not in sender or any(c in sender for c in "\r\n"):
        raise SystemExit("A valid sender email address is required.")
    print(f"Configuring email for {sender}. No email will be sent by this setup check.")
    password = getpass.getpass("SMTP app password (hidden): ")
    if not password.strip():
        raise SystemExit("No password entered; settings unchanged.")
    host, port = os.getenv("SMTP_HOST", "smtp.gmail.com"), int(os.getenv("SMTP_PORT", "587"))
    if host == "smtp.gmail.com":
        password = password.replace(" ", "")
    try:
        with smtplib.SMTP(host, port, timeout=20) as server:
            server.starttls(context=ssl.create_default_context())
            server.login(sender, password)
    except (OSError, smtplib.SMTPException) as exc:
        raise SystemExit(f"SMTP login failed ({type(exc).__name__}); settings unchanged.") from None
    set_key(str(ROOT / ".env"), "SMTP_USER", sender)
    set_key(str(ROOT / ".env"), "SMTP_PASS", password)
    print("SMTP login verified and saved locally. Restart ScorPred, then use Email these props.")


if __name__ == "__main__":
    main()
