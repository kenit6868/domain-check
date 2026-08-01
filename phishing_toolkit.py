#!/usr/bin/env python3
"""
phishing_toolkit.py — Bộ công cụ hỗ trợ phát hiện & báo cáo domain phishing.

3 lệnh con:

  check <domain>
      Kiểm tra đầy đủ 1 domain: SSL issuer/serial, WHOIS, Cloudflare,
      VirusTotal, Google Safe Browsing (nếu có API key), ghi log vào
      case_log.csv, và sinh sẵn email báo cáo trong thư mục reports/.

  related <keyword>
      Tìm các domain khác có chứng chỉ SSL chứa từ khóa (vd tên thương hiệu)
      qua Certificate Transparency log (crt.sh) — giúp phát hiện các domain
      "anh em" cùng chiến dịch phishing.

  brandscan <domain>
      Sinh các biến thể gõ nhầm/giống domain thật (dùng dnstwist) và lọc ra
      những cái đã bị đăng ký — để chủ động phát hiện phishing trước khi có
      người báo cáo.

Cài đặt:
    pip install requests python-whois cryptography dnstwist

Cấu hình:
    Copy config.example.ini -> config.ini và điền VT/GSB API key (tùy chọn).
"""

import argparse
import concurrent.futures
import configparser
import csv
import imaplib
import json
import os
import re
import smtplib
import socket
import ssl
import subprocess
import sys
from datetime import datetime, timezone
from email.mime.text import MIMEText
from urllib.parse import urlparse

try:
    import whois
except ImportError:
    whois = None

try:
    import requests
    requests.packages.urllib3.disable_warnings()  # detect_cdn() gọi HEAD với verify=False cho domain nghi vấn
except ImportError:
    requests = None

try:
    import dns.resolver
except ImportError:
    dns = None

try:
    # IP WHOIS (registry ARIN/RIPE/APNIC... qua RDAP) — KHÁC hẳn `whois` ở trên (domain WHOIS).
    # Dùng ipwhois thay vì shell ra lệnh `whois <IP>` như 03_Technical_Guide.md gợi ý vì lệnh
    # `whois` không có sẵn mặc định trên Windows — sẽ lặp lại đúng vấn đề PATH đã gặp với
    # streamlit/dnstwist. ipwhois dùng RDAP thuần Python qua mạng, không cần binary ngoài.
    from ipwhois import IPWhois
except ImportError:
    IPWhois = None

try:
    # PySocks — chỉ cần khi cấu hình proxy cho SMTP (smtp_proxies trong config.ini).
    # Nếu không cài, gửi email vẫn chạy bình thường miễn là smtp_proxies để trống.
    import socks as _socks
    _SOCKS5 = _socks.SOCKS5
    _SOCKS4 = _socks.SOCKS4
    _SOCKS_HTTP = _socks.HTTP
except ImportError:
    _socks = None
    _SOCKS5 = _SOCKS4 = _SOCKS_HTTP = None


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.ini")
LOG_PATH = os.path.join(BASE_DIR, "case_log.csv")
REPORTS_DIR = os.path.join(BASE_DIR, "reports")
SENT_LOG_PATH = os.path.join(BASE_DIR, "sent_log.csv")

CA_ABUSE_NOTES = {
    "google trust services": {
        "report_url": None,
        "note": (
            "Google Trust Services KHÔNG xử lý report abuse vì lý do phishing/"
            "malware/nội dung, chỉ xử lý mis-issuance. Bỏ qua bước report CA này."
        ),
    },
    "let's encrypt": {
        "report_url": "https://letsencrypt.org/repository/ hoặc abuse@letsencrypt.org",
        "note": "Chủ yếu xử lý mis-issuance, không đảm bảo revoke vì nội dung phishing.",
    },
    "sectigo": {
        "report_url": "https://sectigo.com/support-resources/report-abuse-ssl-certificate",
        "note": "Có xử lý report phishing/malware.",
    },
    "digicert": {
        "report_url": "https://www.digicert.com/reporting-abuse/ hoặc abuse@digicert.com",
        "note": "Có xử lý report phishing/malware.",
    },
    "zerossl": {
        "report_url": "abuse@zerossl.com",
        "note": "Có nhận report abuse qua email.",
    },
    "godaddy": {
        "report_url": "abuse@godaddy.com hoặc https://sg.godaddy.com/help/report-abuse-24108",
        "note": "Có xử lý report phishing/malware.",
    },
}

# CDN/anti-DDoS proxy phổ biến hay bị phishing lợi dụng để ẩn IP gốc (03_Technical_Guide.md mục 2).
# "cloudflare" ở đây chỉ cung cấp report_url/note dùng chung — việc PHÁT HIỆN có dùng Cloudflare hay
# không vẫn do is_cloudflare() (tra nameserver) đảm nhiệm, không phải detect_cdn().
CDN_ABUSE_CONTACTS = {
    "cloudflare": {
        "report_url": "https://abuse.cloudflare.com/phishing",
        "note": "Chọn mục \"Phishing & Malware\" khi report — có thể chặn proxy hoặc gắn interstitial cảnh báo.",
    },
    "fastly": {
        "report_url": "https://www.fastly.com/abuse hoặc abuse@fastly.com",
        "note": "Report qua form hoặc gửi email abuse trực tiếp.",
    },
    "akamai": {
        "report_url": "abuse@akamai.com hoặc https://www.akamai.com/legal/compliance/report-abuse",
        "note": "Report qua email hoặc form compliance.",
    },
    "cloudfront": {
        "report_url": "https://aws.amazon.com/premiumsupport/knowledge-center/report-cloudfront-abuse/ hoặc abuse@amazonaws.com",
        "note": "CDN của AWS — report qua knowledge center hoặc email abuse chung của AWS.",
    },
    "stormwall": {
        "report_url": "abuse@stormwall.pro",
        "note": "Cũng có thể report qua live chat trên trang chủ Stormwall.",
    },
    "ddos-guard": {
        "report_url": "abuse@ddos-guard.net hoặc https://ddos-guard.net/en/abuse",
        "note": "Anti-DDoS proxy — thường ẩn IP gốc, kết hợp quét subdomain (origin_ip_scan) để tìm IP thật.",
    },
}

# Mail providers phổ biến hay bị phishing lợi dụng để gửi email lừa đảo.
# Khi domain phishing có MX record trỏ về 1 provider trong bảng này, cần report thêm
# tới abuse team của provider đó (không chỉ registrar/CA/CDN).
MAIL_PROVIDER_ABUSE = {
    "google":     {"name": "Google Workspace",  "abuse_url": "https://support.google.com/mail/contact/abuse",         "email": None},
    "googlemail": {"name": "Google Workspace",  "abuse_url": "https://support.google.com/mail/contact/abuse",         "email": None},
    "outlook":    {"name": "Microsoft 365",     "abuse_url": "https://msrc.microsoft.com/report/abuse",               "email": None},
    "office365":  {"name": "Microsoft 365",     "abuse_url": "https://msrc.microsoft.com/report/abuse",               "email": None},
    "microsoft":  {"name": "Microsoft 365",     "abuse_url": "https://msrc.microsoft.com/report/abuse",               "email": None},
    "zoho":       {"name": "Zoho Mail",         "abuse_url": "https://www.zoho.com/mail/spamreport.html",             "email": "abuse@zoho.com"},
    "yahoodns":   {"name": "Yahoo Mail",        "abuse_url": None,                                                    "email": "abuse@yahoo.com"},
    "protonmail": {"name": "ProtonMail",        "abuse_url": None,                                                    "email": "abuse@proton.me"},
    "mailgun":    {"name": "Mailgun",           "abuse_url": None,                                                    "email": "abuse@mailgun.com"},
    "sendgrid":   {"name": "SendGrid (Twilio)", "abuse_url": "https://sendgrid.com/policies/tos/",                    "email": "abuse@sendgrid.com"},
    "amazonaws":  {"name": "Amazon SES",        "abuse_url": "https://support.aws.amazon.com/#/contacts/report-abuse","email": "abuse@amazonaws.com"},
}

# Registry quản lý ccTLD — bảng tĩnh tra TRƯỚC (không cần mạng), chỉ TLD 1 nhãn (03_Technical_Guide.md
# mục 5 bước 3). TLD không có trong bảng này fallback sang iana_referral() (cần mạng).
CCTLD_REGISTRY_CONTACTS = {
    "cn": {
        "registry": "CNNIC",
        "abuse_email": "supervision@cnnic.cn",
        "note": "Yêu cầu bản dịch nhãn hiệu tiếng Trung/Anh có công chứng.",
    },
    "in": {"registry": "NIXI", "abuse_email": "abuse@registry.in", "note": None},
    "io": {"registry": "Identity Digital", "abuse_email": "abuse@identity.digital", "note": None},
    "jp": {
        "registry": "JPRS",
        "abuse_email": "info@jprs.jp",
        "note": "Đòi hỏi xác thực nhãn hiệu đăng ký tại Nhật Bản.",
    },
    "kr": {
        "registry": "KISA",
        "abuse_email": "abuse@kisa.or.kr",
        "note": "Phối hợp báo cáo qua KrCERT.",
    },
    "ru": {
        "registry": "Coordination Center for TLD RU",
        "abuse_email": "abuse@cctld.ru / ru-abuse@cctld.ru",
        "note": None,
    },
    "uk": {"registry": "Nominet", "abuse_email": "abuse@nominet.uk", "note": None},
    "eu": {"registry": "EURid", "abuse_email": "abuse@eurid.eu / legal@eurid.eu", "note": None},
    "tw": {"registry": "TWNIC", "abuse_email": "abuse@twnic.tw", "note": None},
    "hk": {"registry": "HKIRC", "abuse_email": "abuse@hkirc.hk", "note": None},
    "us": {"registry": "GoDaddy Registry", "abuse_email": "abuse@about.us", "note": None},
    "me": {"registry": "doMEn", "abuse_email": "abuse@domain.me", "note": None},
    "xyz": {"registry": "XYZ.COM LLC", "abuse_email": "abuse@xyz.xyz", "note": None},
    "top": {"registry": ".top registry", "abuse_email": "abuse@nic.top", "note": None},
    "club": {"registry": "GoDaddy Registry", "abuse_email": "clubabuse@godaddy.com", "note": None},
    "co": {"registry": "GoDaddy Registry", "abuse_email": "abuse@godaddy.com", "note": None},
}


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

def load_config():
    cfg = configparser.ConfigParser()
    if os.path.exists(CONFIG_PATH):
        cfg.read(CONFIG_PATH, encoding="utf-8")

    # --- Multi-account SMTP (mới) + backward compat với cấu hình đơn cũ ---
    # Ưu tiên: nếu có key "accounts" (JSON array) → dùng đó.
    # Fallback: nếu chỉ có host/username/password cũ → wrap thành list 1 phần tử.
    accounts_raw = cfg.get("smtp", "accounts", fallback="").strip()
    if accounts_raw:
        try:
            smtp_accounts = json.loads(accounts_raw)
        except json.JSONDecodeError:
            smtp_accounts = []
    else:
        old_host = cfg.get("smtp", "host", fallback="")
        old_user = cfg.get("smtp", "username", fallback="")
        old_pass = cfg.get("smtp", "password", fallback="")
        if old_host and old_user and old_pass:
            smtp_accounts = [{
                "host": old_host,
                "port": cfg.getint("smtp", "port", fallback=587),
                "username": old_user,
                "password": old_pass,
            }]
        else:
            smtp_accounts = []

    proxies_raw = cfg.get("smtp", "proxies", fallback="").strip()
    if proxies_raw:
        try:
            smtp_proxies = json.loads(proxies_raw)
        except json.JSONDecodeError:
            smtp_proxies = []
    else:
        smtp_proxies = []

    return {
        "vt_api_key": cfg.get("api", "vt_api_key", fallback="") or os.environ.get("VT_API_KEY", ""),
        "gsb_api_key": cfg.get("api", "gsb_api_key", fallback="") or os.environ.get("GSB_API_KEY", ""),
        "urlscan_api_key": cfg.get("api", "urlscan_api_key", fallback="") or os.environ.get("URLSCAN_API_KEY", ""),
        "brand_name": cfg.get("company", "brand_name", fallback="[TÊN THƯƠNG HIỆU]"),
        "contact_name": cfg.get("company", "contact_name", fallback="[TÊN NGƯỜI BÁO CÁO]"),
        "contact_email": cfg.get("company", "contact_email", fallback="[EMAIL LIÊN HỆ]"),
        # Multi-account + proxy (mới)
        "smtp_accounts": smtp_accounts,
        "smtp_proxies": smtp_proxies,
        # Keys cũ giữ lại để backward compat với bất kỳ nơi nào còn dùng trực tiếp
        "smtp_host": cfg.get("smtp", "host", fallback=""),
        "smtp_port": cfg.getint("smtp", "port", fallback=587),
        "smtp_username": cfg.get("smtp", "username", fallback=""),
        "smtp_password": cfg.get("smtp", "password", fallback=""),
    }


