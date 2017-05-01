from datetime import datetime, timezone

from django.contrib.auth.models import User
from django.test import TestCase

from maker.models import Repository, RemoteRepository, App, RemoteApp


class AppTestCase(TestCase):

    def setUp(self):
        # local objects
        self.user = User.objects.create(username='user2')
        self.repo = Repository.objects.create(user=self.user)
        self.app = App.objects.create(repo=self.repo)

        # remote objects
        date = datetime.fromtimestamp(0, timezone.utc)
        self.remote_repo = RemoteRepository.objects.create(last_change_date=date)
        self.remote_app = RemoteApp.objects.create(repo=self.remote_repo, last_updated_date=date)

    def test_copy_translations_from_remote_app(self):
        app = self.app
        remote_app = self.remote_app

        # add two translations to RemoteApp
        remote_app.translate('en')
        remote_app.l_summary = 'dog'
        remote_app.l_description = 'cat'
        remote_app.save()
        remote_app.translate('de')
        remote_app.l_summary = 'hund'
        remote_app.l_description = 'katze'
        remote_app.save()

        # copy the translations to the App
        app.copy_translations_from_remote_app(remote_app)

        # assert that English translation was copied
        app = RemoteApp.objects.language('en').get(pk=self.app.pk)
        self.assertEqual('dog', app.l_summary)
        self.assertEqual('cat', app.l_description)

        # assert that German translation was copied
        app = RemoteApp.objects.language('de').get(pk=self.app.pk)
        self.assertEqual('hund', app.l_summary)
        self.assertEqual('katze', app.l_description)

    def test_get_translations_dict(self):
        # load two translations from other test
        self.test_copy_translations_from_remote_app()
        self.assertEqual({'en', 'de'}, set(self.app.get_available_languages()))

        # get localized dict
        localized = {'en': {'otherKey': 'test'}}
        self.app._add_translations_to_localized(localized)  # pylint: disable=protected-access

        # assert that dict was created properly
        self.assertEqual({'en', 'de'}, set(localized.keys()))
        self.assertEqual('dog', localized['en']['summary'])
        self.assertEqual('cat', localized['en']['description'])
        self.assertEqual('hund', localized['de']['summary'])
        self.assertEqual('katze', localized['de']['description'])

        # assert that existing content is not deleted
        self.assertEqual('test', localized['en']['otherKey'])


class RemoteAppTestCase(TestCase):

    def setUp(self):
        date = datetime.fromtimestamp(0, timezone.utc)
        self.repo = RemoteRepository.objects.create(name="Test", last_change_date=date)
        self.app = RemoteApp.objects.create(repo=self.repo, package_id="org.example",
                                            last_updated_date=date)

    def test_update_translations_new(self):
        # update remote app translation with a new one
        localized = {'en': {'summary': 'foo', 'description': 'bar', 'video': 'bla'}}
        self.app._update_translations(localized)  # pylint: disable=protected-access

        # assert that translation has been saved
        app = RemoteApp.objects.language('en').get(pk=self.app.pk)
        self.assertEqual(localized['en']['summary'], app.l_summary)
        self.assertEqual(localized['en']['description'], app.l_description)

    def test_update_translations_existing(self):
        # add a new translation
        self.test_update_translations_new()
        self.assertTrue(RemoteApp.objects.language('en').exists())

        # update existing translation
        localized = {'en': {'summary': 'newfoo', 'description': 'newbar', 'video': 'bla'}}
        self.app._update_translations(localized)  # pylint: disable=protected-access

        # assert that translation has been updated
        app = RemoteApp.objects.language('en').get(pk=self.app.pk)
        self.assertEqual(localized['en']['summary'], app.l_summary)
        self.assertEqual(localized['en']['description'], app.l_description)

    def test_apply_translation(self):
        # apply new translation
        translation = {'summary': 'test1', 'description': 'test2'}
        self.app.translate('en')
        self.app.apply_translation(translation)

        # assert that translation has been saved
        app = RemoteApp.objects.language('en').get(pk=self.app.pk)
        self.assertEqual(translation['summary'], app.l_summary)
        self.assertEqual(translation['description'], app.l_description)