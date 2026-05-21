import random
import re
import uuid
from datetime import datetime

from jinja2.sandbox import SandboxedEnvironment

from CTFd.utils import get_config
from CTFd.models import db
from CTFd.plugins.dynamic_challenges import DynamicChallenge

from .utils.exceptions import WhaleError


_TEMPLATE_ENV = SandboxedEnvironment(autoescape=False)
_SUBDOMAIN_RE = re.compile(r"^(?=.{1,63}$)[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")


def _render_template(source, **context):
    return _TEMPLATE_ENV.from_string(source).render(**context)


def _validate_subdomain(subdomain):
    subdomain = str(subdomain).strip().lower()
    if not _SUBDOMAIN_RE.fullmatch(subdomain):
        raise WhaleError(
            "Invalid rendered subdomain. Use only letters, digits and dashes; "
            "it must be 1-63 characters and must not start or end with a dash."
        )
    return subdomain


class WhaleConfig(db.Model):
    key = db.Column(db.String(length=128), primary_key=True)
    value = db.Column(db.Text)

    def __init__(self, key, value):
        self.key = key
        self.value = value

    def __repr__(self):
        return "<WhaleConfig {0} {1}>".format(self.key, self.value)


class WhaleRedirectTemplate(db.Model):
    key = db.Column(db.String(20), primary_key=True)
    frp_template = db.Column(db.Text)
    access_template = db.Column(db.Text)

    def __init__(self, key, access_template, frp_template):
        self.key = key
        self.access_template = access_template
        self.frp_template = frp_template

    def __repr__(self):
        return "<WhaleRedirectTemplate {0}>".format(self.key)


class DynamicDockerChallenge(DynamicChallenge):
    __mapper_args__ = {"polymorphic_identity": "dynamic_docker"}
    id = db.Column(
        db.Integer, db.ForeignKey("dynamic_challenge.id", ondelete="CASCADE"), primary_key=True
    )

    memory_limit = db.Column(db.Text, default="128m")
    cpu_limit = db.Column(db.Float, default=0.5)
    dynamic_score = db.Column(db.Integer, default=0)

    docker_image = db.Column(db.Text, default=0)
    redirect_type = db.Column(db.Text, default="http")
    redirect_port = db.Column(db.Integer, default=0)

    def __init__(self, *args, **kwargs):
        kwargs["initial"] = kwargs["value"]
        super(DynamicDockerChallenge, self).__init__(**kwargs)


class WhaleContainer(db.Model):
    STATUS_CREATING = 0
    STATUS_RUNNING = 1
    STATUS_REMOVING = 2

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(None, db.ForeignKey("users.id"))
    challenge_id = db.Column(None, db.ForeignKey("challenges.id"))
    start_time = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    renew_count = db.Column(db.Integer, nullable=False, default=0)
    status = db.Column(db.Integer, default=STATUS_CREATING)
    uuid = db.Column(db.String(256))
    port = db.Column(db.Integer, nullable=True, default=0)
    flag = db.Column(db.String(128), nullable=False)

    # Relationships
    user = db.relationship(
        "Users", foreign_keys="WhaleContainer.user_id", lazy="select")
    challenge = db.relationship(
        "DynamicDockerChallenge", foreign_keys="WhaleContainer.challenge_id", lazy="select"
    )

    @property
    def http_subdomain(self):
        rendered = _render_template(get_config(
            'whale:template_http_subdomain', '{{ container.uuid }}'
        ), container=self)
        return _validate_subdomain(rendered)

    def __init__(self, user_id, challenge_id):
        self.user_id = user_id
        self.challenge_id = challenge_id
        self.start_time = datetime.now()
        self.renew_count = 0
        self.status = self.STATUS_CREATING
        self.uuid = str(uuid.uuid4())
        self.flag = _render_template(get_config(
            'whale:template_chall_flag', '{{ "flag{"+uuid.uuid4()|string+"}" }}'
        ), container=self, uuid=uuid, random=random, get_config=get_config).strip()
        if len(self.flag) > 128:
            raise WhaleError('Rendered flag is too long (maximum 128 characters)')

    @property
    def user_access(self):
        return _render_template(WhaleRedirectTemplate.query.filter_by(
            key=self.challenge.redirect_type
        ).first().access_template, container=self, get_config=get_config)

    @property
    def frp_config(self):
        return _render_template(WhaleRedirectTemplate.query.filter_by(
            key=self.challenge.redirect_type
        ).first().frp_template, container=self, get_config=get_config)

    def __repr__(self):
        return "<WhaleContainer ID:{0} {1} {2} {3} {4}>".format(self.id, self.user_id, self.challenge_id,
                                                                self.start_time, self.renew_count)
