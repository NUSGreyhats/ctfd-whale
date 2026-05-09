import functools
import json

from flask import request, current_app
from flask_restx import abort
from sqlalchemy.sql import and_

from CTFd.models import Challenges
from CTFd.utils import get_config
from CTFd.utils.user import is_admin, get_current_user

from .models import DynamicDockerChallenge
from .utils.cache import CacheProvider
from .utils.participants import is_team_mode, get_current_team_id


def _identity_decorator(func):
    return func


try:
    from CTFd.utils.decorators import during_ctf_time_only, require_verified_emails
except Exception:  # pragma: no cover - compatibility with older CTFd versions
    during_ctf_time_only = _identity_decorator
    require_verified_emails = _identity_decorator


def _challenge_prerequisites(challenge_id):
    try:
        from CTFd.models import ChallengeRequirements
    except Exception:
        return []

    req = ChallengeRequirements.query.filter_by(challenge_id=challenge_id).first()
    if req is None:
        return []

    requirements = getattr(req, "requirements", None) or {}
    if isinstance(requirements, str):
        try:
            requirements = json.loads(requirements)
        except Exception:
            return []

    prerequisites = requirements.get("prerequisites", []) if isinstance(requirements, dict) else []
    try:
        return [int(challenge_id) for challenge_id in prerequisites]
    except Exception:
        return []


def _solved_challenge_ids(user_id, team_id=None):
    try:
        from CTFd.models import Solves
    except Exception:
        return set()

    q = Solves.query
    if is_team_mode() and team_id is not None and hasattr(Solves, "team_id"):
        q = q.filter_by(team_id=team_id)
    else:
        q = q.filter_by(user_id=user_id)
    return {solve.challenge_id for solve in q.all()}


def _check_challenge_requirements(challenge_id):
    if is_admin():
        return

    prerequisites = _challenge_prerequisites(challenge_id)
    if not prerequisites:
        return

    user = get_current_user()
    team_id = get_current_team_id() if is_team_mode() else None
    if is_team_mode() and team_id is None:
        abort(403, 'team required', success=False)

    solved = _solved_challenge_ids(user.id, team_id=team_id)
    if not set(prerequisites).issubset(solved):
        abort(403, 'challenge requirements not satisfied', success=False)


def challenge_visible(func):
    @functools.wraps(func)
    @during_ctf_time_only
    @require_verified_emails
    def _challenge_visible(*args, **kwargs):
        challenge_id = request.args.get('challenge_id', type=int)
        if challenge_id is None:
            abort(400, 'missing or invalid challenge_id', success=False)

        if is_admin():
            if not Challenges.query.filter(Challenges.id == challenge_id).first():
                abort(404, 'no such challenge', success=False)
        else:
            if is_team_mode() and get_current_team_id() is None:
                abort(403, 'team required', success=False)
            if not Challenges.query.filter(
                Challenges.id == challenge_id,
                and_(Challenges.state != "hidden", Challenges.state != "locked"),
            ).first():
                abort(403, 'challenge not visible', success=False)
            _check_challenge_requirements(challenge_id)

        if not DynamicDockerChallenge.query.filter_by(id=challenge_id).first():
            abort(400, 'challenge is not a docker challenge', success=False)
        return func(*args, **kwargs)

    return _challenge_visible


def frequency_limited(func):
    @functools.wraps(func)
    def _frequency_limited(*args, **kwargs):
        if is_admin():
            return func(*args, **kwargs)

        user = get_current_user()
        team_id = get_current_team_id() if is_team_mode() else None
        if is_team_mode() and team_id is None:
            abort(403, 'team required', success=False)

        scope = f'team-{team_id}' if team_id is not None else f'user-{user.id}'
        redis_util = CacheProvider(app=current_app, user_id=scope)
        if not redis_util.acquire_lock():
            abort(403, 'Request Too Fast!', success=False)

        try:
            seconds = int(get_config("whale:rate_limit_seconds", 60))
            allowed, remaining = redis_util.acquire_cooldown(
                f'ctfd_whale_cooldown-{scope}', seconds
            )
            if not allowed:
                abort(
                    403,
                    f'Frequency limit, You should wait at least {remaining} seconds.',
                    success=False,
                )
            return func(*args, **kwargs)
        finally:
            redis_util.release_lock()

    return _frequency_limited
