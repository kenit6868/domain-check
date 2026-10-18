"""Layered, read-only domain/URL observations. Never infer takedown from HTTP errors."""
from __future__ import annotations

import contextlib
import io
import json
import queue
import socket
import subprocess
import sys
import threading
from datetime import UTC, datetime
from functools import lru_cache
from urllib.parse import urljoin, urlsplit

import dns.exception
import dns.resolver
import requests
import tldextract

from domaincheck.core.investigation import validate_target
from domaincheck.core.networking import EgressDenied, PublicEgressPolicy

_extract = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)
HOLD = {'clienthold', 'serverhold'}


def system_addresses(host):
    """Use the resolver used by HTTP sockets, bounded by a local deadline."""
    result = queue.Queue(maxsize=1)

    def lookup():
        try:
            result.put(socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM))
        except OSError as exc:
            result.put(exc)

    threading.Thread(target=lookup, daemon=True).start()
    try:
        value = result.get(timeout=3)
    except queue.Empty:
        raise dns.exception.Timeout from None
    if isinstance(value, OSError):
        raise value
    return value


def system_resolve(host, kind, **_kwargs):
    family = socket.AF_INET if kind == 'A' else socket.AF_INET6
    return sorted({item[4][0] for item in system_addresses(host) if item[0] == family})


def link_policy():
    # Keep public-address/redirect guards, using the same DNS source as requests.
    return PublicEgressPolicy(resolver=system_resolve)


def registered_domain(value):
    host = urlsplit(value if '://' in value else 'https://' + value).hostname or ''
    host = host.encode('idna').decode('ascii').lower().rstrip('.')
    return _extract(host).top_domain_under_public_suffix


def unknown(url, reason='probe_failed'):
    return {'url': url, 'domain': registered_domain(url), 'domain_status': 'UNKNOWN',
        'website_status': 'UNKNOWN', 'status': 'UNKNOWN', 'http_code': None,
        'final_url': '', 'redirects': [], 'provider': '', 'reason': reason,
        'registration': {'state': 'UNKNOWN', 'source': '', 'statuses': [], 'hold_flags': []},
        'dns': {'state': 'UNKNOWN', 'records': {}}, 'checked_at': datetime.now(UTC).isoformat(),
        'vantage': 'backend'}


@lru_cache(maxsize=1)
def bootstrap():
    try:
        with requests.get('https://data.iana.org/rdap/dns.json', timeout=6, stream=True) as response:
            response.raise_for_status()
            data = read_json(response)
        return {tld: urls[0] for tlds, urls in data['services'] for tld in tlds
                if urls and urls[0].startswith('https://')}
    except Exception:
        return {}


def read_json(response):
    body = bytearray()
    for part in response.iter_content(8192):
        body.extend(part)
        if len(body) > 1024 * 1024:
            raise ValueError('registration_response_too_large')
    return json.loads(body)


def registration(domain, endpoints):
    result = {'state': 'UNKNOWN', 'source': '', 'statuses': [], 'hold_flags': []}
    endpoint = endpoints.get(domain.rsplit('.', 1)[-1], '')
    if endpoint:
        try:
            # Trusted bootstrap, but validate every response redirect before following it.
            url = endpoint.rstrip('/') + '/domain/' + domain
            policy = link_policy()
            for _ in range(5):
                policy.validate_url(url)
                with requests.get(url, timeout=6, allow_redirects=False, stream=True,
                                  headers={'Accept': 'application/rdap+json'}) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        url = urljoin(url, response.headers.get('Location', ''))
                        if urlsplit(url).scheme != 'https': raise ValueError('insecure_rdap_redirect')
                        continue
                    if response.status_code == 404:
                        result.update(state='NOT_FOUND', source='RDAP')
                        break
                    response.raise_for_status()
                    data = read_json(response)
                    if data.get('objectClassName') != 'domain' or str(data.get('ldhName', '')).lower().rstrip('.') != domain:
                        raise ValueError('rdap_domain_mismatch')
                    statuses = [str(v)[:100] for v in data.get('status', [])][:30]
                    flags = sorted({v.replace(' ', '').lower() for v in statuses} & HOLD)
                    result.update(state='HOLD' if flags else 'REGISTERED', source='RDAP', statuses=statuses, hold_flags=flags)
                    break
        except Exception:
            pass
    # RDAP "inactive" alone does not establish a specific EPP hold.
    if result['state'] in {'UNKNOWN', 'REGISTERED'} and not result['hold_flags']:
        try:
            completed = subprocess.run([sys.executable, '-m', __name__, 'whois'], input=domain,
                capture_output=True, text=True, encoding='utf-8', timeout=9)
            data = json.loads(completed.stdout)
            statuses = data['statuses']
            flags = sorted({v.split()[0].lower() for v in statuses if v.split()} & HOLD)
            if flags or result['state'] == 'UNKNOWN':
                result.update(state='HOLD' if flags else ('REGISTERED' if statuses else 'UNKNOWN'),
                    source='WHOIS', statuses=statuses, hold_flags=flags)
        except Exception:
            pass
    return result


