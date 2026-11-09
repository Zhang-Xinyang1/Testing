"""Shared formatting rules for Platform / EQP.TYPE, without machine aliases."""
import re
import unicodedata


def clean_type(value):
    text = unicodedata.normalize('NFKC', str(value or ''))
    text = re.sub(r'[\ufeff\u200b\u200c\u200d\u2060]', '', text)
    return re.sub(r'\s+', '', text).upper()


def split_platforms(value):
    text = unicodedata.normalize('NFKC', str(value or ''))
    return list(dict.fromkeys(clean_type(part) for part in
                             re.split(r'[;,/|、\r\n]+', text) if clean_type(part)))


def normalize_platforms(data):
    """Return a copy with platform names resolved to the effective EQP types.

    Repeated TYPEs are normal: several machines can belong to one group. Case
    and typography are normalized consistently, including on the EQP side.
    Unknown models are retained for validation, never guessed or discarded.
    """
    result = dict(data)
    equipment = [dict(row, type=clean_type(row.get('type')), status=clean_type(row.get('status')))
                 for row in data.get('eqp', [])]
    result['eqp'] = equipment
    cp_types = {row['type'] for row in equipment if 'OVEN' not in row['type'].upper()}
    def resolve(value):
        return ';'.join(sorted(split_platforms(value)))

    for field in ('mps', 'weekly'):
        if field in data:
            result[field] = [dict(row, platform=resolve(row.get('platform')))
                             for row in data[field]]
    if 'lots' in data:
        result['lots'] = [list(row[:2]) + [resolve(row[2])] + list(row[3:])
                          for row in data['lots']]
    # Resource aggregates also use the canonical group names.
    if 'platformType' in data:
        result['platformType'] = {resolve(key): resolve(value)
                                  for key, value in data['platformType'].items()}
        result['platformType'].update({name: name for name in cp_types})
    return result
