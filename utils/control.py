import datetime
import traceback

from flask import current_app
from CTFd.utils import get_config

from ..models import WhaleContainer
from .cache import CacheProvider
from .db import DBContainer, db
from .docker import DockerUtils
from .participants import get_team_id_for_user, is_team_mode
from .routers import Router


class ControlUtil:
    @staticmethod
    def try_add_container(user_id, challenge_id, team_id=None):
        if team_id is None and is_team_mode():
            team_id = get_team_id_for_user(user_id)
        cache = CacheProvider(app=current_app)
        if not cache.acquire_global_lock():
            return False, 'Server busy, please retry.'

        container = None
        msg = 'Container creation failed'
        try:
            limit = int(get_config("whale:docker_max_container_count", 1000))
            if int(DBContainer.get_all_alive_container_count()) >= limit:
                return False, 'Max container count exceed.'

            if DBContainer.get_current_containers(
                user_id=user_id, team_id=team_id, include_inactive=True
            ):
                return False, 'Container already exists.'

            container = DBContainer.create_container_record(user_id, challenge_id)
            DockerUtils.add_container(container)

            # Mark running before router registration so a locked reload can see
            # the new instance. On failure the cleanup below removes the record.
            DBContainer.mark_container_status(container, WhaleContainer.STATUS_RUNNING)
            ok, msg = Router.register(container)
            if ok:
                return True, 'Container created'
            raise RuntimeError(msg)
        except Exception:
            print(traceback.format_exc())
            if container is not None:
                try:
                    Router.unregister(container)
                except Exception:
                    print(traceback.format_exc())
                try:
                    DockerUtils.remove_container(container)
                except Exception:
                    print(traceback.format_exc())
                try:
                    DBContainer.remove_container_record(container_ids=[container.id])
                except Exception:
                    print(traceback.format_exc())
            return False, msg
        finally:
            cache.release_global_lock()

    @staticmethod
    def try_remove_container(user_id, team_id=None):
        if team_id is None and is_team_mode():
            team_id = get_team_id_for_user(user_id)
        cache = CacheProvider(app=current_app)
        if not cache.acquire_global_lock():
            return False, 'Server busy, please retry.'

        try:
            containers = DBContainer.get_current_containers_list(
                user_id=user_id, team_id=team_id, include_inactive=True
            )
            if not containers:
                return False, 'No such container'

            removed_ids = []
            failures = []
            for container in containers:
                try:
                    DBContainer.mark_container_status(container, WhaleContainer.STATUS_REMOVING)
                except Exception:
                    print(traceback.format_exc())

                route_ok = True
                route_msg = 'success'
                try:
                    route_ok, route_msg = Router.unregister(container)
                except Exception as e:
                    route_ok = False
                    route_msg = str(e) or 'router unregister failed'
                    print(traceback.format_exc())

                docker_ok = True
                try:
                    DockerUtils.remove_container(container)
                except Exception as e:
                    docker_ok = False
                    failures.append(str(e) or 'docker remove failed')
                    print(traceback.format_exc())

                if not route_ok:
                    failures.append(route_msg)

                if docker_ok:
                    removed_ids.append(container.id)
                else:
                    # Keep the DB record visible so cleanup can be retried.
                    try:
                        DBContainer.mark_container_status(container, WhaleContainer.STATUS_RUNNING)
                    except Exception:
                        print(traceback.format_exc())

            if removed_ids:
                DBContainer.remove_container_record(container_ids=removed_ids)

            if failures:
                return False, '; '.join(failures)
            return True, 'Container destroyed'
        finally:
            cache.release_global_lock()

    @staticmethod
    def try_renew_container(user_id, team_id=None):
        if team_id is None and is_team_mode():
            team_id = get_team_id_for_user(user_id)
        container = DBContainer.get_current_containers(user_id=user_id, team_id=team_id)
        if not container:
            return False, 'No such container'
        timeout = int(get_config("whale:docker_timeout", "3600"))
        container.start_time = container.start_time + \
                               datetime.timedelta(seconds=timeout)
        if container.start_time > datetime.datetime.now():
            container.start_time = datetime.datetime.now()
            # race condition? useless maybe?
            # useful when docker_timeout < poll timeout (10 seconds)
            # doesn't make any sense
        else:
            return False, 'Invalid container'
        container.renew_count += 1
        db.session.commit()
        return True, 'Container Renewed'
