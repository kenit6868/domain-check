"""Allowlisted IMAP reader using the canonical legacy parser and classifiers."""
import email
import imaplib
import re
import ssl
from email.utils import getaddresses, parseaddr
from time import monotonic
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import provider_replies as legacy

from domaincheck.core.mail.connectivity import AllowlistedMailConnectionTester


class ProviderMailboxAdapter:
    def __init__(self, endpoints):
        self.policy = AllowlistedMailConnectionTester(None, endpoints)

    def connect(self, spec):
        self.policy._check(spec.host, spec.port)
        server = imaplib.IMAP4_SSL(spec.host, spec.port, ssl_context=ssl.create_default_context(), timeout=60)
        try:
            server.login(spec.username, spec.password)
        except BaseException:
            try: server.logout()
            except Exception: pass
            raise
        return server

    @staticmethod
    def select_folder(server, folder, *, readonly):
        if not folder or len(folder) > 500 or any(ord(char) < 32 or ord(char) == 127 for char in folder):
            raise ValueError('invalid_mailbox_name')
        return server.select(legacy._imap_mailbox_name(folder), readonly=readonly)

    @staticmethod
    def validity(server):
        response = server.response('UIDVALIDITY')[1]
        value = (response[0].decode('ascii') if response and isinstance(response[0], bytes) else '')
        if not value.isdigit(): raise ValueError('mailbox_uidvalidity_missing')
        return value

    def sync(self, spec, config, date_from, date_to, timezone, limit=100, unread_only=False, progress=None):
        folder_total = 0
        def notify(folder='', processed=0, total=0, index=0, stage='reading'):
            if progress: progress({'stage': stage, 'folder': folder, 'processed': processed, 'total': total,
                'folder_index': index, 'folder_total': folder_total, 'folders': [dict(row) for row in statistics]})
        statistics = []
        notify(stage='connecting')
        server = self.connect(spec)
        messages, statistics, sent_headers = [], [], []
        deadline = monotonic() + 120
        try:
            status, lines = server.list()
            parsed = [legacy._parse_imap_list_line(line) for line in (lines or [])] if status == 'OK' else []
            names = [name for _, _, name in parsed if name]
            known = {name.casefold(): name for name in names}
            configured_junk = str(config.get('junk_mailbox') or '')
            junk = known.get(configured_junk.casefold()) or next((name for flags, _, name in parsed if '\\junk' in flags.lower()), '')
            if not junk: junk = next((name for name in names if name.lower() in {'junk', 'spam', 'junk email', 'inbox.junk', '[gmail]/spam'}), '')
            if not junk: junk = configured_junk
            configured_cf = str(config.get('cloudflare_mailbox') or legacy.CLOUDFLARE_MAILBOX)
            cf_matches = [name for name in names if re.split(r'[/.]', name)[-1].strip().lower() == 'cloudflare']
            cloudflare = known.get(configured_cf.casefold()) or (cf_matches[0] if len(cf_matches) == 1 else configured_cf)
            folders = [('Inbox', str(config.get('mailbox') or 'INBOX')), ('Junk/Spam', junk),
                ('Evidence', str(config.get('evidence_mailbox') or legacy.EVIDENCE_MAILBOX)), ('Cloudflare', cloudflare)]
            if config.get('reply_mailboxes') is not None:
                folders = [(name, known.get(name.casefold(), name)) for name in config['reply_mailboxes']]
            # Explicit lists replace legacy folders; folder names never classify providers.
            folders = list(dict((folder, (label, folder)) for label, folder in folders if folder).values())
            sent_folder = known.get(str(config.get('sent_mailbox') or '').casefold()) or next((name for flags, _, name in parsed if '\\sent' in flags.lower()), '')
            if not sent_folder: sent_folder = next((name for name in names if name.lower() in {'sent', 'sent items', 'sent mail', '[gmail]/sent mail'}), '')
            folder_total = len(folders) + bool(sent_folder)
            seen = set()
            for folder_index, (label, folder) in enumerate(folders, 1):
                if not folder or folder in seen: continue
                seen.add(folder)
                stat = {'folder': label, 'mailbox': folder, 'fetched': 0, 'matched': 0, 'skipped_large': 0, 'error_code': None}
                notify(folder, index=folder_index)
                if monotonic() >= deadline:
                    stat['error_code'] = 'sync_time_limit'; statistics.append(stat); continue
                try:
                    if self.select_folder(server, folder, readonly=True)[0] != 'OK':
                        raise ValueError('mailbox_unavailable')
                    validity = self.validity(server)
                    query = f'(SINCE {(date_from-timedelta(days=1)).strftime("%d-%b-%Y")} BEFORE {(date_to+timedelta(days=2)).strftime("%d-%b-%Y")}' + (' UNSEEN' if unread_only else '') + ')'
                    status, ids = server.uid('search', None, query)
                    if status != 'OK': raise ValueError('mailbox_search_failed')
                    available = ids[0].split() if ids and ids[0] else []
                    candidates = available[-limit:]
                    stat.update(total=len(candidates), processed=0, limited=len(available) > limit)
                    for uid in candidates:
                        notify(folder, stat['processed'], len(candidates), folder_index)
                        if monotonic() >= deadline:
                            stat['error_code'] = 'sync_time_limit'; break
                        stat['processed'] += 1
                        if not uid.isdigit(): continue
                        status, fetched = server.uid('fetch', uid, '(INTERNALDATE FLAGS BODY.PEEK[]<0.2097152>)')
                        part = next((row for row in fetched or [] if isinstance(row, tuple) and isinstance(row[1], bytes)), None)
                        if status != 'OK' or part is None: continue
                        if len(part[1]) >= 2097152:
                            stat['skipped_large'] += 1; continue
                        mail = legacy.parse_message(uid.decode(), spec.username, part[1])
                        mail.server_date = legacy._imap_internal_date(part[0]); mail.source_mailbox = folder
                        received = legacy.received_datetime(mail)
                        if not received or not date_from <= received.astimezone(ZoneInfo(timezone)).date() <= date_to: continue
                        stat['fetched'] += 1
                        if mail.provider == 'unknown' and mail.request_type == 'manual_review': continue
                        original = email.message_from_bytes(part[1])
                        references = re.findall(r'<[^<>\s]+>', str(original.get('References') or ''))[-15:]
                        in_reply_to = re.findall(r'<[^<>\s]+>', str(original.get('In-Reply-To') or ''))
                        messages.append({'mail': mail.to_dict(), 'uidvalidity': validity,
                            'references': references, 'in_reply_to': in_reply_to,
                            'seen': b'\\seen' in part[0].lower(), 'received_at': received.isoformat()})
                        stat['matched'] += 1
                except Exception:
                    stat['error_code'] = 'mailbox_read_failed'
                statistics.append(stat)
                notify(folder, stat.get('processed', 0), stat.get('total', 0), folder_index, 'folder_complete')
            sent_folder = known.get(str(config.get('sent_mailbox') or '').casefold()) or next((name for flags, _, name in parsed if '\\sent' in flags.lower()), '')
            if not sent_folder: sent_folder = next((name for name in names if name.lower() in {'sent', 'sent items', 'sent mail', '[gmail]/sent mail'}), '')
            if sent_folder:
                stat = {'folder': 'Sent (headers)', 'mailbox': sent_folder, 'fetched': 0, 'matched': 0, 'skipped_large': 0, 'error_code': None}
                notify(sent_folder, index=folder_total)
                try:
                    if monotonic() >= deadline: raise TimeoutError('sync_time_limit')
                    if self.select_folder(server, sent_folder, readonly=True)[0] != 'OK': raise ValueError('sent_unavailable')
                    status, ids = server.uid('search', None, f'(SINCE {(date_from-timedelta(days=31)).strftime("%d-%b-%Y")})')
                    if status != 'OK': raise ValueError('sent_search_failed')
                    available = ids[0].split() if ids and ids[0] else []
                    candidates = available[-500:]
                    stat.update(total=len(candidates), processed=0, limited=len(available) > 500)
                    for uid in candidates:
                        notify(sent_folder, stat['processed'], len(candidates), folder_total)
                        if monotonic() >= deadline: stat['error_code'] = 'sync_time_limit'; break
                        stat['processed'] += 1
                        if not uid.isdigit(): continue
                        status, fetched = server.uid('fetch', uid, '(BODY.PEEK[HEADER.FIELDS (FROM TO MESSAGE-ID IN-REPLY-TO REFERENCES)])')
                        part = next((row for row in fetched or [] if isinstance(row, tuple) and isinstance(row[1], bytes)), None)
                        if status != 'OK' or part is None or len(part[1]) > 65536: continue
                        header = email.message_from_bytes(part[1])
                        if parseaddr(header.get('From') or '')[1].casefold() != str(config.get('sender_email') or spec.username).casefold(): continue
                        if not re.fullmatch(r'<[^<>\s]+>', str(header.get('Message-ID') or '')): continue
                        refs = re.findall(r'<[^<>\s]+>', str(header.get('In-Reply-To') or '') + ' ' + str(header.get('References') or ''))
                        sent_headers.append({'message_id': str(header.get('Message-ID') or '')[:998], 'references': refs[-20:],
                            'recipients': [address.lower() for _, address in getaddresses([str(header.get('To') or '')])]})
                        stat['fetched'] += 1
                except Exception: stat['error_code'] = 'sent_headers_read_failed'
                statistics.append(stat)
                notify(sent_folder, stat.get('processed', 0), stat.get('total', 0), folder_total, 'folder_complete')
            notify(stage='saving')
            return {'messages': messages, 'folders': statistics, 'sent_headers': sent_headers}
        finally:
            try: server.logout()
            except Exception: pass

    def mark_seen(self, spec, location):
        server = self.connect(spec)
        try:
            if self.select_folder(server, location['mailbox'], readonly=False)[0] != 'OK':
                raise ValueError('mailbox_unavailable')
            if self.validity(server) != location['uidvalidity'] or not str(location['uid']).isdigit():
                raise ValueError('mailbox_changed_sync_again')
            if server.uid('store', location['uid'], '+FLAGS.SILENT', '(\\Seen)')[0] != 'OK':
                raise ValueError('mark_seen_failed')
        finally:
            try: server.logout()
            except Exception: pass
