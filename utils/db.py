import datetime

from CTFd.models import db
from CTFd.utils import get_config

from ..models import WhaleContainer, WhaleRedirectTemplate
from .participants import is_team_mode, get_team_member_ids


class DBContainer:
    @staticmethod
    def _apply_actor_filter(q, user_id=None, team_id=None):
        if team_id is not None and is_team_mode():
            member_ids = get_team_member_ids(team_id)
            if not member_ids:
                return q.filter(WhaleContainer.user_id == -1)
            return q.filter(WhaleContainer.user_id.in_(member_ids))
        return q.filter(WhaleContainer.user_id == user_id)

    @staticmethod
    def _apply_alive_filter(q):
        timeout = int(get_config("whale:docker_timeout", "3600"))
        return q.filter(
            WhaleContainer.start_time >=
            datetime.datetime.now() - datetime.timedelta(seconds=timeout),
            WhaleContainer.status == WhaleContainer.STATUS_RUNNING,
        )

    @staticmethod
    def create_container_record(user_id, challenge_id):
        container = WhaleContainer(user_id=user_id, challenge_id=challenge_id)
        db.session.add(container)
        db.session.commit()

        return container

    @staticmethod
    def mark_container_status(container, status):
        container.status = status
        db.session.commit()

    @staticmethod
    def get_current_containers(user_id=None, team_id=None, challenge_id=None, include_inactive=False):
        q = db.session.query(WhaleContainer)
        q = DBContainer._apply_actor_filter(q, user_id=user_id, team_id=team_id)
        if challenge_id is not None:
            q = q.filter(WhaleContainer.challenge_id == challenge_id)
        if not include_inactive:
            q = q.filter(WhaleContainer.status == WhaleContainer.STATUS_RUNNING)
        return q.order_by(WhaleContainer.start_time.desc()).first()

    @staticmethod
    def get_current_containers_list(user_id=None, team_id=None, challenge_id=None, include_inactive=False):
        q = db.session.query(WhaleContainer)
        q = DBContainer._apply_actor_filter(q, user_id=user_id, team_id=team_id)
        if challenge_id is not None:
            q = q.filter(WhaleContainer.challenge_id == challenge_id)
        if not include_inactive:
            q = q.filter(WhaleContainer.status == WhaleContainer.STATUS_RUNNING)
        return q.order_by(WhaleContainer.start_time.desc()).all()

    @staticmethod
    def get_container_by_port(port):
        q = db.session.query(WhaleContainer)
        q = q.filter(
            WhaleContainer.port == port,
            WhaleContainer.status == WhaleContainer.STATUS_RUNNING,
        )
        return q.first()

    @staticmethod
    def get_container_by_id(container_id):
        return db.session.query(WhaleContainer).filter(
            WhaleContainer.id == container_id
        ).first()

    @staticmethod
    def remove_container_record(user_id=None, team_id=None, container_ids=None):
        q = db.session.query(WhaleContainer)
        if container_ids is not None:
            q = q.filter(WhaleContainer.id.in_(container_ids))
        else:
            q = DBContainer._apply_actor_filter(q, user_id=user_id, team_id=team_id)
        q.delete(synchronize_session=False)
        db.session.commit()

    @staticmethod
    def get_all_expired_container():
        timeout = int(get_config("whale:docker_timeout", "3600"))

        q = db.session.query(WhaleContainer)
        q = q.filter(
            WhaleContainer.start_time <
            datetime.datetime.now() - datetime.timedelta(seconds=timeout)
        )
        return q.all()

    @staticmethod
    def get_all_alive_container():
        q = db.session.query(WhaleContainer)
        q = DBContainer._apply_alive_filter(q)
        return q.all()

    @staticmethod
    def get_all_container():
        q = db.session.query(WhaleContainer)
        return q.all()

    @staticmethod
    def get_all_alive_container_page(page_start, page_end):
        q = db.session.query(WhaleContainer)
        q = DBContainer._apply_alive_filter(q)
        q = q.slice(page_start, page_end)
        return q.all()

    @staticmethod
    def get_all_alive_container_count():
        q = db.session.query(WhaleContainer)
        q = DBContainer._apply_alive_filter(q)
        return q.count()


class DBRedirectTemplate:
    @staticmethod
    def get_all_templates():
        return WhaleRedirectTemplate.query.all()

    @staticmethod
    def create_template(name, access_template, frp_template):
        if WhaleRedirectTemplate.query.filter_by(key=name).first():
            return  # already existed
        db.session.add(WhaleRedirectTemplate(
            name, access_template, frp_template
        ))
        db.session.commit()

    @staticmethod
    def delete_template(name):
        WhaleRedirectTemplate.query.filter_by(key=name).delete()
        db.session.commit()
