"""Lightweight explanation number audit, independent of production calculations.

It checks source paths, allowed operations, formatting and literal coverage.
It does not infer unit correctness or complete natural-language semantics.
"""
import math
import re

# Fixed analysis settings allowed in prose; not observed business facts.
ALLOWED_POLICIES = {'significance_alpha': 0.05, 'confidence_level_pct': 95}
ALLOWED_FORMATS = {',.0f', '.4f', '.2f', '.0f'}
NUMBER = re.compile(r'(?<![A-Za-z_0-9.])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?![0-9.])')


def source_value(data, path):
    for segment in path.split('/'):
        if isinstance(data, list):
            field, expected = segment.split('=', 1)
            matches = [item for item in data if str(item.get(field)) == expected]
            if len(matches) != 1:
                raise ValueError('Nonunique source selector')
            data = matches[0]
        else:
            data = data[segment]
    if isinstance(data, bool) or not isinstance(data, (int, float)) or not math.isfinite(data):
        raise ValueError('Source is not a finite number')
    return data


def strings(value):
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for item in value.values() for s in strings(item)]
    if isinstance(value, list):
        return [s for item in value for s in strings(item)]
    return []


def numeric_grounding_check(output):
    interpretation = output.get('interpretation', {})
    evidence = interpretation.get('numeric_evidence')
    errors = []
    displays = set()
    entries_checked = 0
    if not isinstance(evidence, list):
        return {'passed': False, 'errors': ['Missing numeric_evidence ledger'],
                'checked_literals': 0, 'checked_entries': 0}
    for index, entry in enumerate(evidence):
        entries_checked += 1
        try:
            operation, paths = entry['operation'], entry['paths']
            if operation == 'policy':
                if paths:
                    raise ValueError('Policy must not reference data paths')
                expected = ALLOWED_POLICIES[entry['policy']]
            else:
                operands = [source_value(output.get('key_data', {}), path) for path in paths]
                if operation == 'identity' and len(operands) == 1:
                    expected = operands[0]
                elif operation == 'difference' and len(operands) == 2:
                    expected = operands[1] - operands[0]
                elif operation == 'pct_change' and len(operands) == 2 and operands[0] != 0:
                    expected = (operands[1] / operands[0] - 1) * 100
                else:
                    raise ValueError('Derivation is not on the allowlist')
            actual = entry['value']
            if isinstance(actual, bool) or not isinstance(actual, (int, float)) or not math.isfinite(actual):
                raise ValueError('Claimed number is not finite')
            if not math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-9):
                raise ValueError('Claimed number differs from independently recomputed value')
            if entry['format_spec'] not in ALLOWED_FORMATS:
                raise ValueError('Display format is not on the allowlist')
            if entry['display'] != format(expected, entry['format_spec']):
                raise ValueError('Display does not match source value and format')
            displays.add(entry['display'])
        except (KeyError, TypeError, ValueError, ZeroDivisionError) as error:
            errors.append(f'entry {index}: {error}')
    body = {key: value for key, value in interpretation.items() if key != 'numeric_evidence'}
    literals = [match.group(0) for s in strings([body, output.get('caveats', [])])
                for match in NUMBER.finditer(s)]
    # Identifier labels such as P1 and Q75 are excluded by the word boundary rule;
    # unannotated numeric dates or other literals fail, rather than being guessed.
    ungrounded = sorted(set(literals) - displays)
    if ungrounded:
        errors.append(f'Unannotated numeric literals: {ungrounded}')
    unused = sorted(displays - set(literals))
    if unused:
        errors.append(f'Unused evidence displays: {unused}')
    return {'passed': not errors, 'errors': errors,
            'checked_literals': len(literals), 'checked_entries': entries_checked}