def dns_status(host, resolver=None):
    if resolver is None:
        try:
            values = system_addresses(host)
            records = {kind: sorted({item[4][0] for item in values if item[0] == family})[:20]
                       for kind, family in (('A', socket.AF_INET), ('AAAA', socket.AF_INET6))}
            if records['A'] or records['AAAA']:
                return {'state': 'RESOLVES', 'records': records, 'source': 'SYSTEM'}
        except (OSError, dns.exception.Timeout):
            pass
        # A system lookup failure alone cannot establish authoritative NXDOMAIN.
        # Retain explicit DNS observations when the system resolver cannot answer.
    resolver = resolver or dns.resolver.Resolver()
    records, errors, nxdomain = {}, [], False
    for kind in ('A', 'AAAA', 'CNAME', 'NS'):
        try:
            query = registered_domain(host) if kind == 'NS' else host
            records[kind] = [str(v)[:300] for v in resolver.resolve(query or host, kind, lifetime=2)][:20]
        except dns.resolver.NXDOMAIN:
            if kind == 'NS':
                errors.append('NS_NXDOMAIN')
                continue
            nxdomain = True
        except dns.resolver.NoAnswer:
            records[kind] = []
        except dns.exception.Timeout:
            errors.append('TIMEOUT')
        except dns.resolver.NoNameservers:
            errors.append('SERVFAIL_OR_REFUSED')
        except (dns.exception.DNSException, OSError):
            errors.append('DNS_ERROR')
    state = 'RESOLVES' if records.get('A') or records.get('AAAA') else 'NXDOMAIN' if nxdomain else (errors[0] if errors else 'NO_ADDRESS')
    return {'state': state, 'records': records}


def classify(code, headers, body):
    headers = {str(k).lower(): str(v) for k, v in headers.items()}
    cf = 'cf-ray' in headers or 'cloudflare' in headers.get('server', '').lower()
    provider = 'Cloudflare' if cf else ''
    text = body.lower()
    # Require the warning title AND warning wording; a keyword in site content is insufficient.
    phishing = 'suspected phishing' in text and 'reported for potential phishing' in text
    malware = 'suspected malware' in text and 'reported for potential malware' in text
    if cf and (phishing or malware):
        return ('CF_PHISHING_BLOCKED' if phishing else 'CF_MALWARE_BLOCKED'), provider, 'cloudflare_abuse_warning'
    if headers.get('cf-mitigated', '').lower() == 'challenge':
        return 'CF_CHALLENGE', 'Cloudflare', 'cloudflare_challenge_header'
    if (any(key in headers for key in ('x-geo-block', 'x-country-block', 'x-region-block'))
            and any(marker in text for marker in ('not available in your country', 'not available in your region', 'blocked in your country'))):
        return 'GEO_RESTRICTED', provider, 'observed_geo_restriction'
    if code in {401, 403, 407, 418, 423}:
        return 'ACCESS_BLOCKED', provider, 'http_access_denied'
    if code == 429: return 'RATE_LIMITED', provider, 'http_rate_limited'
    if code == 451: return 'LEGAL_RESTRICTION', provider, 'http_legal_restriction'
    if code in {404, 410}: return ('URL_NOT_FOUND' if code == 404 else 'URL_GONE'), provider, 'url_unavailable'
    if 500 <= code <= 599: return 'SERVER_ERROR', provider, 'cloudflare_or_origin_error' if cf else 'http_server_error'
    if 200 <= code < 300: return 'HTTP_OK', provider, 'http_response_ok'
    if 300 <= code < 400: return 'REDIRECT', provider, 'redirect_without_destination'
    return 'UNKNOWN', provider, 'unclassified_http_response'


