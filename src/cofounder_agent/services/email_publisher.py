"""
Email Publishing Service

Handles publishing content via email using SMTP.

Requirements:
- Email server (Gmail, SendGrid, Custom SMTP)
- Environment variables:
  - EMAIL_FROM: Sender email address
  - SMTP_HOST: SMTP server hostname
  - SMTP_PORT: SMTP server port (usually 587 for TLS or 465 for SSL)
  - SMTP_USER: SMTP authentication username
  - SMTP_PASSWORD: SMTP authentication password (use app password for Gmail)
  - SMTP_USE_TLS: true/false (default: true)
"""

import logging
import os
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, List, Optional, cast

import aiosmtplib
import html2text

logger = logging.getLogger(__name__)


class EmailPublisher:
    """Email content publisher"""

    def __init__(self):
        """Initialize email publisher from environment variables"""
        self.smtp_host = os.getenv("SMTP_HOST")
        self.smtp_port = int(os.getenv("SMTP_PORT", "587"))
        self.smtp_user = os.getenv("SMTP_USER")
        self.smtp_password = os.getenv("SMTP_PASSWORD")
        self.email_from = os.getenv("EMAIL_FROM", self.smtp_user)
        self.use_tls = os.getenv("SMTP_USE_TLS", "true").lower() == "true"

        if not all([self.smtp_host, self.smtp_user, self.smtp_password, self.email_from]):
            logger.warning(
                "⚠️  Email not fully configured. Set SMTP_HOST, SMTP_USER, SMTP_PASSWORD, EMAIL_FROM"
            )
            self.available = False
        else:
            self.available = True
            logger.info(f"✅ Email publisher initialized ({self.smtp_host}:{self.smtp_port})")

    async def publish(
        self,
        subject: str,
        content: str,
        recipient_emails: List[str],
        html_content: Optional[str] = None,
        from_name: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Publish content via email.

        Args:
            subject: Email subject line
            content: Email body (plain text)
            recipient_emails: List of recipient email addresses
            html_content: Optional HTML version of content
            from_name: Optional sender display name
            **kwargs: Additional metadata

        Returns:
            Dictionary with email send result:
            {
                "success": bool,
                "recipients": int,
                "message_id": str,
                "error": str (if failed)
            }
        """
        if not self.available:
            return {
                "success": False,
                "error": "Email not configured",
                "recipients": 0,
                "message_id": None,
            }

        if not recipient_emails:
            return {
                "success": False,
                "error": "No recipient emails provided",
                "recipients": 0,
                "message_id": None,
            }

        try:
            # Create message
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = (
                f"{from_name} <{self.email_from}>" if from_name else cast(str, self.email_from)
            )
            msg["To"] = ", ".join(recipient_emails)

            # Add plain text part
            text_part = MIMEText(content, "plain")
            msg.attach(text_part)

            # Add HTML part if provided
            if html_content:
                html_part = MIMEText(html_content, "html")
                msg.attach(html_part)
            else:
                # Convert plain text to basic HTML
                html_content = f"<html><body><pre>{content}</pre></body></html>"
                html_part = MIMEText(html_content, "html")
                msg.attach(html_part)

            # Send email
            async with aiosmtplib.SMTP(
                hostname=self.smtp_host,
                port=self.smtp_port,
                use_tls=self.use_tls,
            ) as smtp:
                await smtp.login(self.smtp_user, self.smtp_password)

                send_result = await smtp.send_message(
                    msg,
                    mail_from=self.email_from,
                    rcpt_tos=recipient_emails,
                )

            logger.info(f"✅ Email sent to {len(recipient_emails)} recipient(s)")

            return {
                "success": True,
                "recipients": len(recipient_emails),
                "message_id": msg.get("Message-ID", ""),
                "error": None,
            }

        except Exception as e:
            logger.error(f"Email publishing error: {str(e)}")
            return {
                "success": False,
                "error": f"Email send failed: {str(e)}",
                "recipients": 0,
                "message_id": None,
            }

    async def send_newsletter(
        self,
        subject: str,
        content: str,
        list_name: str,
        preview_text: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Send newsletter to mailing list.

        Args:
            subject: Newsletter subject
            content: Newsletter content (plain text)
            list_name: Mailing list identifier (for template/tracking)
            preview_text: Optional preview text shown before opening email
            **kwargs: Additional metadata

        Returns:
            Newsletter send result

        Note: This is a placeholder that would integrate with newsletter services
        like ConvertKit, Substack, or custom mailing list databases
        """
        if not self.available:
            return {
                "success": False,
                "error": "Email not configured",
                "subscribers_count": 0,
            }

        try:
            # In production, this would:
            # 1. Fetch subscribers from mailing list database
            # 2. Prepare newsletter template
            # 3. Send via email service provider
            # 4. Track opens/clicks

            logger.info(f"✅ Newsletter queued for list: {list_name}")

            return {
                "success": True,
                "list": list_name,
                "subscribers_count": 0,  # Would come from database
                "error": None,
            }

        except Exception as e:
            logger.error(f"Newsletter send error: {str(e)}")
            return {
                "success": False,
                "error": str(e),
                "subscribers_count": 0,
            }

    async def send_notification(
        self, recipient: str, title: str, message: str, action_url: Optional[str] = None, **kwargs
    ) -> Dict[str, Any]:
        """
        Send transactional notification email.

        Args:
            recipient: Recipient email address
            title: Notification title
            message: Notification message
            action_url: Optional URL for action button
            **kwargs: Additional metadata

        Returns:
            Notification send result
        """
        if not self.available:
            return {
                "success": False,
                "error": "Email not configured",
                "recipient": recipient,
            }

        try:
            # Create HTML template for notification
            html_content = f"""
            <html>
                <body style="font-family: Arial, sans-serif;">
                    <h2>{title}</h2>
                    <p>{message}</p>
                    {f'<a href="{action_url}" style="padding: 10px 20px; background-color: #0066cc; color: white; text-decoration: none;">View</a>' if action_url else ''}
                </body>
            </html>
            """

            result = await self.publish(
                subject=title,
                content=message,
                recipient_emails=[recipient],
                html_content=html_content,
                from_name="Glad Labs",
            )

            return result

        except Exception as e:
            logger.error(f"Notification send error: {str(e)}")
            return {
                "success": False,
                "error": str(e),
                "recipient": recipient,
            }