# --------------------------------------------------------------------------
# Helpers dùng chung
# --------------------------------------------------------------------------

def normalize_domain(target: str) -> str:
    if "://" in target:
        target = urlparse(target).netloc
    return target.split(":")[0].split("/")[0].strip()


def get_cert_info(domain: str, port: int = 443, timeout: float = 8.0):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    with socket.create_connection((domain, port), timeout=timeout) as sock:
        ip = sock.getpeername()[0]
        with ctx.wrap_socket(sock, server_hostname=domain) as ssock:
            der_cert = ssock.getpeercert(binary_form=True)

    try:
        from cryptography import x509
        from cryptography.hazmat.backends import default_backend

        cert = x509.load_der_x509_certificate(der_cert, default_backend())
        return {
            "ip": ip,
            "issuer": cert.issuer.rfc4514_string(),
            "serial": format(cert.serial_number, "X"),
            "not_before": str(cert.not_valid_before_utc),
            "not_after": str(cert.not_valid_after_utc),
        }
    except ImportError:
        return {"ip": ip, "issuer": None, "serial": None}


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
        return {"error": "python-whois chưa được cài (pip install python-whois)"}
    try:
        w = whois.whois(whois_query_domain(domain))
        return {
            "registrar": w.get("registrar"),
            "creation_date": str(w.get("creation_date")),
            "expiration_date": str(w.get("expiration_date")),
            "name_servers": w.get("name_servers"),
            "emails": w.get("emails"),
            "status": w.get("status"),
        }
    except Exception as e:
        return {"error": str(e)}


def get_ip_whois(ip: str) -> dict:
    """Tra WHOIS của 1 địa chỉ IP (registry ARIN/RIPE/APNIC... qua RDAP) — dùng cho IP gốc tìm
    được qua scan_common_subdomains(), KHÁC hẳn get_whois_info() ở trên (domain WHOIS qua
    python-whois). 2 loại dữ liệu khác nhau, không thay thế nhau.

    depth=2 bắt buộc: mặc định (depth=1) RDAP chỉ trả về entity "registrant", không có entity
    role="abuse" — abuse contact thật của ISP/hosting chỉ xuất hiện khi tăng depth. contact.email
    trong RDAP là 1 list dict [{"value": "..."}], không phải string thẳng.
    """
    if IPWhois is None:
        return {"error": "ipwhois chưa được cài (pip install ipwhois)"}
    try:
        result = IPWhois(ip).lookup_rdap(depth=2)
        objects = result.get("objects") or {}

        abuse_email = None
        org = None
        for obj in objects.values():
            if "abuse" in (obj.get("roles") or []):
                emails = (obj.get("contact") or {}).get("email") or []
                if emails:
                    abuse_email = emails[0].get("value")
                break
        for obj in objects.values():
            # Ưu tiên tên tổ chức từ contact registrant (dễ đọc, vd "Google LLC") hơn
            # network.name (thường chỉ là handle ngắn, vd "GOGL") — dùng cho dòng "Dear {org}
            # Abuse Team" trong draft, cùng phong cách với generate_email_drafts().
            name = (obj.get("contact") or {}).get("name")
            if name:
                org = name
                break
        if not org:
            org = (result.get("network") or {}).get("name")

        return {
            "org": org,
            "abuse_email": abuse_email,
            "asn": result.get("asn"),
            "asn_description": result.get("asn_description"),
        }
    except Exception as e:
        return {"error": str(e)}


# --------------------------------------------------------------------------
# Registry WHOIS thô (raw socket) — leo thang ccTLD lạ (03_Technical_Guide.md mục 5)
# --------------------------------------------------------------------------
#
# Đây là loại WHOIS THỨ BA trong file này, khác cả 2 loại trên:
#   - get_whois_info()  → domain WHOIS qua thư viện python-whois (registrar/nameserver...)
#   - get_ip_whois()    → IP WHOIS qua RDAP/ipwhois (registry ARIN/RIPE/APNIC sở hữu dải IP)
#   - (dưới đây)        → nói chuyện TRỰC TIẾP với 1 WHOIS server bất kỳ qua giao thức WHOIS
#                         thô (raw socket port 43), vì python-whois tự chọn server theo TLD đã
#                         biết sẵn trong thư viện — không truy vấn được tới 1 WHOIS server tùy ý
#                         (cần cho ccTLD lạ/mới mà thư viện chưa biết, đi qua referral của IANA).

def query_whois_server(server: str, query: str, timeout: float = 8.0) -> str:
    """Gửi 1 truy vấn WHOIS thô tới `server` qua TCP port 43, trả về response dạng text.

    Không raise — lỗi kết nối/timeout trả về chuỗi rỗng, để lookup_registry_contact() coi như
    "không tra được" thay vì làm hỏng cả run_check(). Giống get_cert_info() ở trên: dùng
    socket.create_connection() thô, không qua thư viện WHOIS cấp cao (vì cần gửi query tới đúng
    server chỉ định, python-whois không hỗ trợ việc này). decode utf-8 với errors="ignore" vì
    WHOIS server của registry nước ngoài có thể trả về ký tự khó decode.
    """
    try:
        with socket.create_connection((server, 43), timeout=timeout) as sock:
            sock.sendall(f"{query}\r\n".encode())
            chunks = []
            while True:
                data = sock.recv(4096)
                if not data:
                    break
                chunks.append(data)
        return b"".join(chunks).decode("utf-8", errors="ignore")
    except Exception:
        return ""


_IANA_REFERRAL_RE = re.compile(r"^(refer|whois):\s*(\S+)", re.IGNORECASE | re.MULTILINE)


def iana_referral(tld: str):
    """Hỏi whois.iana.org xem TLD này do WHOIS server nào của registry phụ trách, trả về
    hostname WHOIS server đó (vd "whois.jprs.jp") hoặc None nếu không parse được.

    Ưu tiên dòng "whois:" hơn "refer:" nếu cả 2 cùng xuất hiện — "whois:" trỏ thẳng tới server
    của registry, còn "refer:" đôi khi chỉ trỏ tới 1 tầng trung gian khác.
    """
    raw = query_whois_server("whois.iana.org", tld)
    if not raw:
        return None
    fields = {key.lower(): value for key, value in _IANA_REFERRAL_RE.findall(raw)}
    return fields.get("whois") or fields.get("refer")



# whois.iana.org trả về referral cho MỌI TLD nó biết, kể cả gTLD cổ điển như .com/.net (không chỉ
# ccTLD) — nếu không loại trừ, lookup_registry_contact() sẽ "leo thang Registry" cho cả domain
# .com/.net bình thường, sinh draft thừa vô nghĩa (gTLD này đã có đầy đủ quy trình UDRP + registrar
# report ở mục "4. Registrar" rồi). 03_Technical_Guide.md mục 5 chỉ nói về ccTLD ("quy trình UDRP
# của ICANN không được áp dụng hoàn toàn") — phát hiện lúc test: iana_referral("com") trả về
# whois.verisign-grs.com bình thường, phải chặn tay các gTLD cổ điển này ở đây.
_SKIP_REGISTRY_ESCALATION_TLDS = {"com", "net", "org", "info", "biz", "name", "pro", "mobi"}


def lookup_registry_contact(domain: str) -> dict:
    """Tra abuse contact của Registry quản lý TLD của domain (03_Technical_Guide.md mục 5) —
    dùng để leo thang khi report registrar-level không đủ hoặc bị phớt lờ, đặc biệt với ccTLD lạ
    mà UDRP của ICANN không áp dụng hoàn toàn.

    Tra CCTLD_REGISTRY_CONTACTS (bảng tĩnh, không cần mạng) TRƯỚC — chỉ fallback sang
    iana_referral() (cần mạng, WHOIS output không có cấu trúc cố định) khi TLD không có trong
    bảng VÀ không nằm trong _SKIP_REGISTRY_ESCALATION_TLDS (gTLD cổ điển, xem comment trên).
    Khác get_whois_info(): output từ iana_referral trả về nguyên văn (`raw`) không parse thành
    field có cấu trúc, vì format WHOIS mỗi registry quốc gia rất khác nhau — không đáng để viết
    parser riêng cho từng cái, con người tự đọc abuse email trong đó.

    Giới hạn: chỉ xử lý TLD 1 nhãn (vd .jp) — KHÔNG xử lý ccTLD 2 nhãn kiểu .co.uk/.com.cn,
    domain dạng đó rơi vào nhánh "not_found" thay vì cố xử lý sai.
    """
    tld = domain.lower().rsplit(".", 1)[-1]
    if tld in CCTLD_REGISTRY_CONTACTS:
        return {"source": "static_table", **CCTLD_REGISTRY_CONTACTS[tld]}
    if tld in _SKIP_REGISTRY_ESCALATION_TLDS:
        return {"source": "not_found"}
    referral_server = iana_referral(tld)
    if referral_server:
        raw = query_whois_server(referral_server, domain)
        return {"source": "iana_referral", "whois_server": referral_server, "raw": raw}
    return {"source": "not_found"}


def is_cloudflare(ns_list):
    if not ns_list:
        return False
    ns_list = ns_list if isinstance(ns_list, list) else [ns_list]
    return any("cloudflare.com" in str(ns).lower() for ns in ns_list)


# Hậu tố CNAME quen thuộc của từng CDN — chỉ nameserver là không đủ vì (khác Cloudflare) khách hàng
# của Fastly/Akamai/CloudFront thường vẫn giữ nguyên nameserver của registrar, chỉ CNAME bản ghi A.
_CDN_CNAME_SUFFIXES = {
    "fastly.net": "fastly",
    "akamaiedge.net": "akamai",
    "akamai.net": "akamai",
    "cloudfront.net": "cloudfront",
}


def detect_cdn(domain: str) -> list:
    """Nhận diện CDN đang che domain (KHÔNG gồm Cloudflare — xem is_cloudflare()).

    Kết hợp 2 nguồn tín hiệu: chuỗi CNAME (qua dnspython) và header HTTP response
    (Server/Via/X-Cache/X-Served-By/X-Amz-Cf-Id). Không raise — lỗi ở từng nguồn tín hiệu
    (domain không resolve được, không có CNAME, không kết nối HTTP được) chỉ khiến nguồn đó
    bị bỏ qua, không làm hỏng cả hàm.
    """
    detected = set()

    if dns is not None:
        try:
            answer = dns.resolver.resolve(domain, "CNAME")
            for rdata in answer:
                target = str(rdata.target).rstrip(".").lower()
                for suffix, name in _CDN_CNAME_SUFFIXES.items():
                    if target.endswith(suffix):
                        detected.add(name)
        except Exception:
            pass

    if requests is not None:
        for scheme in ("http", "https"):
            try:
                r = requests.head(
                    f"{scheme}://{domain}/", timeout=5, allow_redirects=False, verify=False
                )
                blob = " ".join(
                    str(r.headers.get(h, ""))
                    for h in ("Server", "Via", "X-Cache", "X-Served-By", "X-Amz-Cf-Id")
                ).lower()
                if blob.strip():
                    if "cloudfront" in blob or r.headers.get("X-Amz-Cf-Id"):
                        detected.add("cloudfront")
                    if "fastly" in blob or "varnish" in blob:
                        detected.add("fastly")
                    if "akamai" in blob:
                        detected.add("akamai")
                    if "ddos-guard" in blob:
                        detected.add("ddos-guard")
                    if "stormwall" in blob:
                        detected.add("stormwall")
            except Exception:
                continue

    return sorted(detected)


