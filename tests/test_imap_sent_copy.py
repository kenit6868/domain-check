import unittest
from unittest.mock import Mock, patch

import phishing_toolkit as toolkit


class SentCopyTests(unittest.TestCase):
    def account(self):
        return {'imap_host': 'imap.example.com', 'imap_port': 993, 'username': 'sender@example.com', 'password': 'fixture'}

    def mailbox(self):
        mail = Mock()
        mail.list.return_value = ('OK', [b'(\\HasNoChildren) "/" "Sent"', b'(\\Sent) "/" "Sent Items"'])
        mail.select.return_value = ('OK', [b'0'])
        mail.uid.return_value = ('OK', [b''])
        mail.append.return_value = ('OK', [b'APPEND completed'])
        return mail

    def test_special_use_quoted_folder_timeout_and_same_message_bytes(self):
        mail = self.mailbox()
        with patch.object(toolkit.imaplib, 'IMAP4_SSL', return_value=mail) as connect:
            result = toolkit._imap_save_sent(self.account(), b'exact MIME', message_id='<fixture@example.com>')
        self.assertIsNone(result)
        self.assertEqual(connect.call_args.kwargs['timeout'], 30)
        mail.select.assert_called_once_with('"Sent Items"', readonly=True)
        self.assertEqual(mail.append.call_args.args[0], '"Sent Items"')
        self.assertEqual(mail.append.call_args.args[-1], b'exact MIME')
        mail.logout.assert_called_once()

    def test_existing_message_id_does_not_append_duplicate(self):
        mail = self.mailbox(); mail.uid.return_value = ('OK', [b'42'])
        with patch.object(toolkit.imaplib, 'IMAP4_SSL', return_value=mail):
            self.assertIsNone(toolkit._imap_save_sent(self.account(), b'exact MIME', message_id='<fixture@example.com>'))
        mail.append.assert_not_called(); mail.logout.assert_called_once()

    def test_rejected_append_and_lookup_are_not_reported_as_saved(self):
        mail = self.mailbox(); mail.append.return_value = ('NO', [b'rejected'])
        with patch.object(toolkit.imaplib, 'IMAP4_SSL', return_value=mail):
            self.assertEqual(toolkit._imap_save_sent(self.account(), b'exact MIME'), 'IMAP Sent APPEND rejected')
        mail = self.mailbox(); mail.uid.return_value = ('NO', [])
        with patch.object(toolkit.imaplib, 'IMAP4_SSL', return_value=mail):
            self.assertEqual(toolkit._imap_save_sent(self.account(), b'exact MIME', message_id='<fixture@example.com>'), 'IMAP Sent lookup rejected')
        mail.append.assert_not_called()

    def test_gmail_does_not_append_a_second_sent_copy(self):
        with patch.object(toolkit.imaplib, 'IMAP4_SSL') as connect:
            self.assertIsNone(toolkit._imap_save_sent({**self.account(), 'imap_host': 'imap.gmail.com'}, b'copy'))
        connect.assert_not_called()
