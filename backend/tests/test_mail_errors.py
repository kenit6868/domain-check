import unittest

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from domaincheck.api.mail_settings import _raise
from domaincheck.application.mail_settings import MailSettingsConflict


class MailErrorTests(unittest.TestCase):
    def test_missing_key_is_service_configuration_error_not_duplicate(self):
        with self.assertRaises(HTTPException) as result:
            _raise(MailSettingsConflict('Mail secret encryption is not configured'))
        self.assertEqual(result.exception.status_code, 503)
        self.assertIn('DOMAINCHECK_SECRET_MASTER_KEY', result.exception.detail)

    def test_duplicate_has_actionable_message_without_sql_or_credentials(self):
        original = Exception('private-database-error')
        original.__cause__ = type('ConstraintError', (Exception,), {
            'constraint_name': 'uq_sender_accounts_owner_user_id_normalized_identity'})()
        error = IntegrityError('private-sql', {'password': 'private-fixture-secret'}, original)
        with self.assertRaises(HTTPException) as result:
            _raise(error)
        self.assertEqual(result.exception.status_code, 409)
        self.assertIn('SMTP host', result.exception.detail)
        self.assertNotIn('private', result.exception.detail)

    def test_unknown_conflict_never_exposes_internal_message(self):
        with self.assertRaises(HTTPException) as result:
            _raise(MailSettingsConflict('private-secret-or-account'))
        self.assertNotIn('private', result.exception.detail)
        self.assertEqual(result.exception.status_code, 409)

    def test_connection_preconditions_keep_specific_messages(self):
        for message in ('SMTP password is not configured', 'Mail account is disabled',
                        'IMAP host, username and password are required'):
            with self.assertRaises(HTTPException) as result:
                _raise(MailSettingsConflict(message))
            self.assertEqual(result.exception.status_code, 409)
            self.assertNotIn('secret policy', result.exception.detail)
