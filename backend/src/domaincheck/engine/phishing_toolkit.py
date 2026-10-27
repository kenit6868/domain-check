#!/usr/bin/env python3
"""Domain investigation, report formatting and mail primitives for the web backend.

Configuration is supplied explicitly by application services; no config.ini is read.
"""

import concurrent.futures
import imaplib
import os
import random
import re
import socket
import ssl
import time
from datetime import datetime, timezone
from email.utils import getaddresses
from urllib.parse import urlparse

from domaincheck.engine import cloaking_detector
from domaincheck.engine import browser_evidence
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


CA_ABUSE_NOTES = {
    "google trust services": {
        "report_url": None,
        "abuse_email": None,
        "note": (
            "Google Trust Services KHÔNG xử lý report phishing/malware — chỉ xử lý mis-issuance. "
            "Bỏ qua bước này."
        ),
        "can_revoke": False,
    },
    "let's encrypt": {
        "report_url": "https://letsencrypt.org/abuse/",
        "abuse_email": "abuse@letsencrypt.org",
        "note": "Let's Encrypt xử lý report qua form hoặc email. Xác suất revoke cert thấp hơn các CA thương mại.",
        "can_revoke": True,
    },
    "sectigo": {
        "report_url": "https://sectigo.com/support-resources/report-abuse-ssl-certificate",
        "abuse_email": None,
        "note": "Sectigo có xử lý report phishing/malware — dùng web form.",
        "can_revoke": True,
    },
    "comodo": {
        "report_url": "https://sectigo.com/support-resources/report-abuse-ssl-certificate",
        "abuse_email": None,
        "note": "Comodo CA (nay là Sectigo) — dùng form Sectigo.",
        "can_revoke": True,
    },
    "digicert": {
        "report_url": "https://www.digicert.com/reporting-abuse/",
        "abuse_email": "abuse@digicert.com",
        "note": "DigiCert có xử lý report phishing/malware — form hoặc email.",
        "can_revoke": True,
    },
    "zerossl": {
        "report_url": None,
        "abuse_email": "abuse@zerossl.com",
        "note": "ZeroSSL nhận report qua email.",
        "can_revoke": True,
    },
    "godaddy": {
        "report_url": "https://sg.godaddy.com/help/report-abuse-24108",
        "abuse_email": "abuse@godaddy.com",
        "note": "GoDaddy (vai trò CA) có xử lý report phishing — form hoặc email.",
        "can_revoke": True,
    },
    "globalsign": {
        "report_url": "https://www.globalsign.com/en/company/legal/abuse/",
        "abuse_email": "abuse@globalsign.com",
        "note": "GlobalSign có xử lý report phishing/malware.",
        "can_revoke": True,
    },
    "entrust": {
        "report_url": None,
        "abuse_email": "certissues@entrust.com",
        "note": "Entrust nhận report qua email.",
        "can_revoke": True,
    },
    "actalis": {
        "report_url": None,
        "abuse_email": "tls-abuse@actalis.it",
        "note": "Actalis nhận report qua email.",
        "can_revoke": True,
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
        "report_url": "https://www.fastly.com/abuse",
        "note": "Report qua form web.",
    },
    "akamai": {
        "report_url": "https://www.akamai.com/legal/compliance/report-abuse",
        "note": "Report qua form compliance.",
    },
    "cloudfront": {
        "report_url": "https://support.aws.amazon.com/#/contacts/report-abuse",
        "note": "CDN của AWS — dùng form Trust & Safety của AWS.",
    },
    "stormwall": {
        "report_url": "abuse@stormwall.pro",
        "note": "Cũng có thể report qua live chat trên trang chủ Stormwall.",
    },
    "ddos-guard": {
        "report_url": "https://ddos-guard.net/en/abuse",
        "note": "Anti-DDoS proxy — thường ẩn IP gốc, kết hợp quét subdomain (origin_ip_scan) để tìm IP thật.",
    },
    # Hosting platform / cloud — phishing thường host trực tiếp trên đây
    "vercel": {
        "report_url": "https://vercel.com/abuse",
        "note": "Vercel hosting — điền web form.",
    },
    "netlify": {
        "report_url": "fraud@netlify.com",
        "note": "Netlify hosting — chỉ nhận qua email fraud@netlify.com.",
    },
    "github-pages": {
        "report_url": "https://support.github.com/contact/report-abuse",
        "note": "GitHub Pages — report qua form support.",
    },
    "digitalocean": {
        "report_url": "https://www.digitalocean.com/company/contact/abuse",
        "note": "DigitalOcean VPS/Droplet.",
    },
    "hetzner": {
        "report_url": "https://abuse.hetzner.com",
        "note": "Hetzner Cloud/dedicated.",
    },
    "ovh": {
        "report_url": "https://www.ovhcloud.com/en/abuse",
        "note": "OVH/OVHcloud — có form riêng.",
    },
    "azure": {
        "report_url": "https://msrc.microsoft.com/report/abuse",
        "note": "Microsoft Azure — report qua MSRC.",
    },
    "amazonaws": {
        "report_url": "https://support.aws.amazon.com/#/contacts/report-abuse",
        "note": "AWS — EC2/S3/CloudFront/ELB. Form Trust & Safety.",
    },
    "gcp": {
        "report_url": "https://support.google.com/code/contact/cloud_platform_report",
        "note": "Google Cloud Platform (GCP) — report qua form.",
    },
    "firebase": {
        "report_url": "https://support.google.com/legal/troubleshooter/1114905",
        "note": "Firebase Hosting (Google) — dùng form Legal Troubleshooter.",
    },
    "google-cloud": {
        "report_url": "https://support.google.com/code/contact/cloud_platform_report",
        "note": "Google Cloud Platform (App Engine, GCS...) — report qua form.",
    },
    "cloudflare-pages": {
        "report_url": "https://abuse.cloudflare.com",
        "note": "Cloudflare Pages (pages.dev) — dùng form abuse Cloudflare.",
    },
    "aws": {
        "report_url": "https://support.aws.amazon.com/#/contacts/report-abuse",
        "note": "AWS (S3, EC2, Elastic Beanstalk...) — form Trust & Safety.",
    },
    "render": {
        "report_url": "https://render.com/abuse",
        "note": "Render.com hosting — report qua form.",
    },
    "heroku": {
        "report_url": "https://www.heroku.com/policy/heroku-trust-and-safety-program",
        "note": "Heroku (Salesforce) — report qua trang Trust & Safety.",
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
    # --- ccTLD thật ---
    "cn": {
        "registry": "CNNIC",
        "abuse_email": "supervision@cnnic.cn",
        "note": "Yêu cầu bản dịch nhãn hiệu tiếng Trung/Anh có công chứng.",
    },
    "in": {"registry": "NIXI", "note": "Hay không hồi âm — nên đi đường nhà đăng ký."},
    "io": {"registry": "Identity Digital", "abuse_email": "abuse@identity.digital", "note": None, "report_webform": "https://identity.digital/policies/report-abuse",},
    "jp": {
        "registry": "JPRS",
        "abuse_email": "abuse@jprs.jp",
        "note": "Đòi hỏi xác thực nhãn hiệu đăng ký tại Nhật Bản.",
    },
    "kr": {
        "registry": "KISA",
        "abuse_email": "abuse@kisa.or.kr",
        "note": "Phối hợp báo cáo qua KrCERT.",
    },
    "ru": {
        "registry": "Coordination Center for TLD RU",
        "abuse_email": "abuse@cctld.ru",
        "note": None,
    },
    "uk": {
        "registry": "Nominet",
        "abuse_email": None,
        "report_webform": "https://abuse.cleandns.space/nominet/phishing",
        "note": "Áp dụng cho cả .co.uk và .org.uk.",
    },
    "eu": {"registry": "EURid", "abuse_email": "info@eurid.eu", "note": None},
    "cl": {"registry": "NIC Chile", "abuse_email": "abuse@nic.cl", "note": None},
    "tw": {
        "registry": "TWNIC",
        "abuse_email": None,
        "report_webform": "https://phishingcheck.tw",
        "note": "Áp dụng cho cả .com.tw.",
    },
    "hk": {"registry": "HKIRC", "abuse_email": "abuse@hkirc.hk", "note": None},
    "de": {
        "registry": "DENIC",
        "abuse_email": None,
        "report_webform": "https://www.denic.de/en/report-a-concern/",
        "note": None,
    },
    "fr": {"registry": "AFNIC", "abuse_email": None, "note": None},
    "nl": {"registry": "SIDN", "abuse_email": "support@sidn.nl", "note": None},
    "pl": {"registry": "NASK", "abuse_email": "info@dns.pl", "note": None},
    "it": {"registry": "IIT-CNR", "abuse_email": "info@nic.it", "note": None},
    "mx": {"registry": "NIC Mexico", "abuse_email": "legal@nic.mx", "note": "Áp dụng cho cả .com.mx"},
    "au": {"registry": "auDA", "abuse_email": "compliance@auda.org.au", "note": None},
    "ca": {"registry": "CIRA", "abuse_email": "cira@cira.ca", "note": None},
    "br": {"registry": "NIC.br", "abuse_email": "abuse@registro.br", "note": None},
    "vn": {
        "registry": "VNNIC",
        "abuse_email": "abuse@vnnic.vn",
        "note": "Đối với domain .vn phishing, report thêm tới VNCERT (cert@vncert.gov.vn).",
    },
    # --- gTLD / nTLD phổ biến trong phishing ---
    "com": {
        "registry": "Verisign",
        "abuse_email": None,
        "report_webform": "https://verisign.my.site.com/DNSAbuse",
        "note": "Áp dụng cho cả .net .name .cc",
    },
    "net": {
        "registry": "Verisign",
        "abuse_email": None,
        "report_webform": "https://verisign.my.site.com/DNSAbuse",
        "note": None,
    },
    "name": {
        "registry": "Verisign",
        "abuse_email": None,
        "report_webform": "https://verisign.my.site.com/DNSAbuse",
        "note": None,
    },
    "cc": {
        "registry": "Verisign",
        "abuse_email": None,
        "report_webform": "https://verisign.my.site.com/DNSAbuse",
        "note": None,
    },
    "org": {
        "registry": "Public Interest Registry (PIR)",
        "abuse_email": None,
        "report_webform": "https://pir.org/report-abuse/",
        "note": "Báo cáo abuse qua web form chính thức của PIR.",
    },
    "ong": {
        "registry": "Public Interest Registry (PIR)",
        "abuse_email": None,
        "report_webform": "https://pir.org/report-abuse/",
        "note": "Báo cáo abuse qua web form chính thức của PIR.",
    },
    "us": {
        "registry": "GoDaddy Registry",
        "abuse_email": None,
        "report_webform": "https://domainabuse.registry.godaddy/s/",
        "note": "Áp dụng cho cả .co .biz .vip .club (GoDaddy Registry).",
    },
    "co": {
        "registry": "Registry Co",
        "abuse_email": "abuse@registry.co",
        "report_webform": "https://registry.co/report-abuse/form?type=phishing",
        "note": None,
    },
    "biz": {
        "registry": "GoDaddy Registry",
        "abuse_email": None,
        "report_webform": "https://domainabuse.registry.godaddy/s/",
        "note": None,
    },
    "vip": {
        "registry": "GoDaddy Registry",
        "abuse_email": None,
        "report_webform": "https://domainabuse.registry.godaddy/s/",
        "note": None,
    },
    "club": {
        "registry": "GoDaddy Registry",
        "abuse_email": None,
        "report_webform": "https://domainabuse.registry.godaddy/s/",
        "note": None,
    },
    "top": {
        "registry": "Jiangsu Bangning Science & Technology (nic.top)",
        "abuse_email": "abuse@nic.top",
        "report_webform": "https://www.nic.top/cn/Complaintsnew.asp",
        "note": "Có xử lý thật — nhận email + web form. Gửi kèm file Excel (tối đa 200 domain/lần) và ảnh chụp màn hình có thanh địa chỉ.",
    },
    "xyz": {
        "registry": "XYZ.COM LLC",
        "abuse_email": "xyz_abuse@gen.xyz",
        "report_webform": "https://gen.xyz/account/submitticket.php?step=2&deptid=6",
        "note": "Áp dụng cho 35+ TLD của XYZ (monster, quest, baby, cars, beauty, hair, homes, game, lol, mom, pics, hosting, audio, diet, ceo...). Nhiều domain cùng lúc gửi email xyz_abuse@gen.xyz. Hiệu quả — thường hôm sau bị khoá.",
    },
    "lat": {
            "registry": "XYZ.COM LLC",
            "abuse_email": "xyz_abuse@gen.xyz",
            "report_webform": "https://gen.xyz/account/submitticket.php?step=2&deptid=6",
            "note": "Áp dụng cho 35+ TLD của XYZ (monster, quest, baby, cars, beauty, hair, homes, game, lol, mom, pics, hosting, audio, diet, ceo...). Nhiều domain cùng lúc gửi email xyz_abuse@gen.xyz. Hiệu quả — thường hôm sau bị khoá.",
    },
    "cn.com": {
            "registry": "XYZ.COM LLC",
            "abuse_email": "xyz_abuse@gen.xyz",
            "report_webform": "https://gen.xyz/account/submitticket.php?step=2&deptid=6",
            "note": "Áp dụng cho 35+ TLD của XYZ (monster, quest, baby, cars, beauty, hair, homes, game, lol, mom, pics, hosting, audio, diet, ceo...). Nhiều domain cùng lúc gửi email xyz_abuse@gen.xyz. Hiệu quả — thường hôm sau bị khoá.",
    },
    # XYZ TLD family — cùng registry/abuse contact với .xyz
    "monster":  {"registry": "XYZ.COM LLC", "abuse_email": "xyz_abuse@gen.xyz", "report_webform": "https://gen.xyz/account/submitticket.php?step=2&deptid=6", "note": None},
    "quest":    {"registry": "XYZ.COM LLC", "abuse_email": "xyz_abuse@gen.xyz", "report_webform": "https://gen.xyz/account/submitticket.php?step=2&deptid=6", "note": None},
    "baby":     {"registry": "XYZ.COM LLC", "abuse_email": "xyz_abuse@gen.xyz", "report_webform": "https://gen.xyz/account/submitticket.php?step=2&deptid=6", "note": None},
    "cars":     {"registry": "XYZ.COM LLC", "abuse_email": "xyz_abuse@gen.xyz", "report_webform": "https://gen.xyz/account/submitticket.php?step=2&deptid=6", "note": None},
    "beauty":   {"registry": "XYZ.COM LLC", "abuse_email": "xyz_abuse@gen.xyz", "report_webform": "https://gen.xyz/account/submitticket.php?step=2&deptid=6", "note": None},
    "hair":     {"registry": "XYZ.COM LLC", "abuse_email": "xyz_abuse@gen.xyz", "report_webform": "https://gen.xyz/account/submitticket.php?step=2&deptid=6", "note": None},
    "homes":    {"registry": "XYZ.COM LLC", "abuse_email": "xyz_abuse@gen.xyz", "report_webform": "https://gen.xyz/account/submitticket.php?step=2&deptid=6", "note": None},
    "game":     {"registry": "XYZ.COM LLC", "abuse_email": "xyz_abuse@gen.xyz", "report_webform": "https://gen.xyz/account/submitticket.php?step=2&deptid=6", "note": None},
    "lol":      {"registry": "XYZ.COM LLC", "abuse_email": "xyz_abuse@gen.xyz", "report_webform": "https://gen.xyz/account/submitticket.php?step=2&deptid=6", "note": None},
    "mom":      {"registry": "XYZ.COM LLC", "abuse_email": "xyz_abuse@gen.xyz", "report_webform": "https://gen.xyz/account/submitticket.php?step=2&deptid=6", "note": None},
    "pics":     {"registry": "XYZ.COM LLC", "abuse_email": "xyz_abuse@gen.xyz", "report_webform": "https://gen.xyz/account/submitticket.php?step=2&deptid=6", "note": None},
    "hosting":  {"registry": "XYZ.COM LLC", "abuse_email": "xyz_abuse@gen.xyz", "report_webform": "https://gen.xyz/account/submitticket.php?step=2&deptid=6", "note": None},
    "audio":    {"registry": "XYZ.COM LLC", "abuse_email": "xyz_abuse@gen.xyz", "report_webform": "https://gen.xyz/account/submitticket.php?step=2&deptid=6", "note": None},
    "diet":     {"registry": "XYZ.COM LLC", "abuse_email": "xyz_abuse@gen.xyz", "report_webform": "https://gen.xyz/account/submitticket.php?step=2&deptid=6", "note": None},
    "ceo":      {"registry": "XYZ.COM LLC", "abuse_email": "xyz_abuse@gen.xyz", "report_webform": "https://gen.xyz/account/submitticket.php?step=2&deptid=6", "note": None},
    "auto":      {"registry": "XYZ.COM LLC", "abuse_email": "xyz_abuse@gen.xyz", "report_webform": "https://gen.xyz/account/submitticket.php?step=2&deptid=6", "note": None},
    "autos":      {"registry": "XYZ.COM LLC", "abuse_email": "xyz_abuse@gen.xyz", "report_webform": "https://gen.xyz/account/submitticket.php?step=2&deptid=6", "note": None},
    "boats":      {"registry": "XYZ.COM LLC", "abuse_email": "xyz_abuse@gen.xyz", "report_webform": "https://gen.xyz/account/submitticket.php?step=2&deptid=6", "note": None},
    "car":      {"registry": "XYZ.COM LLC", "abuse_email": "xyz_abuse@gen.xyz", "report_webform": "https://gen.xyz/account/submitticket.php?step=2&deptid=6", "note": None},
    "christmas":      {"registry": "XYZ.COM LLC", "abuse_email": "xyz_abuse@gen.xyz", "report_webform": "https://gen.xyz/account/submitticket.php?step=2&deptid=6", "note": None},
    "college":      {"registry": "XYZ.COM LLC", "abuse_email": "xyz_abuse@gen.xyz", "report_webform": "https://gen.xyz/account/submitticket.php?step=2&deptid=6", "note": None},
    "flowers":      {"registry": "XYZ.COM LLC", "abuse_email": "xyz_abuse@gen.xyz", "report_webform": "https://gen.xyz/account/submitticket.php?step=2&deptid=6", "note": None},
    "guitars":      {"registry": "XYZ.COM LLC", "abuse_email": "xyz_abuse@gen.xyz", "report_webform": "https://gen.xyz/account/submitticket.php?step=2&deptid=6", "note": None},
    "makeup":      {"registry": "XYZ.COM LLC", "abuse_email": "xyz_abuse@gen.xyz", "report_webform": "https://gen.xyz/account/submitticket.php?step=2&deptid=6", "note": None},
    "motorcycles":      {"registry": "XYZ.COM LLC", "abuse_email": "xyz_abuse@gen.xyz", "report_webform": "https://gen.xyz/account/submitticket.php?step=2&deptid=6", "note": None},
    "protection":      {"registry": "XYZ.COM LLC", "abuse_email": "xyz_abuse@gen.xyz", "report_webform": "https://gen.xyz/account/submitticket.php?step=2&deptid=6", "note": None},
    "rent":      {"registry": "XYZ.COM LLC", "abuse_email": "xyz_abuse@gen.xyz", "report_webform": "https://gen.xyz/account/submitticket.php?step=2&deptid=6", "note": None},
    "security":      {"registry": "XYZ.COM LLC", "abuse_email": "xyz_abuse@gen.xyz", "report_webform": "https://gen.xyz/account/submitticket.php?step=2&deptid=6", "note": None},
    "skin":      {"registry": "XYZ.COM LLC", "abuse_email": "xyz_abuse@gen.xyz", "report_webform": "https://gen.xyz/account/submitticket.php?step=2&deptid=6", "note": None},
    "storage":      {"registry": "XYZ.COM LLC", "abuse_email": "xyz_abuse@gen.xyz", "report_webform": "https://gen.xyz/account/submitticket.php?step=2&deptid=6", "note": None},
    "theatre":      {"registry": "XYZ.COM LLC", "abuse_email": "xyz_abuse@gen.xyz", "report_webform": "https://gen.xyz/account/submitticket.php?step=2&deptid=6", "note": None},
    "tickets":      {"registry": "XYZ.COM LLC", "abuse_email": "xyz_abuse@gen.xyz", "report_webform": "https://gen.xyz/account/submitticket.php?step=2&deptid=6", "note": None},
    "yachts":      {"registry": "XYZ.COM LLC", "abuse_email": "xyz_abuse@gen.xyz", "report_webform": "https://gen.xyz/account/submitticket.php?step=2&deptid=6", "note": None},
    # CentralNic 2-level domains — không phải ccTLD thật, dùng report_webform chung
    "uk.com":   {"registry": "CentralNic", "abuse_email": "abuse@centralnic.com", "report_webform": "https://www.centralnicregistry.com/contact", "note": "Chọn mục Report Abuse. Không phải ccTLD UK — đừng gửi Nominet."},
    "jpn.com":  {"registry": "CentralNic", "abuse_email": "abuse@centralnic.com", "report_webform": "https://www.centralnicregistry.com/contact", "note": "Không phải ccTLD JP — đừng gửi JPRS."},
    "jp.net":   {"registry": "CentralNic", "abuse_email": "abuse@centralnic.com", "report_webform": "https://www.centralnicregistry.com/contact", "note": None},
    "in.net":   {"registry": "CentralNic", "abuse_email": "abuse@centralnic.com", "report_webform": "https://www.centralnicregistry.com/contact", "note": None},
    "com.co":   {"registry": "CentralNic", "abuse_email": "abuse@centralnic.com", "report_webform": "https://www.centralnicregistry.com/contact", "note": None},
    "radio.am":   {"registry": "CentralNic", "abuse_email": "abuse@centralnic.com", "report_webform": "https://www.centralnicregistry.com/contact", "note": None},
    "radio.fm":   {"registry": "CentralNic", "abuse_email": "abuse@centralnic.com", "report_webform": "https://www.centralnicregistry.com/contact", "note": None},
    # Alias 2 cấp — trỏ về registry của TLD cha
    "co.in":    {"registry": "NIXI", "note": "Hay không hồi âm — nên đi đường nhà đăng ký."},
    "co.jp":    {"registry": "JPRS", "abuse_email": "abuse@jprs.jp", "note": "Đòi hỏi xác thực nhãn hiệu đăng ký tại Nhật Bản."},
    "com.mx":   {"registry": "NIC Mexico", "abuse_email": "legal@nic.mx", "note": None},
    "shop": {
        "registry": "GMO Registry / Shopify",
        "abuse_email": None,
        "report_webform": "https://get.shop/abuse",
        "note": None,
    },
    "buzz": {"registry": "DOTSTRATEGY CO.", "abuse_email": "buzzabuse@dotstrategy.biz", "note": None},
    "site": {"registry": "Radix Registry", "abuse_email": "abuse@radix.support", "note": None},
    "online": {"registry": "Radix Registry", "abuse_email": "abuse@radix.support", "note": None},
    "store": {"registry": "Radix Registry", "abuse_email": "abuse@radix.support", "note": None},
    "pw": {"registry": "Radix Registry", "abuse_email": "abuse@radix.support", "note": "Formerly Palau ccTLD, now managed by Radix."},
    "live": {"registry": "Identity Digital", "abuse_email": "abuse@identity.digital", "note": "Web form có trên site họ nhưng không hoạt động — chỉ dùng email."},
    "ltd": {"registry": "Identity Digital", "abuse_email": "abuse@identity.digital", "note": None},
    "ninja": {"registry": "Identity Digital", "abuse_email": "abuse@identity.digital", "note": None},
    "pro": {"registry": "Identity Digital", "abuse_email": "abuse@identity.digital", "note": None},
    "vc": {"registry": "Identity Digital", "abuse_email": "abuse@identity.digital", "note": None},
    "info": {"registry": "Identity Digital", "abuse_email": "abuse@identity.digital", "note": None},
    "forsale": {"registry": "Identity Digital", "abuse_email": "abuse@identity.digital", "note": None},
    "black": {"registry": "Identity Digital", "abuse_email": "abuse@identity.digital", "note": None},
    "me": {"registry": "doMEn", "abuse_email": None, "report_webform": "https://identity.digital/policies/report-abuse", "note": "Không có kênh registry riêng — báo nhà đăng ký."},
    "tv": {"registry": "Verisign", "abuse_email": None, "report_webform": None, "note": "Không có kênh registry riêng — báo nhà đăng ký."},
    "best": {"registry": "domains.best", "abuse_email": None, "report_webform": "https://domains.best/contact?type=abuse", "note": "Không có kênh registry riêng — báo nhà đăng ký."},
    "bond": {"registry": "shortdot.bond", "abuse_email": None, "report_webform": "https://shortdot.bond/report-abuse", "note": "Không có kênh registry riêng — báo nhà đăng ký."},
    "hk.com":   {"registry": "UDR Limited", "abuse_email": None, "report_webform": "https://www.udr.hk.com/report-abuse", "note": "Bạn gửi báo cáo qua form Report Abuse chính thức của UDR"},
    "hk.org":   {"registry": "UDR Limited", "abuse_email": None, "report_webform": "https://www.udr.hk.com/report-abuse", "note": "Bạn gửi báo cáo qua form Report Abuse chính thức của UDR"},
    "ltd.hk":   {"registry": "UDR Limited", "abuse_email": None, "report_webform": "https://www.udr.hk.com/report-abuse", "note": "Bạn gửi báo cáo qua form Report Abuse chính thức của UDR"},
    "inc.hk":   {"registry": "UDR Limited", "abuse_email": None, "report_webform": "https://www.udr.hk.com/report-abuse", "note": "Bạn gửi báo cáo qua form Report Abuse chính thức của UDR"},
    "africa.com":   {"registry": "DNS Africa / Gateway SRS", "abuse_email": "abuse@dns.business", "report_webform": None, "note": "Bạn gửi báo cáo qua form Report Abuse chính thức của DNS Africa / Gateway SRS"},
}

# Bảng tĩnh abuse email của các registrar phổ biến — dùng làm fallback khi WHOIS không trả về email.
# Match theo "contains" case-insensitive với tên registrar từ WHOIS (xem lookup_registrar_abuse_email).
REGISTRAR_ABUSE_EMAILS = {
    "namecheap":           "abuse@namecheap.com",
    "godaddy":             "abuse@godaddy.com",
    "tucows":              "domainabuse@tucows.com",
    "opensrs":             "domainabuse@tucows.com",
    "enom":                "abuse@enom.com",
    "porkbun":             "abuse@porkbun.com",
    "gandi":               "abuse@support.gandi.net",
    "ovh":                 "abuse@ovh.net",
    "cloudflare":          "registrar-abuse@cloudflare.com",
    "google domains":      "registrar-abuse@google.com",
    "squarespace":         "registrar-abuse@google.com",
    "markmonitor":         "abusecomplaints@markmonitor.com",
    "alibaba":             "DomainAbuse@service.aliyun.com",
    "aliyun":              "DomainAbuse@service.aliyun.com",
    "huaweicloud":         "abuse@huaweicloud.com",
    "reg.ru":              "abuse@reg.ru",
    "namesilo":            "abuse@namesilo.com",
    "hostinger":           "abuse@hostinger.com",
    "hostgator":           "abuse@hostgator.com",
    "bluehost":            "abuse@bluehost.com",
    "dynadot":             "abuse@dynadot.com",
    "1api":                "abuse@1api.net",
    "internetbs":          "abuse@internet.bs",
    "cheapnames":          "abuse@cheapnames.com",
    "bigrock":             "abuse@bigrock.com",
    "crazydomains":        "abuse@crazydomains.com",
    "name.com":            "abuse@name.com",
    "rebel.com":           "abuse@rebel.com",
    "ionos":               "abuse@ionos.com",
    "1&1":                 "abuse@ionos.com",
    "netim":               "abuse@netim.com",
    "spaceship":           "abuse@spaceship.com",
    "epik":                "abuse@epik.com",
    "key-systems":         "abuse@key-systems.net",
    "inwx":                "abuse@inwx.com",
    "regtons":             "abuse@regtons.com",
    "publicdomainregistry": "abuse@publicdomainregistry.com",
    # Các entry dưới đây là WHOIS privacy services, KHÔNG phải abuse handlers thật.
    # Chỉ giữ để nhận diện, filter sẽ loại bỏ chúng trong generate_email_drafts().
    # "withheldforprivacy":  "abuse@withheldforprivacy.com",  # WHOIS privacy của Namecheap, không xử lý report
    # "domainsbyproxy":      "abuse@domainsbyproxy.com",      # WHOIS privacy của GoDaddy, không xử lý report
    "whoisguard":          "compliance@namecheap.com",
    "resellerclub":        "abuse-staff@resellerclub.com",
    "netearth":            "abuse@netearth.net",
    "nicenic":             "abuse@nicenic.net",
    "west263":             "abuse@hkdns.hk",
    "hkdns":               "abuse@hkdns.hk",
    "net-chinese":         "abuse@net-chinese.com.tw",
    "hichina":             "DomainAbuse@service.aliyun.com",
    "wix":                 "abuse@wix.com",
    "wordpress":           "abuse@automattic.com",
    "shopify":             "trust@shopify.com",
}

# Domain của các WHOIS privacy service — email từ các domain này KHÔNG phải abuse contact thật.
# Khi WHOIS trả về email thuộc 1 domain trong set này, bỏ qua và dùng fallback static table.
WHOIS_PRIVACY_DOMAINS = {
    "withheldforprivacy.com",   # Namecheap WHOIS privacy
    "domainsbyproxy.com",       # GoDaddy WHOIS privacy
    "domain-contact.org",       # WHOIS relay không xử lý report
    "privacyprotect.org",       # WHOIS privacy service
    "whoisguard.com",           # Namecheap WhoisGuard
    "contactprivacy.com",       # Contact Privacy Inc (Tucows)
    "anonymize.com",            # anonymize WHOIS
    "privacydaemon.com",        # Privacy Daemon
}

# Registrars CHỈ nhận report qua web form, KHÔNG nhận email.
# Khi registrar khớp 1 key trong dict này, sinh file hướng dẫn web form thay vì email.
WEB_FORM_REGISTRARS = {
    "godaddy":              "https://legalportal.godaddy.com/abuse/phishing",
    "dynadot":              "https://www.dynadot.com/report-abuse",
    "tucows":               "https://www.tucowsdomains.com/report-abuse",
    "opensrs":              "https://www.tucowsdomains.com/report-abuse",
    "namesilo":             "https://www.namesilo.com/report_abuse.php",
    "porkbun":              "https://www.porkbun.com/abuse",
#     "namecheap":            "https://support.namecheap.com/index.php?/Tickets/Submit",  # tạm dùng web form, không gửi email
    "sav.com":              "https://abuse.sav.com",
    "key-systems":          "https://abuse.cleandns.space",
    "instra":               "https://abuse.cleandns.space",
    "cleandns":             "https://abuse.cleandns.space",
    "gname":                "https://www.gname.com/abuse",
    "cosmotown":            "https://portal.cosmotown.com/report",
    "realtime register":    "https://www.mydomainprovider.com/contact_abuse",
    "publicdomainregistry": "https://www.publicdomainregistry.com/process-for-handling-abuse",
    "alibaba":              "https://report.alibabacloud.com",
    "aliyun":               "https://report.alibabacloud.com",
    "hichina":              "https://report.alibabacloud.com",
    "dnspod":               "https://intl.cloud.tencent.com/report-platform/dnsabuse",
    "tencent":              "https://intl.cloud.tencent.com/report-platform/dnsabuse",
    "west263":              "https://abuse.west.cn/",
    "verisign":             "https://verisign.my.site.com/DNSAbuse",
    "network solutions":    "https://www.networksolutions.com/support/aup/",
    "web.com":              "https://www.web.com/legal/abuse",
}


_CLOUDFLARE_UNMONITORED_RECIPIENTS = {"abuse@cloudflare.com"}


def is_cloudflare_proxy_contact(ip_whois: dict | None) -> bool:
    """Identify a Cloudflare proxy/CDN IP, not an origin hosting provider."""
    info = ip_whois or {}
    email = str(info.get("abuse_email") or "").strip().lower()
    org = str(info.get("org") or "").lower()
    asn = str(info.get("asn") or "").lower().replace("as", "").strip()
    return (
        email in _CLOUDFLARE_UNMONITORED_RECIPIENTS
        or "cloudflare" in org
        or asn == "13335"
    )


def is_blocked_report_recipient(value: str) -> bool:
    """Reject known unmonitored provider inboxes before SMTP."""
    addresses = {
        address.strip().lower()
        for _name, address in getaddresses([str(value or "")])
        if address.strip()
    }
    return bool(addresses & _CLOUDFLARE_UNMONITORED_RECIPIENTS)


_REGISTRAR_WEBFORM_VARIANTS = [
    {
        "opening": "We are submitting this report after identifying suspected phishing and apparent unauthorized impersonation of {brand} at the URL below.",
        "request": "Please investigate the complete user flow, preserve relevant registration and access records, and take appropriate registrar-level action under your phishing and abuse policies if the violation is confirmed.",
    },
    {
        "opening": "This report concerns suspected phishing content that appears to impersonate {brand} without authorization under the domain identified below.",
        "request": "Please review the reported URL and any linked registration or sign-in flow, retain relevant account and access records, and apply the action available under your registrar abuse policy if the violation is confirmed.",
    },
    {
        "opening": "We identified suspected phishing and apparent unauthorized use of the {brand} identity under the domain below and are submitting it for abuse review.",
        "request": "Please independently verify the reported content and connected user flow, preserve records relevant to the registrant and abuse investigation, and take proportionate registrar-level action if the violation is confirmed.",
    },
    {
        "opening": "The URL below is being reported for suspected phishing involving apparent impersonation of {brand} and requires registrar review.",
        "request": "Please investigate the URL, associated destinations, and registration data, preserve relevant records, and mitigate the domain in accordance with your applicable abuse procedures if the violation is confirmed.",
    },
    {
        "opening": "We are reporting a suspected phishing and brand-impersonation concern involving {brand} at the full URL below.",
        "request": "Please assess the complete flow rather than only the landing page, preserve registration and access records, and take the registrar-level measures provided by your policies if the violation is confirmed.",
    },
]


_REGISTRY_WEBFORM_VARIANTS = [
    {
        "opening": "We are submitting this report for registry review after identifying suspected phishing and apparent unauthorized impersonation of {brand} under the domain below.",
        "request": "Please investigate the reported domain and complete user flow, coordinate prompt mitigation with the sponsoring registrar, and apply proportionate registry-level measures under your abuse policy if the violation is confirmed.",
    },
    {
        "opening": "This registry report concerns suspected phishing content that appears to use the {brand} identity without authorization.",
        "request": "Please independently review the full URL, coordinate the investigation with the sponsoring registrar, and apply proportionate registry-level measures under your policy if the violation is confirmed.",
    },
    {
        "opening": "We are submitting a suspected phishing and brand-impersonation concern involving {brand} for review at the registry level.",
        "request": "Please assess the reported domain and connected user flow, preserve relevant registry records, coordinate with the sponsoring registrar, and apply proportionate registry-level measures if the violation is confirmed.",
    },
    {
        "opening": "The domain below is being reported for registry review because its full URL presents suspected phishing and apparent impersonation of {brand}.",
        "request": "Please review the available facts, engage the sponsoring registrar for prompt mitigation, and apply proportionate registry-level measures under your abuse framework if the violation is confirmed.",
    },
    {
        "opening": "We identified suspected phishing activity under the registered domain below that appears to impersonate {brand} without authorization and are submitting it for review.",
        "request": "Please investigate the complete reported flow, coordinate necessary action with the sponsoring registrar, and apply proportionate registry-level measures if the violation is confirmed.",
    },
]


def _webform_observation_block(observations: dict | None) -> str:
    """Format only supplied, sanitized observations; never infer missing evidence."""
    if not isinstance(observations, dict):
        return ""

    def clean(value, limit=500):
        return " ".join(str(value or "").split())[:limit]

    lines = []
    for key, label in (
        ("checked_at", "Observation timestamp"),
        ("page_title", "Observed page title"),
        ("brand_text", "Observed brand text"),
        ("resolved_destination", "DOM-declared destination"),
        ("final_url", "Verified final URL"),
    ):
        value = clean(observations.get(key))
        if value:
            lines.append(f"- {label}: {value}")

    controls = observations.get("auth_controls") or []
    if isinstance(controls, str):
        controls = [controls]
    controls = [clean(value, 100) for value in controls[:10] if clean(value, 100)]
    if controls:
        lines.append(f"- Observed authentication controls: {', '.join(controls)}")

    redirects = observations.get("redirect_chain") or []
    if isinstance(redirects, str):
        redirects = [redirects]
    redirects = [clean(value) for value in redirects[:10] if clean(value)]
    if redirects:
        lines.append(f"- Observed HTTP redirect chain: {' -> '.join(redirects)}")

    evidence_files = observations.get("evidence_files") or []
    if isinstance(evidence_files, str):
        evidence_files = [evidence_files]
    evidence_count = len([value for value in evidence_files if str(value or "").strip()])
    if evidence_count:
        lines.append(f"- Supporting evidence supplied: {evidence_count} attachment(s)")

    return "Supporting observations:\n" + "\n".join(lines) if lines else ""


def get_webform_draft_text(
    domain: str,
    registrar: str,
    webform_url: str,
    cfg: dict,
    target_url: str = "",
    vt_link: str = "",
    *,
    observations: dict | None = None,
) -> str:
    """Generate one stable factual registrar web-form variant."""
    from datetime import datetime, timezone as _tz

    brand = cfg.get("brand_name") or "[BRAND]"
    contact_name = cfg.get("contact_name") or "[NAME]"
    contact_email = cfg.get("contact_email") or "[EMAIL]"

    detected_date = datetime.now(_tz.utc).strftime("%Y-%m-%d")

    t_url = target_url or f"https://{domain}"
    variant = _pick(
        _draft_rng(domain + "_registrar_webform"), _REGISTRAR_WEBFORM_VARIANTS,
    )
    observation_block = _webform_observation_block(observations)
    evidence_lines = (
        f"{variant['opening'].format(brand=brand)}\n\n"
        f"The reported page appears to use {brand} branding to present itself "
        f"as an authorized service. The suspected flow may present registration "
        f"or sign-in controls that direct visitors to deceptive content and "
        f"induce them to submit account credentials, personal information, or "
        f"other sensitive data. If confirmed, this activity may expose affected "
        f"users to account compromise, fraud, or financial loss.\n\n"
        f"Reported URL: {t_url}\n"
        f"Registered domain: {domain}\n"
        f"Registrar: {registrar}\n"
    )

    evidence_lines += (
        f"First detected: {detected_date}\n"
        + (f"\n{observation_block}\n" if observation_block else "")
        + f"\n{variant['request']}\n\n"
        + f"Reported by: {contact_name} <{contact_email}>"
    )

    return evidence_lines


def lookup_registrar_abuse_email(registrar: str) -> str | None:
    """Tra bảng tĩnh REGISTRAR_ABUSE_EMAILS theo tên registrar (contains match, case-insensitive).
    Trả về abuse email nếu tìm được, None nếu không."""
    if not registrar:
        return None
    r_lower = registrar.lower()
    for key, email in REGISTRAR_ABUSE_EMAILS.items():
        if key in r_lower:
            return email
    return None


def resolve_worker_report_recipients(who: dict, rdap: dict, registry: dict, hosting: dict | None = None) -> list[dict]:
    """Pure legacy Domain Worker email routing, shared by local and web workers."""
    recipients = []
    registrar = who.get("registrar") if isinstance(who, dict) else None
    emails = who.get("emails") if isinstance(who, dict) else None
    if isinstance(emails, str):
        emails = [emails]
    emails = [
        email for email in (emails or [])
        if email and not any(private in email.lower() for private in WHOIS_PRIVACY_DOMAINS)
    ]
    if not registrar:
        registrar = rdap.get("registrar")
    registrar_uses_webform = bool(
        registrar and any(key in registrar.lower() for key in WEB_FORM_REGISTRARS)
    )
    if not registrar_uses_webform:
        registrar_email = ", ".join(emails) if emails else rdap.get("abuse_email")
        if not registrar_email and registrar:
            registrar_email = lookup_registrar_abuse_email(registrar)
        if registrar_email:
            recipients.append({"channel": "registrar", "email": registrar_email})

    if registry.get("source") == "static_table" and registry.get("abuse_email"):
        recipients.append({"channel": "registry", "email": registry["abuse_email"]})

    if hosting and not is_cloudflare_proxy_contact(hosting) and hosting.get("abuse_email"):
        recipients.append({"channel": "hosting", "email": hosting["abuse_email"]})

    unique = []
    seen = set()
    for recipient in recipients:
        key = str(recipient.get("email") or "").strip().lower()
        if key and key not in seen:
            seen.add(key)
            unique.append(recipient)
    return unique


def resolve_quick_report_recipients(
    domain: str,
    who: dict | None = None,
    registry_contact: dict | None = None,
    rdap: dict | None = None,
) -> list[dict]:
    """Return lightweight registrar/registry report contacts for Quick Report.

    This intentionally omits hosting discovery, browser access and evidence
    capture. It reuses already-fetched WHOIS/registry data and only needs the
    caller's one RDAP fallback result.
    """
    who = who if isinstance(who, dict) else {}
    registry_contact = registry_contact if isinstance(registry_contact, dict) else {}
    rdap = rdap if isinstance(rdap, dict) else {}
    registrar = str(who.get("registrar") or rdap.get("registrar") or "").strip()
    # Generic WHOIS emails can be registrant/admin contacts, not abuse handlers.
    whois_emails = who.get("abuse_email") or []
    if isinstance(whois_emails, str):
        whois_emails = [whois_emails]
    usable_whois = _registrar_abuse_emails(whois_emails)

    recipients = []
    registrar_uses_webform = bool(
        registrar and any(key in registrar.lower() for key in WEB_FORM_REGISTRARS)
    )
    if not registrar_uses_webform:
        registrar_email = ", ".join(usable_whois)
        if not registrar_email:
            registrar_email = ", ".join(_registrar_abuse_emails(rdap.get("abuse_email")))
        if not registrar_email and registrar:
            registrar_email = lookup_registrar_abuse_email(registrar) or ""
        if registrar_email and not is_blocked_report_recipient(registrar_email):
            recipients.append({
                "channel": "registrar", "label": "Registrar",
                "email": registrar_email,
            })

    registry_email = str(registry_contact.get("abuse_email") or "").strip()
    if registry_email and not is_blocked_report_recipient(registry_email):
        recipients.append({
            "channel": "registry", "label": "Registry",
            "email": registry_email,
        })

    unique = []
    seen = set()
    for recipient in recipients:
        key = recipient["email"].lower()
        if key not in seen:
            seen.add(key)
            unique.append(recipient)
    return unique


# RDAP bootstrap cache (TLD → RDAP server URL). Load 1 lần per process.
# RDAP là chuẩn mới của ICANN thay thế WHOIS text — trả về JSON có cấu trúc,
# có entity role "abuse" rõ ràng, đáng tin cậy hơn python-whois parse text nhiều.
_rdap_bootstrap_cache: dict = {}
_rdap_bootstrap_loaded: bool = False


def _load_rdap_bootstrap() -> dict:
    """Load IANA RDAP bootstrap cho DNS TLD (https://data.iana.org/rdap/dns.json).
    Cache sau lần load đầu — không fetch lại trong cùng 1 process."""
    global _rdap_bootstrap_cache, _rdap_bootstrap_loaded
    if _rdap_bootstrap_loaded:
        return _rdap_bootstrap_cache
    try:
        r = requests.get("https://data.iana.org/rdap/dns.json", timeout=8)
        if r.status_code == 200:
            data = r.json()
            mapping = {}
            for service in data.get("services", []):
                tlds, servers = service[0], service[1]
                if servers:
                    for tld in tlds:
                        mapping[tld.lower()] = servers[0].rstrip("/")
            _rdap_bootstrap_cache = mapping
    except Exception:
        pass
    _rdap_bootstrap_loaded = True
    return _rdap_bootstrap_cache


def get_rdap_abuse_email(domain: str) -> dict:
    """Tra abuse email của registrar qua RDAP (ICANN standard JSON, đáng tin hơn WHOIS text).

    Dùng IANA bootstrap để tìm RDAP server đúng cho TLD của domain — không hardcode,
    hoạt động cho mọi gTLD/ccTLD có hỗ trợ RDAP. Đây chính xác là cách ICANN Lookup
    (lookup.icann.org) tra thông tin abuse contact.

    Trả về dict với các key:
      "registrar"   : tên registrar (str) — nếu parse được
      "abuse_email" : email abuse (str) — nếu có
      "error"       : lý do thất bại (str) — nếu không lấy được

    KHÔNG raise exception — lỗi trả về trong key "error".
    """
    if _private_registry_target(domain):
        # The public .am/.fm bootstrap describes the parent ccTLD registry, not
        # registrations in the independently operated radio namespace.
        return {"error": "Private radio registrations use their registry WHOIS"}
    if requests is None:
        return {"error": "requests library not available"}

    tld = domain.lower().rsplit(".", 1)[-1]
    bootstrap = _load_rdap_bootstrap()
    rdap_base = bootstrap.get(tld)
    if not rdap_base:
        return {"error": f"No RDAP server for .{tld} in IANA bootstrap"}

    try:
        resp = requests.get(
            f"{rdap_base}/domain/{domain}",
            timeout=10,
            headers={"Accept": "application/rdap+json"},
        )
        if resp.status_code == 404:
            return {"error": "Domain not found in RDAP"}
        if resp.status_code != 200:
            return {"error": f"RDAP HTTP {resp.status_code}"}
        data = resp.json()
    except Exception as e:
        return {"error": str(e)}

    registrar_name = None
    abuse_email = None

    def _vcard_field(vcard_array, field_name):
        """Lấy giá trị 1 field từ vcardArray dạng [['fn',{},'text','Value'],...]."""
        if len(vcard_array) < 2:
            return None
        for prop in vcard_array[1]:
            try:
                if prop[0] == field_name:
                    return prop[3]
            except (IndexError, TypeError):
                continue
        return None

    for entity in data.get("entities", []):
        if "registrar" not in entity.get("roles", []):
            continue
        registrar_name = _vcard_field(entity.get("vcardArray", []), "fn") or registrar_name
        # Tìm entity con có role "abuse"
        for sub in entity.get("entities", []):
            if "abuse" in sub.get("roles", []):
                email = _vcard_field(sub.get("vcardArray", []), "email")
                if email:
                    abuse_email = email
                    break
        if registrar_name or abuse_email:
            break

    result = {}
    if registrar_name:
        result["registrar"] = registrar_name
    if abuse_email:
        result["abuse_email"] = abuse_email
    if not result:
        result["error"] = "No registrar/abuse entity found in RDAP response"
    return result


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
    "cn.com", "hu.com", "za.com", "uy.com"
}


