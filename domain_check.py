#!/usr/bin/env python3
"""
domain_check.py — Tra cứu nhanh thông tin phục vụ báo cáo domain phishing.

Với một domain/URL đầu vào, script sẽ:
  1. Lấy issuer (CA) + serial number của SSL certificate
  2. Tra WHOIS: registrar, ngày đăng ký, abuse email, nameservers
  3. Phát hiện domain có đứng sau Cloudflare hay không
  4. Gợi ý kênh report abuse tương ứng (CA / registrar / Cloudflare / Safe Browsing)

Cách dùng:
    python3 domain_check.py <domain hoặc URL>
    python3 domain_check.py chass.ru.com
    python3 domain_check.py https://chass.ru.com/login

Yêu cầu: pip install python-whois
"""

import argparse
import socket
import ssl
import sys
from urllib.parse import urlparse

try:
    import whois
except ImportError:
    whois = None


# Các CA hiện KHÔNG còn xử lý report abuse vì lý do phishing/nội dung
# (chỉ xử lý report về sai kỹ thuật cấp phát chứng chỉ - mis-issuance)
CA_ABUSE_NOTES = {
    "google trust services": {
        "report_url": None,
        "note": (
            "Google Trust Services KHÔNG xử lý report abuse vì lý do phishing/"
            "malware/nội dung. Họ chỉ xử lý report về mis-issuance (cấp sai quy trình). "
            "Không mất thời gian report CA này — chuyển sang Safe Browsing + registrar."
        ),
    },
    "let's encrypt": {
        "report_url": "https://letsencrypt.org/repository/ (mục Certificate Problem Report) hoặc abuse@letsencrypt.org",
        "note": "Let's Encrypt cũng chủ yếu xử lý mis-issuance, không đảm bảo revoke vì nội dung phishing.",
    },
    "sectigo": {
        "report_url": "https://sectigo.com/support-resources/report-abuse-ssl-certificate",
        "note": "Có xử lý report phishing/malware, khả năng revoke cao hơn CA khác.",
    },
    "digicert": {
        "report_url": "https://www.digicert.com/reporting-abuse/ hoặc abuse@digicert.com",
        "note": "Có xử lý report phishing/malware.",
    },
    "zerossl": {
        "report_url": "abuse@zerossl.com",
        "note": "Có nhận report abuse qua email.",
    },
}

CLOUDFLARE_NS_HINTS = ["cloudflare.com"]


def normalize_domain(target: str) -> str:
    if "://" in target:
        target = urlparse(target).netloc
    return target.split(":")[0].split("/")[0].strip()


def get_cert_info(domain: str, port: int = 443, timeout: float = 8.0):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # ta chỉ đọc thông tin, không cần verify chain

    with socket.create_connection((domain, port), timeout=timeout) as sock:
        ip = sock.getpeername()[0]
        with ctx.wrap_socket(sock, server_hostname=domain) as ssock:
            der_cert = ssock.getpeercert(binary_form=True)

    # Parse bằng cryptography nếu có, fallback sang cách thô nếu không
    try:
        from cryptography import x509
        from cryptography.hazmat.backends import default_backend

        cert = x509.load_der_x509_certificate(der_cert, default_backend())
        issuer = cert.issuer.rfc4514_string()
        serial = format(cert.serial_number, "X")
        not_before = cert.not_valid_before_utc
        not_after = cert.not_valid_after_utc
        return {
            "ip": ip,
            "issuer": issuer,
            "serial": serial,
            "not_before": str(not_before),
            "not_after": str(not_after),
        }
    except ImportError:
        return {"ip": ip, "issuer": None, "serial": None, "raw": True}


# "TLD giả": các second-level domain được CentralNic/đối tác bán lại như thể TLD riêng
# (vd. đăng ký "chass.ru.com" thực chất là subdomain của ru.com, WHOIS thật nằm ở ru.com,
# không phải ở registry .com). Hay bị lợi dụng cho phishing vì rẻ, WHOIS lỏng lẻo hơn ccTLD thật.
FAKE_TLD_SUFFIXES = {
    "ru.com", "uk.com", "us.com", "eu.com", "gb.com", "gb.net", "de.com",
    "jpn.com", "sa.com", "se.com", "kr.com", "no.com", "qc.com", "br.com",
    "cn.com", "hu.com", "za.com", "uy.com",
}


