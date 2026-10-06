"""Parse pasted infrastructure leads and reuse Domain Worker prechecks."""

from __future__ import annotations

import ipaddress
import csv
import hashlib
import json
from email.utils import getaddresses
import os
import re
import socket
import tempfile
import time
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.parse import urlsplit

import dns.resolver

import browser_evidence
import cloaking_review_queue as review_queue
import cloaking_review_sender as review_sender
import domain_worker
import phishing_toolkit as pt
from domain_utils import extract_domains_from_text


MODULE_VERSION = 24
REVIEW_CACHE_PATH = os.path.join(pt.DATA_DIR, "lead_triage_cache.json")


def load_review_cache(path: str | None = None) -> dict:
    """Load the local review snapshot without invoking any domain/network check."""
    try:
        with open(path or REVIEW_CACHE_PATH, encoding="utf-8") as handle:
            payload = json.load(handle)
        if payload.get("version") == 1 and isinstance(payload.get("state"), dict):
            return payload["state"]
    except (OSError, UnicodeError, ValueError, TypeError, AttributeError):
        pass
    return {}


def save_review_cache(state: dict, path: str | None = None) -> bool:
    """Atomically keep one operator review; callers pass only whitelisted fields."""
    target = path or REVIEW_CACHE_PATH
    temporary = ""
    try:
        directory = os.path.dirname(os.path.abspath(target))
        os.makedirs(directory, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=directory,
                                         prefix=".lead-triage-", suffix=".tmp", delete=False) as handle:
            temporary = handle.name
            json.dump({"version": 1, "state": state}, handle, ensure_ascii=False, default=str)
        os.replace(temporary, target)
        return True
    except (OSError, TypeError, ValueError):
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass
        return False


def clear_review_cache(path: str | None = None) -> bool:
    try:
        os.unlink(path or REVIEW_CACHE_PATH)
    except FileNotFoundError:
        pass
    except OSError:
        return False
    return True


def refresh_worker_recipients(result: dict) -> dict:
    """Re-run the same recipient resolver used by Domain Worker for this domain."""
    try:
        result["recipients"] = domain_worker._precheck_report_recipients(result["domain"])
        result.pop("recipients_error", None)
        result["recipients_checked_at"] = datetime.now(timezone.utc).isoformat()
    except Exception as exc:
        result["recipients"] = []
        result["recipients_error"] = type(exc).__name__
    return result


def worker_report_recipient(result: dict) -> tuple[str, str]:
    """Choose an address for the selected Worker draft's report channel."""
    draft = result.get("worker_draft") or {}
    channel = pt.report_channel_from_draft(draft.get("filename") or "")

    def valid_addresses(value):
        for _name, address in getaddresses([str(value or "")]):
            address = address.strip()
            if re.fullmatch(r"[^\s@,]+@[^\s@,]+\.[^\s@,]+", address) and not pt.is_blocked_report_recipient(address):
                yield address

    for entry in result.get("recipients") or []:
        if entry.get("channel") == channel:
            for address in valid_addresses(entry.get("email")):
                return address, f"Domain Worker precheck — {channel}"
    for address in valid_addresses(draft.get("to")):
        return address, f"report Domain Worker — {channel}"
    return "", f"Domain Worker — chưa tìm thấy email {channel}"


def sent_lead_targets() -> set[str]:
    """Restore successful Lead Triage targets from delivery metadata."""
    try:
        with open(pt.SENT_LOG_PATH, newline="", encoding="utf-8-sig") as handle:
            return {
                row["target_url"] for row in csv.DictReader(handle)
                if row.get("send_mode") == "lead_triage"
                and row.get("status") == "sent"
                and row.get("target_url")
            }
    except (OSError, UnicodeError, csv.Error):
        return set()


def validated_dom_attachments(evidence: dict | None, target: str) -> list[str]:
    """Accept only a complete DOM source/destination capture for this URL."""
    if not isinstance(evidence, dict) or evidence.get("capture_strategy") != "dom_destination":
        return []
    if not browser_evidence.evidence_url_matches(target, evidence.get("requested_url", "")):
        return []
    paths = browser_evidence.evidence_attachment_paths(evidence)
    if len(paths) != 3:
        return []
    validation = browser_evidence.validate_evidence_artifacts(evidence)
    if not validation["valid"] or validation["manifest"].get("evidence_type") != "dom_destination_opened":
        return []
    return paths


def _without_excluded_brand(text: str) -> str:
    """Keep Worker phishing claims while omitting unsupported brand attribution."""
    excluded = re.compile(r"(?<![\w./-])au88(?:8)?(?![\w-]|\.[a-z0-9])", re.IGNORECASE)
    lines = []
    for line in text.splitlines():
        if not excluded.search(line):
            lines.append(line)
        elif re.search(r"\bassociated with\s+au88(?:8)?\b", line, re.IGNORECASE):
            continue
        elif re.search(r"\b(Subject: )?Abuse Report:\s*au88(?:8)?\s*$", line, re.IGNORECASE):
            continue
        else:
            lines.append(excluded.sub("our brand", line))
    cleaned = "\n".join(lines).strip()
    cleaned = re.sub(r"\bthe my brand\b", "our brand", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bmy brand\b", "our brand", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"(?m)^the our brand\b", "Our brand", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bthe our brand\b", "our brand", cleaned, flags=re.IGNORECASE)
    return re.sub(r"\bour brand branding\b", "our branding", cleaned, flags=re.IGNORECASE)


def _add_report_heading_after_greeting(body: str, heading: str) -> str:
    """Add a readable report section without changing the Worker narrative."""
    greeting = re.search(r"(?im)^(Dear\s+[^\n]+,|Hello\s+[^\n]+,)[ \t]*\n", body)
    if not greeting or heading.casefold() in body.casefold():
        return body
    return body[:greeting.end()] + f"\n{heading}\n\n" + body[greeting.end():].lstrip("\n")


def _linked_backend_results(result: dict, backends: list[dict], evidence: dict | None) -> list[dict]:
    """Only include leads with a concrete DNS or observed URL link to this report."""
    dns = result.get("dns") or {}
    target_ips = set() if dns.get("error") or result.get("dns_error") else set(dns.get("ips") or [])
    observed_hosts = set()
    if evidence:
        validation = browser_evidence.validate_evidence_artifacts(evidence)
        if validation.get("valid"):
            manifest = validation["manifest"]
            navigation = manifest.get("navigation") or {}
            control = manifest.get("control") or {}
            urls = [manifest.get("requested_url"), manifest.get("landing_url"),
                    manifest.get("final_url"), control.get("resolved_destination")]
            urls.extend(navigation.get(key) for key in ("source_url", "requested_destination", "destination_url"))
            for hop in navigation.get("redirect_chain") or []:
                if isinstance(hop, dict):
                    urls.extend((hop.get("url"), hop.get("location")))
            for url in urls:
                try:
                    host = urlsplit(str(url or "")).hostname
                except ValueError:
                    host = None
                if host:
                    observed_hosts.add(host.lower().rstrip("."))
    linked = []
    for backend in backends:
        domain = str(backend.get("domain") or "").lower().rstrip(".")
        backend_dns = backend.get("dns") or {}
        backend_ips = set() if backend_dns.get("error") else set(backend_dns.get("ips") or [])
        name_link = bool(domain and any(
            host == domain or host.endswith("." + domain) for host in observed_hosts
        ))
        if name_link or target_ips.intersection(backend_ips):
            linked.append(backend)
    return linked


