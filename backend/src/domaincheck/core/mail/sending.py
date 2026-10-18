"""Send one immutable MIME message; uncertain DATA outcomes are never retried."""
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, formatdate

from domaincheck.core.mail.connectivity import AllowlistedMailConnectionTester, StandardMailConnectionTester


def compose_message(intent, blobs):
    row = intent.exact_delivery
    message = EmailMessage()
    message['From'] = formataddr((row.get('sender_name', ''), row['sender_email']))
    message['To'], message['Subject'] = row['recipient'], row['subject']
    message['Message-ID'], message['Date'] = intent.message_id, formatdate(usegmt=True)
    if row.get('in_reply_to'):
        message['In-Reply-To'] = row['in_reply_to']
        message['References'] = row.get('references') or row['in_reply_to']
    message.set_content(row['text_body'], charset='utf-8')
    html_part = None
    if row.get('html_body'):
        message.add_alternative(row['html_body'], subtype='html', charset='utf-8')
        html_part = message.get_payload()[-1]
    for binding, data in blobs:
        major, minor = binding['mime_type'].split('/', 1)
        if binding['disposition'] == 'inline':
            if html_part is None:
                raise ValueError('Inline logo requires the HTML body')
            html_part.add_related(data, maintype=major, subtype=minor,
                cid=f"<{binding['content_id']}>", disposition='inline')
        else:
            message.add_attachment(data, maintype=major, subtype=minor,
                filename=f"{binding['artifact_id']}.{'json' if minor == 'json' else 'png' if minor == 'png' else 'jpg'}")
    return message


class SmtpReportSender:
    def __init__(self, endpoints):
        self.policy = AllowlistedMailConnectionTester(None, endpoints)

    def send(self, spec, message):
        try:
            self.policy._check(spec.host, spec.port)
            if spec.proxy:
                self.policy._check(spec.proxy.host, spec.proxy.port)
        except Exception:
            return 'failed', 'policy', 'mail_endpoint_denied'
        # Retry only before send_message: a disconnect after DATA may mean accepted.
        for attempt in range(2):
            server, stage = None, 'connect'
            try:
                server = StandardMailConnectionTester._open_smtp(spec, ssl.create_default_context())
                if spec.transport == 'starttls':
                    stage = 'tls'
                    server.ehlo(); server.starttls(context=ssl.create_default_context()); server.ehlo()
                stage = 'authenticate'
                server.login(spec.username, spec.password)
                stage = 'send_message'
                refused = server.send_message(message)
                return ('failed', stage, 'recipient_rejected') if refused else ('sent', stage, 'accepted')
            except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused,
                    smtplib.SMTPAuthenticationError, smtplib.SMTPDataError):
                return 'failed', stage, 'smtp_rejected'
            except Exception as exc:
                if stage == 'send_message':
                    return 'unknown', stage, 'smtp_outcome_uncertain'
                transient = isinstance(exc, (OSError, TimeoutError, smtplib.SMTPServerDisconnected)) and not isinstance(exc, ssl.SSLError)
                if attempt or not transient:
                    return 'failed', stage, 'smtp_connection_failed'
            finally:
                if server is not None:
                    from phishing_toolkit import _close_smtp_server
                    _close_smtp_server(server)
        return 'failed', 'connect', 'smtp_connection_failed'
