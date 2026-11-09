"""Own deterministic table extraction and exports; never ask a model to recreate result rows."""
import csv
import io
import json

MAX_ROWS = 5000


def at(value, path):
    if not path:
        return value
    for key in path.split('.'):
        value = value[int(key)] if isinstance(value, list) and key.isdigit() else value[key]
    return value


def normalize(result, mapping=None):
    mapping = mapping or {}
    if result.get('isError'):
        raise ValueError('The MCP tool reported an error. Inspect the service configuration or revise the request.')
    payload = result.get('structuredContent')
    if payload is None:
        blocks = result.get('content', [])
        text = '\n'.join(x['text'] for x in blocks if x.get('type') == 'text' and isinstance(x.get('text'), str))
        try:
            payload = json.loads(text)
        except (ValueError, TypeError):
            return dict(table=None, text=text[:16000], text_limited=len(text) > 16000,
                        completeness='unknown', explanation='The service returned text without a recognized table.')
    try:
        if mapping.get('rows'):
            rows = at(payload, mapping['rows'])
        elif isinstance(payload, list):
            rows = payload
        else:
            rows = payload.get('rows')
        if not isinstance(rows, list):
            raise ValueError()
        names = at(payload, mapping['columns']) if mapping.get('columns') else payload.get('columns') if isinstance(payload, dict) else None
        if names is not None:
            names = [v.get('name') if isinstance(v, dict) else v for v in names]
            if not all(isinstance(v, str) for v in names) or len(set(names)) != len(names):
                raise ValueError()
        elif all(isinstance(row, dict) for row in rows):
            names = list(dict.fromkeys(k for row in rows for k in row))
        else:
            raise ValueError()
        if len(names) > 200:
            raise ValueError('too many columns')
        values = []
        for row in rows[:MAX_ROWS]:
            values.append([row.get(n) for n in names] if isinstance(row, dict) else list(row))
            if len(values[-1]) != len(names):
                raise ValueError()
        total = at(payload, mapping['total']) if mapping.get('total') else payload.get('total_rows') if isinstance(payload, dict) else None
        truncated = at(payload, mapping['truncated']) if mapping.get('truncated') else payload.get('truncated') if isinstance(payload, dict) else None
        total = total if type(total) is int and total >= len(rows) else None
        limited = len(rows) > MAX_ROWS
        completeness = 'partial' if limited or truncated is True or (total is not None and total > len(values)) else 'complete' if truncated is False else 'unknown'
        return dict(table=dict(columns=names, rows=values), received_rows=len(rows), returned_rows=len(values),
                    total_rows=total, client_limited=limited, completeness=completeness,
                    explanation='Completeness is reported by the service.' if completeness == 'complete' else
                    'Only received rows are available for display and export; the full database result may be larger.')
    except (KeyError, IndexError, TypeError, ValueError):
        return dict(table=None, text=json.dumps(payload, ensure_ascii=False)[:16000], completeness='unknown',
                    explanation='Table format is not recognized. A source manager can configure result field paths.')


def export(run, format):
    if format == 'json':
        return json.dumps(run, ensure_ascii=False, indent=2).encode('utf-8'), 'application/json', 'json'
    if format != 'csv' or not run['result'].get('table'):
        raise ValueError('Choose JSON, or CSV for a table result.')
    table = run['result']['table']
    stream = io.StringIO(newline='')
    writer = csv.writer(stream)

    def cell(value):
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@', '\t', '\r', '\n')):
            value = "'" + value
        return value

    writer.writerow([cell(v) for v in table['columns']])
    writer.writerows([cell(v) for v in row] for row in table['rows'])
    return ('\ufeff' + stream.getvalue()).encode('utf-8'), 'text/csv; charset=utf-8', 'csv'
