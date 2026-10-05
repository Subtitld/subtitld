"""No function both assigns `_` and calls the translation function `_()`.

Assigning `_` anywhere in a function (`_, x = pair`, `for _ in ...`) makes it
a local for the WHOLE function, so a `_('some.key')` elsewhere in it raises
UnboundLocalError on any path that did not run the assignment first. That
crashed the timeline's paint (its empty-state label) whenever no recording
was pending. This scans the source for the pattern.

Standalone script, like the other suites here.
"""
import ast
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / 'src' / 'subtitld'
offenders = []
for path in sorted(SRC.rglob('*.py')):
    tree = ast.parse(path.read_text(encoding='utf-8'))
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        assigns = calls = False
        for node in ast.walk(fn):
            if isinstance(node, ast.Name) and node.id == '_' and isinstance(node.ctx, ast.Store):
                assigns = True
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == '_':
                calls = True
        if assigns and calls:
            offenders.append(f'{path.relative_to(SRC.parent)}:{fn.lineno} {fn.name}')

for offender in offenders:
    print(f'  [FAIL] assigns `_` and calls `_()`: {offender}')
print('FAILED' if offenders else 'ALL PASS')
sys.exit(1 if offenders else 0)
