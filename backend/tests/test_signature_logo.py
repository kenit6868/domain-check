import os
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch

from domaincheck.core.email_content import InlineLogo, bundled_logo_bytes, set_email_content
from domaincheck.application.signature_logo import import_bundled_logo, load_profile_logo
from domaincheck.infrastructure.artifacts import FilesystemArtifactStore
from domaincheck.infrastructure.db.models import Artifact, User, UserSettings
from domaincheck.infrastructure.db.owned import OwnedRepository
from domaincheck.infrastructure.db.session import Database
from domaincheck.ports.artifacts import ArtifactIntegrityError


class SignatureContentTests(unittest.TestCase):
    def test_inline_mime_and_html_match_legacy_and_preserve_plain_body(self):
        import phishing_toolkit as toolkit
        data = bundled_logo_bytes()
        path = Path(__file__).parents[1] / 'src/domaincheck/assets/email/logo-win.jpg'
        body = 'Review <example> & evidence\n\nRegards,\nOperator\noperator@example.com\n'
        old, new = EmailMessage(), EmailMessage()
        with patch.object(toolkit, '_signature_logo_path', return_value=str(path)), \
             patch.object(toolkit, 'make_msgid', return_value='<logo-fixture>'):
            toolkit.set_report_email_content(old, body, {})
        set_email_content(new, body, InlineLogo(data, 'image/jpeg', 'logo-fixture'))
        self.assertEqual(new.get_body(preferencelist=('plain',)).get_content(), body)
        self.assertEqual(new.get_body(preferencelist=('html',)).get_content(), old.get_body(preferencelist=('html',)).get_content())
        image = [part for part in new.walk() if part.get_content_maintype() == 'image'][0]
        self.assertEqual(image['Content-ID'], '<logo-fixture>')
        self.assertEqual(image.get_content_disposition(), 'inline')
        self.assertEqual(image.get_payload(decode=True), data)
        html = new.get_body(preferencelist=('html',)).get_content()
        self.assertLess(html.index('cid:logo-fixture'), html.index('Regards,'))
        self.assertIn('&lt;example&gt; &amp;', html)
        self.assertNotIn('assets/', html)

    def test_default_logo_is_mandatory_and_logo_fallback_when_signature_marker_missing(self):
        message = EmailMessage()
        set_email_content(message, 'Body')
        self.assertTrue(message.is_multipart())
        self.assertEqual(len([part for part in message.walk() if part.get_content_maintype() == 'image']), 1)
        with_logo = EmailMessage()
        set_email_content(with_logo, 'Body', InlineLogo(bundled_logo_bytes(), 'image/jpeg', 'fallback'))
        self.assertIn('cid:fallback', with_logo.get_body(preferencelist=('html',)).get_content())


@unittest.skipUnless(os.environ.get('DOMAINCHECK_TEST_DATABASE_URL'), 'requires isolated PostgreSQL')
class SignatureLogoIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_import_is_owned_idempotent_integrity_checked_and_versions_profile(self):
        db = Database(os.environ['DOMAINCHECK_TEST_DATABASE_URL'])
        try:
            async with db.session_factory() as session, session.begin():
                a, b = User(display_name='Logo A', status='active'), User(display_name='Logo B', status='active')
                session.add_all([a, b]); await session.flush()
                aid, bid = a.id, b.id
            with tempfile.TemporaryDirectory() as directory:
                store = FilesystemArtifactStore(Path(directory))
                logo_id = await import_bundled_logo(db.session_factory, store, aid)
                self.assertEqual(await import_bundled_logo(db.session_factory, store, aid), logo_id)
                async with db.session_factory() as session:
                    profile = await session.get(UserSettings, aid)
                    version = profile.version
                    logo = await load_profile_logo(session, store, aid, profile)
                    self.assertEqual(logo.data, bundled_logo_bytes())
                    self.assertEqual(profile.logo_artifact_id, logo_id)
                    self.assertIsNone(await OwnedRepository(session, Artifact, bid).get(logo_id))
                    with self.assertRaises(ArtifactIntegrityError):
                        await load_profile_logo(session, store, bid, profile)
                    artifact = await OwnedRepository(session, Artifact, aid).get(logo_id)
                    (store.root / artifact.storage_key).write_bytes(b'tampered')
                    with self.assertRaises(ArtifactIntegrityError):
                        await load_profile_logo(session, store, aid, profile)
                self.assertEqual(version, 2)
        finally:
            await db.dispose()

    async def test_upload_replaces_only_owner_logo_and_reset_keeps_default_logo(self):
        import io
        from PIL import Image
        from domaincheck.application.signature_logo import save_profile_logo, reset_profile_logo
        from domaincheck.ports.artifacts import ImageUpload
        db = Database(os.environ['DOMAINCHECK_TEST_DATABASE_URL'])
        try:
            async with db.session_factory() as session, session.begin():
                user = User(display_name='Upload owner', status='active')
                session.add(user); await session.flush(); owner = user.id
            with tempfile.TemporaryDirectory() as directory:
                store = FilesystemArtifactStore(Path(directory))
                async with db.session_factory() as session:
                    self.assertEqual((await load_profile_logo(session, store, owner, None)).data, bundled_logo_bytes())
                blob = io.BytesIO(); Image.new('RGB', (64, 64), 'blue').save(blob, format='PNG')
                new_id = await save_profile_logo(db.session_factory, store, owner, ImageUpload('custom.png', 'image/png', blob.getvalue()))
                from domaincheck.ports.artifacts import ArtifactValidationError
                with self.assertRaises(ArtifactValidationError):
                    await save_profile_logo(db.session_factory, store, owner, ImageUpload('fake.png', 'image/png', b'not-an-image'))
                async with db.session_factory() as session:
                    profile = await session.get(UserSettings, owner)
                    self.assertEqual(profile.logo_artifact_id, new_id)
                    self.assertEqual((await load_profile_logo(session, store, owner, profile)).data, blob.getvalue())
                await reset_profile_logo(db.session_factory, owner)
                async with db.session_factory() as session:
                    profile = await session.get(UserSettings, owner)
                    self.assertIsNone(profile.logo_artifact_id)
                    self.assertEqual((await load_profile_logo(session, store, owner, profile)).data, bundled_logo_bytes())
                    self.assertIsNotNone(await OwnedRepository(session, Artifact, owner).get(new_id))
        finally:
            await db.dispose()


class LogoUploadBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_upload_rejects_wrong_mime_and_oversize_without_writing(self):
        import io
        from types import SimpleNamespace
        from fastapi import HTTPException, UploadFile
        from starlette.datastructures import Headers
        from domaincheck.api.mail_settings import upload_signature_logo
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=SimpleNamespace(
            artifact_max_file_bytes=2 * 1024 * 1024))))
        for mime, data in (('text/html', b'<html>'), ('image/png', b'x' * (2 * 1024 * 1024 + 1))):
            file = UploadFile(io.BytesIO(data), filename='untrusted', headers=Headers({'content-type': mime}))
            with self.assertRaises(HTTPException) as error:
                await upload_signature_logo(request, file, SimpleNamespace())
            self.assertEqual(error.exception.status_code, 422)
