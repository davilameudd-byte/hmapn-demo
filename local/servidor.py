"""HMAPN - Importação de relatórios SALUX + painel local.

Python 3.10+, zero dependências externas, SQLite local, importação por pasta.
Configuração por ambiente: HMAPN_DATA_DIR, HMAPN_INPUT_DIR, HMAPN_PORT e
HMAPN_SCAN_SECONDS. Acesso somente neste computador, sem serviços pagos.
"""
from __future__ import annotations
import csv
import io
import math
import re
import tempfile
from contextlib import contextmanager
import datetime as dt
import hashlib
import json
import os
import secrets
import sqlite3
import threading
import time
import urllib.parse
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from leitor_salux import parse_report

SOURCE_ROOT=Path(__file__).resolve().parent
DATA_ROOT=Path(os.environ.get('HMAPN_DATA_DIR', str(SOURCE_ROOT.parent/'runtime'))).expanduser().resolve()
ROOT=DATA_ROOT
DEFAULT={
    'pasta_relatorios':str(ROOT/'Entrada'),
    'host':'127.0.0.1',
    'porta':8765,
    'segundos_verificacao':30,
    'limite_critico_dias':7,
    'limite_atencao_dias':30,
    'limite_excesso_dias':120,
}

def config_load():
    # Configuração externa por ambiente; nenhum caminho pessoal no código.
    return DEFAULT.copy()

CFG=config_load()
INPUT_DIR=Path(os.environ.get('HMAPN_INPUT_DIR',CFG['pasta_relatorios'])).expanduser().resolve()
DB=ROOT/'DADOS'/'hmapn.sqlite3'
STATIC=SOURCE_ROOT.parent
LOCK=threading.RLock()
STOP=threading.Event()
CSRF_TOKEN=secrets.token_urlsafe(32)
MAX_FILE_BYTES=25*1024*1024
STATE={'last_scan':None,'last_scan_error':None,'files_seen':0}

@contextmanager
def dbopen():
    conn=sqlite3.connect(DB, timeout=20)
    conn.row_factory=sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA busy_timeout=20000')
    try:
        with conn:yield conn
    finally:conn.close()

def initdb():
    for sub in ('DADOS','BACKUP','ARQUIVOS_PROCESSADOS','ERROS','LOGS','TEMP'):
        (ROOT/sub).mkdir(parents=True,exist_ok=True)
    INPUT_DIR.mkdir(parents=True,exist_ok=True)
    with dbopen() as conn:
        conn.executescript('''
        CREATE TABLE IF NOT EXISTS imports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL, filename TEXT NOT NULL, content_hash TEXT NOT NULL,
            source_day TEXT NOT NULL, file_sha256 TEXT NOT NULL, file_modified TEXT NOT NULL,
            imported_at TEXT NOT NULL, row_count INTEGER NOT NULL,
            UNIQUE(kind,source_day,content_hash)
        );
        CREATE TABLE IF NOT EXISTS estoque (
            import_id INTEGER NOT NULL REFERENCES imports(id),
            estoque TEXT NOT NULL, material TEXT NOT NULL, descricao TEXT,
            grupo TEXT, grupo_compra TEXT, local TEXT, unidade TEXT,
            saldo REAL NOT NULL, custo REAL NOT NULL, cmd REAL NOT NULL,
            curva_salux TEXT, fornecedor TEXT,
            PRIMARY KEY(import_id,estoque,material)
        );
        CREATE TABLE IF NOT EXISTS consumo_mensal (
            import_id INTEGER NOT NULL REFERENCES imports(id),
            material TEXT, competencia TEXT, qtd REAL,
            PRIMARY KEY(import_id,material,competencia)
        );
        CREATE TABLE IF NOT EXISTS arquivos_vistos (
            filepath TEXT PRIMARY KEY, filesize INTEGER, mtime_ns INTEGER,
            status TEXT, mensagem TEXT, verificado_em TEXT
        );
        CREATE TABLE IF NOT EXISTS eventos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT, file_name TEXT, status TEXT, message TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_estoque_material ON estoque(material);
        ''')