def whois_query_domain(domain: str) -> str:
    parts = domain.lower().split(".")
    if len(parts) > 2 and ".".join(parts[-2:]) in FAKE_TLD_SUFFIXES:
        return ".".join(parts[-2:])
    return domain


def get_whois_info(domain: str):
    if whois is None:
        return {"error": "Thư viện python-whois chưa được cài (pip install python-whois)"}
    try:
        w = whois.whois(whois_query_domain(domain))
        return {
            "registrar": w.get("registrar"),
            "creation_date": str(w.get("creation_date")),
            "expiration_date": str(w.get("expiration_date")),
            "updated_date": str(w.get("updated_date")),
            "name_servers": w.get("name_servers"),
            "emails": w.get("emails"),
            "status": w.get("status"),
        }
    except Exception as e:
        return {"error": str(e)}


def match_ca_notes(issuer: str):
    if not issuer:
        return None
    issuer_lower = issuer.lower()
    for key, info in CA_ABUSE_NOTES.items():
        if key in issuer_lower:
            return {"ca": key, **info}
    return None


def is_cloudflare(ns_list):
    if not ns_list:
        return False
    ns_list = ns_list if isinstance(ns_list, list) else [ns_list]
    return any(any(hint in str(ns).lower() for hint in CLOUDFLARE_NS_HINTS) for ns in ns_list)


def main():
    parser = argparse.ArgumentParser(description="Tra cứu domain phục vụ báo cáo phishing takedown")
    parser.add_argument("target", help="Domain hoặc URL cần tra, ví dụ: chass.ru.com")
    args = parser.parse_args()

    domain = normalize_domain(args.target)
    print(f"\n=== Đang tra cứu: {domain} ===\n")

    # --- SSL certificate ---
    print("--- SSL Certificate ---")
    try:
        cert = get_cert_info(domain)
        print(f"  IP kết nối     : {cert.get('ip')}")
        print(f"  Issuer (CA)    : {cert.get('issuer')}")
        print(f"  Serial number  : {cert.get('serial')}")
        if cert.get("not_before"):
            print(f"  Hiệu lực từ    : {cert.get('not_before')}")
            print(f"  Hiệu lực đến   : {cert.get('not_after')}")
    except Exception as e:
        cert = {}
        print(f"  Lỗi khi lấy chứng chỉ: {e}")

    # --- WHOIS ---
    print("\n--- WHOIS ---")
    who = get_whois_info(domain)
    if "error" in who:
        print(f"  Lỗi WHOIS: {who['error']}")
    else:
        for k, v in who.items():
            print(f"  {k:15s}: {v}")

    # --- Cloudflare check ---
    cf = is_cloudflare(who.get("name_servers")) if isinstance(who, dict) else False
    print(f"\n--- Cloudflare ---\n  Đứng sau Cloudflare: {'Có' if cf else 'Không rõ / Không'}")

    # --- Khuyến nghị report ---
    print("\n--- Khuyến nghị kênh báo cáo ---")
    print("  1. Google Safe Browsing (ưu tiên số 1, chặn cảnh báo trình duyệt):")
    print("     https://safebrowsing.google.com/safebrowsing/report_phish/")

    ca_note = match_ca_notes(cert.get("issuer")) if cert else None
    if ca_note:
        print(f"\n  2. CA ({ca_note['ca']}):")
        print(f"     {ca_note['note']}")
        if ca_note["report_url"]:
            print(f"     Report tại: {ca_note['report_url']}")

    registrar = who.get("registrar") if isinstance(who, dict) else None
    if registrar:
        print(f"\n  3. Registrar: {registrar}")
        emails = who.get("emails")
        if emails:
            print(f"     Email liên hệ tra được: {emails}")
        print("     Tra thêm abuse email chính thức tại: https://lookup.icann.org/")

    if cf:
        print("\n  4. Cloudflare (vì domain dùng nameserver Cloudflare):")
        print("     https://abuse.cloudflare.com/phishing")

    print()


if __name__ == "__main__":
    main()