_PRIVATE_REGISTRY_WHOIS = {
    "radio.am": "whois.centralnic.com",
    "radio.fm": "whois.centralnic.com",
}


def _private_registry_target(domain: str) -> tuple[str, str] | None:
    host = domain.lower().rstrip(".")
    for suffix, server in _PRIVATE_REGISTRY_WHOIS.items():
        if host.endswith(f".{suffix}"):
            return ".".join(host.split(".")[-3:]), server
    return None


def _registrar_abuse_emails(value) -> list[str]:
    values = value if isinstance(value, (list, tuple)) else [value or ""]
    emails = []
    for _, email in getaddresses([str(item) for item in values]):
        if (re.fullmatch(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", email)
                and not any(private in email.casefold() for private in WHOIS_PRIVACY_DOMAINS)
                and not is_blocked_report_recipient(email)
                and email.casefold() not in {item.casefold() for item in emails}):
            emails.append(email)
    return emails


def _whois_fields(raw: str) -> dict[str, list[str]]:
    fields = {}
    for line in raw.splitlines():
        match = re.match(r"^([A-Za-z][A-Za-z ]+):\s*(.*?)\s*$", line)
        if match:
            fields.setdefault(match[1].casefold(), []).append(match[2])
    return fields


def _private_registry_whois(domain: str, server: str) -> dict:
    fields = _whois_fields(query_whois_server(server, domain))
    # Do not accept parent-domain records or infer contacts after a failed lookup.
    names = fields.get("domain name", [])
    if not names or any(name.casefold().rstrip(".") != domain for name in names):
        return {"error": "Private registry WHOIS record is unavailable or mismatched"}
    emails = _registrar_abuse_emails(fields.get("registrar abuse contact email", []))
    return {
        "registrar": next(iter(fields.get("registrar", [])), None),
        "creation_date": next(iter(fields.get("creation date", [])), None),
        "expiration_date": next(iter(fields.get("registry expiry date", [])), None),
        "name_servers": fields.get("name server", []),
        "status": fields.get("domain status", []),
        "emails": emails,
        "abuse_email": ", ".join(emails) or None,
    }


def whois_query_domain(domain: str) -> str:
    private = _private_registry_target(domain)
    if private:
        return private[0]
    parts = domain.lower().split(".")
    if len(parts) > 2 and ".".join(parts[-2:]) in FAKE_TLD_SUFFIXES:
        return ".".join(parts[-2:])
    return domain


def get_whois_info(domain: str):
    private = _private_registry_target(domain)
    if private:
        return _private_registry_whois(*private)
    if whois is None:
        return {"error": "python-whois chưa được cài (pip install python-whois)"}
    try:
        w = whois.whois(whois_query_domain(domain), inc_raw=True, timeout=10, quiet=True)
        # Retain only explicitly labelled abuse contacts; raw WHOIS and its
        # unqualified collection of personal emails never leave this boundary.
        fields = _whois_fields(str(w.get("raw") or ""))
        emails = _registrar_abuse_emails(fields.get("registrar abuse contact email", []))
        return {
            "registrar": w.get("registrar"),
            "creation_date": str(w.get("creation_date")),
            "expiration_date": str(w.get("expiration_date")),
            "name_servers": w.get("name_servers"),
            "emails": emails,
            "abuse_email": ", ".join(emails) or None,
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
    if timeout <= 0 or any(char in query for char in "\r\n\x00"):
        return ""
    deadline = time.monotonic() + timeout
    try:
        with socket.create_connection((server, 43), timeout=timeout) as sock:
            sock.sendall(f"{query}\r\n".encode())
            chunks = []
            size = 0
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return ""
                sock.settimeout(remaining)
                data = sock.recv(4096)
                if not data:
                    break
                size += len(data)
                if size > 1024 * 1024:
                    return ""
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


# whois.iana.org trả về referral cho MỌI TLD nó biết, kể cả gTLD — nếu không loại trừ,
# lookup_registry_contact() sẽ "leo thang Registry" cho cả gTLD bình thường.
# Các TLD đã có route tĩnh trong CCTLD_REGISTRY_CONTACTS không cần nằm trong skip list.
_SKIP_REGISTRY_ESCALATION_TLDS = {"info", "biz", "name", "pro", "mobi"}


def lookup_registry_contact(domain: str) -> dict:
    """Tra abuse contact của Registry quản lý TLD của domain.

    Tra CCTLD_REGISTRY_CONTACTS (bảng tĩnh, không cần mạng) TRƯỚC — thử 2-level suffix
    (.co.jp, .uk.com, .com.mx...) trước, rồi mới thử 1-level (.jp, .com...).
    Fallback sang iana_referral() (cần mạng) khi không tìm thấy trong bảng.
    """
    d = domain.lower()
    # Thử 2-level suffix trước (vd "co.jp", "uk.com")
    parts = d.rsplit(".", 2)
    if len(parts) >= 3:
        two_level = f"{parts[-2]}.{parts[-1]}"
        if two_level in CCTLD_REGISTRY_CONTACTS:
            return {"source": "static_table", **CCTLD_REGISTRY_CONTACTS[two_level]}
    # Thử 1-level TLD
    tld = d.rsplit(".", 1)[-1]
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
    # Vercel CNAME
    "vercel.app": "vercel",
    "vercel-dns.com": "vercel",
    # Netlify CNAME
    "netlify.app": "netlify",
    "netlify.com": "netlify",
    # GitHub Pages
    "github.io": "github-pages",
    "github.com": "github-pages",
    # Firebase / GCP
    "web.app": "firebase",
    "firebaseapp.com": "firebase",
    "appspot.com": "google-cloud",
    # DigitalOcean
    "ondigitalocean.app": "digitalocean",
    "digitaloceanspaces.com": "digitalocean",
    # Azure
    "azurewebsites.net": "azure",
    "azurestaticapps.net": "azure",
    "cloudapp.azure.com": "azure",
    # AWS (non-CloudFront)
    "amazonaws.com": "aws",
    "elasticbeanstalk.com": "aws",
    # Render
    "onrender.com": "render",
    # Heroku
    "herokuapp.com": "heroku",
}

# Hosting platform phishing thường host trực tiếp — detect qua domain suffix (đáng tin nhất)
_HOSTING_DOMAIN_SUFFIXES = {
    ".vercel.app": "vercel",
    ".netlify.app": "netlify",
    ".github.io": "github-pages",
    ".pages.dev": "cloudflare-pages",
    ".web.app": "firebase",
    ".firebaseapp.com": "firebase",
    ".onrender.com": "render",
    ".herokuapp.com": "heroku",
    ".azurewebsites.net": "azure",
    ".azurestaticapps.net": "azure",
    ".ondigitalocean.app": "digitalocean",
    ".appspot.com": "google-cloud",
    ".amazonaws.com": "aws",
    ".s3-website": "aws",
}


def detect_cdn(domain: str) -> list:
    """Nhận diện CDN/hosting platform đang che hoặc host domain.

    3 nguồn tín hiệu (theo thứ tự độ tin cậy giảm dần):
    1. Domain suffix — chắc chắn nhất (vd .vercel.app = luôn là Vercel)
    2. CNAME chain — qua dnspython
    3. HTTP response headers — Server/Via/X-Cache/X-Served-By/X-Amz-Cf-Id

    KHÔNG gồm Cloudflare — xem is_cloudflare() (dùng nameserver riêng).
    Không raise — lỗi ở từng nguồn chỉ khiến nguồn đó bị bỏ qua.
    """
    detected = set()
    domain_lower = domain.lower()

    # 1. Domain suffix check (ưu tiên cao nhất — không cần mạng)
    for suffix, name in _HOSTING_DOMAIN_SUFFIXES.items():
        if domain_lower.endswith(suffix):
            detected.add(name)

    # 2. CNAME chain
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

    # 3. HTTP headers
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
                powered_by = str(r.headers.get("X-Powered-By", "")).lower()
                if blob.strip() or powered_by:
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
                    if "vercel" in blob or "vercel" in powered_by:
                        detected.add("vercel")
                    if "netlify" in blob or "netlify" in powered_by:
                        detected.add("netlify")
                    if "github" in blob:
                        detected.add("github-pages")
                    if "firebase" in blob or "firebasehosting" in blob:
                        detected.add("firebase")
                    if "heroku" in blob:
                        detected.add("heroku")
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
        return {"skipped": "VirusTotal API key is not configured"}
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
        return {"skipped": "Safe Browsing API key is not configured"}
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


def generate_safebrowsing_report_text(
    domain: str, cfg: dict, target_url: str = "",
) -> str:
    """Sinh sẵn 1 đoạn mô tả ngắn để copy-paste vào ô mô tả của form report thủ công tại
    https://safebrowsing.google.com/safebrowsing/report_phish/ (Google không có API submit report,
    chỉ có API tra cứu qua check_safebrowsing() ở trên).

    KHÔNG ghi ra file trong reports/ như các generate_*_draft() khác — đây là text dán vào 1 ô
    textbox của form web, không phải nội dung email gửi qua mail client hay SMTP, nên không cần
    (và không nên) có file .txt riêng hay xuất hiện trong danh sách gửi email của
    email_send_ui.py. Chỉ trả về string để cmd_check/pages/1_Check_Domain.py tự hiển thị bằng
    st.code()/print() cho người dùng copy.
    """
    reported_url = target_url or f"https://{domain}"
    rng = _draft_rng(domain)
    brand = cfg.get("brand_name") or "our brand"
    variants = [
        f"The reported URL {reported_url} is a suspected phishing page impersonating {brand}. It presents deceptive brand and login-related content designed to induce users to enter account credentials or personal information. Please investigate this specific URL and add it to Google Safe Browsing protections if confirmed.",
        f"We are reporting {reported_url} for suspected phishing and unauthorized impersonation of {brand}. The page may deceive visitors into submitting login credentials or other sensitive user information. Please review the URL and show an appropriate Safe Browsing warning if the violation is confirmed.",
        f"The URL {reported_url} appears to misuse the identity of {brand} in a deceptive page intended to obtain user credentials or personal data. Please investigate and block this phishing URL through Google Safe Browsing if confirmed.",
        f"Please review {reported_url} as a suspected phishing URL targeting users of {brand}. Its branding and login-related presentation may mislead visitors into entering sensitive account information. We request Safe Browsing protection for this exact URL if your review confirms the abuse.",
        f"This report concerns {reported_url}, which appears to impersonate {brand} and solicit credentials or personal information through deceptive content. Please verify the page and add a browser phishing warning under Google Safe Browsing policy if confirmed.",
    ]
    return _pick(rng, variants)


def generate_cloudflare_report_text(
    domain: str, cfg: dict, target_url: str = "",
) -> str:
    """Sinh sẵn nội dung mô tả để copy-paste vào form report Cloudflare tại
    https://abuse.cloudflare.com/ (mục Phishing & Malware) — tương tự
    generate_safebrowsing_report_text(), không ghi file, chỉ trả về string.
    """
    reported_url = target_url or f"https://{domain}"
    rng = _draft_rng(domain + "_cf")  # seed khác GSB để variant không trùng
    brand = cfg.get("brand_name") or "our brand"
    variants = [
        f"The reported URL {reported_url} is a suspected phishing page impersonating {brand}. Its deceptive brand and login-related content may induce users to submit credentials or personal information. Please investigate the use of Cloudflare services and take appropriate action or forward this report to the responsible hosting provider if confirmed.",
        f"We are reporting {reported_url} for suspected phishing and unauthorized impersonation of {brand}. The page appears designed to obtain sensitive user information through deceptive content. Please review the associated Cloudflare service and apply the appropriate abuse response, including notifying the origin host where applicable.",
        f"The specific URL {reported_url} appears to misuse the identity of {brand} to solicit account credentials or personal data. Please investigate this phishing report under Cloudflare's abuse policies and coordinate with the responsible hosting provider if the content is confirmed.",
        f"Please investigate {reported_url}, a suspected phishing page targeting users of {brand}. The page's branding and login-related presentation may deceive visitors into entering sensitive information. We request appropriate action concerning the Cloudflare services involved and referral to the origin provider when necessary.",
        f"This abuse report concerns {reported_url}, which appears to impersonate {brand} and solicit user credentials or personal information. Please verify the reported content and take the action available to Cloudflare, or transmit the report to the relevant hosting provider, if confirmed.",
    ]
    return _pick(rng, variants)


def generate_community_report_text(
    domain: str, cfg: dict, target_url: str = "",
) -> str:
    """Return provider-neutral text for community phishing report forms."""
    reported_url = target_url or f"https://{domain}"
    brand = cfg.get("brand_name") or "our brand"
    return (
        f"The reported URL {reported_url} appears to be a suspected phishing page "
        f"impersonating {brand}. Its deceptive branding and login-related content "
        "may mislead visitors into providing account credentials or personal "
        "information. Please review this exact URL and add an appropriate unsafe-site "
        "warning or blocklist entry if the violation is confirmed."
    )


# --------------------------------------------------------------------------
# A1 — HTTP check: page còn sống không, title, redirect, login form
# --------------------------------------------------------------------------

def check_http(target: str) -> dict:
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
    if "://" in target:
        parsed = urlparse(target)
        urls = [target]
        if parsed.scheme.lower() == "https":
            urls.append(parsed._replace(scheme="http").geturl())
    else:
        urls = [f"https://{target}", f"http://{target}"]

    for url in urls:
        scheme = urlparse(url).scheme
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


def prepare_external_email_body(body: str) -> str:
    """Remove internal instructions and retired scan evidence before sending."""
    rendered = body or ""
    # Drafts created by older releases may still contain the retired external
    # scan block.  Remove it at the final send boundary so a legacy draft can
    # never leak links or verdicts from that service.
    rendered = re.sub(
        r"(?ims)^\s*---\s*Evidence:\s*URLScan(?:\.io)?.*?---\s*End(?:\s+of)?\s+URLScan evidence\s*---\s*",
        "\n",
        rendered,
    )
    rendered = re.sub(r"(?im)^.*https?://urlscan\.io/[^\r\n]*\r?\n?", "", rendered)
    rendered = re.sub(r"(?im)^.*\bURLScan(?:\.io)?\b.*\r?\n?", "", rendered)
    rendered = re.sub(r"(?im)^.*\bNOT\s+flagged\b.*\r?\n?", "", rendered)
    rendered = re.sub(
        r"(?mi)^Screenshots?:\s*\[ĐÍNH KÈM ẢNH CHỤP MÀN HÌNH[^\]]*\]\s*$",
        "",
        rendered,
    )
    rendered = re.sub(
        r"(?mi)^\[ĐÍNH KÈM ẢNH CHỤP MÀN HÌNH[^\]]*\]\s*$",
        "",
        rendered,
    )
    rendered = re.sub(
        r"(?mi)^Screenshots?(?: evidence)?:\s*Not available(?: at the time of submission)?\.\s*$",
        "",
        rendered,
    )
    rendered = re.sub(
        r'(?mi)^\(Ảnh chụp phải hiển thị rõ URL ["“].*?["”] trên thanh địa chỉ của trình duyệt\)\s*$',
        "",
        rendered,
    )
    rendered = re.sub(r"(?ms)^\[NOTE:.*?\]\s*", "", rendered)
    return re.sub(r"\n{3,}", "\n\n", rendered).strip()


# --------------------------------------------------------------------------
# B3 — Wayback Machine: archive trang phishing làm bằng chứng tĩnh
# --------------------------------------------------------------------------


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


# --------------------------------------------------------------------------
# dnstwist — brand monitoring / typosquat scan
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# CSV log
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# Email drafts
# --------------------------------------------------------------------------

# Seed random bằng domain + ngày để cùng 1 domain trong 1 ngày cho ra cùng variant
# nhưng ngày khác nhau thì khác → tránh gửi đúng 1 bản lặp lại nhiều lần.
def _draft_rng(domain: str) -> random.Random:
    seed = domain + datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return random.Random(seed)


def _pick(rng: random.Random, options: list) -> str:
    return rng.choice(options)


# Pool các variant cho từng phần của email — mỗi pool có ít nhất 4 variant.
_SUBJECT_REGISTRAR = [
    "Phishing Abuse Report - {domain}",
    "Urgent Review Requested: Suspected Phishing at {domain}",
    "Brand Impersonation Report - {domain}",
    "Abuse Investigation Request - {domain}",
    "Reported Phishing URL Hosted on {domain}",
]

_OPENING_REGISTRAR = [
    "We are reporting suspected phishing and unauthorized brand impersonation at {domain} and request an urgent abuse review.",

    "We identified content at {domain} that appears to impersonate our brand and may mislead visitors into interacting with a fraudulent website.",

    "This is a formal abuse report concerning suspected phishing content and unauthorized use of our brand at {domain}.",

    "Our review found content at {domain} consistent with suspected phishing and unauthorized brand impersonation.",
]

_DESCRIPTION_REGISTRAR = [
    "The reported page appears to reproduce our brand identity in a way that may cause visitors to believe it is an authorized service and disclose sensitive information.",

    "The observed content appears to present itself as an official service associated with our brand. We have not authorized this use and believe it warrants investigation under your phishing and abuse policies.",

    "The reported URL uses our brand without authorization and displays content consistent with a deceptive or phishing page that may put visitors' information at risk.",

    "The page appears designed to mislead visitors through unauthorized brand impersonation. Please independently verify the reported content and associated evidence.",
]

_REQUEST_REGISTRAR = [
    "We request the immediate suspension of this domain in accordance with your Acceptable Use Policy and applicable registrar abuse procedures.",

    "We respectfully request that you immediately suspend this domain and take any appropriate registrar-level action under your Acceptable Use Policy and applicable abuse policies.",

    "Please take immediate action to suspend this domain and prevent its continued use for phishing and unauthorized brand impersonation in accordance with your applicable abuse policies.",

    "We urge you to suspend this domain without delay and take appropriate action under your Acceptable Use Policy and registrar abuse procedures to prevent further harm to users.",
]

_CLOSING_REGISTRAR = [
    "Please confirm receipt of this report and any action taken at your earliest convenience.",

    "We appreciate your prompt attention to this matter and would appreciate confirmation of any action taken.",

    "Thank you for your attention to this urgent matter. Please acknowledge receipt and advise us of any action taken.",

    "We appreciate your prompt assistance in addressing this matter and look forward to your confirmation.",
]

_SUBJECT_CA = [
    "SSL Certificate Abuse Report - {domain} (Phishing)",
    "Phishing Site Using SSL Certificate - {domain}",
    "Certificate Abuse Report: Phishing Domain - {domain}",
    "Request for SSL Certificate Review - {domain} (Phishing)",
]

_OPENING_CA = [
    "We are reporting the SSL certificate associated with {domain}, which is being used by an active phishing website impersonating our brand.",
    "We are writing to report that the certificate associated with {domain} is currently being used on a phishing website targeting our brand and users.",
    "This is an abuse report regarding the SSL certificate used by {domain}, an active phishing site impersonating our brand.",
    "We have identified {domain} as an active phishing website impersonating our brand and using an SSL certificate to serve the fraudulent site over HTTPS.",
]

_DESCRIPTION_CA = [
    "The fraudulent site clones our brand's official login page to deceive users into submitting sensitive information, including login credentials, OTP tokens, and payment details.",
    "This domain hosts a phishing page that replicates our brand's login interface and is used to steal user credentials, harvest OTP tokens, and collect payment information.",
    "The site impersonates our official brand portal and is being used to steal login credentials, OTP tokens, and sensitive payment information from users.",
    "The website uses our brand identity and cloned authentication interface to operate an active phishing page and collect sensitive information from victims.",
]


def render_registrar_email_draft(domain, cert, vt, cfg, registrar, abuse_emails, target_url=None, *, rng=None, detected_date=None):
    """Render the legacy registrar email variant without writing a draft file."""
    rng = rng or _draft_rng(domain)
    detected_date = detected_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    reported_url = target_url or f"https://{domain}"
    contact_name = cfg.get("contact_name") or ""
    contact_email = cfg.get("contact_email") or ""
    signature = f"\nRegards,\n{contact_name}\n{contact_email}\n" if contact_name else "\nRegards,\n"
    subject = _pick(rng, _SUBJECT_REGISTRAR).format(domain=domain)
    opening = _pick(rng, _OPENING_REGISTRAR).format(domain=domain)
    description = _pick(rng, _DESCRIPTION_REGISTRAR)
    request = _pick(rng, _REQUEST_REGISTRAR)
    closing = _pick(rng, _CLOSING_REGISTRAR)

    # Evidence bullets — random thứ tự (trừ Domain luôn đứng đầu)
    vt_count = vt.get("malicious", 0) or 0
    vt_suspicious = vt.get("suspicious", 0) or 0
    vt_info = ""
    if vt_count:
        vt_info = f"VirusTotal: {vt.get('link', 'N/A')} ({vt_count} security engines flagged as malicious)"
    elif vt_suspicious:
        vt_info = f"VirusTotal: {vt.get('link', 'N/A')} ({vt_suspicious} security engines flagged as suspicious)"

    extra_bullets = [
        f"Registrar: {registrar}",
        f"SSL Issuer: {cert.get('issuer', 'N/A')}",
        f"SSL Serial: {cert.get('serial', 'N/A')}",
        f"First detected: {detected_date}",
    ]
    if vt_info:
        extra_bullets.append(vt_info)
    rng.shuffle(extra_bullets)
    evidence = "\n".join(f"- {b}" for b in [f"Domain: {domain}"] + extra_bullets)

    # Đoạn bổ sung chi tiết động — số engine VT + ngày phát hiện cụ thể
    _vt_sentences = []
    if vt_count:
        _vt_sentences = [
            f"As of {detected_date}, {vt_count} security engines on VirusTotal flagged this domain as malicious.",
            f"VirusTotal shows {vt_count} malicious detections for this domain as of {detected_date}.",
            f"VirusTotal analysis dated {detected_date} records {vt_count} malicious detections for this domain.",
            f"As supporting context, VirusTotal reports {vt_count} malicious detections on {detected_date}.",
        ]
    elif vt_suspicious:
        _vt_sentences = [
            f"As of {detected_date}, {vt_suspicious} security vendors on VirusTotal have flagged this domain as suspicious.",
            f"VirusTotal analysis dated {detected_date} shows {vt_suspicious} security vendors flagging this domain as suspicious.",
        ]
    vt_detail = _pick(rng, _vt_sentences) if _vt_sentences else ""

    _detection_sentences = [
        f"This domain was first identified by our security team on {detected_date} during routine brand monitoring.",
        f"Our security team detected this phishing site on {detected_date} through proactive brand protection monitoring.",
        f"We identified this threat on {detected_date} as part of our ongoing brand monitoring operations.",
        f"This domain came to our attention on {detected_date} during a routine brand impersonation scan.",
    ]
    detection_detail = _pick(rng, _detection_sentences)

    extra_context = "\n\n".join(filter(None, [vt_detail, detection_detail]))

    return f"""To: {abuse_emails or '[TRA ABUSE EMAIL TẠI https://lookup.icann.org/]'}
Subject: {subject}
Dear {registrar} Abuse Team,

{opening}

{description}

{extra_context}

Evidence:
- Phishing URL  : {reported_url}
- Domain        : {domain}
{chr(10).join(f'- {b}' for b in extra_bullets)}


{request}

{closing}
{signature}"""


def render_hosting_email_draft(domain, ip, ip_whois, cfg):
    if is_cloudflare_proxy_contact(ip_whois):
        return None
    """Sinh draft DMCA/AUP takedown gửi ISP/hosting sở hữu IP gốc."""
    contact_name = cfg.get("contact_name") or ""
    contact_email = cfg.get("contact_email") or ""
    signature = f"\nRegards,\n{contact_name}\n{contact_email}\n" if contact_name else "\nRegards,\n"
    detected_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rng = _draft_rng(domain + "_hosting")

    org = ip_whois.get("org") or "[TÊN TỔ CHỨC HOSTING CHƯA XÁC ĐỊNH]"
    abuse_email = ip_whois.get("abuse_email") or "[TRA ABUSE EMAIL TẠI https://lookup.icann.org/ hoặc rdap.arin.net]"
    asn = ip_whois.get("asn") or "N/A"

    subject = f"Abuse Report: Active Phishing Site - {domain}"
    opening = _pick(rng, [
        f"We are writing to request the immediate suspension of a phishing website hosted on your infrastructure that is impersonating our brand.",
        f"We have identified an active phishing site hosted on your network (IP: {ip}) that is impersonating our brand and actively harvesting user credentials.",
        f"This is a formal takedown request for a phishing site running on your infrastructure (IP: {ip}) that fraudulently impersonates our brand.",
        f"We are reporting an active phishing operation hosted on your network that is cloning our brand's official website to steal user credentials.",
    ])
    description = _pick(rng, [
        f"The website at https://{domain} (hosted on IP {ip}) is an unauthorized clone of our brand's official platform, actively harvesting user credentials, OTP tokens, and payment details from unsuspecting visitors.",
        f"This phishing site at https://{domain} (IP: {ip}) replicates our brand's login interface to deceive users into submitting their credentials, OTP tokens, and financial information.",
        f"The domain {domain} (hosted at IP {ip}) hosts a fraudulent replica of our brand's authentication portal designed to steal login credentials and sensitive personal data from victims.",
        f"Hosted on IP {ip}, the site {domain} is a phishing page cloning our official brand portal to harvest user credentials, OTP codes, and payment details.",
    ])
    request = _pick(rng, [
        f"We request you suspend this hosting account immediately under your Acceptable Use Policy. This is causing ongoing harm to our users and brand reputation.",
        f"Please immediately suspend or null-route traffic to this IP/account under your AUP to prevent further credential theft from our users.",
        f"We urge you to terminate this hosting account without delay in accordance with your Acceptable Use Policy to stop the ongoing phishing operation.",
        f"Immediate suspension of this account/IP under your AUP is requested to protect users from ongoing credential harvesting.",
    ])

    return f"""To: {abuse_email}
Subject: {subject}

Dear {org} Abuse Team,

{opening}

{description}

Hosting details:
- Phishing domain: {domain}
- Origin IP: {ip}
- ASN: {asn}
- Hosting organization: {org}
- First detected: {detected_date}

Note: This IP was identified as a candidate origin server via subdomain
enumeration — please verify independently before taking action.

{request}
{signature}"""


def get_registry_webform_draft_text(
    domain: str, registry_info: dict, cfg: dict, target_url: str = "", *,
    observations: dict | None = None, registrar_reported: bool = False,
    registrar_report_date: str | None = None,
) -> str:
    """Generate one stable factual registry web-form variant."""
    brand = cfg.get("brand_name") or "[BRAND]"
    contact_name = cfg.get("contact_name") or "[NAME]"
    contact_email = cfg.get("contact_email") or "[EMAIL]"
    reported_url = target_url or f"https://{domain}"
    detected_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    registry_name = registry_info.get("registry") or "Registry"
    variant = _pick(
        _draft_rng(domain + "_registry_webform"), _REGISTRY_WEBFORM_VARIANTS,
    )
    observation_block = _webform_observation_block(observations)
    if registrar_reported:
        date_suffix = f" on {registrar_report_date}" if registrar_report_date else ""
        escalation = (
            f"This matter was previously reported to the sponsoring registrar{date_suffix}. "
            "The reported URL remains available, so registry-level review is requested."
        )
    else:
        escalation = (
            "We identified this URL while reviewing suspected phishing activity involving apparent "
            f"impersonation of {brand}, and we are submitting it for your review."
        )
    return (
        f"{variant['opening'].format(brand=brand)}\n\n"
        f"The reported page appears to use {brand} branding to present itself "
        f"as an authorized service. The suspected flow may present registration "
        f"or sign-in controls leading visitors to deceptive content and may "
        f"induce them to submit account credentials, personal information, or "
        f"other sensitive data. If confirmed, this activity may expose affected "
        f"users to account compromise, fraud, or financial loss.\n\n"
        f"Reported URL: {reported_url}\n"
        f"Registered domain: {domain}\n"
        f"Registry: {registry_name}\n"
        f"First detected: {detected_date}\n"
        + (f"\n{observation_block}\n" if observation_block else "")
        + f"\n{escalation}\n\n"
        + f"{variant['request']}\n\n"
        + f"Reported by: {contact_name} <{contact_email}>"
    )


def render_registry_email_draft(
    domain, registry_info, cfg, target_url=None, *, registrar_reported=False,
    registrar_report_date=None, rng=None, detected_date=None,
):
    """Render the legacy registry email variant without writing a file."""
    rng = rng or _draft_rng(domain + "_registry")
    detected_date = detected_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    reported_url = target_url or f"https://{domain}"
    brand_name = cfg.get("brand_name") or "[BRAND]"
    contact_name = cfg.get("contact_name") or ""
    contact_email = cfg.get("contact_email") or ""
    signature = f"\nRegards,\n{contact_name}\n{contact_email}\n" if contact_name else "\nRegards,\n"
    if registry_info.get("source") == "static_table":
        registry_name = registry_info.get("registry") or "Registry"
        abuse_email = registry_info.get("abuse_email") or "[TRA ABUSE EMAIL TẠI https://www.iana.org/domains/root/db]"
    else:
        registry_name = f"Registry (via {registry_info.get('whois_server', 'IANA')})"
        abuse_email = "[TRA ABUSE EMAIL TẠI https://www.iana.org/domains/root/db]"
    note_line = ""
    subject = _pick(rng, [
        f"Registry Abuse Review Request - {domain}",
        f"Suspected Phishing Report for {domain}",
        f"Registry-Level Abuse Report - {domain}",
        f"Brand Impersonation Review Requested - {domain}",
        f"Urgent Domain Abuse Investigation - {domain}",
    ])
    opening = _pick(rng, [
        f"We are requesting a registry-level review of suspected phishing and unauthorized impersonation of {brand_name} at {domain}.",
        f"We are reporting suspected phishing content hosted under {domain} that appears to impersonate {brand_name} without authorization.",
        f"This report concerns suspected phishing and brand impersonation observed at the reported URL under {domain}.",
        f"We request your assistance in reviewing a suspected abuse case involving {domain} and unauthorized use of our brand identity.",
    ])
    if registrar_reported:
        date_suffix = f" on {registrar_report_date}" if registrar_report_date else ""
        escalation_reason = (
            f"This matter was previously reported to the sponsoring registrar{date_suffix}. "
            "The reported URL remains available, so we are requesting registry-level review."
        )
    else:
        escalation_reason = (
            "We are contacting the registry because the reported URL appears to present "
            "an ongoing abuse risk. Please independently review the evidence and "
            "coordinate with the sponsoring registrar where appropriate."
        )
    request = _pick(rng, [
        "Please investigate and take proportionate registry-level action under your abuse policy if the reported violation is confirmed.",
        "Please review the reported URL and coordinate prompt mitigation with the sponsoring registrar where appropriate.",
        "We request an urgent abuse investigation and any action available to the registry after independent verification.",
        "Please preserve relevant records, investigate the reported content, and apply the measures provided by your abuse policy if confirmed.",
    ])

    return f"""To: {abuse_email}
Subject: {subject}

Dear {registry_name} Abuse Department,

{opening}

{escalation_reason}

{request}

Domain: {domain}
Reported URL: {reported_url}
First detected: {detected_date}
{note_line}
{signature}"""


# --------------------------------------------------------------------------
# Gửi báo cáo qua email thật (SMTP)
# --------------------------------------------------------------------------
#
# Chỉ gửi khi người dùng đã tự xác nhận thủ công (Streamlit: tick checkbox "đã đọc và xác
# nhận" trước khi nút "Gửi" hết disabled; CLI `send`: input("Gửi email này? (y/N): ")) — không
# có đường nào tự động gửi hàng loạt không qua duyệt, giữ đúng nguyên tắc xác minh-trước-khi-
# report xuyên suốt dự án (plan_phishing_takedown.md bước 2).


SMTP_DEFAULT_TIMEOUT = 30.0
SMTP_ATTACHMENT_TIMEOUT = 60.0
SMTP_TRANSIENT_RETRIES = 1


def _close_smtp_server(server) -> None:
    """Close a connection without masking the original send error."""
    if server is None:
        return
    try:
        server.quit()
    except Exception:
        try:
            server.close()
        except Exception:
            pass


def _imap_save_sent(account: dict, raw_msg: bytes, *, message_id: str | None = None) -> str | None:
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
    if imap_host.lower().rstrip('.') in {"gmail.com", "smtp.gmail.com", "imap.gmail.com", "smtp.googlemail.com", "imap.googlemail.com"}:
        return None

    mail = None
    try:
        mail = imaplib.IMAP4_SSL(imap_host, imap_port, timeout=30, ssl_context=ssl.create_default_context())
        mail.login(username, password)

        # Tìm thư mục Sent — các mail server dùng tên khác nhau
        sent_folder = None
        status, folders = mail.list()
        if status != 'OK':
            return 'IMAP LIST rejected'
        candidates = []
        for folder_line in (folders or []):
            name = folder_line.decode("utf-8", errors="ignore") if isinstance(folder_line, bytes) else str(folder_line)
            parsed = re.match(r'\(([^)]*)\)\s+(?:"(?:[^"\\]|\\.)*"|NIL)\s+(.+)$', name)
            if not parsed:
                continue
            flags, mailbox = parsed.groups()
            if r'\sent' in flags.casefold().split():
                sent_folder = mailbox
                break
            leaf = mailbox.strip('"').replace('\\"', '"').casefold().rsplit('/', 1)[-1]
            if leaf in {"sent", "sent items", "sent messages", "sent mail", "gesendete", "enviados"}:
                candidates.append(mailbox)
        if not sent_folder:
            sent_folder = candidates[0] if candidates else "Sent"

        # Recovery may only add the existing SMTP message, never send it again.
        if message_id:
            if any(char in message_id for char in '\r\n"'):
                return 'Invalid Sent Message-ID'
            status, _ = mail.select(sent_folder, readonly=True)
            if status != 'OK':
                return 'IMAP Sent selection rejected'
            status, matches = mail.uid('SEARCH', None, 'HEADER', 'Message-ID', f'"{message_id}"')
            if status != 'OK':
                return 'IMAP Sent lookup rejected'
            if any(value and value.strip() for value in matches or []):
                return None

        # APPEND email vào Sent folder với flag \Seen
        now = imaplib.Time2Internaldate(datetime.now().timestamp())
        status, _ = mail.append(sent_folder, r"\Seen", now, raw_msg)
        if status != 'OK':
            return 'IMAP Sent APPEND rejected'
        return None
    except Exception as e:
        return str(e)
    finally:
        if mail is not None:
            try:
                mail.logout()
            except Exception:
                pass


def personalize_email_body(body: str, cfg: dict, account: dict) -> str:
    """Render shared draft contact fields for one SMTP sender account.

    By default the signature email is the SMTP username. An account may override
    it with ``contact_email`` and may optionally provide its own ``contact_name``.
    """
    rendered = prepare_external_email_body(body)
    company_email = str(cfg.get("contact_email") or "").strip()
    company_name = str(cfg.get("contact_name") or "").strip()
    sender_email = str(account.get("contact_email") or account.get("username") or company_email).strip()
    sender_name = str(account.get("contact_name") or company_name).strip()
    if company_email and sender_email:
        rendered = rendered.replace(company_email, sender_email)
    if company_name and sender_name and sender_name != company_name:
        rendered = rendered.replace(company_name, sender_name)
    organization = str(
        account.get("company_name") or cfg.get("company_name") or ""
    ).strip()
    role = str(account.get("signature_role") or cfg.get("signature_role") or "").strip()
    registration = str(
        account.get("business_registration_no")
        or cfg.get("business_registration_no")
        or ""
    ).strip()
    signature_lines = []
    role_line = " — ".join(value for value in (role, organization) if value)
    if role_line and role_line not in rendered:
        signature_lines.append(role_line)
    registration_line = f"Business Registration No. {registration}" if registration else ""
    if registration_line and registration_line not in rendered:
        signature_lines.append(registration_line)
    if signature_lines:
        rendered = rendered.rstrip() + "\n" + "\n".join(signature_lines) + "\n"
    return rendered


# file khi ghi sent_log.csv, không liên quan gì tới parse_draft_email() bên dưới.
_DRAFT_FILENAME_SUFFIXES = [
    "_registrar_report.txt",
    "_ca_report.txt",
    "_apwg_report.txt",
    "_hosting_report.txt",
    "_registry_report.txt",
    "_vncert_report.txt",
]


def validate_report_delivery(
    parsed: dict, *, target_url: str = "", attachments: list[str] | None = None,
    require_browser_evidence: bool = False,
) -> list[str]:
    """Return user-facing quality errors that must block an external send."""
    errors = []
    subject = str((parsed or {}).get("subject") or "").strip()
    recipient = str((parsed or {}).get("to") or "").strip()
    body = str((parsed or {}).get("body") or "")
    if not recipient:
        errors.append("Draft không có email nhận hợp lệ")
    if recipient and is_blocked_report_recipient(recipient):
        errors.append("Recipient abuse@cloudflare.com không được Cloudflare theo dõi; hãy dùng form Cloudflare Abuse")
    if not subject:
        errors.append("Draft không có Subject")
    if target_url and f"Reported URL: {target_url}" not in body:
        errors.append("Draft không chứa đúng full Reported URL")
    if re.search(r"(?i)urlscan(?:\.io)?|https?://urlscan\.io/", body):
        errors.append("Draft còn bằng chứng từ dịch vụ scan đã ngừng hỗ trợ")
    if re.search(r"(?i)\[(?:PLEASE|ATTACH|ĐÍNH KÈM|TRA ABUSE|KHÔNG CÓ)", body):
        errors.append("Draft còn placeholder cần xử lý")
    attachment_paths = [os.path.abspath(str(path)) for path in attachments or []]
    missing = [path for path in attachment_paths if not os.path.isfile(path)]
    if missing:
        errors.append("Có attachment không còn tồn tại")
    if require_browser_evidence:
        manifests = [path for path in attachment_paths if path.lower().endswith(".json")]
        images = [path for path in attachment_paths if path.lower().endswith((".png", ".jpg", ".jpeg"))]
        if len(manifests) != 1 or not 1 <= len(images) <= 3:
            errors.append("Browser Evidence phải có từ 1 đến 3 ảnh và đúng một manifest")
        else:
            validation = browser_evidence.validate_evidence_artifacts({
                "success": True, "screenshot_paths": images,
                "screenshot_path": images[0], "manifest_path": manifests[0],
            })
            if not validation["valid"]:
                errors.append("Browser Evidence hoặc hash manifest không hợp lệ")
            elif target_url and not browser_evidence.evidence_url_matches(
                target_url, (validation.get("manifest") or {}).get("requested_url", ""),
            ):
                errors.append("Browser Evidence không thuộc đúng full Reported URL hiện tại")
    return errors


def _insert_before_email_signature(content: str, block: str) -> str:
    """Insert report content before Regards/Kind regards, keeping the signature last."""
    signature = re.search(r"(?im)^\s*(?:Kind regards|Regards),?\s*$", content)
    if not signature:
        return content.rstrip() + "\n\n" + block.strip() + "\n"
    prefix = content[:signature.start()].rstrip()
    suffix = content[signature.start():].lstrip()
    return prefix + "\n\n" + block.strip() + "\n\n" + suffix.rstrip() + "\n"


def run_cloaking_check(target: str, mode: str = "full", cfg: dict | None = None) -> dict:
    """Run the shared passive cloaking detector used by CLI, UI and worker.

    ``fast`` skips the extra ``/vi-vn/`` profiles. All network failures are
    converted to an INCONCLUSIVE result so one provider cannot break a check.
    """
    try:
        return cloaking_detector.probe_http_cloaking(
            target,
            include_path_variant=mode != "fast",
            evidence_root=None,
            vantage_points=(cfg or {}).get("cloaking_vantage_points") or [],
        )
    except Exception as exc:
        return {
            "version": 1, "engine": "http", "target_url": target,
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "verdict": "INCONCLUSIVE", "score": 0, "signals": [],
            "profiles": {}, "comparisons": [], "profiles_available": 0,
            "profiles_failed": 0, "manual_review_required": True,
            "evidence_path": "", "playwright": {}, "error": str(exc),
        }


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def run_cdn_check(target: str, cfg: dict | None = None) -> dict:
    """Pipeline tối giản: CDN/registrar plus passive multi-profile cloaking check."""
    domain = normalize_domain(target)
    who = get_whois_info(domain)
    cf = is_cloudflare(who.get("name_servers")) if isinstance(who, dict) else False
    registrar = who.get("registrar") if isinstance(who, dict) else None
    try:
        cdn_detected = detect_cdn(domain)
    except Exception:
        cdn_detected = []
    # Tra TLD registry từ bảng tĩnh (không cần mạng, không fallback IANA)
    registry_contact = _static_registry_lookup(domain)
    registrar_uses_webform = bool(
        registrar and any(key in registrar.lower() for key in WEB_FORM_REGISTRARS)
    )
    rdap = {}
    if not registrar_uses_webform and (not registrar or not _registrar_abuse_emails(who.get("abuse_email"))):
        rdap = get_rdap_abuse_email(domain)
        registrar = registrar or rdap.get("registrar")
    report_recipients = resolve_quick_report_recipients(
        domain, who=who, registry_contact=registry_contact, rdap=rdap,
    )
    cloaking = run_cloaking_check(target, mode="full", cfg=cfg)
    return {
        "domain": domain,
        "cloudflare": cf,
        "cdn_detected": cdn_detected,
        "registrar": registrar,
        "registry_contact": registry_contact,
        "report_recipients": report_recipients,
        "cloaking": cloaking,
    }


def _static_registry_lookup(domain: str) -> dict | None:
    """Tra CCTLD_REGISTRY_CONTACTS từ bảng tĩnh, thử 2-level trước rồi 1-level.
    Trả về dict entry hoặc None nếu không tìm thấy."""
    d = domain.lower()
    parts = d.rsplit(".", 2)
    if len(parts) >= 3:
        two_level = f"{parts[-2]}.{parts[-1]}"
        if two_level in CCTLD_REGISTRY_CONTACTS:
            return CCTLD_REGISTRY_CONTACTS[two_level]
    tld = d.rsplit(".", 1)[-1]
    return CCTLD_REGISTRY_CONTACTS.get(tld)


_REPORT_BROWSER_MANAGER = None
_REPORT_BROWSER_MANAGER_LOCK = None


def run_check(target: str, submit: bool, cfg: dict, *, persist: bool=False, create_drafts: bool=False, capture_cloaking_evidence: bool=False) -> dict:
    """Run the web investigation pipeline with explicit configuration and no legacy file persistence."""
    if persist or create_drafts or capture_cloaking_evidence:
        raise ValueError('Use backend artifacts and explicit evidence workflows')
    import concurrent.futures as _cf
    domain = normalize_domain(target)
    target_url = target.strip() if '://' in target else f'http://{domain}'
    _cloaking_executor = _cf.ThreadPoolExecutor(max_workers=1)
    _cloaking_future = _cloaking_executor.submit(cloaking_detector.probe_http_cloaking, target_url, include_path_variant=True, evidence_root=None, vantage_points=cfg.get('cloaking_vantage_points') or [])
    try:
        cert = get_cert_info(domain)
    except Exception as e:
        cert = {'error': str(e)}
    who = get_whois_info(domain)
    cf = is_cloudflare(who.get('name_servers')) if isinstance(who, dict) else False
    domain_age_days = compute_domain_age_days(who)
    try:
        http_check = check_http(target_url)
    except Exception as e:
        http_check = {'error': str(e)}
    try:
        mx_records = check_mx_records(domain)
    except Exception as e:
        mx_records = {'records': [], 'providers': [], 'error': str(e)}
    try:
        cdn_detected = detect_cdn(domain)
    except Exception:
        cdn_detected = []
    try:
        origin_ip_scan = scan_common_subdomains(domain)
    except Exception:
        origin_ip_scan = {}
    vt = check_virustotal(domain, cfg['vt_api_key'])
    virustotal_submit = None
    if vt.get('note') == 'Domain chưa từng được VirusTotal quét trước đây' and submit:
        virustotal_submit = submit_virustotal(domain, cfg['vt_api_key'])
    gsb = check_safebrowsing(target_url, cfg['gsb_api_key'])
    reputation = compute_reputation(vt, gsb)
    ca_note = match_ca_notes(cert.get('issuer'))
    try:
        cloaking = _cloaking_future.result(timeout=45)
    except Exception as e:
        cloaking = {'version': 1, 'engine': 'http', 'target_url': target_url, 'observed_at': datetime.now(timezone.utc).isoformat(), 'verdict': 'INCONCLUSIVE', 'score': 0, 'signals': [], 'profiles': {}, 'comparisons': [], 'profiles_available': 0, 'profiles_failed': 0, 'manual_review_required': True, 'evidence_path': '', 'playwright': {}, 'error': str(e)}
    finally:
        _cloaking_executor.shutdown(wait=False)
    log_row = {'timestamp': datetime.now(timezone.utc).isoformat(), 'domain': domain, 'target_url': target_url, 'ip': cert.get('ip'), 'ssl_issuer': cert.get('issuer'), 'ssl_serial': cert.get('serial'), 'registrar': who.get('registrar') if isinstance(who, dict) else None, 'cloudflare': cf, 'vt_malicious': vt.get('malicious'), 'vt_link': vt.get('link'), 'gsb_flagged': gsb.get('flagged'), 'status': 'detected'}
    log_error = None
    registrar_abuse_email_source = None
    registrar_abuse_email_used = None
    try:
        _who_emails = who.get('emails') if isinstance(who, dict) else None
        if isinstance(_who_emails, list):
            _who_emails_filtered = [e for e in _who_emails if e and (not any((priv in e.lower() for priv in WHOIS_PRIVACY_DOMAINS)))]
            _who_emails = ', '.join(_who_emails_filtered) if _who_emails_filtered else None
        _registrar = who.get('registrar') if isinstance(who, dict) else None
        if _who_emails:
            registrar_abuse_email_source = 'whois'
            registrar_abuse_email_used = _who_emails
        else:
            rdap_r = get_rdap_abuse_email(domain)
            if rdap_r.get('abuse_email'):
                registrar_abuse_email_source = 'rdap'
                registrar_abuse_email_used = rdap_r['abuse_email']
            elif _registrar and lookup_registrar_abuse_email(_registrar):
                registrar_abuse_email_source = 'static_table'
                registrar_abuse_email_used = lookup_registrar_abuse_email(_registrar)
            else:
                registrar_abuse_email_source = None
                registrar_abuse_email_used = None
    except Exception:
        registrar_abuse_email_source = None
        registrar_abuse_email_used = None
    drafts = []
    drafts_error = None
    origin_ip_whois = {}
    try:
        registry_info = lookup_registry_contact(domain)
    except Exception as e:
        registry_info = {'source': 'not_found'}
        drafts_error = (drafts_error + '; ' if drafts_error else '') + f'Registry/VNCERT draft: {e}'
    drafts = []
    return {'domain': domain, 'cert': cert, 'whois': who, 'cloudflare': cf, 'cdn_detected': cdn_detected, 'origin_ip_scan': origin_ip_scan, 'origin_ip_whois': origin_ip_whois, 'registry_contact': registry_info, 'virustotal': vt, 'virustotal_submit': virustotal_submit, 'safebrowsing': gsb, 'reputation': reputation, 'ca_note': ca_note, 'http_check': http_check, 'domain_age_days': domain_age_days, 'mx_records': mx_records, 'log_row': log_row, 'log_error': log_error, 'drafts': drafts, 'drafts_error': drafts_error, 'registrar_abuse_email_source': registrar_abuse_email_source, 'registrar_abuse_email_used': registrar_abuse_email_used, 'cloaking': cloaking}
