#!/usr/bin/env python3
"""Build tulir-telethon layer-228 schema + parse-only compatibility overlay.

Regenerates the api.tl overlay + telethon/tl/xcompat.py from a published base
schema and the current official schema page. Run when Telegram leaks new
out-of-layer constructors (TypeNotFoundError in prod), then `setup.py gen`.

Output (in the fork working tree):
  telethon_generator/data/api.tl — 228 base + `---types---` overlay section with
      renamed parse-only ctors: every RECEIVABLE type ctor id present in
      {official ∪ fork} but absent from 228, as `<name>_x<id>#<id> ... = Type;`
  telethon/tl/xcompat.py — converter: wraps each overlay class's from_reader to
      return the canonical 228 class (same-name predicate), dropping unknown
      fields, defaulting required-missing to None. No same-name base → raw.

Rationale: functions must match the declared LAYER 228 exactly (we send them);
types must parse the union of everything Telegram's servers actually send —
they demonstrably serialize hot types from their internal (beta) layer
regardless of the declared one (2026-07-13 message#3ae56482 incident; same
class as the July-2025 message#9815cec8 sync in this fork's history).
"""
import html
import re
import sys
from pathlib import Path

# usage: build_xcompat_overlay.py <base_api.tl> <official_schema.html> [fork_root]
#   base_api.tl          — published-layer schema to use as base (e.g. Codeberg
#                          Lonami/Telethon v1 telethon_generator/data/api.tl)
#   official_schema.html — saved copy of https://core.telegram.org/schema
#   fork_root            — this repo's root (default: parent of tools/)
FORK = Path(sys.argv[3]).resolve() if len(sys.argv) > 3 else Path(__file__).resolve().parent.parent
CB_API = Path(sys.argv[1])
OFFICIAL_HTML = Path(sys.argv[2])

DEF_RE = re.compile(r'^([\w.]+)#([0-9a-f]{5,8})\s+(.*?)=\s*([\w.<>]+);\s*$')


def parse_defs(text):
    """-> (types: {id: (name, full_line)}, functions: {id: (name, full_line)})"""
    types, funcs = {}, {}
    section = types
    for raw in text.splitlines():
        line = raw.strip()
        if line == '---functions---':
            section = funcs
            continue
        if line == '---types---':
            section = types
            continue
        m = DEF_RE.match(line)
        if not m:
            continue
        name, cid = m.group(1), m.group(2).lower().lstrip('0') or '0'
        if name == 'vector':
            continue
        if re.search(r'_x[0-9a-f]{5,8}$', name):
            continue  # already an overlay alias (idempotent re-runs)
        section[cid] = (name, line)
    return types, funcs


def snake_to_camel(name):
    # mirror telethon_generator's class naming (capitalize after _ and start)
    result = []
    up = True
    for c in name:
        if c == '_':
            up = True
        elif up:
            result.append(c.upper())
            up = False
        else:
            result.append(c)
    return ''.join(result)


def class_path(predicate):
    """TL predicate -> dotted generated class path relative to telethon.tl.types."""
    if '.' in predicate:
        ns, nm = predicate.split('.', 1)
        return f'{ns}.{snake_to_camel(nm)}'
    return snake_to_camel(predicate)


