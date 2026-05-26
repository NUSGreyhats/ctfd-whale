from datetime import datetime
import re

from flask import request
from flask_restx import Namespace, Resource, abort
from requests import RequestException, get

from CTFd.utils import get_config
from CTFd.utils.decorators import admins_only, authed_only

from .decorators import challenge_visible, frequency_limited
from .utils.control import ControlUtil
from .utils.db import DBContainer
from .utils.participants import get_current_actor
from .utils.routers import Router

admin_namespace = Namespace("ctfd-whale-admin")
user_namespace = Namespace("ctfd-whale-user")


def _extract_http_url(access):
    match = re.search(r'https?://[^"\'<>\s]+', access or '')
    return match.group(0) if match else ''


def _container_ready(container):
    if container.challenge.redirect_type != 'http':
        return True

    url = _extract_http_url(Router.access(container))
    if not url:
        return False

    try:
        response = get(url, timeout=2.0, allow_redirects=True)
    except RequestException:
        return False

    return response.status_code < 500 and response.status_code != 404


@admin_namespace.errorhandler
@user_namespace.errorhandler
def handle_default(err):
    return {
        'success': False,
        'message': 'Unexpected things happened'
    }, 500


@admin_namespace.route('/container')
class AdminContainers(Resource):
    @staticmethod
    @admins_only
    def get():
        page = abs(request.args.get("page", 1, type=int))
        results_per_page = abs(request.args.get("per_page", 20, type=int))
        page_start = results_per_page * (page - 1)
        page_end = results_per_page * (page - 1) + results_per_page

        count = DBContainer.get_all_alive_container_count()
        containers = DBContainer.get_all_alive_container_page(
            page_start, page_end)

        return {'success': True, 'data': {
            'containers': containers,
            'total': count,
            'pages': int(count / results_per_page) + (count % results_per_page > 0),
            'page_start': page_start,
        }}

    @staticmethod
    @admins_only
    def patch():
        user_id = request.args.get('user_id', -1)
        result, message = ControlUtil.try_renew_container(user_id=int(user_id))
        if not result:
            abort(403, message, success=False)
        return {'success': True, 'message': message}

    @staticmethod
    @admins_only
    def delete():
        user_id = request.args.get('user_id')
        result, message = ControlUtil.try_remove_container(user_id)
        return {'success': result, 'message': message}


@user_namespace.route("/container")
class UserContainers(Resource):
    @staticmethod
    @authed_only
    @challenge_visible
    def get():
        user_id, team_id = get_current_actor()
        challenge_id = request.args.get('challenge_id', type=int)
        container = DBContainer.get_current_containers(user_id=user_id, team_id=team_id)
        if not container:
            return {'success': True, 'data': {}}
        timeout = int(get_config("whale:docker_timeout", "3600"))
        if int(container.challenge_id) != int(challenge_id):
            return abort(403, 'Container already started but not from this challenge', success=False)
        ready = _container_ready(container)
        data = {
            'lan_domain': str(container.user_id) + "-" + container.uuid,
            'ready': ready,
            'remaining_time': timeout - int((datetime.now() - container.start_time).total_seconds()),
        }
        if ready:
            data['user_access'] = Router.access(container)
        else:
            data['message'] = 'Instance is starting. The link will appear once it is ready.'
        return {
            'success': True,
            'data': data
        }

    @staticmethod
    @authed_only
    @challenge_visible
    @frequency_limited
    def post():
        user_id, team_id = get_current_actor()
        ControlUtil.try_remove_container(user_id=user_id, team_id=team_id)

        challenge_id = request.args.get('challenge_id', type=int)
        result, message = ControlUtil.try_add_container(
            user_id=user_id,
            challenge_id=challenge_id,
            team_id=team_id,
        )
        if not result:
            abort(403, message, success=False)
        return {'success': True, 'message': message}

    @staticmethod
    @authed_only
    @challenge_visible
    @frequency_limited
    def patch():
        user_id, team_id = get_current_actor()
        challenge_id = request.args.get('challenge_id', type=int)
        docker_max_renew_count = int(get_config("whale:docker_max_renew_count", 5))
        container = DBContainer.get_current_containers(user_id=user_id, team_id=team_id)
        if container is None:
            abort(403, 'Instance not found.', success=False)
        if int(container.challenge_id) != int(challenge_id):
            abort(403, f'Container started but not from this challenge（{container.challenge.name}）', success=False)
        if container.renew_count >= docker_max_renew_count:
            abort(403, 'Max renewal count exceed.', success=False)
        result, message = ControlUtil.try_renew_container(user_id=user_id, team_id=team_id)
        return {'success': result, 'message': message}

    @staticmethod
    @authed_only
    @frequency_limited
    def delete():
        user_id, team_id = get_current_actor()
        result, message = ControlUtil.try_remove_container(user_id=user_id, team_id=team_id)
        if not result:
            abort(403, message, success=False)
        return {'success': True, 'message': message}