# Wordlist subdomain thường gặp để dò IP gốc phía sau CDN/proxy (03_Technical_Guide.md mục 2).
COMMON_SUBDOMAINS = ["mail", "cpanel", "direct", "ftp", "dev", "staging", "webmail", "secure", "panel"]


def _resolve_subdomain(fqdn: str, timeout: float):
    try:
        if dns is not None:
            answer = dns.resolver.resolve(fqdn, "A", lifetime=timeout)
            return str(answer[0])
        old_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(timeout)
        try:
            return socket.gethostbyname(fqdn)
        finally:
            socket.setdefaulttimeout(old_timeout)
    except Exception:
        return None


def scan_common_subdomains(domain: str, timeout: float = 5.0) -> dict:
    """Resolve 1 wordlist subdomain cố định, trả về {subdomain: ip hoặc None}.

    1 subdomain lỗi/không resolve được không được làm hỏng cả vòng quét — mỗi lượt resolve
    (`_resolve_subdomain`) tự bắt exception riêng. Việc so sánh IP này với IP chính của domain
    (cert["ip"]) để tìm "candidate origin IP" được làm ở nơi hiển thị (cmd_check/Streamlit),
    không phải ở đây, vì hàm này không có quyền truy cập cert — giữ hàm thuần túy chỉ resolve DNS.

    Chạy SONG SONG (ThreadPoolExecutor) thay vì tuần tự — dùng dns.resolver (dnspython, có
    "lifetime" giới hạn timeout thật) khi có, thay vì socket.gethostbyname:
    socket.setdefaulttimeout() KHÔNG áp dụng được cho gethostbyname() (giới hạn đã biết của
    Python: timeout đó chỉ áp dụng cho socket connect/send/recv, không áp dụng cho resolve DNS
    qua thư viện hệ thống). Phát hiện lúc test: quét tuần tự 9 subdomain bằng gethostbyname mất
    77s dù "timeout" được set = 3.0; đổi sang dns.resolver(lifetime=...) sửa được việc timeout
    không có tác dụng, nhưng vẫn chậm (27-36s) vì DNS trong môi trường test có độ trễ thật
    ~4-5s/query — cộng dồn qua 9 subdomain tuần tự là không chấp nhận được cho 1 tool "check
    nhanh". Chạy song song thay vì tuần tự khiến tổng thời gian bị chặn bởi lookup CHẬM NHẤT
    (~timeout giây) thay vì TỔNG của cả 9 lookup — mới thực sự đạt được ý định "timeout ngắn"
    ban đầu của yêu cầu. Vẫn giữ socket.gethostbyname làm fallback nếu dnspython không có sẵn.
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(COMMON_SUBDOMAINS)) as pool:
        future_to_sub = {
            pool.submit(_resolve_subdomain, f"{sub}.{domain}", timeout): sub
            for sub in COMMON_SUBDOMAINS
        }
        results = {sub: None for sub in COMMON_SUBDOMAINS}
        for future in concurrent.futures.as_completed(future_to_sub):
            results[future_to_sub[future]] = future.result()
    return results


def match_ca_notes(issuer: str):
    if not issuer:
        return None
    issuer_lower = issuer.lower()
    for key, info in CA_ABUSE_NOTES.items():
        if key in issuer_lower:
            return {"ca": key, **info}
    return None


# --------------------------------------------------------------------------
# VirusTotal
# --------------------------------------------------------------------------

def check_virustotal(domain: str, api_key: str):
    if not api_key:
        return {"skipped": "Chưa có VT API key trong config.ini"}
    if requests is None:
        return {"error": "Thư viện requests chưa được cài"}
    try:
        r = requests.get(
            f"https://www.virustotal.com/api/v3/domains/{domain}",
            headers={"x-apikey": api_key},
            timeout=15,
        )
        if r.status_code == 404:
            return {"note": "Domain chưa từng được VirusTotal quét trước đây"}
        r.raise_for_status()
        data = r.json()
        stats = data["data"]["attributes"]["last_analysis_stats"]
        return {
            "malicious": stats.get("malicious", 0),
            "suspicious": stats.get("suspicious", 0),
            "harmless": stats.get("harmless", 0),
            "total_engines": sum(stats.values()),
            "link": f"https://www.virustotal.com/gui/domain/{domain}",
        }
    except Exception as e:
        return {"error": str(e)}


def submit_virustotal(domain: str, api_key: str):
    """Gửi domain lên VirusTotal để quét nếu chưa có dữ liệu."""
    if not api_key or requests is None:
        return None
    try:
        r = requests.post(
            "https://www.virustotal.com/api/v3/urls",
            headers={"x-apikey": api_key},
            data={"url": f"http://{domain}"},
            timeout=15,
        )
        r.raise_for_status()
        return "Đã submit domain lên VirusTotal để quét, kiểm tra lại sau vài phút."
    except Exception as e:
        return f"Lỗi khi submit VirusTotal: {e}"


# --------------------------------------------------------------------------
# Google Safe Browsing (chỉ CHECK trạng thái — việc report vẫn phải làm
# thủ công qua form https://safebrowsing.google.com/safebrowsing/report_phish/
# vì Google không có API công khai để submit report)
# --------------------------------------------------------------------------

def check_safebrowsing(url: str, api_key: str):
    if not api_key:
        return {"skipped": "Chưa có GSB API key trong config.ini"}
    if requests is None:
        return {"error": "Thư viện requests chưa được cài"}
    try:
        body = {
            "client": {"clientId": "phishing-toolkit", "clientVersion": "1.0"},
            "threatInfo": {
                "threatTypes": ["MALWARE", "SOCIAL_ENGINEERING", "UNWANTED_SOFTWARE"],
                "platformTypes": ["ANY_PLATFORM"],
                "threatEntryTypes": ["URL"],
                "threatEntries": [{"url": url}],
            },
        }
        r = requests.post(
            f"https://safebrowsing.googleapis.com/v4/threatMatches:find?key={api_key}",
            json=body,
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("matches"):
            return {"flagged": True, "matches": data["matches"]}
        return {"flagged": False}
    except Exception as e:
        return {"error": str(e)}


def generate_safebrowsing_report_text(domain: str, cfg: dict) -> str:
    """Sinh sẵn 1 đoạn mô tả ngắn để copy-paste vào ô mô tả của form report thủ công tại
    https://safebrowsing.google.com/safebrowsing/report_phish/ (Google không có API submit report,
    chỉ có API tra cứu qua check_safebrowsing() ở trên).

    KHÔNG ghi ra file trong reports/ như các generate_*_draft() khác — đây là text dán vào 1 ô
    textbox của form web, không phải nội dung email gửi qua mail client hay SMTP, nên không cần
    (và không nên) có file .txt riêng hay xuất hiện trong danh sách gửi email của
    email_send_ui.py. Chỉ trả về string để cmd_check/pages/1_Check_Domain.py tự hiển thị bằng
    st.code()/print() cho người dùng copy.
    """
    return (
        f"The domain {domain} is actively impersonating our brand by cloning its official "
        "login page to harvest user credentials and OTP tokens. Please add this phishing "
        "URL to your browser security filters to protect users."
    )


def generate_cloudflare_report_text(domain: str, cfg: dict) -> str:
    """Sinh sẵn nội dung mô tả để copy-paste vào form report Cloudflare tại
    https://abuse.cloudflare.com/ (mục Phishing & Malware) — tương tự
    generate_safebrowsing_report_text(), không ghi file, chỉ trả về string.
    """
    return (
        f"The domain {domain} is impersonating our brand by cloning its official login page "
        "to harvest user credentials, OTP tokens, and payment details. "
        "This domain is using Cloudflare services to proxy and conceal the phishing infrastructure. "
        "Please suspend Cloudflare services for this domain and/or display a phishing interstitial warning."
    )



# --------------------------------------------------------------------------
# A1 — HTTP check: page còn sống không, title, redirect, login form
# --------------------------------------------------------------------------

def check_http(domain: str) -> dict:
    """Fetch trang phishing để lấy HTTP status, page title, redirect chain và
    phát hiện login form (input type=password) mà không cần truy cập thủ công.

    Thử HTTPS trước, fallback HTTP nếu SSL error. verify=False vì domain nghi vấn
    thường có cert không hợp lệ. Chỉ đọc 100KB đầu để tránh download trang nặng.
    Kết quả không ghi vào case_log.csv (không thay đổi schema CSV đã có).
    """
    if requests is None:
        return {"error": "requests not installed"}

    ua = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
    for scheme in ("https", "http"):
        url = f"{scheme}://{domain}"
        try:
            r = requests.get(
                url, timeout=10, allow_redirects=True, verify=False,
                headers={"User-Agent": ua}, stream=True,
            )
            # Giới hạn 100KB — tránh download trang nặng, đủ để parse title + form
            raw = b""
            for chunk in r.iter_content(8192):
                raw += chunk
                if len(raw) >= 100_000:
                    break
            text = raw.decode("utf-8", errors="replace")

            title_m = re.search(r"<title[^>]*>([^<]{1,200})", text, re.IGNORECASE)
            has_password = bool(re.search(r'type=["\']password["\']', text, re.IGNORECASE))
            form_count = len(re.findall(r"<form[\s>]", text, re.IGNORECASE))
            redirected = bool(r.history)
            final_url = r.url if r.url != url else None

            return {
                "status_code": r.status_code,
                "scheme": scheme,
                "page_title": title_m.group(1).strip() if title_m else None,
                "has_password_input": has_password,
                "form_count": form_count,
                "redirected": redirected,
                "final_url": final_url,
            }
        except requests.exceptions.SSLError:
            continue  # thử http
        except requests.exceptions.ConnectionError:
            return {"status_code": None, "error": "Không kết nối được — domain có thể đã down"}
        except Exception as e:
            if scheme == "http":
                return {"error": str(e)}
            continue

    return {"error": "Cả HTTPS và HTTP đều thất bại"}


# --------------------------------------------------------------------------
# A2 — Domain age: số ngày kể từ ngày đăng ký
# --------------------------------------------------------------------------

def compute_domain_age_days(who: dict):
    """Tính số ngày domain đã tồn tại từ creation_date của WHOIS.

    get_whois_info() trả về creation_date dạng str(w.get("creation_date")) — có thể là
    "2020-01-15 00:00:00", "[datetime.datetime(2020,1,15,0,0)]" (list repr), hoặc "None".
    Dùng regex tìm pattern YYYY-MM-DD đầu tiên trong chuỗi — đơn giản và đủ robust.
    Trả về int (số ngày) hoặc None nếu không parse được.
    """
    if not isinstance(who, dict):
        return None
    raw = str(who.get("creation_date") or "")
    if not raw or raw == "None":
        return None
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", raw)
    if not m:
        return None
    try:
        dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).days
    except Exception:
        return None


# --------------------------------------------------------------------------
# B2 — MX record check: domain có gửi email không? Provider nào?
# --------------------------------------------------------------------------

def check_mx_records(domain: str) -> dict:
    """Resolve MX records của domain để xác định domain có khả năng gửi email không.

    Nếu có MX records → domain đang được cấu hình để gửi/nhận email → có thể đang
    dùng để gửi phishing email → cần report thêm tới abuse team của mail provider.
    Trả về {"records": [...], "providers": [...]} — providers là list dict từ
    MAIL_PROVIDER_ABUSE cho các provider được nhận diện qua hostname MX.
    Không raise — lỗi trả về {"records": [], "providers": [], "error": str(e)}.
    """
    if dns is None:
        # Fallback: không có dnspython, thử socket (không hỗ trợ timeout per-query)
        return {"records": [], "providers": [], "skipped": "dnspython không có sẵn — pip install dnspython"}

    records = []
    providers_found = {}  # key: provider_key, value: info dict (dedup)

    try:
        answers = dns.resolver.resolve(domain, "MX", lifetime=8.0)
        for rdata in sorted(answers, key=lambda r: r.preference):
            host = str(rdata.exchange).rstrip(".")
            records.append({"host": host, "priority": rdata.preference})
            host_lower = host.lower()
            for key, info in MAIL_PROVIDER_ABUSE.items():
                if key in host_lower and key not in providers_found:
                    providers_found[key] = info
    except dns.resolver.NoAnswer:
        return {"records": [], "providers": []}
    except dns.resolver.NXDOMAIN:
        return {"records": [], "providers": [], "note": "Domain không tồn tại (NXDOMAIN)"}
    except Exception as e:
        return {"records": [], "providers": [], "error": str(e)}

    return {
        "records": records,
        "providers": list(providers_found.values()),
    }


# --------------------------------------------------------------------------
# A3 — URLScan.io: screenshot + DOM analysis (free public API, no key needed)
# --------------------------------------------------------------------------

def urlscan_submit(domain: str, api_key: str = "", use_http: bool = False) -> dict:
    """Submit domain lên URLScan.io để lấy screenshot + DOM/DNS analysis.

    Cần API key miễn phí — đăng ký tại https://urlscan.io/ rồi điền urlscan_api_key
    vào config.ini. Rate limit free: 5000 scans/ngày.
    use_http=True để submit http:// thay vì https:// khi domain không có SSL hoặc bị
    URLScan từ chối do DNS/SSL error.
    """
    if requests is None:
        return {"error": "requests not installed"}
    if not api_key:
        return {"error": "Chưa có urlscan_api_key trong config.ini — đăng ký free tại https://urlscan.io/"}
    scheme = "http" if use_http else "https"
    try:
        r = requests.post(
            "https://urlscan.io/api/v1/scan/",
            json={"url": f"{scheme}://{domain}", "visibility": "public"},
            headers={"Content-Type": "application/json", "API-Key": api_key},
            timeout=15,
        )
        if r.status_code == 429:
            return {"error": "URLScan.io rate limit — thử lại sau vài phút"}
        if r.status_code in (400, 401, 403):
            try:
                msg = r.json().get("message", r.text)
            except Exception:
                msg = r.text
            # DNS Error = domain không resolve được từ phía URLScan — tức là domain đã down
            # hoặc chưa propagate DNS. Trả về warning thay vì error để UI hiện khác biệt.
            if "DNS" in msg or "resolve" in msg.lower() or "Could not" in msg:
                return {
                    "warning": f"URLScan không resolve được domain này ({msg}). "
                               "Có thể domain đã bị gỡ, DNS chưa propagate, hoặc chỉ hoạt động qua IPv6. "
                               "Thử dùng http:// thay vì https:// hoặc kiểm tra lại domain.",
                    "dns_error": True,
                }
            return {"error": f"URLScan từ chối ({r.status_code}): {msg}"}
        r.raise_for_status()
        data = r.json()
        uuid = data.get("uuid") or ""
        return {
            "scan_id": uuid,
            "result_url": f"https://urlscan.io/result/{uuid}/",
            "screenshot_url": f"https://urlscan.io/screenshots/{uuid}.png",
            "status": "submitted",
        }
    except Exception as e:
        return {"error": str(e)}


def urlscan_result(scan_uuid: str, api_key: str = "") -> dict:
    """Lấy kết quả scan đã submit lên URLScan.io. Cần truyền api_key giống lúc submit.

    Trả về {"status": "pending"} nếu scan chưa xong (HTTP 404).
    Trả về dict đầy đủ khi done: screenshot_url, malicious verdict, tags, page_title, IP, country.
    """
    if requests is None:
        return {"error": "requests not installed"}
    try:
        headers = {"API-Key": api_key} if api_key else {}
        r = requests.get(
            f"https://urlscan.io/api/v1/result/{scan_uuid}/",
            headers=headers,
            timeout=15,
        )
        if r.status_code == 404:
            return {"status": "pending"}
        r.raise_for_status()
        data = r.json()
        verdicts = ((data.get("verdicts") or {}).get("overall")) or {}
        page = data.get("page") or {}
        return {
            "status": "done",
            "screenshot_url": f"https://urlscan.io/screenshots/{scan_uuid}.png",
            "result_url": f"https://urlscan.io/result/{scan_uuid}/",
            "malicious": verdicts.get("malicious", False),
            "score": verdicts.get("score", 0),
            "tags": verdicts.get("tags") or [],
            "brands": verdicts.get("brands") or [],
            "page_title": page.get("title"),
            "page_ip": page.get("ip"),
            "page_country": page.get("country"),
        }
    except Exception as e:
        return {"error": str(e)}


def append_urlscan_evidence_to_drafts(drafts: list, urlscan_res: dict) -> list:
    """Append URLScan.io evidence (screenshot link, result URL, verdict) vào cuối tất cả
    draft files đã sinh — gọi từ UI SAU KHI có kết quả URLScan, không phải trong run_check()
    (vì URLScan là bước tùy chọn do người dùng chủ động submit, không chạy tự động).

    Trả về list các draft path đã được cập nhật thành công.
    """
    if not drafts or urlscan_res.get("status") != "done":
        return []

    verdict = "MALICIOUS" if urlscan_res.get("malicious") else "NOT flagged"
    score = urlscan_res.get("score", 0)
    tags = ", ".join(urlscan_res.get("tags") or []) or "—"
    brands = ", ".join(urlscan_res.get("brands") or []) or "—"
    screenshot_url = urlscan_res.get("screenshot_url", "")
    result_url = urlscan_res.get("result_url", "")
    page_title = urlscan_res.get("page_title") or "—"
    page_ip = urlscan_res.get("page_ip") or "—"
    country = urlscan_res.get("page_country") or "—"

    evidence_block = (
        f"\n\n--- Evidence: URLScan.io Analysis ---\n"
        f"Verdict   : {verdict} (score: {score})\n"
        f"Page title: {page_title}\n"
        f"Page IP   : {page_ip} ({country})\n"
        f"Tags      : {tags}\n"
        f"Brands    : {brands}\n"
        f"Screenshot: {screenshot_url}\n"
        f"Full report: {result_url}\n"
        f"--- End of URLScan evidence ---\n"
    )

    updated = []
    for path in drafts:
        try:
            if not os.path.isfile(path):
                continue
            with open(path, encoding="utf-8") as f:
                content = f.read()
            # Không append nếu đã có rồi (tránh duplicate khi bấm nhiều lần)
            if "URLScan.io Analysis" in content:
                updated.append(path)
                continue
            with open(path, "a", encoding="utf-8") as f:
                f.write(evidence_block)
            updated.append(path)
        except Exception:
            pass  # file bị khóa hoặc lỗi khác — bỏ qua, không làm crash UI
    return updated


# --------------------------------------------------------------------------
# B3 — Wayback Machine: archive trang phishing làm bằng chứng tĩnh
# --------------------------------------------------------------------------

def wayback_archive(domain: str) -> dict:
    """Archive domain lên Wayback Machine làm bằng chứng tĩnh.

    Quy trình 2 bước:
    1. Kiểm tra snapshot cũ qua Availability API — nếu đã có snapshot trong 7 ngày gần nhất,
       trả về ngay mà không cần archive lại (tránh rate limit + nhanh hơn).
    2. Nếu chưa có, gửi Save Page Now (GET /save/{url}) và đọc Content-Location header.
       Nhiều error code từ Wayback (520, 523...) là lỗi upstream khi Wayback không thể
       fetch domain — thường gặp với domain mới/down/chặn bot. Xử lý gracefully, trả về
       link archive dự kiến (có thể verify thủ công) thay vì báo fail hoàn toàn.
    Không raise — lỗi trả về {"error": str(e), "manual_url": ...}.
    """
    if requests is None:
        return {"error": "requests not installed"}

    url = f"https://{domain}"
    manual_url = f"https://web.archive.org/web/*/{url}"  # link tra cứu thủ công luôn có sẵn

    # ── Bước 1: kiểm tra snapshot đã có qua Availability API (nhanh, không tốn quota) ──
    try:
        avail = requests.get(
            "https://archive.org/wayback/available",
            params={"url": url, "timestamp": datetime.now(timezone.utc).strftime("%Y%m%d")},
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0 (compatible; PhishingToolkit/1.0)"},
        )
        if avail.status_code == 200:
            snap = avail.json().get("archived_snapshots", {}).get("closest", {})
            if snap.get("available") and snap.get("url"):
                snap_ts = snap.get("timestamp", "")
                # Chỉ dùng snapshot cũ nếu còn trong vòng 7 ngày
                try:
                    snap_dt = datetime.strptime(snap_ts[:8], "%Y%m%d").replace(tzinfo=timezone.utc)
                    age_days = (datetime.now(timezone.utc) - snap_dt).days
                    if age_days <= 7:
                        return {
                            "archive_url": snap["url"],
                            "status": "existing_snapshot",
                            "note": f"Snapshot có sẵn từ {snap_ts[:8]} (cách đây {age_days} ngày) — không cần archive lại.",
                        }
                except Exception:
                    pass
    except Exception:
        pass  # Availability API lỗi — tiếp tục thử save

    # ── Bước 2: gửi Save Page Now ────────────────────────────────────────────
    try:
        r = requests.get(
            f"https://web.archive.org/save/{url}",
            timeout=35,
            allow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; PhishingToolkit/1.0)"},
        )
        # Wayback trả về Content-Location header dạng /web/20240101120000/https://...
        loc = r.headers.get("Content-Location", "")
        if loc.startswith("/web/"):
            return {"archive_url": f"https://web.archive.org{loc}", "status": "archived"}
        if "web.archive.org/web/" in r.url:
            return {"archive_url": r.url, "status": "archived"}
        if r.status_code == 200:
            ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
            return {
                "archive_url": f"https://web.archive.org/web/{ts}/{url}",
                "status": "archived",
                "note": "Link ước tính — verify tại web.archive.org nếu cần.",
            }
        if r.status_code == 429:
            return {"error": "Wayback Machine rate limit — thử lại sau vài phút.", "manual_url": manual_url}
        # 520, 523, 5xx: Wayback không thể fetch domain (domain down, chặn bot, lỗi upstream)
        # Vẫn trả về link dự kiến để đính kèm vào report — người nhận có thể tự verify
        if r.status_code >= 500:
            ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
            return {
                "archive_url": f"https://web.archive.org/web/{ts}/{url}",
                "status": "save_failed",
                "note": (
                    f"Wayback trả về HTTP {r.status_code} khi cố archive — "
                    "domain có thể đang down, chặn bot, hoặc Wayback gặp lỗi upstream. "
                    "Link bên dưới là ước tính, cần verify thủ công."
                ),
                "manual_url": manual_url,
            }
        return {"error": f"Wayback trả về HTTP {r.status_code}", "manual_url": manual_url}
    except Exception as e:
        return {"error": str(e), "manual_url": manual_url}


def append_wayback_evidence_to_drafts(drafts: list, wayback_result: dict) -> list:
    """Append link Wayback Machine archive vào cuối tất cả draft files đã sinh.

    Idempotent: kiểm tra 'Wayback Machine Archive' đã có trong file chưa trước khi append.
    Cùng pattern với append_urlscan_evidence_to_drafts — không crash nếu file bị khóa.
    """
    archive_url = wayback_result.get("archive_url")
    if not drafts or not archive_url:
        return []

    note = wayback_result.get("note", "")
    evidence_block = (
        f"\n\n--- Evidence: Wayback Machine Archive ---\n"
        f"Archived URL : {archive_url}\n"
        + (f"Note         : {note}\n" if note else "")
        + f"--- End of Wayback evidence ---\n"
    )

    updated = []
    for path in drafts:
        try:
            if not os.path.isfile(path):
                continue
            with open(path, encoding="utf-8") as f:
                content = f.read()
            if "Wayback Machine Archive" in content:
                updated.append(path)
                continue
            with open(path, "a", encoding="utf-8") as f:
                f.write(evidence_block)
            updated.append(path)
        except Exception:
            pass
    return updated

def compute_reputation(vt: dict, gsb: dict) -> dict:
    vt_malicious = vt.get("malicious") or 0
    vt_suspicious = vt.get("suspicious") or 0
    gsb_flagged = gsb.get("flagged") is True

    reasons = []
    if vt_malicious:
        reasons.append(f"VirusTotal: {vt_malicious} engine gắn cờ malicious")
    if vt_suspicious:
        reasons.append(f"VirusTotal: {vt_suspicious} engine gắn cờ suspicious")
    if gsb_flagged:
        reasons.append("Google Safe Browsing: đã gắn cờ URL này")

    vt_unknown = "error" in vt or "skipped" in vt or "note" in vt
    gsb_unknown = "error" in gsb or "skipped" in gsb

    if vt_malicious or gsb_flagged:
        verdict = "flagged"
        label = "ĐÃ BỊ GẮN CỜ bởi bên thứ 3"
    elif vt_suspicious:
        verdict = "suspicious"
        label = "Có dấu hiệu nghi ngờ (suspicious)"
    elif vt_unknown and gsb_unknown:
        verdict = "unknown"
        label = "Chưa đủ dữ liệu để đánh giá (thiếu API key hoặc domain chưa từng bị quét)"
    else:
        verdict = "clean"
        label = "Chưa bên nào gắn cờ tại thời điểm kiểm tra"

    return {"verdict": verdict, "label": label, "reasons": reasons}


# --------------------------------------------------------------------------
# crt.sh — Certificate Transparency search (tìm domain "anh em")
# --------------------------------------------------------------------------

def crtsh_related(keyword: str):
    if requests is None:
        return {"error": "Thư viện requests chưa được cài"}
    try:
        r = requests.get(
            "https://crt.sh/", params={"q": f"%{keyword}%", "output": "json"}, timeout=25
        )
        r.raise_for_status()
        entries = r.json()
        names = set()
        for e in entries:
            for n in str(e.get("name_value", "")).split("\n"):
                n = n.strip().lower()
                if n and not n.startswith("*."):
                    names.add(n)
        return {"count": len(names), "domains": sorted(names)}
    except Exception as e:
        return {"error": str(e)}


# --------------------------------------------------------------------------
# dnstwist — brand monitoring / typosquat scan
# --------------------------------------------------------------------------

def brand_scan(domain: str, limit: int = 50):
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "dnstwist", "-r", "-t", "60", "-f", "json", domain],
            capture_output=True,
            text=True,
            timeout=600,
        )
        if proc.returncode != 0 and not proc.stdout.strip():
            return {"error": proc.stderr.strip() or "dnstwist chạy lỗi (đã cài dnstwist chưa?)"}
        results = json.loads(proc.stdout)
        return {"count": len(results), "results": results[:limit]}
    except FileNotFoundError:
        return {"error": "Chưa cài dnstwist (pip install dnstwist)"}
    except subprocess.TimeoutExpired:
        return {"error": "dnstwist quét quá 10 phút chưa xong (domain/brand quá phổ biến, quá nhiều biến thể). Thử lại với --fuzzers để giới hạn bớt thuật toán sinh biến thể."}
    except Exception as e:
        return {"error": str(e)}


# --------------------------------------------------------------------------
# CSV log
# --------------------------------------------------------------------------

def log_case(row: dict):
    is_new = not os.path.exists(LOG_PATH)
    with open(LOG_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(row.keys()))
        if is_new:
            writer.writeheader()
        writer.writerow(row)


# --------------------------------------------------------------------------
# Email drafts
# --------------------------------------------------------------------------

def generate_email_drafts(domain, cert, who, vt, cfg):
    os.makedirs(REPORTS_DIR, exist_ok=True)
    written = []

    contact_name = cfg.get("contact_name") or ""
    contact_email = cfg.get("contact_email") or ""
    signature = f"\nRegards,\n{contact_name}\n{contact_email}\n" if contact_name else "\nRegards,\n"
    detected_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    registrar = who.get("registrar") if isinstance(who, dict) else None
    abuse_emails = who.get("emails") if isinstance(who, dict) else None
    # who.get("emails") từ python-whois có thể trả về list — cần join trước khi ghi vào file.
    if isinstance(abuse_emails, list):
        abuse_emails = ", ".join(e for e in abuse_emails if e)

    if registrar:
        path = os.path.join(REPORTS_DIR, f"{domain}_registrar_report.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"""To: {abuse_emails or '[TRA ABUSE EMAIL TẠI https://lookup.icann.org/]'}
