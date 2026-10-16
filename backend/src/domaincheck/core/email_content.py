"""Portable plain/HTML email bodies with a CID logo; never opens a network connection."""
from dataclasses import dataclass
import hashlib
from email.message import EmailMessage
from html import escape
from importlib.resources import files


@dataclass(frozen=True)
class InlineLogo:
    data: bytes
    mime_type: str
    content_id: str


def bundled_logo_bytes() -> bytes:
    return files("domaincheck").joinpath("assets/email/logo-win.jpg").read_bytes()


def default_logo() -> InlineLogo:
    data = bundled_logo_bytes()
    return InlineLogo(data, 'image/jpeg', f'logo-default-{hashlib.sha256(data).hexdigest()[:16]}')


def html_report_body(body: str, content_id: str) -> str:
    image = (f'<img src="cid:{escape(content_id, quote=True)}" alt="Brand logo" '
        'width="64" height="64" style="display:block;width:64px;height:64px;'
        'object-fit:contain;margin:0 0 8px 0;border:0;" />')
    lines, added = [], False
    for line in body.splitlines():
        if not added and line.strip().lower() in {"regards,", "kind regards,"}:
            lines.append(image)
            added = True
        lines.append(escape(line) if line else "&nbsp;")
    if not added:
        lines.extend(["&nbsp;", image])
    return ('<div style="font-family:Arial,sans-serif;font-size:14px;line-height:1.45;'
        'color:#111;">' + "<br>\n".join(lines) + "</div>")


def set_email_content(message: EmailMessage, body: str, logo: InlineLogo | None = None) -> None:
    message.set_content(body, charset="utf-8")
    logo = logo or default_logo()
    if logo.mime_type not in {"image/png", "image/jpeg"} or not logo.data:
        raise ValueError("Invalid inline logo")
    message.add_alternative(html_report_body(body, logo.content_id), subtype="html", charset="utf-8")
    message.get_payload()[-1].add_related(logo.data, maintype="image", subtype=logo.mime_type.split('/')[1],
        cid=f"<{logo.content_id}>", disposition="inline")