def main():
    cb_text = CB_API.read_text()
    cb_types, cb_funcs = parse_defs(cb_text)
    cb_ids = set(cb_types) | set(cb_funcs)
    cb_type_names = {n for n, _ in cb_types.values()}

    raw = OFFICIAL_HTML.read_text(encoding='utf-8', errors='replace')
    off_text = html.unescape(re.sub(r'<[^>]+>', '', raw))
    off_types, off_funcs = parse_defs(off_text)

    fork_api = FORK / 'telethon_generator/data/api.tl'
    fork_types, fork_funcs = parse_defs(fork_api.read_text())

    # overlay candidates: receivable TYPE ctors absent from 228.
    # official page wins over fork when both define the same id (identical anyway).
    overlay = {}
    for src, label in ((fork_types, 'fork'), (off_types, 'official')):
        for cid, (name, line) in src.items():
            if cid in cb_ids:
                continue
            if name.split('.')[-1].startswith('input'):  # client->server only
                continue
            overlay[cid] = (name, line, label)

    # build renamed overlay lines + converter pairs
    lines, pairs = [], []
    for cid in sorted(overlay):
        name, line, label = overlay[cid]
        if '.' in name:
            ns, nm = name.split('.', 1)
            new_name = f'{ns}.{nm}_x{cid}'
        else:
            new_name = f'{name}_x{cid}'
        new_line = line.replace(f'{name}#', f'{new_name}#', 1)
        lines.append(f'// [{label}] parse-only alias of {name}#{cid}')
        lines.append(new_line)
        base = class_path(name) if name in cb_type_names else None
        pairs.append((class_path(new_name), base, cid, name))

    marker = '// ===== parse-only overlay'
    overlay_block = (
        '\n---types---\n\n'
        f'{marker} (generated {__file__.split("/")[-1]}) =====\n'
        '// Receivable constructors Telegram servers send outside the declared\n'
        '// layer (beta leaks / transition traffic). Renamed to avoid predicate\n'
        '// collisions; telethon/tl/xcompat.py converts them to canonical 228\n'
        '// classes at read time. NEVER construct or send these.\n'
        + '\n'.join(lines) + '\n'
    )

    # 228 base: keep everything incl. its // LAYER 228 comment, append overlay
    assert '// LAYER 228' in cb_text
    new_api = cb_text.rstrip() + '\n' + overlay_block
    fork_api.write_text(new_api)

    # xcompat.py
    pair_lines = '\n'.join(
        f'    ({p[0]!r}, {p[1]!r}),  # {p[3]}#{p[2]}' for p in pairs
    )
    xcompat = f'''"""Parse-only compatibility for out-of-layer constructors (auto-generated).

Telegram's servers serialize hot types from their internal layer regardless of
the layer the client declares (observed 2026-07-13: message#3ae56482 leaked to
layer-211 clients; previously July 2025: message#9815cec8). The overlay
section of api.tl defines those constructors under aliased predicates; this
module rewrites each alias class's from_reader to return the canonical
LAYER-228 class of the same predicate, so downstream code only ever sees
standard types. Fields unknown to the canonical class are dropped;
canonical-required fields missing from the alias default to None. Aliases
with no canonical counterpart are left as-is (parseable, flow through raw).

Generated by tl-layer228-build.py — do not edit by hand.
"""

import inspect

from . import types

_PAIRS = [
{pair_lines},
]


def _resolve(dotted):
    obj = types
    for part in dotted.split('.'):
        obj = getattr(obj, part)
    return obj


def _wrap(alias_cls, base_name):
    # Base resolution is LAZY (first parse) and re-checked against the live
    # types module: telethon/tl/patched/__init__.py replaces types.Message &
    # co. with custom-mixin classes AFTER this module is imported — resolving
    # eagerly would hand downstream the unpatched generated classes.
    orig = alias_cls.from_reader.__func__
    memo = {{}}

    def from_reader(cls, reader):
        obj = orig(cls, reader)
        base_cls = _resolve(base_name)
        spec = memo.get(base_cls)
        if spec is None:
            optional, required = [], []
            for pname, p in inspect.signature(base_cls.__init__).parameters.items():
                if pname == 'self':
                    continue
                (optional if p.default is not inspect.Parameter.empty
                 else required).append(pname)
            spec = memo[base_cls] = (required, optional)
        required, optional = spec
        kwargs = {{name: getattr(obj, name, None) for name in required}}
        for name in optional:
            if hasattr(obj, name):
                kwargs[name] = getattr(obj, name)
        return base_cls(**kwargs)

    alias_cls.from_reader = classmethod(from_reader)


def _install():
    for alias_name, base_name in _PAIRS:
        if base_name is None:
            continue
        try:
            alias_cls = _resolve(alias_name)
            _resolve(base_name)
        except AttributeError:
            continue
        _wrap(alias_cls, base_name)


_install()
'''
    (FORK / 'telethon/tl/xcompat.py').write_text(xcompat)

    # hook the import (idempotent)
    init = FORK / 'telethon/tl/__init__.py'
    content = init.read_text()
    if 'xcompat' not in content:
        init.write_text(content.rstrip() + '\n\nfrom . import alltlobjects as _alltl  # noqa: register ids first\nfrom . import xcompat as _xcompat  # noqa: out-of-layer parse compat\n')

    n_conv = sum(1 for p in pairs if p[1])
    print(f'overlay: {len(pairs)} ctors ({n_conv} converted to canonical, {len(pairs) - n_conv} raw)')
    for p in pairs:
        if not p[1]:
            print('  RAW (no canonical base):', p[3], '#', p[2])


if __name__ == '__main__':
    main()