_DOMAIN = re.compile(
    r"^(?:https?://)?(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z]{2,63}(?:[/:?#][^\s]*)?$",
    re.IGNORECASE,
)
_IP = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")


def parse_lead(raw: str) -> list[dict]:
    """Only accept domain/URL rows; metadata and contact emails are not targets."""
    rows: list[dict] = []
    seen: set[str] = set()
    backend_section = False
    for line in (raw or "").splitlines():
        value = line.strip()
        if value.startswith("==="):
            backend_section = "backend" in value.lower()
            continue
        value = re.sub(r"^(?:[-*]\s*|\d+[.)]\s*)", "", value).strip(" `")
        if not value:
            continue
        targets = extract_domains_from_text(value)
        if not targets or not value.lower().startswith(targets[0].lower()):
            continue
        target = targets[0]
        if not _DOMAIN.fullmatch(target):
            continue
        domain = pt.normalize_domain(target).lower().rstrip(".")
        registrar_match = re.search(r"\breg\s*=\s*(.*?)(?:\s+(?:tao|hold)\s*=|$)", value, re.I)
        registrar = registrar_match.group(1).strip() if registrar_match else ""
        if registrar in {"", "?", "-"}:
            registrar = ""
        key = target.lower()
        if key in seen:
            continue
        seen.add(key)
        ip_text = value.split("A:", 1)[1] if "A:" in value else ""
        supplied_ips = []
        for candidate in _IP.findall(ip_text):
            try:
                address = str(ipaddress.IPv4Address(candidate))
            except ipaddress.AddressValueError:
                continue
            if address not in supplied_ips:
                supplied_ips.append(address)
        rows.append({
            "target": target,
            "domain": domain,
            "registrar": registrar,
            "backend_lead": backend_section,
            "supplied_ips": supplied_ips,
            "full_url": target.lower().startswith(("http://", "https://")),
        })
    return rows


def parse_host_claims(raw: str) -> dict[str, dict]:
    """Keep host RDAP lines supplied by the operator as unverified context."""
    hosts: dict[str, dict] = {}
    current_ip = ""
    for line in (raw or "").splitlines():
        heading = re.match(r"^===\s*host\s+((?:\d{1,3}\.){3}\d{1,3})\b", line.strip(), re.I)
        if heading:
            try:
                current_ip = str(ipaddress.IPv4Address(heading.group(1)))
                hosts.setdefault(current_ip, {})
            except ipaddress.AddressValueError:
                current_ip = ""
            continue
        if line.strip().startswith("==="):
            current_ip = ""
            continue
        if current_ip and ":" in line:
            key, value = line.split(":", 1)
            if key.strip().lower() in {"netname", "orgname", "country", "orgabuseemail"}:
                hosts[current_ip][key.strip().lower()] = value.strip()
    return hosts


def _resolve_a(domain: str) -> dict:
    try:
        answer = dns.resolver.resolve(domain, "A", lifetime=6.0)
        return {"ips": sorted({str(record) for record in answer}), "error": ""}
    except Exception as primary_error:
        # System DNS can still resolve when dnspython's configured resolver times out.
        pool = ThreadPoolExecutor(max_workers=1)
        try:
            future = pool.submit(socket.getaddrinfo, domain, None, socket.AF_INET, socket.SOCK_STREAM)
            answers = future.result(timeout=4.0)
            ips = sorted({entry[4][0] for entry in answers})
            return {"ips": ips, "error": "" if ips else type(primary_error).__name__}
        except Exception as fallback_error:
            return {"ips": [], "error": f"{type(primary_error).__name__}/{type(fallback_error).__name__}"}
        finally:
            pool.shutdown(wait=False, cancel_futures=True)


def check_lead(item: dict, cfg: dict) -> dict:
    """Run independent Domain Worker prechecks; defer the full report pipeline."""
    target = item["target"]
    domain = item["domain"]
    check_url = target if item["full_url"] else f"https://{domain}/"
    result = {**item, "check_url": check_url, "checked_at": datetime.now(timezone.utc).isoformat()}
    expected = set(item.get("supplied_ips") or [])
    with ThreadPoolExecutor(max_workers=min(8, 4 + len(expected))) as pool:
        futures = {
            "dns": pool.submit(_resolve_a, domain),
            "http": pool.submit(pt.check_http, check_url),
            "recipients": pool.submit(domain_worker._precheck_report_recipients, domain),
            "cloaking": pool.submit(domain_worker._precheck_cloaking, check_url, cfg),
        }
        ip_futures = {ip: pool.submit(pt.get_ip_whois, ip) for ip in sorted(expected)}
        for key, future in futures.items():
            try:
                result[key] = future.result()
            except Exception as exc:
                result[key] = {} if key != "recipients" else []
                result[f"{key}_error"] = type(exc).__name__
        result["ip_contacts"] = []
        for ip, future in ip_futures.items():
            try:
                info = future.result()
            except Exception as exc:
                info = {"error": type(exc).__name__}
            result["ip_contacts"].append({"ip": ip, **info})
    observed = set(result.get("dns", {}).get("ips") or [])
    result["ip_match"] = (
        "DNS lỗi/chưa xác định" if result.get("dns_error") or result.get("dns", {}).get("error") else
        "không có IP trong ghi chú" if not expected else
        "khớp" if expected == observed else
        "thay đổi/không khớp"
    )
    return result


def check_leads(items: list[dict], cfg: dict, on_progress=None) -> list[dict]:
    """Check selected targets concurrently while retaining the pasted order."""
    if not items:
        return []
    results = [None] * len(items)
    with ThreadPoolExecutor(max_workers=min(6, len(items))) as pool:
        pending = {pool.submit(check_lead, item, cfg): index for index, item in enumerate(items)}
        for completed, future in enumerate(as_completed(pending), start=1):
            index = pending[future]
            results[index] = future.result()
            if on_progress is not None:
                on_progress(completed, len(items))
    return results


