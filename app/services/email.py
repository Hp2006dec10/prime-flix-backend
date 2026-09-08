import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import aiosmtplib
from app.core.config import settings

logger = logging.getLogger("uvicorn.error")


async def send_otp_email(to_email: str, otp: str, purpose: str = "registration") -> bool:
    """
    Sends OTP email to user using aiosmtplib.
    Logs OTP to console for development/testing ease.
    """
    subject = "Your PrimeFlix Verification Code" if purpose == "registration" else "Your PrimeFlix Password Reset Code"
    heading = "Verify Your Account" if purpose == "registration" else "Reset Your Password"
    action_text = "use the verification code below to complete your registration" if purpose == "registration" else "use the code below to reset your password"

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #141414; color: #ffffff; margin: 0; padding: 0; }}
            .container {{ max-width: 500px; margin: 40px auto; background-color: #1f1f1f; border-radius: 8px; overflow: hidden; box-shadow: 0 4px 15px rgba(0,0,0,0.5); }}
            .header {{ background-color: #e50914; padding: 20px; text-align: center; font-size: 24px; font-weight: bold; letter-spacing: 1px; color: #ffffff; }}
            .content {{ padding: 30px; text-align: center; }}
            .otp-box {{ background-color: #2b2b2b; border: 1px solid #e50914; border-radius: 6px; font-size: 32px; font-weight: bold; letter-spacing: 6px; color: #ffffff; padding: 15px 20px; margin: 20px 0; display: inline-block; }}
            .footer {{ background-color: #141414; padding: 15px; text-align: center; font-size: 12px; color: #808080; border-top: 1px solid #2b2b2b; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">PRIMEFLIX</div>
            <div class="content">
                <h2>{heading}</h2>
                <p>Hello,</p>
                <p>Please {action_text}. This code is valid for <strong>10 minutes</strong>.</p>
                <div class="otp-box">{otp}</div>
                <p style="color: #a0a0a0; font-size: 14px;">If you did not request this, please ignore this email.</p>
            </div>
            <div class="footer">
                &copy; {2026} PrimeFlix. All rights reserved.
            </div>
        </div>
    </body>
    </html>
    """

    plain_text = f"PrimeFlix Code: {otp}. Valid for 10 minutes."

    # Always log OTP to console for local testing visibility
    logger.info(f"========== [OTP SENT] Purpose: {purpose} | To: {to_email} | OTP: {otp} ==========")

    if not settings.SMTP_USER or not settings.SMTP_HOST:
        logger.warning("SMTP credentials not fully configured in .env. Skipping actual SMTP email dispatch.")
        return True

    msg = MIMEMultipart("alternative")
    msg["From"] = settings.SMTP_FROM_EMAIL or settings.SMTP_USER
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(plain_text, "plain"))
    msg.attach(MIMEText(html_content, "html"))

    try:
        smtp = aiosmtplib.SMTP(
            hostname=settings.SMTP_HOST,
            port=settings.SMTP_PORT,
            start_tls=not settings.SMTP_SECURE,
            use_tls=settings.SMTP_SECURE,
            timeout=10
        )
        await smtp.connect()
        if settings.SMTP_USER and settings.SMTP_PASS:
            await smtp.login(settings.SMTP_USER, settings.SMTP_PASS)
        await smtp.send_message(msg)
        await smtp.quit()
        logger.info(f"Successfully sent OTP email to {to_email}")
        return True
    except Exception as e:
        logger.error(f"Failed to send email via SMTP to {to_email}: {e}")
        return False