Subject: Phishing Abuse Report - {domain}

Dear {registrar} Abuse Team,

We are writing to report the domain {domain} for active phishing activity
targeting users of our brand.

This domain is impersonating our brand's official website by cloning its login
interface to harvest user credentials, OTP tokens, and personal data without
authorization. Users who visit this site are deceived into submitting sensitive
information (login credentials, passwords, payment details, etc.).

Evidence:
- Domain: {domain}
- Registrar: {registrar}
- SSL Issuer: {cert.get('issuer', 'N/A')}
- SSL Serial: {cert.get('serial', 'N/A')}
- VirusTotal report: {vt.get('link', 'N/A')}
- First detected: {detected_date}

We request the immediate suspension or transfer-lock of this domain in
accordance with your Acceptable Use Policy and ICANN Registrar Accreditation
Agreement (RAA) §3.18.

Please confirm receipt and any action taken at your earliest convenience.
{signature}""")
        written.append(path)

    ca_note = match_ca_notes(cert.get("issuer"))
    if ca_note and ca_note.get("report_url"):
        path = os.path.join(REPORTS_DIR, f"{domain}_ca_report.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"""Subject: URGENT: SSL Certificate Revocation Request - {domain}

Dear {ca_note['ca'].title()} Security / Abuse Team,