def now():return dt.datetime.now().astimezone().isoformat(timespec='seconds')
def numeric(x):
    if isinstance(x,bool) or x is None:raise ValueError('Valor numérico ausente/inválido.')
    if isinstance(x,(int,float)):value=float(x)
    elif isinstance(x,str):
        text=x.strip().replace(' ','').replace('\u00a0','')
        if not text:raise ValueError('Campo numérico em branco. Informe zero somente quando confirmado.')
        if ',' in text:
            if not re.fullmatch(r'[+-]?(?:\d+|\d{1,3}(?:\.\d{3})+),\d+',text):raise ValueError('Número inválido: '+text)
            text=text.replace('.','').replace(',','.')
        elif not re.fullmatch(r'[+-]?\d+(?:\.\d+)?',text):raise ValueError('Número inválido: '+text)
        value=float(text)
    else:raise ValueError('Tipo numérico inválido.')
    if not math.isfinite(value) or abs(value)>1_000_000_000_000:raise ValueError('Número não finito ou acima do limite.')
    return value

def keystr(x):
    if x is None:return ''
    if isinstance(x,(int,float)):
        n=numeric(x)
        if not n.is_integer():raise ValueError('Código numérico precisa ser inteiro.')
        return str(int(n))
    return str(x).strip().strip('\x00')

def textfield(x):return '' if x is None else str(x).strip().strip('\x00')

def event(conn,filename,status,message):
    conn.execute('INSERT INTO eventos(created_at,file_name,status,message) VALUES(?,?,?,?)',(now(),filename,status,message))

def _recognized_rows(kind,entries):
    if kind=='estoque':
        rows=[]
        for rec in entries:
            code=keystr(rec.get('cd_material'))
            loc=keystr(rec.get('cd_estoque'))
            if not code:raise ValueError('Registro preenchido sem código de material.')
            saldo,custo,cmd=(numeric(rec.get(k)) for k in ('qt_estoque','vl_custo_medio','qt_consumo_dia'))
            if custo<0 or cmd<0:raise ValueError('Custo médio e consumo diário não podem ser negativos.')
            rows.append((loc,code,textfield(rec.get('ds_material')),textfield(rec.get('ds_gr_estocagem')),
                         textfield(rec.get('ds_gr_compra')),textfield(rec.get('ds_estoque')),
                         textfield(rec.get('sigla')),saldo,custo,cmd,
                         textfield(rec.get('id_abc')),textfield(rec.get('ult_fornecedor'))))
        if len({(r[0],r[1]) for r in rows})!=len(rows):raise ValueError('Material duplicado no mesmo estoque. Consolide as linhas.')
        return sorted(rows,key=lambda x:(x[0],x[1]))
    rows=[]
    for rec in entries:
        code=keystr(rec.get('cd_material'))
        if not code:raise ValueError('Registro preenchido sem código de material.')
        mes=textfield(rec.get('mes',rec.get('competencia')))
        qtd=numeric(rec.get('qt_consumida',rec.get('quantidade')))
        if qtd<0:raise ValueError('Consumo mensal não pode ser negativo.')
        m=re.fullmatch(r'(\d{4})[-/](0[1-9]|1[0-2])',mes)
        r=re.fullmatch(r'(0[1-9]|1[0-2])/(\d{4})',mes)
        if m:mes=m[1]+'-'+m[2]
        elif r:mes=r[2]+'-'+r[1]
        else:raise ValueError('Competência inválida. Use AAAA-MM ou MM/AAAA.')
        if code and mes:rows.append((code,mes,qtd))
    if len({(r[0],r[1]) for r in rows})!=len(rows):raise ValueError('Material duplicado na mesma competência.')
    return sorted(rows)