def check_backend_context(items: list[dict]) -> list[dict]:
    """Verify pasted backend DNS/IP leads without treating them as report targets."""
    def check_one(item):
        ips = list(item.get("supplied_ips") or [])
        with ThreadPoolExecutor(max_workers=min(8, 1 + len(ips))) as pool:
            dns_future = pool.submit(_resolve_a, item["domain"])
            contacts = {ip: pool.submit(pt.get_ip_whois, ip) for ip in ips}
            try:
                dns = dns_future.result()
            except Exception as exc:
                dns = {"ips": [], "error": type(exc).__name__}
            ip_contacts = []
            for ip, future in contacts.items():
                try:
                    info = future.result()
                except Exception as exc:
                    info = {"error": type(exc).__name__}
                ip_contacts.append({"ip": ip, **info})
        return {**item, "dns": dns, "ip_contacts": ip_contacts}

    if not items:
        return []
    with ThreadPoolExecutor(max_workers=min(4, len(items))) as pool:
        return list(pool.map(check_one, items))


def prepare_worker_report(result: dict, cfg: dict) -> dict:
    """Snapshot the same report pipeline/hosting formatter used by Domain Worker."""
    notes = []
    try:
        checked = pt.run_check(result["check_url"], False, cfg)
        paths = list(checked.get("drafts") or [])
        if checked.get("drafts_error"):
            notes.append(browser_evidence.redact_text(checked["drafts_error"]))
        result["worker_cloaking"] = checked.get("cloaking") or {}
    except Exception as exc:
        paths = []
        notes.append(browser_evidence.redact_text(exc))
    # Worker creates this report only for discovered candidate origins. This
    # menu also accepts an operator-supplied IP and uses the very same formatter.
    if result.get("supplied_ips") and not any(str(path).endswith("_hosting_report.txt") for path in paths):
        try:
            ip = result["supplied_ips"][0]
            contact = next((entry for entry in result.get("ip_contacts") or [] if entry.get("ip") == ip), {})
            path = pt.generate_hosting_draft(result["domain"], ip, contact, cfg)
            if path:
                paths.extend(pt.append_reported_url_to_drafts([path], result["check_url"]))
        except Exception as exc:
            notes.append(browser_evidence.redact_text(exc))
    snapshots = []
    for path in paths:
        try:
            parsed = pt.parse_draft_email(path)
            if parsed.get("subject") and parsed.get("body"):
                snapshots.append({**parsed, "filename": os.path.basename(path)})
        except Exception as exc:
            notes.append(browser_evidence.redact_text(exc))
    snapshots.sort(key=lambda draft: (
        0 if draft["filename"].endswith("_hosting_report.txt") else
        1 if draft["filename"].endswith("_registrar_report.txt") else 2
    ))
    result["worker_draft"] = snapshots[0] if snapshots else {}
    result["worker_drafts"] = snapshots
    result["worker_report_error"] = "; ".join(notes)
    return result


