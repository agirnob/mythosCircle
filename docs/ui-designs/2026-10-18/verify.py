#!/usr/bin/env python3
"""Check frozen artifact hashes, literal code extraction, and JavaScript syntax."""
from pathlib import Path
import hashlib
import json
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
errors = []
manifest = json.loads((ROOT / 'manifest.json').read_text())
checksums = json.loads((ROOT / 'checksums.json').read_text())
for name, expected in checksums.items():
    path = (ROOT / name).resolve()
    if not path.is_relative_to(ROOT) or not path.is_file():
        errors.append(f'Missing or unsafe file: {name}')
    elif hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        errors.append(f'Frozen file changed: {name}')
actual = {str(p.relative_to(ROOT)) for p in ROOT.rglob('*') if p.is_file() and p.name != 'checksums.json'}
if actual != set(checksums):
    errors.append(f'Unlisted files: {sorted(actual - set(checksums))}; missing: {sorted(set(checksums) - actual)}')
node = shutil.which('node')
if not node:
    errors.append('Node.js 22 is required for JavaScript syntax verification')
else:
    version = subprocess.check_output([node, '--version'], text=True).strip()
    if not version.startswith('v22.'):
        errors.append(f'Expected Node.js 22, got {version}')
for design in manifest['designs']:
    source = ROOT / design['source']
    if not source.is_file():
        errors.append(f'Missing source: {design["key"]}')
        continue
    raw = source.read_bytes()
    if hashlib.sha256(raw).hexdigest() != design['sha256']:
        errors.append(f'Source differs from recorded design: {design["key"]}')
    text = raw.decode()
    styles = re.findall(r'<style\b[^>]*>(.*?)</style>', text, re.S | re.I)
    scripts = re.findall(r'<script\b([^>]*)>(.*?)</script>', text, re.S | re.I)
    body = re.search(r'<body\b[^>]*>(.*?)</body>', text, re.S | re.I)
    if not body or not text.lstrip().lower().startswith('<!doctype html>'):
        errors.append(f'Incomplete HTML document: {design["key"]}')
        continue
    parts = {
        'styles.css': '\n\n'.join(styles) + '\n',
        'interactions.js': '\n\n'.join(code for attrs, code in scripts if not re.search(r'\bsrc\s*=', attrs)) + '\n',
        'template.html': re.sub(r'<script\b[^>]*>.*?</script>', '', body.group(1), flags=re.S | re.I) + '\n',
    }
    for filename, expected in parts.items():
        path = ROOT / design['extractedCode'] / filename
        if not path.is_file() or path.read_text() != expected:
            errors.append(f'Extracted {filename} differs: {design["key"]}')
    if node:
        result = subprocess.run([node, '--check', str(ROOT / design['extractedCode'] / 'interactions.js')], capture_output=True, text=True)
        if result.returncode:
            errors.append(f'Invalid JavaScript: {design["key"]}\n{result.stderr}')
if errors:
    print('\n'.join(errors), file=sys.stderr)
    raise SystemExit(1)
print(f'OK: {len(checksums)} frozen files; {len(manifest["designs"])} exact sources and extracted code sets; JavaScript syntax under Node 22.')
