import logging
import re

from django.core.validators import RegexValidator, ValidationError, slug_re, force_text
from django.db import models
from django.utils.deconstruct import deconstructible
from django.utils.translation import ugettext_lazy as _
from fdroidserver import server

from maker.storage import get_identity_file_path, RepoStorage
from .repository import Repository


UL = '\u00a1-\uffff'  # unicode letters range (must be a unicode string, not a raw string)


@deconstructible
class UsernameValidator(RegexValidator):
    regex = slug_re
    message = _("Enter a valid user name consisting of letters, numbers, underscores or hyphens.")


@deconstructible
class HostnameValidator(RegexValidator):
    # IP patterns
    ipv4_re = r'(?:25[0-5]|2[0-4]\d|[0-1]?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|[0-1]?\d?\d)){3}'
    ipv6_re = r'\[[0-9a-f:\.]+\]'  # (simple regex, validated later)

    # Host patterns
    hostname_re = r'[a-z' + UL + r'0-9](?:[a-z' + UL + r'0-9-]{0,61}[a-z' + UL + r'0-9])?'
    # Max length for domain name labels is 63 characters per RFC 1034 sec. 3.1
    domain_re = r'(?:\.(?!-)[a-z' + UL + r'0-9-]{1,63}(?<!-))*'
    tld_re = (
        '\.'                                # dot
        '(?!-)'                             # can't start with a dash
        '(?:[a-z' + UL + '-]{2,63}'         # domain label
                         '|xn--[a-z0-9]{1,59})'              # or punycode label
                         '(?<!-)'                            # can't end with a dash
                         '\.?'                               # may have a trailing dot
    )
    host_re = '(' + hostname_re + domain_re + tld_re + '|localhost)'

    regex = re.compile(r'(?:' + ipv4_re + '|' + ipv6_re + '|' + host_re + ')\Z',
                       re.IGNORECASE)
    message = _('Enter a valid hostname.')

    def __call__(self, value):
        value = force_text(value)
        # The maximum length of a full host name is 253 characters per RFC 1034
        # section 3.1. It's defined to be 255 bytes or less, but this includes
        # one byte for the length of the name and one byte for the trailing dot
        # that's used to indicate absolute names in DNS.
        if len(value) > 253:
            raise ValidationError(self.message, code=self.code)

        super(HostnameValidator, self).__call__(value)


@deconstructible
class PathValidator(RegexValidator):
    regex = re.compile(r'(/[a-z' + UL + r'0-9-]+)+?/?\Z', re.IGNORECASE)
    message = _('Enter a valid path.')


class SshStorage(models.Model):
    repo = models.ForeignKey(Repository, on_delete=models.CASCADE)
    username = models.CharField(max_length=64, validators=[UsernameValidator()])
    host = models.CharField(max_length=256, validators=[HostnameValidator()])
    path = models.CharField(max_length=512, validators=[PathValidator()])
    identity_file = models.FileField(upload_to=get_identity_file_path, storage=RepoStorage(),
                                     blank=True)

    def __str__(self):
        return self.get_url()

    def get_url(self):
        return '%s@%s:%s' % (self.username, self.host, self.path)

    def publish(self):
        logging.info("Publishing '%s' to %s" % (self.repo, self))
        config = self.repo.get_config()
        if self.identity_file is not None and self.identity_file != '':
            config['identity_file'] = self.identity_file.name
        local = self.repo.get_repo_path()
        remote = self.__str__()
        server.update_serverwebroot(remote, local)