def _report_review_vi(body: str, domain: str) -> str:
    """Translate known Worker email templates for review; preserve evidence data."""
    rules = [
        (r"^Dear (.+) Abuse Department,$", r"Kính gửi bộ phận xử lý lạm dụng của \1,"),
        (r"^We are contacting the registry because the reported URL appears to present an ongoing abuse risk\. Please independently review the evidence and coordinate with the sponsoring registrar where appropriate\.$", "Chúng tôi liên hệ registry vì URL bị báo cáo có dấu hiệu gây rủi ro lạm dụng đang diễn ra. Vui lòng đánh giá độc lập bằng chứng và phối hợp với nhà đăng ký quản lý domain khi phù hợp."),
        (r"^This matter was previously reported to the sponsoring registrar(?: on ([0-9-]+))?\. The reported URL remains available, so we are requesting registry-level review\.$", lambda match: "Vụ việc đã được báo cho nhà đăng ký quản lý domain" + (f" vào ngày {match.group(1)}" if match.group(1) else "") + ". URL bị báo cáo vẫn truy cập được, vì vậy chúng tôi đề nghị registry xem xét."),
        (r"^Please investigate and take proportionate registry-level action under your abuse policy if the reported violation is confirmed\.$", "Vui lòng điều tra và áp dụng biện pháp phù hợp ở cấp registry theo chính sách xử lý lạm dụng nếu xác nhận vi phạm."),
        (r"^Please review the reported URL and coordinate prompt mitigation with the sponsoring registrar where appropriate\.$", "Vui lòng xem xét URL bị báo cáo và phối hợp xử lý kịp thời với nhà đăng ký quản lý domain khi phù hợp."),
        (r"^We request an urgent abuse investigation and any action available to the registry after independent verification\.$", "Chúng tôi đề nghị điều tra khẩn cấp hành vi lạm dụng và áp dụng biện pháp thuộc thẩm quyền registry sau khi xác minh độc lập."),
        (r"^Please preserve relevant records, investigate the reported content, and apply the measures provided by your abuse policy if confirmed\.$", "Vui lòng bảo toàn hồ sơ liên quan, điều tra nội dung bị báo cáo và áp dụng biện pháp theo chính sách xử lý lạm dụng nếu xác nhận vi phạm."),
        (r"^We are requesting a registry-level review of suspected phishing.*$", "Chúng tôi đề nghị registry điều tra URL bị báo cáo do nghi phishing và giả mạo thương hiệu của chúng tôi."),
        (r"^We are reporting suspected phishing content hosted under .*$", "Chúng tôi báo cáo nội dung nghi phishing dưới domain này có dấu hiệu giả mạo thương hiệu của chúng tôi."),
        (r"^This report concerns suspected phishing and brand impersonation.*$", "Báo cáo này liên quan nội dung nghi phishing và giả mạo thương hiệu tại URL đã báo cáo."),
        (r"^We request your assistance in reviewing a suspected abuse case.*$", "Chúng tôi đề nghị điều tra hành vi nghi lạm dụng và sử dụng trái phép thương hiệu của chúng tôi."),
        (r"^We are reporting suspected phishing and unauthorized brand impersonation.*$", "Chúng tôi báo cáo hành vi nghi phishing và giả mạo thương hiệu trái phép, đồng thời đề nghị điều tra khẩn cấp."),
        (r"^We are reporting suspected phishing and impersonation of our brand.*$", "Chúng tôi báo cáo nội dung nghi phishing và giả mạo thương hiệu của chúng tôi."),
        (r"^The site may trick visitors into sharing credentials.*$", "Website có thể đánh lừa người truy cập cung cấp thông tin đăng nhập."),
        (r"^We identified content at .*that appears to impersonate our brand.*$", "Chúng tôi phát hiện nội dung có dấu hiệu giả mạo thương hiệu và gây nhầm lẫn cho người truy cập."),
        (r"^This is a formal abuse report concerning suspected phishing content.*$", "Đây là báo cáo chính thức về nội dung nghi phishing và sử dụng thương hiệu trái phép."),
        (r"^Our review found content at .*consistent with suspected phishing.*$", "Kết quả rà soát cho thấy nội dung có dấu hiệu phishing và giả mạo thương hiệu trái phép."),
        (r"^The reported page appears to reproduce our brand identity.*$", "Trang được báo cáo dường như sử dụng lại nhận diện thương hiệu của chúng tôi, có thể khiến người truy cập tưởng là dịch vụ chính thức và cung cấp thông tin nhạy cảm."),
        (r"^The observed content appears to present itself as an official service.*$", "Nội dung quan sát được dường như tự nhận là dịch vụ chính thức liên quan thương hiệu của chúng tôi. Chúng tôi chưa cho phép cách sử dụng này và đề nghị điều tra theo chính sách chống phishing, lạm dụng."),
        (r"^The reported URL uses our brand without authorization.*$", "URL bị báo cáo sử dụng thương hiệu của chúng tôi khi chưa được cho phép và hiển thị nội dung có dấu hiệu lừa đảo, có thể gây rủi ro cho thông tin của người truy cập."),
        (r"^The page appears designed to mislead visitors.*$", "Trang có dấu hiệu gây nhầm lẫn bằng cách giả mạo thương hiệu trái phép. Vui lòng xác minh độc lập nội dung và bằng chứng kèm theo."),
        (r"^We request the immediate suspension of this domain.*$", "Chúng tôi đề nghị đình chỉ ngay tên miền này theo chính sách sử dụng chấp nhận được và quy trình xử lý lạm dụng của nhà đăng ký."),
        (r"^We respectfully request that you immediately suspend this domain.*$", "Chúng tôi đề nghị quý vị đình chỉ ngay tên miền này và áp dụng biện pháp phù hợp thuộc thẩm quyền nhà đăng ký theo chính sách xử lý lạm dụng."),
        (r"^Please take immediate action to suspend this domain.*$", "Vui lòng nhanh chóng đình chỉ tên miền và ngăn việc tiếp tục sử dụng cho hành vi phishing, giả mạo thương hiệu theo chính sách xử lý lạm dụng."),
        (r"^We urge you to suspend this domain without delay.*$", "Chúng tôi đề nghị đình chỉ tên miền không chậm trễ và áp dụng biện pháp phù hợp để ngăn tổn hại thêm cho người dùng."),
        (r"^Please confirm receipt of this report.*$", "Vui lòng xác nhận đã nhận báo cáo và cho biết biện pháp đã thực hiện khi có thể."),
        (r"^We appreciate your prompt attention to this matter.*$", "Cảm ơn quý vị xử lý kịp thời và vui lòng xác nhận biện pháp đã thực hiện."),
        (r"^Thank you for your attention to this urgent matter.*$", "Cảm ơn quý vị quan tâm tới vụ việc khẩn cấp này. Vui lòng xác nhận đã nhận báo cáo và cho biết biện pháp xử lý."),
        (r"^We appreciate your prompt assistance in addressing this matter.*$", "Cảm ơn quý vị hỗ trợ xử lý nhanh chóng; mong nhận được phản hồi xác nhận."),
        (r"^This domain was first identified by our security team on ([0-9-]+).*$", r"Bộ phận an ninh của chúng tôi phát hiện tên miền này lần đầu vào ngày \1 khi giám sát thương hiệu."),
        (r"^Our security team detected this phishing site on ([0-9-]+).*$", r"Bộ phận an ninh phát hiện website nghi phishing này vào ngày \1 qua hoạt động giám sát thương hiệu."),
        (r"^We identified this threat on ([0-9-]+).*$", r"Chúng tôi phát hiện mối nguy này vào ngày \1 trong quá trình giám sát thương hiệu."),
        (r"^This domain came to our attention on ([0-9-]+).*$", r"Chúng tôi biết đến tên miền này vào ngày \1 khi rà soát hành vi giả mạo thương hiệu."),
        (r"^As of ([0-9-]+), (\d+) security engines on VirusTotal.*$", r"Tính đến \1, có \2 công cụ bảo mật trên VirusTotal đánh dấu tên miền này là độc hại."),
        (r"^VirusTotal shows (\d+) malicious detections.*as of ([0-9-]+)\.$", r"VirusTotal ghi nhận \1 kết quả phát hiện độc hại đối với tên miền này tính đến \2."),
        (r"^VirusTotal analysis dated ([0-9-]+) records (\d+) malicious detections.*$", r"Phân tích VirusTotal ngày \1 ghi nhận \2 kết quả phát hiện độc hại đối với tên miền này."),
        (r"^As supporting context, VirusTotal reports (\d+) malicious detections on ([0-9-]+)\.$", r"Theo VirusTotal, ngày \2 có \1 kết quả phát hiện độc hại."),
        (r"^As of ([0-9-]+), (\d+) security vendors on VirusTotal.*$", r"Tính đến \1, có \2 nhà cung cấp bảo mật trên VirusTotal đánh dấu tên miền này là đáng ngờ."),
        (r"^VirusTotal analysis dated ([0-9-]+) shows (\d+) security vendors.*$", r"Phân tích VirusTotal ngày \1 cho thấy \2 nhà cung cấp bảo mật đánh dấu tên miền này là đáng ngờ."),
        (r"^Dear (.+) Abuse Team,$", r"Kính gửi bộ phận xử lý lạm dụng của \1,"),
        (r"^We are writing to request the immediate suspension.*$", "Chúng tôi đề nghị đình chỉ ngay website phishing trên hạ tầng của quý vị đang giả mạo thương hiệu của chúng tôi."),
        (r"^We have identified an active phishing site hosted on your network \(IP: (.+)\).*$", r"Chúng tôi phát hiện website phishing trên mạng của quý vị (IP: \1), giả mạo thương hiệu và thu thập thông tin đăng nhập."),
        (r"^This is a formal takedown request.*$", "Đây là yêu cầu gỡ bỏ chính thức đối với website phishing trên hạ tầng của quý vị đang giả mạo thương hiệu của chúng tôi."),
        (r"^We are reporting an active phishing operation.*$", "Chúng tôi báo cáo hoạt động phishing trên mạng của quý vị, sao chép website thương hiệu để đánh cắp thông tin đăng nhập."),
        (r"^The website at (.+) \(hosted on IP (.+)\) is an unauthorized clone.*$", r"Website \1 (IP \2) là bản sao trái phép của nền tảng thương hiệu, thu thập thông tin đăng nhập, mã OTP và thông tin thanh toán."),
        (r"^This phishing site at (.+) \(IP: (.+)\) replicates.*$", r"Website phishing \1 (IP \2) sao chép giao diện đăng nhập để lừa người dùng cung cấp thông tin đăng nhập, mã OTP và thông tin tài chính."),
        (r"^The domain (.+) \(hosted at IP (.+)\) hosts a fraudulent replica.*$", r"Domain \1 (IP \2) chứa bản sao giả mạo cổng xác thực, nhằm đánh cắp thông tin đăng nhập và dữ liệu cá nhân nhạy cảm."),
        (r"^Hosted on IP (.+), the site (.+) is a phishing page.*$", r"Website \2 trên IP \1 là trang phishing sao chép cổng thương hiệu để thu thập thông tin đăng nhập, mã OTP và thông tin thanh toán."),
        (r"^We request you suspend this hosting account immediately.*$", "Chúng tôi đề nghị đình chỉ ngay tài khoản hosting theo chính sách sử dụng chấp nhận được (AUP) để ngăn tổn hại cho người dùng và thương hiệu."),
        (r"^Please immediately suspend or null-route.*$", "Vui lòng đình chỉ ngay hoặc chặn lưu lượng tới IP/tài khoản này theo AUP để ngăn việc đánh cắp thông tin đăng nhập."),
        (r"^We urge you to terminate this hosting account.*$", "Chúng tôi đề nghị chấm dứt tài khoản hosting theo AUP để ngăn hoạt động phishing đang diễn ra."),
        (r"^Immediate suspension of this account/IP.*$", "Đề nghị đình chỉ ngay tài khoản/IP theo AUP để bảo vệ người dùng khỏi hoạt động thu thập thông tin đăng nhập."),
    ]
    text = body
    for pattern, translated in rules:
        text = re.sub(pattern, translated, text, flags=re.M)
    for english, vietnamese in {
        "BRAND IMPERSONATION AND FRAUD CONCERNS": "NGHI VẤN GIẢ MẠO THƯƠNG HIỆU VÀ LỪA ĐẢO",
        "Hosting details:": "Thông tin hosting:",
        "- Phishing domain:": "- Domain phishing:",
        "- Origin IP:": "- IP hosting:",
        "- Hosting IP:": "- IP hosting:",
        "- Hosting organization:": "- Tổ chức hosting:",
        "- First detected:": "- Ngày phát hiện:",
        "First detected:": "Ngày phát hiện:",
        "Domain:": "Tên miền:",
        "Registrar:": "Nhà đăng ký:",
        "Evidence:": "Bằng chứng:",
        "- Registrar:": "- Nhà đăng ký:",
        "- SSL Issuer:": "- Đơn vị cấp SSL:",
        "- SSL Serial:": "- Số sê-ri SSL:",
        " security engines flagged as malicious": " công cụ bảo mật đánh dấu là độc hại",
        " security engines flagged as suspicious": " công cụ bảo mật đánh dấu là đáng ngờ",
        "Reported URL:": "URL đã báo cáo:",
        "Regards,": "Trân trọng,",
    }.items():
        text = text.replace(english, vietnamese)
    return text