def import_file(file):
    file=Path(file)
    if file.is_symlink() or not file.is_file():raise ValueError('Selecione um arquivo regular.')
    before=file.stat()
    if not 0<before.st_size<=MAX_FILE_BYTES:raise ValueError('Arquivo vazio ou acima de 25 MB.')
    kind,entries,headers=parse_report(file)
    # O arquivo de exemplo CONSUMO.xls tem estrutura de posição (e muda só o saldo).
    # Não deve gerar evolução mensal falsa nem substituir a posição principal.
    if kind=='estoque' and 'consumo' in file.stem.lower():
        kind='estoque_auxiliar'
    rows=_recognized_rows('estoque' if kind=='estoque_auxiliar' else kind,entries)
    if not rows: raise ValueError('Nenhuma linha válida para importação')
    canonical=json.dumps([kind,rows],ensure_ascii=False,sort_keys=True,separators=(',',':'))
    content_hash=hashlib.sha256(canonical.encode('utf-8')).hexdigest()
    raw=file.read_bytes()
    after=file.stat()
    if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise ValueError('Arquivo alterado durante a leitura; aguarde a cópia terminar.')
    filehash=hashlib.sha256(raw).hexdigest()
    modified_dt=dt.datetime.fromtimestamp(file.stat().st_mtime).astimezone()
    modified=modified_dt.isoformat(timespec='seconds')
    source_day=modified_dt.date().isoformat()
    # Impedir 'CONSUMO.xls' com colunas de posição de estoque de criar falso histórico de consumo mensal.
    note=''
    if kind=='estoque_auxiliar':
        note='ALERTA: nome CONSUMO, mas contém posição de estoque com consumo diário. Preservado à parte; não usado como consumo mensal nem como posição oficial.'
    with dbopen() as conn:
        existing=conn.execute('SELECT id,filename FROM imports WHERE content_hash=? AND kind=?',(content_hash,kind)).fetchone()
        if existing:
            msg=f'Dados idênticos ao arquivo {existing["filename"]}; não duplicados.'
            if note: msg+=' '+note
            event(conn,file.name,'repetido',msg)
            return 'repetido',msg
        # Preserva o original antes de confirmar a transação no banco.
        dest=ROOT/'ARQUIVOS_PROCESSADOS'/f'{filehash[:16]}_{file.name[:180]}'
        if not dest.exists() or hashlib.sha256(dest.read_bytes()).hexdigest()!=filehash:
            temporary=None
            try:
                with tempfile.NamedTemporaryFile(dir=dest.parent,prefix=filehash[:16]+'_',suffix='.tmp',delete=False) as archived:
                    temporary=Path(archived.name)
                    archived.write(raw);archived.flush();os.fsync(archived.fileno())
                os.replace(temporary,dest)
            finally:
                if temporary is not None:temporary.unlink(missing_ok=True)
        cur=conn.execute('INSERT INTO imports(kind,filename,content_hash,source_day,file_sha256,file_modified,imported_at,row_count) VALUES(?,?,?,?,?,?,?,?)',
                         (kind,file.name,content_hash,source_day,filehash,modified,now(),len(rows)))
        import_id=cur.lastrowid
        if kind in ('estoque','estoque_auxiliar'):
            conn.executemany('INSERT INTO estoque (import_id,estoque,material,descricao,grupo,grupo_compra,local,unidade,saldo,custo,cmd,curva_salux,fornecedor) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                            [(import_id,*x) for x in rows])
        else:
            conn.executemany('INSERT INTO consumo_mensal(import_id,material,competencia,qtd) VALUES(?,?,?,?)',[(import_id,*x) for x in rows])
        msg=f'Importado: {len(rows)} materiais.' if kind in ('estoque','estoque_auxiliar') else f'Importado: {len(rows)} registros mensais.'
        if note:msg+=' '+note
        event(conn,file.name,'importado',msg)
    # Backup diário consistente; falha no backup não desfaz uma importação já salva.
    try:backup_db()
    except (OSError,sqlite3.Error):
        msg+=' Importação salva, mas o backup falhou; confira o espaço e as permissões da pasta BACKUP.'
        with dbopen() as conn:event(conn,file.name,'aviso',msg)
    return 'importado',msg

def scan_once(force_errors=False):
    with LOCK:
        try:
            INPUT_DIR.mkdir(parents=True,exist_ok=True)
            files=sorted((p for p in INPUT_DIR.iterdir() if p.is_file() and not p.is_symlink() and p.suffix.lower() in ('.xls','.xlsx','.csv') and not p.name.startswith(('~','.'))),key=lambda p:p.stat().st_mtime)
            STATE['files_seen']=len(files)
            for f in files:
                st=f.stat()
                if time.time()-st.st_mtime<3:continue # evita ler arquivo ainda sendo salvo
                with dbopen() as conn:
                    old=conn.execute('SELECT filesize,mtime_ns,status FROM arquivos_vistos WHERE filepath=?',(str(f),)).fetchone()
                if old and old['filesize']==st.st_size and old['mtime_ns']==st.st_mtime_ns and not (force_errors and old['status']=='erro'):continue
                try:status,msg=import_file(f)
                except Exception as exc:
                    status,msg='erro',str(exc)
                    with dbopen() as conn:event(conn,f.name,'erro',msg)
                    with (ROOT/'LOGS'/'erros.txt').open('a',encoding='utf-8') as log:log.write(f'{now()} | {f.name} | {msg}\n')
                with dbopen() as conn:
                    conn.execute('INSERT INTO arquivos_vistos(filepath,filesize,mtime_ns,status,mensagem,verificado_em) VALUES(?,?,?,?,?,?) ON CONFLICT(filepath) DO UPDATE SET filesize=excluded.filesize,mtime_ns=excluded.mtime_ns,status=excluded.status,mensagem=excluded.mensagem,verificado_em=excluded.verificado_em',
                                 (str(f),st.st_size,st.st_mtime_ns,status,msg,now()))
            STATE['last_scan_error']=None
        except Exception as exc:
            STATE['last_scan_error']=str(exc)
        STATE['last_scan']=now()

def scanner():
    while not STOP.is_set():
        scan_once(); STOP.wait(CFG['segundos_verificacao'])

def backup_db():
    today=dt.date.today().isoformat();target=ROOT/'BACKUP'/f'hmapn_{today}.sqlite3'
    tmp=target.with_suffix('.tmp')
    with dbopen() as src:
        dst=sqlite3.connect(tmp)
        try:src.backup(dst)
        finally:dst.close()
    os.replace(tmp,target)

def classify(item):
    if item['saldo']<=0:return 'zerado'
    if item['cmd']<=0:return 'sem_consumo'
    days=item['saldo']/item['cmd']
    if days<=float(CFG['limite_critico_dias']):return 'critico'
    if days<=float(CFG['limite_atencao_dias']):return 'atencao'
    if days>float(CFG['limite_excesso_dias']):return 'excesso'
    return 'normal'

def latest_snapshot(conn):
    imp=conn.execute('SELECT * FROM imports WHERE kind="estoque" ORDER BY file_modified DESC,id DESC LIMIT 1').fetchone()
    if not imp:return None,[]
    rows=[dict(x) for x in conn.execute('SELECT estoque,material,descricao,grupo,grupo_compra,local,unidade,saldo,custo,cmd,curva_salux,fornecedor FROM estoque WHERE import_id=?',(imp['id'],))]
    for x in rows:
        x['valor']=round(max(0,x['saldo'])*x['custo'],2)
        x['dias']=round(x['saldo']/x['cmd'],1) if x['cmd']>0 else None
        x['status']=classify(x)
        x['cmm_estimado']=round(x['cmd']*30,2)
    return dict(imp),rows

def build_summary():
    with dbopen() as conn:
        imp,items=latest_snapshot(conn)
        status=defaultdict(int);groups=defaultdict(lambda:{'nome':'','valor':0.,'qtd':0})
        for x in items:
            status[x['status']]+=1
            g=x['grupo'] or 'Sem grupo'
            groups[g]['nome']=g;groups[g]['valor']+=x['valor'];groups[g]['qtd']+=1
        total=sum(x['valor'] for x in items)
        estimated=sum(x['cmd']*30*x['custo'] for x in items if x['cmd']>0)
        sorted_items=sorted(items,key=lambda x:x['valor'],reverse=True)
        abc={'A':0,'B':0,'C':0}
        acc=0
        for x in sorted_items:
            acc+=x['valor']
            cls='A' if total>0 and (acc-x['valor'])/total<0.8 else 'B' if total>0 and (acc-x['valor'])/total<0.95 else 'C'
            abc[cls]+=1
        history=[]
        for r in conn.execute('''SELECT i.id, i.file_modified, i.imported_at, i.filename, COUNT(*) qtd, SUM(MAX(0,saldo)*custo) valor,
            SUM(CASE WHEN saldo<=0 THEN 1 ELSE 0 END) zerados,
            SUM(CASE WHEN cmd>0 AND saldo>0 AND saldo/cmd<=? THEN 1 ELSE 0 END) criticos
            FROM imports i JOIN estoque e ON e.import_id=i.id WHERE i.kind='estoque' GROUP BY i.id ORDER BY i.file_modified DESC,i.id DESC LIMIT 365''',(CFG['limite_critico_dias'],)):
            history.append({**dict(r),'valor':round(r['valor'] or 0,2)})
        events=[dict(r) for r in conn.execute('SELECT created_at,file_name,status,message FROM eventos ORDER BY id DESC LIMIT 15')]
        count=conn.execute('SELECT COUNT(*) n FROM imports').fetchone()['n']
        latest_monthly=conn.execute('SELECT COUNT(*) AS n FROM imports WHERE kind="consumo_mensal"').fetchone()['n']
    units=defaultdict(float)
    for item in items:units[item['unidade'] or 'Não informada']+=item['cmd']
    history.reverse()
    return {
        'mode':'local',
        'limites':{'critico':CFG['limite_critico_dias'],'atencao':CFG['limite_atencao_dias'],'excesso':CFG['limite_excesso_dias']},
        'origem':'Posição de Estoque SALUX',
        'ultimo':imp,'atualizado':imp['file_modified'] if imp else None,
        'total_itens':len(items),'valor_total':round(total,2),
        'cmd_unidades_total':round(next(iter(units.values())),2) if len(units)==1 else None,
        'cmd_por_unidade':[{'unidade':k,'valor':round(v,2)} for k,v in sorted(units.items())],
        'consumo_estimado_reais_mes':round(estimated,2),
        'status':dict(status),'grupos':sorted(groups.values(),key=lambda v:v['valor'],reverse=True),
        'abc':abc,'history':history,'events':events,'import_count':count,
        'consumo_mensal_disponivel':bool(latest_monthly),
        'pasta':str(INPUT_DIR),'scanned':STATE.copy(),
        'nota':'Consumo mensal em R$ é estimativa (qt_consumo_dia × 30 × custo médio). As datas das posições são as datas de alteração dos arquivos, não o consumo mensal realizado.'
    }

def _filtered_items(params):
    with dbopen() as conn:imp,rows=latest_snapshot(conn)
    search=(params.get('q',[''])[0]).lower().strip();status=params.get('status',['todos'])[0]
    grupo=params.get('grupo',[''])[0]
    if status not in ('todos','zerado','critico','atencao','normal','excesso','sem_consumo'):raise ValueError('Situação inválida.')
    if search:rows=[r for r in rows if search in (r['descricao']+' '+r['material']+' '+r['grupo']).lower()]
    if status!='todos':rows=[r for r in rows if r['status']==status]
    if grupo:rows=[r for r in rows if r['grupo']==grupo]
    severity={'zerado':0,'critico':1,'atencao':2,'normal':3,'excesso':4,'sem_consumo':5}
    rows.sort(key=lambda x:(severity.get(x['status'],9),x['dias'] if x['dias'] is not None else 10**12,-x['valor']))
    return imp,rows

def build_items(params):
    imp,rows=_filtered_items(params)
    limit=int(params.get('limit',['100'])[0]);offset=int(params.get('offset',['0'])[0])
    if not 1<=limit<=500 or offset<0:raise ValueError('Limite deve ser de 1 a 500; posição não negativa.')
    return {'rows':rows[offset:offset+limit],'total':len(rows),'offset':offset,'latest':imp['file_modified'] if imp else None}

def export_csv(params):
    _,rows=_filtered_items(params)
    fields=[('estoque','Código do estoque'),('material','Código do material'),('descricao','Descrição'),('grupo','Grupo'),('local','Estoque'),('unidade','Unidade'),('saldo','Saldo'),('custo','Custo médio'),('valor','Valor do saldo positivo'),('cmd','Consumo diário'),('dias','Cobertura em dias'),('status','Situação')]
    def cell(value):
        if isinstance(value,(int,float)):return str(value).replace('.',',')
        text='' if value is None else str(value)
        return "'"+text if text.lstrip().startswith(('=','+','-','@')) else text
    out=io.StringIO(newline='');writer=csv.writer(out,delimiter=';',quoting=csv.QUOTE_ALL)
    writer.writerow([label for _,label in fields])
    writer.writerows([[cell(row[key]) for key,_ in fields] for row in rows])
    return ('\ufeff'+out.getvalue()).encode('utf-8')

class Handler(BaseHTTPRequestHandler):
    server_version='HMAPN/2.0'
    def _auth(self):
        port=self.server.server_port
        hosts={f'127.0.0.1:{port}',f'localhost:{port}'}
        if self.client_address[0] not in ('127.0.0.1','::1') or self.headers.get('Host','') not in hosts:return False
        # Abrir um link do painel em outra aplicação é navegação legítima.
        # A exceção não permite fetch, iframe nem qualquer rota de dados/alteração.
        navigation=(self.command=='GET' and urllib.parse.urlsplit(self.path).path in ('/','/index.html')
                    and self.headers.get('Sec-Fetch-Mode')=='navigate'
                    and self.headers.get('Sec-Fetch-Dest')=='document')
        origin=self.headers.get('Origin')
        if origin is not None and origin not in {'http://'+host for host in hosts} and not navigation:return False
        if self.headers.get('Sec-Fetch-Site')=='cross-site' and not navigation:return False
        if self.command=='POST' and not secrets.compare_digest(self.headers.get('X-HMAPN-Token',''),CSRF_TOKEN):return False
        return True
    def _send(self,code,data,content_type='application/json; charset=utf-8',filename=None):
        raw=json.dumps(data,ensure_ascii=False,allow_nan=False).encode('utf-8') if not isinstance(data,bytes) else data
        self.send_response(code);self.send_header('Content-Type',content_type)
        self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer');self.send_header('X-Frame-Options','DENY')
        if filename:self.send_header('Content-Disposition','attachment; filename="'+filename+'"')
        self.end_headers();self.wfile.write(raw)
    def _upload(self,params):
        if self.headers.get('Transfer-Encoding'):raise ValueError('Envie o tamanho completo do arquivo.')
        length=int(self.headers.get('Content-Length','0'))
        if not 0<length<=MAX_FILE_BYTES:raise ValueError('Arquivo vazio ou acima de 25 MB.')
        filename=params.get('name',[''])[0]
        if not filename or len(filename)>180 or re.search(r'[<>:"/\\|?*\x00-\x1f]',filename) or filename.startswith(('.','~')):raise ValueError('Nome de arquivo inválido.')
        if Path(filename).suffix.lower() not in ('.csv','.xlsx','.xls'):raise ValueError('Envie CSV, XLSX ou XLS do SALUX.')
        self.connection.settimeout(30)
        raw=self.rfile.read(length)
        if len(raw)!=length:raise ValueError('Upload incompleto.')
        with LOCK:
            temporary=None
            try:
                with tempfile.NamedTemporaryFile(dir=ROOT/'TEMP',suffix=Path(filename).suffix,delete=False) as tmp:
                    tmp.write(raw);temporary=Path(tmp.name)
                kind,entries,_=parse_report(temporary)
                _recognized_rows(kind,entries) # Valida antes de arquivar/gravar o banco.
                dest=INPUT_DIR/filename
                if dest.exists():dest=INPUT_DIR/(secrets.token_hex(4)+'_'+filename)
                with dest.open('xb') as target:target.write(raw)
                status,message=import_file(dest)
                st=dest.stat()
                with dbopen() as conn:
                    conn.execute('''INSERT INTO arquivos_vistos(filepath,filesize,mtime_ns,status,mensagem,verificado_em)
                        VALUES(?,?,?,?,?,?) ON CONFLICT(filepath) DO UPDATE SET filesize=excluded.filesize,
                        mtime_ns=excluded.mtime_ns,status=excluded.status,mensagem=excluded.mensagem,verificado_em=excluded.verificado_em''',
                        (str(dest),st.st_size,st.st_mtime_ns,status,message,now()))
                STATE['last_scan']=now();STATE['last_scan_error']=None
                STATE['files_seen']=sum(p.is_file() and p.suffix.lower() in ('.csv','.xlsx','.xls') for p in INPUT_DIR.iterdir())
                self._send(201,{'ok':True,'status':status,'message':message})
            finally:
                if temporary is not None:temporary.unlink(missing_ok=True)
    def _handle(self):
        if not self._auth():
            self._send(403,{'erro':'Host, origem ou sessão inválida. Use o endereço local e recarregue o painel.'});return
        p=urllib.parse.urlparse(self.path)
        if self.command=='POST':
            if p.path=='/api/rescan':
                scan_once(force_errors=True);self._send(200,{'ok':STATE['last_scan_error'] is None,'last_scan':STATE['last_scan'],'erro':STATE['last_scan_error']});return
            if p.path=='/api/import':self._upload(urllib.parse.parse_qs(p.query));return
            if p.path=='/api/shutdown':
                self._send(200,{'ok':True});STOP.set()
                threading.Thread(target=self.server.shutdown,daemon=True).start();return
            self._send(404,{'erro':'Rota não encontrada'});return
        if p.path=='/api/summary':self._send(200,build_summary());return
        if p.path=='/api/items':
            try:self._send(200,build_items(urllib.parse.parse_qs(p.query)))
            except ValueError:self._send(400,{'erro':'Filtro inválido'})
            return
        if p.path=='/api/health':self._send(200,{'ok':True,'mode':'local','csrfToken':CSRF_TOKEN,'pasta':str(INPUT_DIR),'scan':STATE.copy(),'banco':'SQLite'});return
        if p.path=='/api/export':self._send(200,export_csv(urllib.parse.parse_qs(p.query)),'text/csv; charset=utf-8','HMAPN-estoque-filtrado.csv');return
        path={'/':'index.html','/index.html':'index.html','/app.css':'app.css','/app.js':'app.js','/api.js':'api.js','/demo-data.js':'demo-data.js','/examples/estoque-ficticio.csv':'examples/estoque-ficticio.csv'}.get(p.path)
        mime={'.html':'text/html; charset=utf-8','.css':'text/css; charset=utf-8','.js':'application/javascript; charset=utf-8','.csv':'text/csv; charset=utf-8','.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg','.svg':'image/svg+xml','.webp':'image/webp','.ico':'image/x-icon','.woff':'font/woff','.woff2':'font/woff2'}
        if p.path.startswith('/assets/') and re.fullmatch(r'/assets/[A-Za-z0-9_./-]+',p.path):
            candidate=(STATIC/p.path.lstrip('/')).resolve()
            if candidate.is_relative_to((STATIC/'assets').resolve()) and candidate.suffix.lower() in mime:path=candidate
        if path:
            full=(STATIC/path).resolve()
            if full.is_relative_to(STATIC) and full.is_file():
                content=full.read_bytes()
                if full.name=='index.html':
                    content=content.replace(b'<meta name="hmapn-mode" content="demo">',b'<meta name="hmapn-mode" content="local">')
                    content=content.replace(b'<meta name="hmapn-token" content="">',('<meta name="hmapn-token" content="'+CSRF_TOKEN+'">').encode())
                self._send(200,content,mime[full.suffix.lower()]);return
        self._send(404,{'erro':'Não encontrado'})
    def do_GET(self):
        try:self._handle()
        except ValueError as ex:self._send(400,{'erro':str(ex)})
        except (BrokenPipeError,ConnectionResetError,TimeoutError):pass
        except Exception:self._send(500,{'erro':'Falha local. Confira a pasta de dados e o espaço disponível.'})
    def do_POST(self):
        try:self._handle()
        except ValueError as ex:self._send(400,{'erro':str(ex)})
        except (BrokenPipeError,ConnectionResetError,TimeoutError):pass
        except Exception:self._send(500,{'erro':'Falha local. Confira a pasta de dados e o espaço disponível.'})
    def log_message(self,format,*args):pass

def main():
    print('HMAPN | Sistema local de indicadores, importação por pasta')
    host=os.environ.get('HMAPN_HOST',CFG.get('host','127.0.0.1'))
    if host not in ('localhost','127.0.0.1'):raise SystemExit('Esta versão aceita apenas acesso local em 127.0.0.1.')
    try:
        port=int(os.environ.get('HMAPN_PORT',8765));interval=int(os.environ.get('HMAPN_SCAN_SECONDS',30))
        if not 1<=port<=65535 or not 5<=interval<=3600:raise ValueError
    except ValueError:raise SystemExit('HMAPN_PORT: 1 a 65535. HMAPN_SCAN_SECONDS: 5 a 3600.')
    CFG['segundos_verificacao']=interval
    initdb()
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    STOP.clear();worker=threading.Thread(target=scanner,daemon=True);worker.start()
    print('Pasta monitorada:',INPUT_DIR)
    print('Banco SQL:',DB)
    print('Endereço local:',f'http://127.0.0.1:{port}/')
    print('Verificação a cada',CFG['segundos_verificacao'],'segundos')
    print('Ctrl+C para encerrar')
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:
        STOP.set();server.server_close();worker.join(timeout=30)

if __name__=='__main__':main()