We are requesting the immediate revocation of the SSL certificate issued for
the domain {domain}, which is being used to impersonate our brand for phishing
and credential harvesting.

The fraudulent site clones our brand's official login page to deceive users
into submitting sensitive information (credentials, OTP tokens, payment details).

Certificate details:
- Domain: {domain}
- Serial Number: {cert.get('serial', 'N/A')}
- Issued by: {cert.get('issuer', 'N/A')}
- VirusTotal report: {vt.get('link', 'N/A')}
- First detected: {detected_date}

Revoking this certificate will immediately degrade the phishing site's
credibility and browser trust indicators. We kindly request expedited handling
under your abuse/phishing revocation policy.
{signature}""")
        written.append(path)

    return written


def generate_apwg_draft(domain, vt, cfg):
    """Sinh sẵn email report gửi APWG eCrime (reportphishing@apwg.org).

    APWG (Anti-Phishing Working Group) là tổ chức phối hợp xử lý phishing quốc tế,
    nhận report qua email và phân phối tới các thành viên (ISP, hosting, browser vendors).
    Địa chỉ reportphishing@apwg.org đã được xác nhận hoạt động.
    """
    contact_name = cfg.get("contact_name") or ""
    contact_email = cfg.get("contact_email") or ""
    signature = f"\nRegards,\n{contact_name}\n{contact_email}\n" if contact_name else "\nRegards,\n"

    os.makedirs(REPORTS_DIR, exist_ok=True)
    path = os.path.join(REPORTS_DIR, f"{domain}_apwg_report.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"""To: reportphishing@apwg.org
Subject: Phishing URL Report - {domain}

Hello APWG eCrime Team,

We are reporting the following URL as an active phishing site impersonating
our brand to harvest user credentials and sensitive information.

Phishing URL: https://{domain}
Domain: {domain}
VirusTotal report: {vt.get('link', 'N/A')}

The site clones our brand's official login page to deceive users into submitting
their credentials, OTP tokens, and payment details. Please add this URL to the
APWG eCrime dataset to protect users across platforms.
{signature}""")
    return path


def generate_hosting_draft(domain, ip, ip_whois, cfg):
    """Sinh draft DMCA/AUP takedown gửi ISP/hosting sở hữu IP gốc."""
    contact_name = cfg.get("contact_name") or ""
    contact_email = cfg.get("contact_email") or ""
    signature = f"\nRegards,\n{contact_name}\n{contact_email}\n" if contact_name else "\nRegards,\n"

    os.makedirs(REPORTS_DIR, exist_ok=True)
    org = ip_whois.get("org") or "[TÊN TỔ CHỨC HOSTING CHƯA XÁC ĐỊNH]"
    abuse_email = ip_whois.get("abuse_email") or "[TRA ABUSE EMAIL TẠI https://lookup.icann.org/ hoặc rdap.arin.net]"
    asn = ip_whois.get("asn") or "N/A"
    path = os.path.join(REPORTS_DIR, f"{domain}_hosting_report.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"""To: {abuse_email}
Subject: URGENT: Phishing Site Takedown Request - {domain} (IP: {ip})

Dear {org} Abuse Team,

We are writing to request the immediate suspension of a phishing website hosted
on your infrastructure that is impersonating our brand.

The website at http://{domain} (hosted on IP {ip}) is an unauthorized clone of
our brand's official platform, actively harvesting user credentials, OTP tokens,
and payment details from unsuspecting visitors.

Hosting details:
- Phishing domain: {domain}
- Origin IP: {ip}
- ASN: {asn}
- Hosting organization: {org}

Note: This IP was identified as a candidate origin server via subdomain
enumeration — please verify independently before taking action.

We request you suspend this hosting account immediately under your Acceptable
Use Policy. This is causing ongoing harm to our users and brand reputation.
{signature}""")
    return path


def generate_registry_draft(domain, registry_info, cfg):
    """Sinh draft leo thang gửi Registry quản lý ccTLD."""
    if registry_info.get("source") == "not_found":
        return None

    contact_name = cfg.get("contact_name") or ""
    contact_email = cfg.get("contact_email") or ""
    signature = f"\nRegards,\n{contact_name}\n{contact_email}\n" if contact_name else "\nRegards,\n"

    os.makedirs(REPORTS_DIR, exist_ok=True)
    if registry_info["source"] == "static_table":
        registry_name = registry_info.get("registry") or "Registry"
        abuse_email = registry_info.get("abuse_email") or "[TRA ABUSE EMAIL TẠI https://www.iana.org/domains/root/db]"
        note_line = f"\nSpecial note: {registry_info['note']}\n" if registry_info.get("note") else ""
        raw_section = ""
    else:
        registry_name = f"Registry (via {registry_info.get('whois_server', 'IANA')})"
        abuse_email = "[TRA ABUSE EMAIL TỪ NỘI DUNG WHOIS THÔ BÊN DƯỚI, hoặc https://www.iana.org/domains/root/db]"
        note_line = ""
        raw_section = (
            f"\n--- WHOIS raw from {registry_info.get('whois_server')} (for reference) ---\n"
            f"{registry_info.get('raw', '')}\n"
        )

    path = os.path.join(REPORTS_DIR, f"{domain}_registry_report.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"""To: {abuse_email}
