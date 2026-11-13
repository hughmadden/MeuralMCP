"""Input bounds for declarative modes and explicit menu navigation."""
import json
import re
from typing import Literal

MenuDirection = Literal['left', 'right', 'up', 'down']
IDENTIFIER = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z')


def mode_id(value: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError('invalid mode/menu identifier')
    return value


def direction(value: str) -> str:
    if value not in ('left', 'right', 'up', 'down'):
        raise ValueError('direction must be left, right, up or down')
    return value


def mode_definition(value: dict) -> dict:
    if not isinstance(value, dict):
        raise ValueError('mode definition must be an object')
    mode_id(value.get('id'))
    try:
        encoded = json.dumps(value, allow_nan=False).encode('utf-8')
    except (ValueError, TypeError, RecursionError):
        raise ValueError('invalid mode definition') from None
    if len(encoded) > 64 * 1024:
        raise ValueError('mode definition exceeds 64 KiB')
    return value


def menu_timeout(seconds: int | None, menu_id: str | None = None) -> None:
    if seconds is not None and (type(seconds) is not int or not 1 <= seconds <= 3600):
        raise ValueError('idle timeout must be an integer from 1 to 3600 seconds or null')
    if menu_id is not None:
        mode_id(menu_id)


def event_cursor(after: int, epoch: str | None = None) -> None:
    if type(after) is not int or not 0 <= after < 10 ** 20:
        raise ValueError('invalid event cursor')
    if epoch is not None and (not isinstance(epoch, str) or len(epoch) > 128):
        raise ValueError('invalid event epoch')
