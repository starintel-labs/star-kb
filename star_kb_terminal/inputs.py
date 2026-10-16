"""Conservative lexical inference; never resolve DNS or fetch a URL."""
import ipaddress
import re
from urllib.parse import urlsplit


def valid_host(value):
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return (len(value) <= 253 and bool(value)
                and all(re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', x)
                        for x in value.rstrip('.').split('.')))


def interpret(value, documents, canonical, kind=None):
    if not isinstance(value, str) or not 0 < len(value) <= 2048 or any(ord(c) < 32 for c in value):
        raise ValueError('input must be 1..2048 printable characters')
    candidates = []
    if value in documents or (value.startswith('starintel:') and re.fullmatch(r'[A-Za-z0-9._~:/+-]{1,512}', value)):
        candidates.append({'kind': 'document-id', 'value': value,
                           'dtype': documents.get(value, {}).get('dtype')})
    try:
        ip = ipaddress.ip_address(value)
        candidates.append({'kind': 'ip', 'value': str(ip), 'dtype': None})
    except ValueError:
        pass
    try:
        url = urlsplit(value)
        if url.scheme in ('http', 'https') and url.hostname and valid_host(url.hostname) and not url.username and not url.password and not any(c.isspace() for c in value):
            _ = url.port
            candidates.append({'kind': 'url', 'value': value, 'dtype': 'url'})
    except ValueError:
        pass
    if (len(value) <= 253 and '.' in value and not re.fullmatch(r'[0-9.]+', value)
            and all(re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', x)
                    for x in value.rstrip('.').split('.'))):
        candidates.append({'kind': 'domain', 'value': value.lower().rstrip('.'), 'dtype': 'domain'})
    if kind:
        if kind.startswith('document:'):
            dtype = kind.removeprefix('document:')
            if not re.fullmatch(r'[A-Za-z0-9._~:/+-]{1,512}', value):
                raise ValueError('supplied document type requires a canonical document ID')
            if dtype not in canonical.types:
                raise ValueError('unknown supplied document type')
            if value in documents and documents[value]['dtype'] != dtype:
                raise ValueError('supplied type conflicts with canonical document')
            candidates = [{'kind': 'document-id', 'value': value, 'dtype': dtype}]
        else:
            candidates = [x for x in candidates if x['kind'] == kind]
    # Generic IDs overlap domains and URLs by schema. Require explicit selection
    # unless the prefix or a loaded record establishes document identity.
    if kind == 'document-id' and not candidates and re.fullmatch(r'[A-Za-z0-9._~:/+-]{1,512}', value):
        candidates = [{'kind': 'document-id', 'value': value, 'dtype': None}]
    if not candidates:
        return {'state': 'unresolved', 'raw': value, 'candidates': [],
                'reason': 'supply a type or a canonical document; free text is not an entity'}
    if len(candidates) > 1:
        return {'state': 'ambiguous', 'raw': value, 'candidates': candidates,
                'reason': 'choose an explicit input type'}
    return {'state': 'interpreted', 'raw': value, **candidates[0]}


def goal(text, documents, canonical, kind=None):
    if not text.lower().startswith('investigate '):
        raise ValueError('supported language is: investigate X; no model is called')
    subject = interpret(text[len('investigate '):].strip(), documents, canonical, kind)
    return {'protocol': 'star.kb.goal/1', 'intent': 'investigate', 'subject': subject,
            'limits': {'maxSteps': 1}, 'requestedText': text}