Subject: URGENT: Phishing Domain Suspension Request - {domain}

Dear {registry_name} Abuse Department,

We are escalating a phishing domain abuse report directly to your registry
regarding the domain {domain}, which is being used to impersonate our brand
and harvest user credentials.

We have already submitted abuse reports to the registrar, but are escalating
to the registry level due to the ongoing harm and time-sensitive nature of this
phishing campaign.

We respectfully request that {domain} be placed on ClientHold status
immediately to stop active credential harvesting from users of our brand.

Domain: {domain}
{note_line}
This escalation is made in accordance with your registry's abuse handling
policy and ICANN compliance requirements.
{signature}{raw_section}""")
    return path


def generate_vncert_draft(domain, cert, vt, cfg):
    """Sinh draft report VNCERT — LUÔN sinh, người dùng tự quyết định có gửi không."""
    contact_name = cfg.get("contact_name") or ""
    contact_email = cfg.get("contact_email") or ""
    signature = f"\nTrân trọng,\n{contact_name}\n{contact_email}\n" if contact_name else "\nTrân trọng,\n"

    os.makedirs(REPORTS_DIR, exist_ok=True)
    path = os.path.join(REPORTS_DIR, f"{domain}_vncert_report.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"""LƯU Ý: CHỈ gửi report này nếu domain nhắm vào nạn nhân tại Việt Nam
(vd. giả mạo thương hiệu/dịch vụ Việt Nam, nội dung tiếng Việt, nhắm vào người
dùng Việt Nam). Tool không tự xác định được điều này — tự đánh giá trước khi gửi.

To: report@vncert.vn
Subject: [Phản ánh phishing] {domain} giả mạo thương hiệu

Kính gửi VNCERT/CC,

Chúng tôi xin phản ánh domain {domain} đang thực hiện hành vi giả mạo thương
hiệu của chúng tôi nhằm lừa đảo, đánh cắp thông tin nhạy cảm của người dùng
(tài khoản đăng nhập, mã OTP, thông tin thẻ ngân hàng...).

Domain giả mạo hoạt động bằng cách sao chép giao diện đăng nhập chính thức của
thương hiệu chúng tôi, dụ người dùng nhập thông tin vào trang giả mạo.

Thông tin kỹ thuật:
- Domain: {domain}
- SSL Issuer: {cert.get('issuer', 'N/A')}
- SSL Serial: {cert.get('serial', 'N/A')}
- VirusTotal: {vt.get('link', 'N/A')}

