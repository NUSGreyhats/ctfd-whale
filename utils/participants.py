from CTFd.utils import get_config
from CTFd.utils import user as current_user


def is_team_mode():
    """Return True when CTFd is running in team mode.

    CTFd has moved this helper between versions, so keep a small fallback for
    older/newer installs instead of binding the plugin to a single CTFd release.
    """
    try:
        from CTFd.utils.config import is_teams_mode
        return bool(is_teams_mode())
    except Exception:
        return get_config("user_mode") == "teams"


def get_current_team():
    try:
        return current_user.get_current_team()
    except Exception:
        user = current_user.get_current_user()
        return getattr(user, "team", None)


def get_current_team_id():
    team = get_current_team()
    if team is not None:
        return getattr(team, "id", None)
    user = current_user.get_current_user()
    return getattr(user, "team_id", None)


def get_current_actor():
    """Return the current user id and, in team mode, the current team id."""
    user = current_user.get_current_user()
    team_id = get_current_team_id() if is_team_mode() else None
    return user.id, team_id


def get_team_member_ids(team_id):
    if team_id is None:
        return []
    try:
        from CTFd.models import Users
        return [user.id for user in Users.query.filter_by(team_id=team_id).all()]
    except Exception:
        return []


def get_team_id_for_user(user_id):
    try:
        from CTFd.models import Users
        user = Users.query.filter_by(id=user_id).first()
        return getattr(user, "team_id", None) if user is not None else None
    except Exception:
        return None
