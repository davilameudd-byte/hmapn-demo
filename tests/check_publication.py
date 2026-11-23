"""Confere o pacote público sem abrir bancos ou documentos operacionais.

Inclui arquivos novos não ignorados para detectar vazamentos antes do commit.
"""
from html.parser import HTMLParser
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.mode = None
        self.assets = []

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == 'meta' and attrs.get('name') == 'hmapn-mode':
            self.mode = attrs.get('content')
        if tag in ('script', 'img') and attrs.get('src'):
            self.assets.append(attrs['src'])
        if tag == 'link' and attrs.get('rel') == 'stylesheet':
            self.assets.append(attrs.get('href', ''))


def main():
    errors = []
    required = ('index.html', 'app.css', 'app.js', 'api.js', 'demo-data.js',
                'assets/fundo-hospitalar.png', 'local/servidor.py', 'local/leitor_salux.py')
    for name in required:
        if not (ROOT / name).is_file():
            errors.append(f'Recurso obrigatório ausente: {name}')
    if not (ROOT / 'index.html').exists():
        print('\n'.join(errors), file=sys.stderr)
        return 1

    page = Page()
    page.feed((ROOT / 'index.html').read_text('utf-8'))
    if page.mode != 'demo':
        errors.append('index.html público precisa declarar hmapn-mode=demo.')
    for asset in page.assets:
        if asset.startswith(('http:', 'https:', '//')):
            errors.append(f'Recurso depende de serviço externo: {asset}')
        elif asset and not asset.startswith('data:'):
            name = asset.split('?', 1)[0].split('#', 1)[0].lstrip('/')
            if not (ROOT / name).is_file():
                errors.append(f'Recurso HTML não encontrado: {asset}')

    result = subprocess.run(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'],
                            cwd=ROOT, capture_output=True, check=True)
    names = sorted(set(result.stdout.decode('utf-8').split('\0')) - {''})
    blocked_dirs = {'dados', 'backup', 'arquivos_processados', 'entrada', 'logs', 'erros',
                    'runtime', 'node_modules', '__pycache__'}
    blocked_extensions = {'.sqlite', '.sqlite3', '.db', '.log', '.pyc', '.exe', '.dll', '.pdf'}
    secret_patterns = [
        re.compile(r'\bgh[pousr]_[A-Za-z0-9]{20,}\b'),
        re.compile(r'\bgithub_pat_[A-Za-z0-9_]{30,}\b'),
        re.compile(r'\bsb_secret_[A-Za-z0-9_-]{20,}\b'),
        re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    ]
    for name in names:
        path = ROOT / name
        parts = [p.lower() for p in Path(name).parts]
        if any(p in blocked_dirs for p in parts):
            errors.append(f'Pasta operacional ou dependência no pacote público: {name}')
        if path.suffix.lower() in blocked_extensions or path.name.lower().endswith(('-wal', '-shm')):
            errors.append(f'Arquivo operacional/binário indevido: {name}')
        if path.name.lower() == 'configuracao.json' or (path.name.startswith('.env') and path.name != '.env.example'):
            errors.append(f'Configuração privada no pacote público: {name}')
        if path.suffix.lower() in {'.csv', '.xls', '.xlsx'} and not name.startswith(('examples/', 'tests/fixtures/')):
            errors.append(f'Planilha fora das pastas de exemplos fictícios: {name}')
        if not path.is_file() or path.suffix.lower() not in {'.py', '.js', '.json', '.html', '.css', '.md', '.txt', '.yml', '.yaml', '.bat', '.ps1'}:
            continue
        content = path.read_text('utf-8-sig')
        if any(pattern.search(content) for pattern in secret_patterns):
            errors.append(f'Possível credencial privada em: {name}')
        if path.suffix.lower() in {'.py', '.js', '.json', '.html', '.bat', '.ps1'}:
            if re.search(r'[A-Za-z]:[\\/]+Users[\\/]+(?!Public\b|<|SEU_USUARIO\b)[^\\/\s]+', content, re.I):
                errors.append(f'Caminho pessoal fixo em código: {name}')
        if path.suffix.lower() == '.js' and re.search(r'window\.fetch\s*=', content):
            errors.append(f'Interceptação global de fetch em: {name}')

    if errors:
        print('Publicação bloqueada:\n' + '\n'.join('- ' + error for error in errors), file=sys.stderr)
        return 1
    print(f'Publicação conferida: {len(names)} arquivos, modo demo, recursos presentes e nenhum vazamento detectado pelas regras automáticas.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