Kính mong VNCERT/CC hỗ trợ xử lý, phối hợp với registrar/hosting để gỡ bỏ
domain này nhằm bảo vệ người dùng Việt Nam.
{signature}""")
    return path


# --------------------------------------------------------------------------
# Gửi báo cáo qua email thật (SMTP)
# --------------------------------------------------------------------------
#
# Chỉ gửi khi người dùng đã tự xác nhận thủ công (Streamlit: tick checkbox "đã đọc và xác
# nhận" trước khi nút "Gửi" hết disabled; CLI `send`: input("Gửi email này? (y/N): ")) — không
# có đường nào tự động gửi hàng loạt không qua duyệt, giữ đúng nguyên tắc xác minh-trước-khi-
# report xuyên suốt dự án (plan_phishing_takedown.md bước 2).


def _parse_proxy_url(proxy_str: str):
    """Parse chuỗi proxy thành tuple (proxy_type, host, port, username, password).

    Hỗ trợ: ``socks5://[user:pass@]host:port``, ``socks4://...``, ``http://...``,
    hoặc ``host:port`` (mặc định SOCKS5 khi không có scheme).
    Trả về None nếu PySocks chưa được cài.
    """
    if _socks is None:
        return None
    s = proxy_str.strip()
    proxy_type = _SOCKS5
    username = password = None

    if "://" in s:
        scheme, s = s.split("://", 1)
        scheme = scheme.lower()
        if scheme == "socks4":
            proxy_type = _SOCKS4
        elif scheme == "http":
            proxy_type = _SOCKS_HTTP
        # else: socks5 or unknown → SOCKS5

    if "@" in s:
        creds, s = s.rsplit("@", 1)
        username, _, password = creds.partition(":")

    host, _, port_str = s.rpartition(":")
    if not host:            # không có ":" → chỉ có host, dùng port mặc định
        host, port_str = s, "1080"
    return proxy_type, host.strip(), int(port_str.strip()), username or None, password or None


class _SMTPWithProxy(smtplib.SMTP):
    """smtplib.SMTP subclass routing traffic qua SOCKS/HTTP proxy (PySocks).

    Ghi đè _get_socket() — điểm kết nối TCP duy nhất mà smtplib.SMTP.connect() gọi —
    để tạo socks.socksocket() thay vì socket.create_connection() thông thường.
    self._proxy_info PHẢI được gán TRƯỚC super().__init__() vì __init__ gọi connect()
    ngay lập tức.
    """
    def __init__(self, host: str, port: int, proxy_info: tuple, timeout: float = 15):
        self._proxy_info = proxy_info   # ← gán trước, super().__init__ sẽ gọi _get_socket
        super().__init__(host, port, timeout=timeout)

    def _get_socket(self, host, port, timeout):
        proxy_type, p_host, p_port, p_user, p_pass = self._proxy_info
        sock = _socks.socksocket()
        sock.set_proxy(proxy_type, p_host, p_port, username=p_user, password=p_pass)
        sock.settimeout(timeout or socket.getdefaulttimeout())
        sock.connect((host, port))
        return sock


def _imap_save_sent(account: dict, raw_msg: bytes) -> str | None:
    """Lưu 1 bản copy email đã gửi vào thư mục Sent trên IMAP server.

    Chỉ chạy khi account có `imap_host` (hoặc dùng chung `host`), không raise —
    lỗi trả về chuỗi mô tả lỗi, thành công trả về None.
    Gmail tự lưu Sent khi gửi qua SMTP nên không cần — hàm này dành cho mail server
    tự host (vd. mail.camellrp.com) không tự động làm điều đó.
    """
    # Ưu tiên imap_host riêng, fallback dùng chung host với SMTP
    imap_host = account.get("imap_host") or account.get("host", "")
    imap_port = int(account.get("imap_port", 993))
    username = account.get("username", "")
    password = account.get("password", "")

    # Không làm gì với Gmail — Gmail tự lưu Sent qua SMTP
    if "gmail.com" in imap_host.lower():
        return None

    try:
        mail = imaplib.IMAP4_SSL(imap_host, imap_port)
        mail.login(username, password)

        # Tìm thư mục Sent — các mail server dùng tên khác nhau
        sent_folder = None
        _, folders = mail.list()
        for folder_line in (folders or []):
            name = folder_line.decode("utf-8", errors="ignore") if isinstance(folder_line, bytes) else str(folder_line)
            name_lower = name.lower()
            for candidate in ("sent", "sent items", "sent messages", "sent mail", "gesendete", "enviados"):
                if f'"{candidate}"' in name_lower or f"/{candidate}" in name_lower or name_lower.endswith(candidate):
                    # Trích tên folder thực từ chuỗi IMAP LIST response
                    parts = name.rsplit('"', 1)
                    sent_folder = parts[-1].strip().strip('"') if len(parts) > 1 else candidate
                    break
            if sent_folder:
                break
        if not sent_folder:
            sent_folder = "Sent"  # fallback mặc định

        # APPEND email vào Sent folder với flag \Seen
        now = imaplib.Time2Internaldate(datetime.now().timestamp())
        mail.append(sent_folder, r"\Seen", now, raw_msg)
        mail.logout()
        return None
    except Exception as e:
        return str(e)


def _send_via_account(account: dict, proxy_str: str | None, to: str, subject: str, body: str) -> dict:
    """Gửi email qua 1 SMTP account cụ thể, tùy chọn qua proxy.

    Tự detect mode:
    - port 465 hoặc ssl=true  → smtplib.SMTP_SSL (SSL/TLS ngay từ đầu, dùng cho mail.camellrp.com)
    - port 587 (mặc định)     → smtplib.SMTP + starttls() (STARTTLS, dùng cho Gmail)
    Không raise — trả về dict {"account", "proxy", "success", "error"}.
    """
    username = account.get("username", "")
    host = account.get("host", "smtp.gmail.com")
    port = int(account.get("port", 587))
    password = account.get("password", "")
    use_ssl = bool(account.get("ssl", False)) or port == 465
    proxy_label = proxy_str or "—"
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = username
        msg["To"] = to

        to_list = [a.strip() for a in to.split(",") if a.strip()]

        if proxy_str:
            if _socks is None:
                raise RuntimeError("Proxy được cấu hình nhưng PySocks chưa cài (pip install PySocks)")
            proxy_info = _parse_proxy_url(proxy_str)
            if proxy_info is None:
                raise RuntimeError(f"Không parse được proxy: {proxy_str}")
            server_ctx = _SMTPWithProxy(host, port, proxy_info, timeout=15)
            with server_ctx as server:
                if not use_ssl:
                    server.starttls()
                server.login(username, password)
                server.sendmail(username, to_list, msg.as_string())
        elif use_ssl:
            with smtplib.SMTP_SSL(host, port, timeout=15) as server:
                server.login(username, password)
                server.sendmail(username, to_list, msg.as_string())
        else:
            with smtplib.SMTP(host, port, timeout=15) as server:
                server.starttls()
                server.login(username, password)
                server.sendmail(username, to_list, msg.as_string())

        # Lưu copy vào Sent folder qua IMAP (không làm gì với Gmail — tự lưu)
        imap_err = _imap_save_sent(account, msg.as_bytes())
        imap_note = f" (IMAP Sent: {imap_err})" if imap_err else ""

        return {"account": username, "proxy": proxy_label, "success": True, "error": None, "imap_note": imap_note}
    except Exception as e:
        return {"account": username, "proxy": proxy_label, "success": False, "error": str(e)}


def send_report_email_bulk(to: str, subject: str, body: str, cfg: dict) -> list:
    """Gửi email qua TẤT CẢ smtp_accounts đã cấu hình, mỗi account dùng 1 proxy xoay vòng
    (cycle nếu ít proxy hơn account), tất cả chạy ĐỒNG THỜI (ThreadPoolExecutor).

    Trả về list dict — 1 phần tử / account: {"account", "proxy", "success", "error"}.
    Không raise — nơi gọi luôn nhận được list để render kết quả / ghi log.
    """
    accounts = cfg.get("smtp_accounts", [])
    proxies = cfg.get("smtp_proxies", [])

    if not accounts:
        return [{"account": "—", "proxy": "—", "success": False,
                 "error": "Chưa cấu hình smtp_accounts trong config.ini"}]

    # Pair mỗi account với proxy xoay vòng (vd. 3 account + 2 proxy → proxy 0, 1, 0)
    pairs = [
        (account, proxies[i % len(proxies)] if proxies else None)
        for i, account in enumerate(accounts)
    ]

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(pairs)) as pool:
        futures = {
            pool.submit(_send_via_account, account, proxy, to, subject, body): i
            for i, (account, proxy) in enumerate(pairs)
        }
        # Giữ thứ tự theo index account (as_completed trả về không theo thứ tự)
        indexed = {}
        for future in concurrent.futures.as_completed(futures):
            indexed[futures[future]] = future.result()
    return [indexed[i] for i in sorted(indexed)]


def send_report_email_single(to: str, subject: str, body: str, account: dict, proxy_str: str | None = None) -> dict:
    """Gửi email qua 1 account chỉ định (dùng khi UI cho phép chọn account cụ thể — P3).

    Trả về dict {"account", "proxy", "success", "error"} — không raise.
    """
    return _send_via_account(account, proxy_str, to, subject, body)

# file khi ghi sent_log.csv, không liên quan gì tới parse_draft_email() bên dưới.
_DRAFT_FILENAME_SUFFIXES = [
    "_registrar_report.txt",
    "_ca_report.txt",
    "_apwg_report.txt",
    "_hosting_report.txt",
    "_registry_report.txt",
    "_vncert_report.txt",
]


def domain_from_draft_filename(filename: str) -> str:
    for suffix in _DRAFT_FILENAME_SUFFIXES:
        if filename.endswith(suffix):
            return filename[: -len(suffix)]
    return os.path.splitext(filename)[0]


def parse_draft_email(path: str) -> dict:
    """Đọc 1 file draft đã sinh bởi generate_*_draft() ở trên, tách ra {"to", "subject", "body"}.

    KHÔNG giả định "To:"/"Subject:" luôn nằm ở 2 dòng đầu file: generate_vncert_draft() ghi 1
    đoạn cảnh báo ("LƯU Ý: CHỈ gửi report này nếu...") đứng TRƯỚC khối header, có dòng trống ở
    giữa — nếu chỉ lấy "dòng trống đầu tiên trong cả file" làm ranh giới body thì sẽ cắt nhầm
    ngay sau đoạn cảnh báo, làm "to"/"subject" thật ở phía dưới lẫn vào phần đầu của "body". Vì
    vậy hàm này quét toàn file tìm dòng "To:" đầu tiên (có thể không tồn tại — draft CA report
    của generate_email_drafts() cố ý không có dòng này vì nhiều CA dùng web form thay vì email)
    và dòng "Subject:" đầu tiên (luôn tồn tại, trừ file hỏng), rồi lấy body là phần sau dòng
    trống đầu tiên NGAY SAU dòng Subject đó — không phải dòng trống đầu tiên của cả file.

    "to" trả về None (coi như KHÔNG gửi được qua email) nếu dòng "To:" không tồn tại, hoặc giá
    trị của nó là 1 placeholder cần tra cứu thủ công (chứa "[TRA ABUSE EMAIL" hoặc "[KHÔNG CÓ")
    — không được cố gửi tới 1 chuỗi placeholder như vậy.
    """
    with open(path, encoding="utf-8") as f:
        lines = f.read().split("\n")

    to = None
    subject = None
    subject_idx = None
    for i, line in enumerate(lines):
        if to is None and line.startswith("To:"):
            to = line[len("To:"):].strip()
        if subject is None and line.startswith("Subject:"):
            subject = line[len("Subject:"):].strip()
            subject_idx = i
            break  # "Subject:" luôn đứng sau "To:" (nếu có) trong mọi draft hiện tại — dừng quét

    if subject_idx is None:
        return {"to": None, "subject": None, "body": "\n".join(lines)}

    body_start = subject_idx + 1
    for i in range(subject_idx + 1, len(lines)):
        if lines[i].strip() == "":
            body_start = i + 1
            break
    body = "\n".join(lines[body_start:])

    if to and ("[TRA ABUSE EMAIL" in to or "[KHÔNG CÓ" in to):
        to = None
    if to:
        to = _normalize_to_addresses(to)

    return {"to": to, "subject": subject, "body": body}


def _normalize_to_addresses(to: str) -> str:
    """Chuẩn hóa trường To: về dạng 'addr1, addr2' thuần túy.

    Xử lý 2 trường hợp lỗi legacy:
    - Python list repr: "['addr1', 'addr2']" — do python-whois trả về list, bị ghi nguyên vào file
    - String list đơn: giữ nguyên

    Trả về chuỗi comma-separated sạch để dùng trong header email và sendmail().
    """
    import ast
    if to and to.startswith("["):
        try:
            parsed = ast.literal_eval(to)
            if isinstance(parsed, list):
                return ", ".join(str(e).strip() for e in parsed if e)
        except Exception:
            pass
    return to
    """Gửi 1 email report thật qua SMTP (smtplib, thư viện chuẩn, không cần cài thêm gì).

    Chỉ được gọi SAU KHI người dùng đã xác nhận thủ công ở nơi gọi (xem comment đầu section
    này) — hàm này tự nó không có gate xác nhận nào, không dùng trực tiếp từ nơi khác mà bỏ
    qua bước xác nhận đó. Bọc try/except toàn bộ và không raise ra ngoài, để nơi gọi luôn nhận
    được dict để hiển thị/ghi log thay vì làm crash Streamlit hoặc CLI.

    Backward compat: dùng account đầu tiên trong smtp_accounts (nếu có), hoặc fallback về
    smtp_host/username/password cũ. Gửi qua proxy đầu tiên nếu smtp_proxies được cấu hình.
    """
    accounts = cfg.get("smtp_accounts", [])
    proxies = cfg.get("smtp_proxies", [])
    if accounts:
        result = _send_via_account(accounts[0], proxies[0] if proxies else None, to, subject, body)
        return {"success": result["success"], "error": result.get("error")}
    # Fallback về keys cũ
    if not cfg.get("smtp_host") or not cfg.get("smtp_username") or not cfg.get("smtp_password"):
        return {
            "success": False,
            "error": "Chưa cấu hình đủ [smtp] trong config.ini (host/username/password)",
        }
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = cfg["smtp_username"]
        msg["To"] = to
        to_list = [a.strip() for a in to.split(",") if a.strip()]

        with smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"], timeout=15) as server:
            server.starttls()
            server.login(cfg["smtp_username"], cfg["smtp_password"])
            server.sendmail(cfg["smtp_username"], to_list, msg.as_string())
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}


def log_sent(row: dict):
    """Ghi 1 dòng vào sent_log.csv (file RIÊNG, không phải cột mới trong case_log.csv — giữ
    đúng nguyên tắc không đổi schema CSV đã có dữ liệu thật, đã áp dụng cho reputation/
    cdn_detected/origin_ip_scan/registry_contact trước đó). Gọi cho mọi lần gửi, kể cả thất
    bại, để sent_log.csv là nhật ký đầy đủ của mọi lần ai đó đã bấm gửi.
    """
    is_new = not os.path.exists(SENT_LOG_PATH)
    with open(SENT_LOG_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(row.keys()))
        if is_new:
            writer.writeheader()
        writer.writerow(row)


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def run_check(target: str, submit: bool, cfg: dict) -> dict:
    """Chạy toàn bộ pipeline kiểm tra 1 domain và trả về dict kết quả đầy đủ.

    NGUỒN LOGIC DUY NHẤT cho lệnh `check`: cả CLI (cmd_check) và Streamlit UI
    gọi thẳng hàm này rồi tự quyết định cách hiển thị, để không bao giờ lệch
    kết quả giữa hai giao diện.
    """
    domain = normalize_domain(target)

    try:
        cert = get_cert_info(domain)
    except Exception as e:
        cert = {"error": str(e)}

    who = get_whois_info(domain)
    cf = is_cloudflare(who.get("name_servers")) if isinstance(who, dict) else False
    domain_age_days = compute_domain_age_days(who)

    try:
        http_check = check_http(domain)
    except Exception as e:
        http_check = {"error": str(e)}

    try:
        mx_records = check_mx_records(domain)
    except Exception as e:
        mx_records = {"records": [], "providers": [], "error": str(e)}

    # detect_cdn()/scan_common_subdomains() tự bọc try/except nội bộ (per-nguồn tín hiệu,
    # per-subdomain) nên hiếm khi raise ra ngoài, nhưng vẫn bọc thêm 1 lớp ở đây để lỗi bất ngờ
    # (vd. domain không resolve được gì cả) không làm hỏng kết quả SSL/WHOIS/VirusTotal đã lấy
    # được ở trên — cùng nguyên tắc với log_case/generate_email_drafts bên dưới.
    try:
        cdn_detected = detect_cdn(domain)
    except Exception:
        cdn_detected = []

    try:
        origin_ip_scan = scan_common_subdomains(domain)
    except Exception:
        origin_ip_scan = {}

    vt = check_virustotal(domain, cfg["vt_api_key"])
    virustotal_submit = None
    if vt.get("note") == "Domain chưa từng được VirusTotal quét trước đây" and submit:
        virustotal_submit = submit_virustotal(domain, cfg["vt_api_key"])

    gsb = check_safebrowsing(f"http://{domain}", cfg["gsb_api_key"])

    reputation = compute_reputation(vt, gsb)

    ca_note = match_ca_notes(cert.get("issuer"))

    log_row = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "domain": domain,
        "ip": cert.get("ip"),
        "ssl_issuer": cert.get("issuer"),
        "ssl_serial": cert.get("serial"),
        "registrar": who.get("registrar") if isinstance(who, dict) else None,
        "cloudflare": cf,
        "vt_malicious": vt.get("malicious"),
        "vt_link": vt.get("link"),
        "gsb_flagged": gsb.get("flagged"),
        "status": "detected",
    }
    # log_case/generate_email_drafts chỉ ghi file phụ trợ (log, email draft) — nếu
    # chúng lỗi (vd. case_log.csv đang bị khóa bởi Excel), phần điều tra
    # SSL/WHOIS/VirusTotal/Safe Browsing đã lấy được ở trên vẫn phải trả về, không
    # được để exception ở bước ghi file làm mất toàn bộ kết quả đã có.
    try:
        log_case(log_row)
        log_error = None
    except Exception as e:
        log_error = str(e)

    try:
        drafts = generate_email_drafts(domain, cert, who, vt, cfg)
        drafts_error = None
    except Exception as e:
        drafts = []
        drafts_error = str(e)

    try:
        apwg_draft = generate_apwg_draft(domain, vt, cfg)
        drafts.append(apwg_draft)
    except Exception as e:
        drafts_error = (drafts_error + "; " if drafts_error else "") + f"APWG draft: {e}"

    # Hosting/ISP takedown (03_Technical_Guide.md mục 3) — CHỈ chạy khi origin_ip_scan (ở trên)
    # tìm thấy ít nhất 1 subdomain có IP KHÁC cert["ip"]. Đặt SAU khối openphish (không phải ngay
    # sau origin_ip_scan) vì cần drafts/drafts_error đã tồn tại để append/gộp lỗi vào — đặt sớm
    # hơn sẽ NameError. Nếu có nhiều candidate IP, chỉ xử lý 1 IP đầu tiên (sort để ổn định giữa
    # các lần chạy) để tránh spam nhiều file draft cho 1 domain — xem CLAUDE.md.
    origin_ip_whois = {}
    candidate_ips = {ip for ip in origin_ip_scan.values() if ip and ip != cert.get("ip")}
    if candidate_ips:
        try:
            first_ip = sorted(candidate_ips)[0]
            origin_ip_whois = {first_ip: get_ip_whois(first_ip)}
            hosting_draft = generate_hosting_draft(domain, first_ip, origin_ip_whois[first_ip], cfg)
            drafts.append(hosting_draft)
        except Exception as e:
            drafts_error = (drafts_error + "; " if drafts_error else "") + f"Hosting draft: {e}"

    # Leo thang Registry (ccTLD lạ, 03_Technical_Guide.md mục 5) + report VNCERT (mục 8). Cùng
    # pattern try/except với các khối draft ở trên — lỗi ở đây không được phá hỏng kết quả điều
    # tra đã có. registry_info luôn được trả về (kể cả "not_found"), vncert_draft luôn được sinh
    # (không điều kiện gì, giống openphish) vì tool không tự biết domain có nhắm vào nạn nhân VN.
    try:
        registry_info = lookup_registry_contact(domain)
        registry_draft = generate_registry_draft(domain, registry_info, cfg)
        if registry_draft:
            drafts.append(registry_draft)
        vncert_draft = generate_vncert_draft(domain, cert, vt, cfg)
        drafts.append(vncert_draft)
    except Exception as e:
        registry_info = {"source": "not_found"}
        drafts_error = (drafts_error + "; " if drafts_error else "") + f"Registry/VNCERT draft: {e}"

    return {
        "domain": domain,
        "cert": cert,
        "whois": who,
        "cloudflare": cf,
        "cdn_detected": cdn_detected,
        "origin_ip_scan": origin_ip_scan,
        "origin_ip_whois": origin_ip_whois,
        "registry_contact": registry_info,
        "virustotal": vt,
        "virustotal_submit": virustotal_submit,
        "safebrowsing": gsb,
        "reputation": reputation,
        "ca_note": ca_note,
        "http_check": http_check,
        "domain_age_days": domain_age_days,
        "mx_records": mx_records,
        "log_row": log_row,
        "log_error": log_error,
        "drafts": drafts,
        "drafts_error": drafts_error,
    }


def cmd_check(args):
    cfg = load_config()
    result = run_check(args.target, args.submit, cfg)
    domain = result["domain"]
    cert = result["cert"]
    who = result["whois"]
    cf = result["cloudflare"]
    cdn_detected = result["cdn_detected"]
    origin_ip_scan = result["origin_ip_scan"]
    vt = result["virustotal"]
    gsb = result["safebrowsing"]
    ca_note = result["ca_note"]

    print(f"\n=== Đang kiểm tra: {domain} ===\n")

    print("--- SSL Certificate ---")
    if "error" in cert:
        print(f"  Lỗi: {cert['error']}")
    else:
        for k, v in cert.items():
            print(f"  {k:15s}: {v}")

    print("\n--- WHOIS ---")
    for k, v in who.items():
        print(f"  {k:15s}: {v}")

    print(f"\n--- Cloudflare ---\n  {'Có' if cf else 'Không rõ / Không'}")

    print("\n--- VirusTotal ---")
    for k, v in vt.items():
        print(f"  {k:15s}: {v}")
    if result["virustotal_submit"]:
        print("  " + result["virustotal_submit"])

    print("\n--- Google Safe Browsing ---")
    for k, v in gsb.items():
        print(f"  {k:15s}: {v}")

    rep = result["reputation"]
    print(f"\n--- Uy tín (tổng hợp VirusTotal + Safe Browsing) ---\n  {rep['label']}")
    for reason in rep["reasons"]:
        print(f"    - {reason}")
    if rep["verdict"] in ("clean", "unknown"):
        print("  Lưu ý: chưa bị gắn cờ KHÔNG có nghĩa là an toàn — domain mới/ít traffic thường")
        print("  chưa kịp bị cộng đồng báo cáo. Vẫn cần xác minh nội dung trang thủ công.")

    print("\n--- Khuyến nghị kênh báo cáo ---")
    print("  1. Google Safe Browsing (submit thủ công, chưa có API report):")
    print("     https://safebrowsing.google.com/safebrowsing/report_phish/")
    print("     Nội dung mô tả mẫu (copy vào ô mô tả của form):")
    print(f"     \"{generate_safebrowsing_report_text(domain, cfg)}\"")
    print("     Microsoft SmartScreen: report thủ công tại")
    print("     https://www.microsoft.com/wdsi/support/report-unsafe-site/")

    if cf or cdn_detected:
        print("  2. CDN:")
        if cf:
            print(f"     - Cloudflare (form thủ công): {CDN_ABUSE_CONTACTS['cloudflare']['report_url']}")
            print(f"       {CDN_ABUSE_CONTACTS['cloudflare']['note']}")
            print("       Nội dung mô tả mẫu (copy vào ô mô tả của form):")
            print(f"       \"{generate_cloudflare_report_text(domain, cfg)}\"")
        for name in cdn_detected:
            info = CDN_ABUSE_CONTACTS.get(name)
            if info:
                print(f"     - {name.title()}: {info['report_url']}")

    print("  3. Cộng đồng bảo mật:")
    if result["virustotal_submit"]:
        print(f"     - VirusTotal: {result['virustotal_submit']}")
    elif vt.get("link"):
        print(f"     - VirusTotal: {vt['link']}")
    else:
        print("     - VirusTotal: chưa từng quét domain này — thêm cờ --submit để tự động submit")
    print("     - PhishTank: report thủ công tại https://phishtank.org/")
    print(f"     - OpenPhish: không còn hoạt động. Thay bằng APWG — draft tại reports/{domain}_apwg_report.txt")

    if ca_note:
        print(f"  4. CA ({ca_note['ca']}): {ca_note['note']}")
        if ca_note["report_url"]:
            print(f"     Report tại: {ca_note['report_url']}")
    if who.get("registrar"):
        print(f"  5. Registrar: {who['registrar']} — email: {who.get('emails')}")

    registry_contact = result["registry_contact"]
    if registry_contact.get("source") != "not_found":
        print("  6. Registry (ccTLD) — leo thang khi registrar không phản hồi:")
        if registry_contact["source"] == "static_table":
            print(f"     {registry_contact.get('registry')}: {registry_contact.get('abuse_email')}")
            if registry_contact.get("note"):
                print(f"     Lưu ý: {registry_contact['note']}")
        else:
            print(f"     WHOIS server: {registry_contact.get('whois_server')} (qua IANA referral)")
            print("     — xem draft để đọc abuse email từ nội dung WHOIS thô")
        print(f"     Draft đã tạo sẵn tại reports/{domain}_registry_report.txt")
    else:
        print(f"  6. Registry (ccTLD): TLD .{domain.rsplit('.', 1)[-1]} chưa có trong danh sách hỗ trợ,")
        print("     tra thủ công tại https://www.iana.org/domains/root/db")

    print(f"  7. VNCERT: draft đã tạo sẵn tại reports/{domain}_vncert_report.txt")
    print("     (CHỈ gửi nếu domain nhắm vào nạn nhân tại Việt Nam — tool không tự xác định được điều này)")

    origin_ip_whois = result["origin_ip_whois"]
    if origin_ip_whois:
        ip, ipw = next(iter(origin_ip_whois.items()))
        print(f"  8. Hosting/ISP (IP gốc {ip}):")
        if "error" in ipw:
            print(f"     Lỗi tra IP WHOIS: {ipw['error']}")
        else:
            print(f"     Tổ chức: {ipw.get('org') or 'N/A'}")
            print(f"     Abuse email: {ipw.get('abuse_email') or 'N/A'}")
            print(f"     ASN: {ipw.get('asn')} ({ipw.get('asn_description')})")
        print(f"     Draft đã tạo sẵn tại reports/{domain}_hosting_report.txt")
    else:
        print("  8. Hosting/ISP (IP gốc): Không phát hiện IP gốc khác qua subdomain scan — có thể")
        print("     domain không dùng CDN, hoặc IP gốc không lộ qua các subdomain thông dụng đã quét.")

    origin_candidates = {
        sub: ip for sub, ip in origin_ip_scan.items() if ip and ip != cert.get("ip")
    }
    if origin_candidates:
        print("\n--- Khả năng lộ IP gốc (candidate origin IP, cần xác minh thêm) ---")
        for sub, ip in origin_candidates.items():
            print(f"  - {sub}.{domain}: {ip}")
        print("  Đây chỉ là gợi ý dựa trên IP khác IP chính của domain, KHÔNG phải kết luận chắc")
        print("  chắn — cần xác minh thêm trước khi dùng để report hosting.")

    if result["log_error"]:
        print(f"\nLỖI khi ghi log vào {LOG_PATH}: {result['log_error']}")
    else:
        print(f"\nĐã ghi log vào {LOG_PATH}")

    if result["drafts_error"]:
        print(f"\nLỖI khi sinh email báo cáo: {result['drafts_error']}")
    elif result["drafts"]:
        print("\nĐã tạo sẵn email báo cáo:")
        for d in result["drafts"]:
            print(f"  - {d}")


def cmd_related(args):
    print(f"\n=== Tìm domain liên quan tới từ khóa: {args.keyword} ===\n")
    res = crtsh_related(args.keyword)
    if "error" in res:
        print(f"Lỗi: {res['error']}")
        return
    print(f"Tìm thấy {res['count']} domain có chứng chỉ chứa '{args.keyword}':\n")
    for d in res["domains"]:
        print(f"  - {d}")


def cmd_brandscan(args):
    domain = normalize_domain(args.target)
    print(f"\n=== Quét biến thể domain giống: {domain} (có thể mất vài phút, brand phổ biến có thể tới 10 phút) ===\n")
    res = brand_scan(domain, args.limit)
    if "error" in res:
        print(f"Lỗi: {res['error']}")
        return
    print(f"Tìm thấy {res['count']} domain biến thể ĐÃ ĐĂNG KÝ:\n")
    for r in res["results"]:
        print(f"  - {r.get('domain')}  (fuzzer: {r.get('fuzzer')})  dns_a: {r.get('dns_a')}")


def cmd_send(args):
    """CLI gửi email đồng loạt qua tất cả smtp_accounts — gửi ngay không hỏi xác nhận."""
    cfg = load_config()
    path = args.draft_path
    if not os.path.isfile(path):
        print(f"Không tìm thấy file: {path}")
        return

    parsed = parse_draft_email(path)
    if not parsed["to"]:
        print("Draft này không có địa chỉ email hợp lệ để gửi tự động (có thể cần report qua")
        print("form web, hoặc chưa tra được abuse email) — xem nội dung file và report thủ công.")
        return

    accounts = cfg.get("smtp_accounts", [])
    proxies = cfg.get("smtp_proxies", [])
    n_acc = len(accounts)
    n_proxy = len(proxies)
    print(f"\n--- Gửi email đồng loạt ---")
    print(f"To      : {parsed['to']}")
    print(f"Subject : {parsed['subject']}")
    print(f"Tài khoản: {n_acc}  |  Proxy: {n_proxy}")
    if proxies:
        for i, acc in enumerate(accounts):
            print(f"  [{i+1}] {acc.get('username')}  →  proxy {proxies[i % len(proxies)]}")
    else:
        for i, acc in enumerate(accounts):
            print(f"  [{i+1}] {acc.get('username')}  (không proxy)")

    results = send_report_email_bulk(parsed["to"], parsed["subject"], parsed["body"], cfg)
    ts = datetime.now(timezone.utc).isoformat()
    for r in results:
        status = "✓ OK" if r["success"] else f"✗ LỖI: {r['error']}"
        print(f"  {r['account']}  [{r['proxy']}]  →  {status}")
        try:
            log_sent({
                "timestamp": ts,
                "domain": domain_from_draft_filename(os.path.basename(path)),
                "draft_file": os.path.basename(path),
                "to": parsed["to"],
                "subject": parsed["subject"],
                "success": r["success"],
                "error": r.get("error") or "",
            })
        except Exception as e:
            print(f"  Cảnh báo: ghi {SENT_LOG_PATH} lỗi: {e}")


def main():
    parser = argparse.ArgumentParser(description="Bộ công cụ phát hiện & báo cáo phishing")
    sub = parser.add_subparsers(dest="command", required=True)

    p_check = sub.add_parser("check", help="Kiểm tra đầy đủ 1 domain")
    p_check.add_argument("target", help="Domain hoặc URL")
    p_check.add_argument("--submit", action="store_true", help="Submit lên VirusTotal nếu chưa có dữ liệu")
    p_check.set_defaults(func=cmd_check)

    p_related = sub.add_parser("related", help="Tìm domain liên quan qua crt.sh")
    p_related.add_argument("keyword", help="Từ khóa / tên thương hiệu")
    p_related.set_defaults(func=cmd_related)

    p_brand = sub.add_parser("brandscan", help="Quét biến thể domain (dnstwist)")
    p_brand.add_argument("target", help="Domain gốc hợp pháp cần bảo vệ")
    p_brand.add_argument("--limit", type=int, default=50)
    p_brand.set_defaults(func=cmd_brandscan)

    p_send = sub.add_parser("send", help="Gửi 1 draft email đã sinh sẵn qua SMTP thật (cần xác nhận thủ công)")
    p_send.add_argument("draft_path", help="Đường dẫn tới file draft trong reports/")
    p_send.set_defaults(func=cmd_send)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