def build_investigation_draft(
    result: dict, raw_note: str, *, additional_english: str = "",
    evidence: dict | None = None, backend_results: list[dict] | None = None,
) -> dict:
    """Keep the Worker subject/body and append backend and shared Browser Evidence."""
    original = result.get("worker_draft") or {}
    if not original.get("subject") or not original.get("body"):
        raise ValueError("Chưa có report Domain Worker; bấm Check lại để tạo nội dung report.")
    extra = _without_excluded_brand(additional_english)
    if extra and any(ord(char) > 127 for char in extra):
        raise ValueError("Mô tả đưa vào report phải viết bằng tiếng Anh không dấu.")
    backend_results = list(backend_results or [])
    linked_backend_ids = {
        id(backend) for backend in _linked_backend_results(result, backend_results, evidence)
    }
    contacts = result.get("ip_contacts") or []
    recipient, source = worker_report_recipient(result)
    en = [] if evidence else ["Additional verification details:"]
    vi = [] if evidence else ["Dữ kiện bổ sung để xác minh:"]
    checked_at = str(result.get("checked_at") or "").strip()
    if checked_at and not evidence:
        en.append(f"- Domain check time (UTC): {checked_at}")
        vi.append(f"- Thời điểm kiểm tra domain (UTC): {checked_at}")
    dns = result.get("dns") or {}
    resolved = [] if dns.get("error") or result.get("dns_error") else list(dns.get("ips") or [])
    shared_ip = any(
        not (backend.get("dns") or {}).get("error")
        and set(resolved).intersection((backend.get("dns") or {}).get("ips") or [])
        for backend in backend_results
    )
    show_target_network = not evidence or shared_ip
    if resolved and show_target_network:
        en.append("- Observed DNS A for the reported domain: " + ", ".join(resolved))
        vi.append("- Bản ghi DNS A quan sát được của domain báo cáo: " + ", ".join(resolved))
    for entry in contacts:
        if show_target_network and entry.get("org") and entry.get("ip") in resolved:
            en.append(f"- Network operator for {entry['ip']}: {entry['org']}")
            vi.append(f"- Đơn vị quản lý mạng của {entry['ip']}: {entry['org']}")
    if backend_results:
        en.append("RELATED BACKEND FINDINGS")
        vi.append("THÔNG TIN BACKEND LIÊN QUAN")
        en.append("The following infrastructure leads were supplied for independent provider review:")
        vi.append("Các đầu mối hạ tầng sau được cung cấp để nhà cung cấp kiểm tra độc lập:")
        for backend in backend_results:
            backend_dns = backend.get("dns") or {}
            supplied_ips = backend.get("supplied_ips") or []
            current_ips = [] if backend_dns.get("error") else backend_dns.get("ips") or []
            overlap = "DNS/URL overlap observed; service relationship unconfirmed" if id(backend) in linked_backend_ids else "no DNS/URL overlap observed"
            overlap_vi = "có giao điểm DNS/URL quan sát được; chưa xác nhận quan hệ dịch vụ" if id(backend) in linked_backend_ids else "chưa thấy giao điểm DNS/URL"
            en.append(f"- {backend['domain']} ({overlap}): IPs supplied: {', '.join(supplied_ips) or 'none'}; current DNS A: {', '.join(current_ips) or 'unavailable'}.")
            vi.append(f"- {backend['domain']} ({overlap_vi}): IP được cung cấp: {', '.join(supplied_ips) or 'không có'}; DNS A hiện tại: {', '.join(current_ips) or 'chưa xác định'}.")
            for contact in backend.get("ip_contacts") or []:
                if contact.get("ip") in supplied_ips and contact.get("org"):
                    if contact["ip"] in current_ips:
                        en.append(f"  - Network operator for {contact['ip']}: {str(contact['org']).rstrip('.')}")
                        vi.append(f"  - Đơn vị quản lý mạng của {contact['ip']}: {str(contact['org']).rstrip('.')}")
                    else:
                        en.append(f"  - RDAP registration for supplied IP {contact['ip']} (not in current DNS A): {str(contact['org']).rstrip('.')}")
                        vi.append(f"  - RDAP của IP được cung cấp {contact['ip']} (không có trong DNS A hiện tại): {str(contact['org']).rstrip('.')}")
        en.append("These findings are investigative context only; they do not establish that the listed infrastructure serves the reported domain or final destination.")
        vi.append("Đây chỉ là thông tin điều tra; các dữ kiện này chưa chứng minh hạ tầng nêu trên phục vụ domain báo cáo hoặc trang đích cuối.")
    if extra:
        en.append("Supporting observations:\n" + extra)
        vi.append("Quan sát hỗ trợ (nguyên văn tiếng Anh):\n" + extra)
    action = "" if evidence else (
        "Please investigate the reported URL, preserve relevant access and account records, "
        "and, if abuse is confirmed, disable the responsible service under your Acceptable Use Policy. "
        "Please confirm receipt and advise what action was taken."
    )
    action_vi = "" if evidence else "Vui lòng điều tra URL bị báo cáo, bảo toàn hồ sơ truy cập và tài khoản liên quan; nếu xác nhận vi phạm, đình chỉ dịch vụ chịu trách nhiệm theo chính sách sử dụng chấp nhận được. Vui lòng xác nhận đã nhận báo cáo và cho biết biện pháp xử lý."
    if backend_results and not evidence:
        action += " Please verify whether the listed infrastructure leads serve or support the reported URL before associating them with this case."
        action_vi += " Vui lòng xác minh các đầu mối hạ tầng nêu trên có phục vụ URL bị báo cáo hay không trước khi liên kết chúng với vụ việc này."
    # This provenance note belongs to Worker discovery, not an operator-IP report.
    body = re.sub(
        r"Note: This IP was identified as a candidate origin server via subdomain\s+enumeration — please verify independently before taking action\.\s*",
        "", _without_excluded_brand(str(original["body"])),
    ).replace("- Origin IP:", "- Hosting IP:")
    body = _add_report_heading_after_greeting(body, "BRAND IMPERSONATION AND FRAUD CONCERNS")
    registrar = str(result.get("registrar") or "").strip()
    if registrar and not re.search(r"(?im)^Registrar:\s*", body):
        registrar_line = f"Registrar: {registrar}"
        reported_url = re.search(r"(?im)^Reported URL:[^\n]*(?:\n|$)", body)
        if reported_url:
            body = body[:reported_url.end()] + registrar_line + "\n" + body[reported_url.end():]
        else:
            body = pt._insert_before_email_signature(body, registrar_line)
    body_vi = _report_review_vi(body, result["domain"])
    backend_block = ("\n".join(en) if en and (evidence or len(en) > 1) else "") + ("\n\n" + action.strip() if action.strip() else "")
    vi_block = ("\n".join(vi) if vi and (evidence or len(vi) > 1) else "") + ("\n\n" + action_vi.strip() if action_vi.strip() else "")
    request_block = ""
    vi_request = ""
    if evidence:
        paths = browser_evidence.evidence_attachment_paths(evidence)
        validation = browser_evidence.validate_evidence_artifacts(evidence)
        if not paths or not browser_evidence.evidence_url_matches(result["check_url"], (validation.get("manifest") or {}).get("requested_url", "")):
            raise ValueError("Browser Evidence không hợp lệ hoặc không khớp URL report.")
        block = browser_evidence.format_email_evidence_block(evidence)
        if not block:
            raise ValueError("Không tạo được nội dung Browser Evidence.")
        manifest = validation["manifest"]
        control = manifest.get("control") or {}
        source_url = (manifest.get("navigation") or {}).get("source_url") or manifest.get("landing_url") or manifest.get("requested_url") or result["check_url"]
        destination = control.get("resolved_destination") or ""
        final_url = manifest.get("final_url") or destination
        if manifest.get("evidence_type") == "dom_destination_opened":
            # The Worker draft already requests abuse review; the shared evidence
            # formatter's second generic request adds no new fact here.
            block = block.replace(
                "Please investigate the reported URL, the disclosed destination, and their "
                "relationship, and take appropriate action under your phishing and abuse policies.\n",
                "",
            )
            block = block.replace(
                "--- Observed Phishing Behavior and Supporting Evidence ---",
                "OBSERVED REDIRECT CHAIN AND SUPPORTING EVIDENCE",
                1,
            )
            request_lines = [
                "EVIDENCE AND REQUEST:",
                "- Assess whether the destination's branding and registration or sign-in prompts mislead visitors about affiliation or request sensitive information.",
                "- Determine whether the registration flow requests account credentials, one-time passwords (OTPs), or other sensitive authentication information.",
            ]
            if backend_results:
                request_lines.append("- Verify whether the listed infrastructure serves the reported URL or its observed destination.")
            request_lines.append("- Please acknowledge receipt and provide a case reference.")
            request_block = "\n".join(request_lines)
            vi_request_lines = [
                "BẰNG CHỨNG VÀ YÊU CẦU:",
                "- Xác minh trang đích có dùng nhận diện thương hiệu gây hiểu sai về mối liên hệ hoặc yêu cầu thông tin nhạy cảm không.",
                "- Ki?m tra lu?ng ??ng k? c? y?u c?u th?ng tin ??ng nh?p, m? m?t l?n (OTP) ho?c th?ng tin x?c th?c nh?y c?m kh?c kh?ng.",
            ]
            if backend_results:
                vi_request_lines.append("- Xác minh hạ tầng nêu trên có phục vụ URL báo cáo hoặc URL đích quan sát được hay không.")
            vi_request_lines.append("- Vui lòng xác nhận đã nhận báo cáo và cung cấp mã vụ việc.")
            vi_request = "\n".join(vi_request_lines)
        body = pt._insert_before_email_signature(body, block)
        if manifest.get("evidence_type") == "dom_destination_opened":
            vi_evidence = (
                f"Bằng chứng DOM đính kèm: {len(paths) - 1} ảnh nguồn–đích và manifest/hash.\n"
                f"Từ {source_url}, nút/link “{control.get('label') or 'không phát hiện'}” dẫn tới {destination or 'không có'}; "
                f"URL cuối quan sát được là {final_url or 'không có'}.\n"
                "Điểm cần xác minh: trang đích có dùng nhận diện thương hiệu hoặc lời mời đăng ký/đăng nhập "
                "gây hiểu sai về mối liên hệ hay yêu cầu thông tin nhạy cảm không."
            )
        else:
            vi_evidence = f"Bằng chứng hỗ trợ đính kèm: {len(paths) - 1} ảnh và manifest/hash.\nURL được báo cáo: {source_url}."
        body_vi = body_vi.replace("Trân trọng,", vi_evidence + "\n\nTrân trọng,") if "Trân trọng," in body_vi else body_vi + "\n\n" + vi_evidence
    if backend_block.strip():
        body = pt._insert_before_email_signature(body, backend_block.strip())
    if vi_block.strip():
        body_vi = body_vi.replace("Trân trọng,", vi_block.strip() + "\n\nTrân trọng,") if "Trân trọng," in body_vi else body_vi + "\n\n" + vi_block.strip()
    if request_block:
        body = pt._insert_before_email_signature(body, request_block)
    if vi_request:
        body_vi = body_vi.replace("Trân trọng,", vi_request + "\n\nTrân trọng,") if "Trân trọng," in body_vi else body_vi + "\n\n" + vi_request
    return {
        "to": recipient, "recipient_source": source,
        "subject": _without_excluded_brand(str(original["subject"])) or f"Abuse Report: {result['domain']}", "body": body,
        "subject_vi": f"Báo cáo nghi phishing: {result['domain']}",
        "body_vi": body_vi, "base_body": original["body"],
        "draft_file": original.get("filename") or "",
    }