def http_status(url, *, policy=None, get=None):
    policy = policy or link_policy()
    get = get or requests.get
    current, redirects = url, []
    for _ in range(8):
        try:
            policy.validate_url(current)
            with get(current, timeout=(4, 5), stream=True, allow_redirects=False,
                     headers={'User-Agent': 'Mozilla/5.0 DomainCheck/1.0', 'Accept': 'text/html'}, verify=True) as response:
                code = response.status_code
                if code in {301, 302, 303, 307, 308} and response.headers.get('Location'):
                    destination = urljoin(current, response.headers['Location'])
                    policy.validate_url(destination)
                    redirects.append({'code': code, 'url': current, 'destination': destination})
                    current = destination
                    continue
                body = bytearray()
                for chunk in response.iter_content(8192):
                    body.extend(chunk)
                    if len(body) >= 128 * 1024: break
                status, provider, reason = classify(code, response.headers, body.decode('utf-8', errors='replace'))
                return {'website_status': status, 'http_code': code, 'final_url': current,
                    'redirects': redirects, 'provider': provider, 'reason': reason}
        except EgressDenied:
            status, reason = 'UNKNOWN', 'egress_denied'
        except requests.exceptions.SSLError:
            status, reason = 'TLS_ERROR', 'tls_verification_failed'
        except requests.Timeout:
            status, reason = 'TIMEOUT', 'http_timeout'
        except requests.RequestException:
            status, reason = 'CONNECTION_ERROR', 'http_connection_failed'
        return {'website_status': status, 'http_code': None, 'final_url': current,
                'redirects': redirects, 'provider': '', 'reason': reason}
    return {'website_status': 'REDIRECT_ERROR', 'http_code': None, 'final_url': current,
            'redirects': redirects, 'provider': '', 'reason': 'redirect_limit'}


def inspect_link(value, endpoints=None):
    url, host = validate_target(value)
    result = unknown(url)
    result['registration'] = registration(result['domain'], endpoints if endpoints is not None else bootstrap())
    result['dns'] = dns_status(host)
    result['domain_status'] = ('DOMAIN_HOLD' if result['registration']['state'] == 'HOLD' else
        'REGISTRATION_NOT_FOUND' if result['registration']['state'] == 'NOT_FOUND' else
        'DNS_NXDOMAIN' if result['dns']['state'] == 'NXDOMAIN' else
        'DNS_OK' if result['dns']['state'] == 'RESOLVES' else 'DNS_ERROR')
    if result['dns']['state'] == 'RESOLVES':
        result.update(http_status(url))
    else:
        result['reason'] = 'dns_' + result['dns']['state'].lower()
    result['status'] = result['domain_status'] if result['domain_status'] != 'DNS_OK' else result['website_status']
    result['conflict'] = result['domain_status'] in {'DOMAIN_HOLD', 'REGISTRATION_NOT_FOUND'} and result['dns']['state'] == 'RESOLVES'
    return result


def check_bounded(url):
    try:
        completed = subprocess.run([sys.executable, '-m', __name__],
            input=json.dumps({'url': url, 'endpoints': bootstrap()}), capture_output=True,
            text=True, encoding='utf-8', timeout=45)
        if completed.returncode != 0: return unknown(url)
        return json.loads(completed.stdout)
    except subprocess.TimeoutExpired:
        return unknown(url, 'probe_deadline_exceeded')


def main():
    try:
        raw = sys.stdin.read(65536)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            if sys.argv[-1] == 'whois':
                import whois
                data = whois.whois(raw, timeout=5)
                values = data.get('status') or []
                result = {'statuses': [str(v)[:150] for v in ([values] if isinstance(values, str) else values)][:30]}
            else:
                data = json.loads(raw)
                result = inspect_link(data['url'], data['endpoints'])
        sys.stdout.write(json.dumps(result))
    except Exception:
        sys.exit(1)


if __name__ == '__main__': main()
