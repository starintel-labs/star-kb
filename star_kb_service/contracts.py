"""Service envelope only. Document contracts are loaded from pinned StarLang."""
import json

PROTOCOL = "star.kb.service/1"
MAX_BYTES = 262144


class Fault(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def closed(value, required, optional=()):
    if (not isinstance(value, dict) or not set(required) <= value.keys()
            or value.keys() - set(required) - set(optional)):
        raise Fault("invalid_request")
    return value


def string(value, maximum=512):
    if (not isinstance(value, str) or not 0 < len(value) <= maximum
            or "\0" in value or any(0xD800 <= ord(c) <= 0xDFFF for c in value)):
        raise Fault("invalid_request")
    return value


def integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise Fault("invalid_request")
    return value


def encode(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def decode(data):
    if len(data) > MAX_BYTES:
        raise Fault("request_limit")
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise Fault("invalid_request")
            result[key] = value
        return result
    try:
        return json.loads(data, object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(Fault("invalid_request")))
    except (ValueError, UnicodeError, RecursionError):
        raise Fault("invalid_request") from None