def review_cloaking_result(result: dict) -> dict:
    """Choose the reviewed precheck evidence without running another detector."""
    candidates = [value for value in (result.get("worker_cloaking"), result.get("cloaking"))
                  if isinstance(value, dict) and domain_worker._cloaking_requires_review(value)]
    for value in candidates:
        if review_sender.confirmed_evidence_status(value)["ready"]:
            return review_sender.normalize_cloaking_result(value)
    return review_sender.normalize_cloaking_result(candidates[0] if candidates else {})


def _review_cfg(cfg: dict) -> dict:
    """Use the same account/company signature fallback for preview and SMTP."""
    fields = ("contact_name", "company_name", "signature_role",
              "business_registration_no", "signature_logo")
    return {**cfg, "smtp_accounts": [
        {**account, **{field: cfg.get(field) for field in fields if not account.get(field)}}
        for account in cfg.get("smtp_accounts") or []
    ]}


def review_context_fingerprint(result: dict, raw_note: str, backend_results: list,
                               additional_english: str, account_name: str, cfg: dict) -> str:
    account = next((value for value in _review_cfg(cfg)["smtp_accounts"]
                    if str(value.get("username") or "").lower() == account_name.lower()), {})
    signature = {field: account.get(field) for field in (
        "username", "contact_name", "company_name", "signature_role",
        "business_registration_no", "signature_logo")}
    payload = [result, raw_note, backend_results, additional_english, signature]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str,
                                     ensure_ascii=False).encode()).hexdigest()


