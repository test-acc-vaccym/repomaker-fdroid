import logging
import os
from io import BytesIO

import qrcode
from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ObjectDoesNotExist
from django.core.files.base import ContentFile
from django.db import models
from django.forms import ModelForm
from django.utils import timezone
from fdroidserver import common
from fdroidserver import server
from fdroidserver import update

from maker.storage import REPO_DIR
from maker.storage import get_media_file_path, get_repo_path
from maker.tasks import update_repo

keydname = "CN=localhost.localdomain, OU=F-Droid"


class Repository(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    name = models.CharField(max_length=255)
    description = models.TextField()
    url = models.URLField(max_length=2048)
    icon = models.ImageField(upload_to=get_media_file_path, default=settings.REPO_DEFAULT_ICON)
    public_key = models.TextField(blank=True)
    fingerprint = models.CharField(max_length=512, blank=True)
    qrcode = models.ImageField(upload_to=get_media_file_path, blank=True)
    created_date = models.DateTimeField(default=timezone.now)
    update_scheduled = models.BooleanField(default=False)
    is_updating = models.BooleanField(default=False)
    last_updated_date = models.DateTimeField(auto_now=True)
    last_publication_date = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.name

    def get_path(self):
        return os.path.join(settings.REPO_ROOT, get_repo_path(self))

    def get_repo_path(self):
        return os.path.join(self.get_path(), REPO_DIR)

    def get_fingerprint_url(self):
        return self.url + "?fingerprint=" + self.fingerprint

    def get_config(self):
        # Setup config
        config = {
            'repo_url': self.url,
            'repo_name': self.name,
            'repo_icon': os.path.join(settings.MEDIA_ROOT, self.icon.name),
            'repo_description': self.description,
            'repo_keyalias': "Key Alias",
            'keydname': keydname,
            'keystore': "keystore.jks",  # common.default_config['keystore'],
            'keystorepass': "uGrqvkPLiGptUScrAHsVAyNSQqyJq4OQJSiN1YZWxes=",  # common.genpassword(),
            'keystorepassfile': '.fdroid.keystorepass.txt',
            'keypass': "uGrqvkPLiGptUScrAHsVAyNSQqyJq4OQJSiN1YZWxes=",  # common.genpassword(),
            'keypassfile': '.fdroid.keypass.txt',
        }
        if self.public_key is not None:
            config['repo_pubkey'] = self.public_key
        common.fill_config_defaults(config)
        common.config = config
        common.options = Options
        update.config = config
        update.options = Options
        server.config = config
        server.options = Options
        return config

    def chdir(self):
        """
        Change into path for user's local repository
        """
        repo_local_path = self.get_path()
        if not os.path.exists(repo_local_path):
            os.makedirs(repo_local_path)
        os.chdir(repo_local_path)

    def create(self):
        """
        Creates the repository on disk including the keystore.
        This also sets the public key and fingerprint for :param repo.
        """
        self.chdir()
        config = self.get_config()

        # Ensure icon directories exist
        for icon_dir in update.get_all_icon_dirs(REPO_DIR):
            if not os.path.exists(icon_dir):
                os.makedirs(icon_dir)

        # Generate keystore
        pubkey, fingerprint = common.genkeystore(config)
        self.public_key = pubkey
        self.fingerprint = fingerprint.replace(" ", "")

        # Generate and save QR Code
        self.generate_qrcode()

        # Generate repository website
        self.generate_page()

        self.save()

    def generate_qrcode(self):
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=4,
            border=4,
        )
        qr.add_data(self.get_fingerprint_url())
        qr.make(fit=True)
        img = qr.make_image()

        # save in database/media location
        f = BytesIO()
        try:
            img.save(f, format='png')
            self.qrcode.save(self.fingerprint + ".png", ContentFile(f.getvalue()), False)
        finally:
            f.close()

        # save in repo
        img.save(os.path.join(self.get_repo_path(), 'qrcode.png'), format='png')

    def generate_page(self):
        with open(os.path.join(self.get_repo_path(), 'index.html'), 'w') as file:
            file.write('<a href="%s"/>' % self.get_fingerprint_url())
            file.write('<img src="qrcode.png"/> ')
            file.write(self.get_fingerprint_url())
            file.write('</a>')

    def update_async(self):
        """
        Schedules the repository to be updated (and published)
        """
        if self.update_scheduled:
            return  # no need to update a repo twice with same data
        self.update_scheduled = True
        self.save()
        update_repo(self.id)

    def update(self):
        """
        Updates the repository on disk, generates index, categories, etc.

        You normally don't need to call this directly
        as it is meant to be run in a background task scheduled by update_async().
        """
        self.chdir()
        self.get_config()

        # Gather information about all the apk files in the repo directory, using
        # cached data if possible.
        apkcache = update.get_cache()

        # Scan all apks in the main repo
        knownapks = common.KnownApks()
        apks, cachechanged = update.scan_apks(apkcache, REPO_DIR, knownapks, False)

        # Apply app metadata from database
        apps = {}
        categories = set()
        for apk in apks:
            try:
                from maker.models.app import App
                app = App.objects.get(repo=self, package_id=apk['packageName']).to_metadata_app()
                apps[app.id] = app
                categories.update(app.Categories)
            except ObjectDoesNotExist:
                logging.warning("App '%s' not found in database" % apk['packageName'])

        update.apply_info_from_latest_apk(apps, apks)

        # Sort the app list by name
        sortedids = sorted(apps.keys(), key=lambda app_id: apps[app_id].Name.upper())

        # Make the index for the repo
        update.make_index(apps, sortedids, apks, REPO_DIR, False)
        update.make_categories_txt(REPO_DIR, categories)

        # Update cache if it changed
        if cachechanged:
            update.write_cache(apkcache)

    def publish(self):
        """
        Publishes the repository to the available storage locations

        You normally don't need to call this manually
        as it is intended to be called automatically after each update.
        """
        # Publish to SSH Storage
        from maker.models.sshstorage import SshStorage
        for storage in SshStorage.objects.filter(repo=self):
            storage.publish()

        # Publish to Amazon S3
        self.chdir()  # expected by server.update_awsbucket()
        from maker.models.s3storage import S3Storage
        for storage in S3Storage.objects.filter(repo=self):
            storage.publish()

        self.last_publication_date = timezone.now()

    class Meta:
        verbose_name_plural = "Repositories"


class RepositoryForm(ModelForm):
    class Meta:
        model = Repository
        fields = ['name', 'description', 'url', 'icon']
        labels = {
            'url': 'Main URL',
        }
        help_texts = {
            'url': 'This is the primary location where your repository will be made available.',
        }


class Options:
    verbose = settings.DEBUG
    pretty = settings.DEBUG
    quiet = not settings.DEBUG
    clean = False
    nosign = False
    no_checksum = False
    identity_file = None