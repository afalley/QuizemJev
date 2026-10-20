import os
import smtplib
from email.message import EmailMessage


def send_email(to_email: str, subject: str, body: str) -> bool:
    """
    Send an email using SMTP credentials from environment variables.
    Falls back to printing to console if SMTP is not configured.

    Env vars:
    - SMTP_HOST
    - SMTP_PORT (default 587)
    - SMTP_USER
    - SMTP_PASS
    - SMTP_USE_TLS (default true)
    - FROM_EMAIL (optional; defaults to SMTP_USER)
    """
    host = os.environ.get('SMTP_HOST')
    user = os.environ.get('SMTP_USER')
    password = os.environ.get('SMTP_PASS')
    port = int(os.environ.get('SMTP_PORT', '587'))
    use_tls = os.environ.get('SMTP_USE_TLS', 'true').lower() != 'false'
    from_email = os.environ.get('FROM_EMAIL') or user or 'no-reply@example.com'

    if not host or not user or not password:
        # Fallback: log to console
        print('--- EMAIL (console fallback) ---')
        print('To:', to_email)
        print('Subject:', subject)
        print(body)
        print('--- END EMAIL ---')
        return False

    msg = EmailMessage()
    msg['From'] = from_email
    msg['To'] = to_email
    msg['Subject'] = subject
    msg.set_content(body)

    try:
        with smtplib.SMTP(host, port, timeout=15) as server:
            if use_tls:
                server.starttls()
            server.login(user, password)
            server.send_message(msg)
        return True
    except Exception as e:
        print('Failed to send email:', e)
        # Fallback to console output to ensure teacher still gets info in logs
        print('--- EMAIL (fallback after SMTP error) ---')
        print('To:', to_email)
        print('Subject:', subject)
        print(body)
        print('--- END EMAIL ---')
        return False