def ensure_lead_review_case(result: dict, cfg: dict, account_names: list[str]) -> dict:
    """Reuse today's URL queue/ledger; create a local case without a worker job."""
    cloaking = review_cloaking_result(result)
    if not cloaking:
        raise ValueError("Domain này không thuộc nhóm cần duyệt cloaking.")
    if result.get("recipients_error"):
        raise ValueError("Cần kiểm tra lại email nhận trước khi tạo draft.")
    recipients = []
    entries = list(result.get("recipients") or [])
    fallback, _source = worker_report_recipient(result)
    if fallback:
        entries.append({"email": fallback})
    for entry in entries:
        for _name, address in getaddresses([str(entry.get("email") or "")]):
            if re.fullmatch(r"[^\s@,]+@[^\s@,]+\.[^\s@,]+", address) and not pt.is_blocked_report_recipient(address):
                recipients.append({"channel": entry.get("channel"), "email": address})
    if not recipients:
        raise ValueError("Domain Worker chưa tìm thấy email nhận hợp lệ.")
    target = result["check_url"]
    if cloaking.get("target_url") and not browser_evidence.evidence_url_matches(target, cloaking["target_url"]):
        raise ValueError("Evidence cloaking không khớp URL đang duyệt.")
    day = review_queue.current_review_day()
    if result.get("checked_at"):
        checked = datetime.fromisoformat(str(result["checked_at"]).replace("Z", "+00:00"))
        if checked.astimezone().date().isoformat() != day:
            raise ValueError("Kết quả thuộc ngày trước; Check lại URL trước khi tạo draft.")
    queue_id = review_queue.queue_id_for(day, review_queue.canonical_target_url(target))
    existing = review_queue.load_item(queue_id)
    if existing:
        # Existing approved/operator evidence and delivery decisions are authoritative.
        return existing
    now = datetime.now(timezone.utc).isoformat()
    cloaking.setdefault("target_url", target)
    return review_queue.enqueue_worker_result(
        job={"job_id": "lead_triage_" + queue_id, "created_at": now,
             "allowed_accounts": account_names},
        job_dir=pt.DATA_DIR,
        prepared={"target_url": target, "domain": result["domain"], "recipients": recipients},
        domain_result={"target_url": target, "domain": result["domain"],
                       "cloaking_result": cloaking, "cloaking_verdict": cloaking.get("verdict"),
                       "cloaking_score": cloaking.get("score", 0),
                       "skipped": "manual_review_required", "send_mode": "lead_triage"},
    )


def prepare_lead_review(result: dict, raw_note: str, *, decision: str, account_name: str,
                        cfg: dict, evidence: dict | None = None,
                        backend_results: list[dict] | None = None,
                        additional_english: str = "") -> dict:
    """Prepare only; use Cloaking Review's evidence rules and exact delivery ledger."""
    if decision not in review_sender.VALID_DECISIONS:
        raise ValueError("Chế độ tạo draft không hợp lệ.")
    if not account_name:
        raise ValueError("Chọn tài khoản gửi trước khi tạo draft.")
    item = ensure_lead_review_case(result, cfg, [account_name])
    normal_evidence = None
    if decision == review_sender.NOT_CLOAKING:
        if not validated_dom_attachments(evidence, result["check_url"]):
            import provider_replies
            evidence = provider_replies.capture_dom_link_evidence(result["check_url"], result["domain"])
        if not validated_dom_attachments(evidence, result["check_url"]):
            raise ValueError("Chụp DOM chưa thành công; chưa tạo draft gửi thường. "
                             + browser_evidence.redact_text((evidence or {}).get("error") or ""))
        normal_evidence = evidence
    prepared = review_sender.prepare_review_delivery(
        item["queue_id"], decision=decision, account_names=[account_name],
        cfg=_review_cfg(cfg), normal_browser_evidence=normal_evidence,
    )
    # Reuse the Lead Triage formatter for backend context; the reviewed evidence
    # from the shared sender stays authoritative and is appended only once.
    for delivery in prepared["deliveries"]:
        body = delivery["body"]
        if normal_evidence:
            body = body.replace(browser_evidence.format_email_evidence_block(normal_evidence), "")
        draft = build_investigation_draft(
            {**result, "worker_draft": {"subject": delivery["subject"], "body": body,
                                       "filename": delivery["draft"], "to": delivery["to"]}},
            raw_note, evidence=normal_evidence, backend_results=backend_results,
            additional_english=additional_english,
        )
        delivery["subject"], delivery["body"] = draft["subject"], draft["body"]
        delivery["body_vi"] = draft["body_vi"]
        if decision == review_sender.CONFIRMED_CLOAKING:
            delivery["body_vi"] = re.sub(
                r"--- Technical Evidence: Multi-profile Cloaking Check ---.*?--- End of Cloaking Evidence ---",
                "BẰNG CHỨNG CLOAKING ĐÃ DUYỆT\nNội dung khác nhau giữa các cấu hình thiết bị, "
                "user-agent hoặc nguồn truy cập. Hai ảnh đại diện và manifest đính kèm ghi nhận "
                "sự khác biệt; đề nghị NCC tái hiện theo các cấu hình trong manifest.",
                delivery["body_vi"], flags=re.S,
            )
    prepared["lead_context"] = review_context_fingerprint(
        result, raw_note, backend_results or [], additional_english, account_name, cfg)
    return prepared


