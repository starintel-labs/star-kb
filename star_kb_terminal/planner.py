"""Bounded Prolog compatibility query; a plan is a proposal, never execution."""
import json
import os
from pathlib import Path
import subprocess
from .contracts import digest


def candidates(registry, dtype):
    executable = os.environ.get('STAR_KB_TERMINAL_SWIPL', 'swipl')
    module = Path(__file__).parent / 'prolog/planner.pl'
    try:
        result = subprocess.run([executable, '-q', '-s', str(module), '-g',
                                 'terminal_planner:main', '-t', 'halt'],
            input=json.dumps({'actors': list(registry.actors.values()), 'dtype': dtype}),
            capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError('Prolog planner unavailable or timed out') from exc
    if result.returncode:
        raise ValueError('Prolog planner rejected request')
    return json.loads(result.stdout)['candidates']


def plan(goal, registry, selected):
    subject = goal['subject']
    rows = candidates(registry, subject.get('dtype'))
    chosen = next((x for x in rows if x['actor'] == selected), None)
    reason = None
    if subject['state'] != 'interpreted':
        reason = subject['state']
    elif subject.get('dtype') is None:
        reason = 'input has no established canonical document type'
    elif chosen is None:
        reason = 'select an actor explicitly'
    elif chosen['compatibility'] != 'compatible':
        reason = 'selected actor input compatibility is ' + chosen['compatibility']
    steps = [] if reason else [{'actor': selected, 'input': subject,
                'expectedOutputTypes': chosen['produces'],
                'manifestDigest': registry.actors[selected]['manifestDigest']}]
    value = {'protocol': 'star.kb.plan/1', 'goal': goal, 'registryDigest': registry.identity,
             'state': 'blocked' if reason else 'planned', 'reason': reason,
             'steps': steps, 'candidates': rows,
             'notice': 'Compatibility is declared, not proof of availability, authorization or success.'}
    value['id'] = digest(value)
    return value


def engine_plan(goal, registry, selected, capability, sources, documents, engine):
    """Inspect the supported engine rule; never manufacture a target conversion."""
    capabilities = engine.capabilities()
    if capability not in capabilities['rules']:
        raise ValueError('capability not advertised by engine')
    if (not isinstance(sources, list) or not 1 <= len(sources) <= capabilities['limits']['sources']
            or len(set(sources)) != len(sources)):
        raise ValueError('provide unique sources within advertised engine bounds')
    subject = goal['subject']
    if (subject.get('state') != 'interpreted' or subject.get('kind') != 'document-id'
            or subject.get('value') not in sources):
        raise ValueError('engine goal subject must be an explicit canonical source ID')
    for source in sources:
        doc = documents.get(source, {})
        if doc.get('dtype') != 'relation' or not doc.get('rev'):
            raise ValueError('load each engine source as a revisioned canonical Relation first')
    rows = candidates(registry, 'target')
    if selected:
        compatible = any(row['actor'] == selected and row['compatibility'] == 'compatible' for row in rows)
        if not compatible or capability not in capabilities['actors'].get(selected, []):
            raise ValueError('actor needs declared Target compatibility and engine capability authorization')
    value = {'protocol': 'star.kb.plan/1', 'goal': goal, 'registryDigest': registry.identity,
             'state': 'planned', 'reason': None, 'candidates': rows,
             'engineGoal': {'sources': sorted(sources), 'capability': capability, 'actor': selected},
             'engineIdentity': engine.identity, 'engineCapabilities': capabilities,
             'sourceDigests': {key: digest(documents[key]) for key in sorted(sources)},
             'steps': [{'engineCapability': capability, 'actor': selected}],
             'notice': 'Engine revalidates canonical sources and policy. Effects are not actor completion.'}
    value['id'] = digest(value)
    return value