def send_lead_review(preparation: dict, *, result: dict, raw_note: str, cfg: dict,
                     account_name: str, backend_results: list[dict], additional_english: str,
                     evidence: dict | None = None) -> dict:
    """Revalidate the displayed context, then delegate exact SMTP/partial retry."""
    if preparation.get("lead_context") != review_context_fingerprint(
        result, raw_note, backend_results, additional_english, account_name, cfg):
        raise ValueError("Nội dung hoặc tài khoản đã thay đổi; hãy tạo draft lại.")
    if not browser_evidence.evidence_url_matches(result["check_url"], preparation.get("target_url", "")):
        raise ValueError("Draft không thuộc URL đang chọn.")
    if preparation.get("decision") == review_sender.NOT_CLOAKING and not validated_dom_attachments(
        evidence or preparation.get("normal_browser_evidence"), result["check_url"]):
        raise ValueError("Cần hai ảnh DOM hợp lệ trước khi gửi report thường.")
    return review_sender.send_prepared_review(preparation, _review_cfg(cfg))


@contextmanager
def _send_lock(fingerprint: str):
    """Serialize one exact delivery across Streamlit sessions/processes."""
    lock_path = f"{pt.SENT_LOG_PATH}.lead-{fingerprint}.lock"
    deadline = time.monotonic() + 15
    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            break
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(lock_path) > 120:
                    os.unlink(lock_path)
                    continue
            except FileNotFoundError:
                continue
            if time.monotonic() >= deadline:
                raise TimeoutError("Delivery is already being sent; check status before retrying")
            time.sleep(0.1)
    try:
        yield
    finally:
        try:
            os.unlink(lock_path)
        except FileNotFoundError:
            pass


def delivery_fingerprint(
    target: str, recipient: str, subject: str, body: str,
    account: str, attachments: list[str],
) -> str:
    """Bind the approved preview to the exact body and evidence file bytes."""
    parts = [target, account.lower(), recipient.lower(), subject, body]
    for path in attachments:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        parts.extend((os.path.basename(path), digest.hexdigest()))
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()


def send_investigation_draft(
    *, target: str, recipient: str, subject: str, body: str,
    account: dict, proxy: str | None = None,
    attachments: list[str] | None = None, evidence: dict | None = None,
    expected_fingerprint: str = "",
    draft_file: str = "lead_triage_hosting_report.txt",
) -> dict:
    """Send the approved Worker report and exact evidence once per delivery."""
    sender = str(account.get("username") or "").strip()
    recipient = recipient.strip()
    if not re.fullmatch(r"[^\s@,]+@[^\s@,]+\.[^\s@,]+", recipient):
        return {"success": False, "error": "Email nhận không hợp lệ"}
    if not sender or not subject.strip() or not body.strip():
        return {"success": False, "error": "Thiếu tài khoản gửi hoặc nội dung draft"}
    attachments = list(attachments or [])
    if attachments != validated_dom_attachments(evidence, target):
        return {"success": False, "error": "Cần chụp thành công hai ảnh nguồn và đích từ DOM trước khi gửi"}
    errors = pt.validate_report_delivery(
        {"to": recipient, "subject": subject, "body": body},
        target_url=target, attachments=attachments, require_browser_evidence=True,
    )
    if errors:
        return {"success": False, "error": "; ".join(errors)}
    try:
        fingerprint = delivery_fingerprint(target, recipient, subject, body, sender, attachments)
        if not expected_fingerprint or expected_fingerprint != fingerprint:
            return {"success": False, "error": "Draft hoặc ảnh bằng chứng đã thay đổi; hãy duyệt lại trước khi gửi"}
        with _send_lock(fingerprint):
            if attachments != validated_dom_attachments(evidence, target):
                return {"success": False, "error": "Ảnh DOM hoặc manifest đã thay đổi trước khi gửi"}
            if delivery_fingerprint(target, recipient, subject, body, sender, attachments) != fingerprint:
                return {"success": False, "error": "Ảnh bằng chứng đã thay đổi trước khi gửi"}
            with pt.sent_log_lock():
                if os.path.exists(pt.SENT_LOG_PATH):
                    with open(pt.SENT_LOG_PATH, newline="", encoding="utf-8-sig") as handle:
                        if any(row.get("draft_fingerprint") == fingerprint and row.get("status") == "sent"
                               for row in csv.DictReader(handle)):
                            return {"success": True, "already_sent": True, "account": sender}
            result = pt.send_report_email_single(
                recipient, subject, body, account, proxy_str=proxy, attachments=attachments,
            )
            try:
                pt.log_sent({
                    "timestamp": result.get("sent_at") or datetime.now(timezone.utc).isoformat(),
                    "domain": pt.normalize_domain(target), "target_url": target,
                    "to": recipient, "account": sender,
                    "report_channel": pt.report_channel_from_draft(draft_file, recipient),
                    "draft_file": draft_file,
                    "subject": subject, "draft_fingerprint": fingerprint,
                    "status": "sent" if result.get("success") else "failed",
                    "success": bool(result.get("success")),
                    "delivery_kind": "report", "send_mode": "lead_triage",
                    "message_id": result.get("message_id") or "",
                    **pt.evidence_log_metadata(attachments),
                    "error": str(result.get("error") or "")[:300],
                })
            except (OSError, TimeoutError) as exc:
                result["log_error"] = type(exc).__name__
            return result
    except (OSError, TimeoutError) as exc:
        return {"success": False, "error": str(exc)}
