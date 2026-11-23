"""Leitores de XLS BIFF (SALUX), XLSX e CSV sem dependências externas.

Este parser XLS cobre planilhas simples exportadas pelo SALUX (LABEL, NUMBER,
RK e MULRK). Strings compartilhadas e fórmulas exigem exportar valores em CSV/XLSX.
Se uma planilha usar recursos BIFF não suportados,
falha com mensagem explícita; nunca importa silenciosamente dados incompletos.
"""
from __future__ import annotations
import csv
import io
import re
import struct
import unicodedata
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

OLE_MAGIC = bytes.fromhex('d0cf11e0a1b11ae1')
MAX_FILE_BYTES = 25*1024*1024
MAX_ROWS = 100_000
MAX_COLS = 201
MAX_CELLS = 2_000_000

def _clean_text(x):
    if x is None: return ''
    if isinstance(x, float) and x.is_integer(): return str(int(x))
    return str(x).replace('\x00','').strip()

def _num(x):
    if isinstance(x,(int,float)): return x
    return x

def _ole_book(path):
    data=Path(path).read_bytes()
    if data[:8]!=OLE_MAGIC: raise ValueError('Arquivo .xls não é um Excel binário válido')
    if len(data)<512: raise ValueError('XLS incompleto')
    sector_size=1<<struct.unpack_from('<H', data,30)[0]
    if sector_size not in (512,4096): raise ValueError('Tamanho de setor XLS não suportado')
    nfat=struct.unpack_from('<I',data,44)[0]
    if nfat>109: raise ValueError('XLS OLE com FAT estendida não suportada')
    fat_sectors=struct.unpack_from('<109I',data,76)[:nfat]
    def sector(i,required=None):
        a=(i+1)*sector_size
        # SALUX omite o padding do último setor do stream Book. Aceitar apenas
        # quando TODOS os bytes declarados no diretório estiverem presentes.
        needed=sector_size if required is None else min(sector_size,required)
        if needed<=0 or a+needed>len(data):raise ValueError('Setor XLS truncado ou fora do arquivo')
        return data[a:a+sector_size]
    fat=[]
    for s in fat_sectors: fat.extend(struct.unpack('<'+str(sector_size//4)+'I',sector(s)))
    def read_chain(i,max_bytes=None):
        out=bytearray();seen=set()
        while i<0xFFFFFFFA:
            if i>=len(fat) or i in seen:raise ValueError('Corrente FAT inválida no XLS')
            seen.add(i)
            out.extend(sector(i,None if max_bytes is None else max_bytes-len(out)))
            i=fat[i]
            if len(out)>128*1024*1024:raise ValueError('XLS excede limite de segurança')
        if max_bytes is not None and len(out)<max_bytes:raise ValueError('Stream XLS menor que o tamanho declarado')
        return bytes(out[:max_bytes] if max_bytes else out)
    root=read_chain(struct.unpack_from('<I',data,48)[0])
    for off in range(0,len(root)-127,128):
        ent=root[off:off+128]; size=struct.unpack_from('<H',ent,64)[0]
        if size<2 or size>64:continue
        name=ent[:size-2].decode('utf-16le','replace')
        if name in ('Book','Workbook'):
            stream_size=struct.unpack_from('<Q',ent,120)[0]
            if stream_size<4096: raise ValueError('XLS pequeno com MiniFAT ainda não suportado')
            return read_chain(struct.unpack_from('<I',ent,116)[0],stream_size)
    raise ValueError('Stream Book não encontrado dentro do XLS')

def _decode_label(buf):
    if len(buf)<8:return ''
    n=struct.unpack_from('<H',buf,6)[0]
    raw=buf[8:]
    # BIFF5 LABEL: tamanho em caracteres; algumas exportações usam final \x00.
    text=raw[:n].decode('cp1252','replace')
    return _clean_text(text)

def _unrk(value):
    is_int=value & 2; divide=value & 1
    if is_int:
        val=struct.unpack('<i',struct.pack('<I',value & 0xfffffffc))[0]/4
    else:
        val=struct.unpack('<d',struct.pack('<II',0,value & 0xfffffffc))[0]
    return val/100 if divide else val

def read_xls(path):
    data=_ole_book(path)
    cells={};pos=0;count=0;sheet_started=False
    while pos+4<=len(data):
        rec,length=struct.unpack_from('<HH',data,pos);pos+=4
        if pos+length>len(data):raise ValueError('Registro BIFF truncado')
        buf=data[pos:pos+length];pos+=length
        if rec==0x0809 and len(buf)>=4:
            # BOF type 0x0010 = worksheet. Start collecting only its cells.
            typ=struct.unpack_from('<H',buf,2)[0]
            if typ==0x0010: sheet_started=True
        if not sheet_started:continue
        if rec==0x000A:break  # EOF first worksheet
        if rec in (0x0006,0x0206,0x0406):raise ValueError('XLS contém fórmulas. Exporte somente valores em CSV ou XLSX.')
        if rec in (0x0204,0x0203,0x027E,0x0205,0x00FD) and len(buf)>=6:
            row,col=struct.unpack_from('<HH',buf,0)
            if row>65535 or col>255:continue
            if rec==0x0204: val=_decode_label(buf)
            elif rec==0x0203:val=struct.unpack_from('<d',buf,6)[0] if len(buf)>=14 else None
            elif rec==0x027E:val=_unrk(struct.unpack_from('<I',buf,6)[0]) if len(buf)>=10 else None
            elif rec==0x0205:
                if len(buf)<8 or buf[7]:raise ValueError('XLS contém erro de célula.')
                val=bool(buf[6])
            else:raise ValueError('XLS usa strings compartilhadas: exporte como CSV ou XLSX para esta versão')
            if val is not None:cells[(row,col)]=val
            count+=1
        elif rec==0x00BD:  # MulRK
            row,first_col=struct.unpack_from('<HH',buf,0)
            last_col=struct.unpack_from('<H',buf,len(buf)-2)[0]
            for i,col in enumerate(range(first_col,last_col+1)):
                base=4+i*6
                if base+6<=len(buf)-2:cells[(row,col)]=_unrk(struct.unpack_from('<I',buf,base+2)[0])
    if not cells:raise ValueError('Nenhuma célula reconhecida neste XLS')
    max_row=max(r for r,c in cells);max_col=max(c for r,c in cells)
    if max_row>=MAX_ROWS or max_col>=MAX_COLS or (max_row+1)*(max_col+1)>MAX_CELLS:raise ValueError('Planilha muito grande para este importador')
    return [[cells.get((r,c),'') for c in range(max_col+1)] for r in range(max_row+1)]

def read_xlsx(path):
    ns='{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
    with zipfile.ZipFile(path) as z:
        if len(z.infolist())>1000 or sum(e.file_size for e in z.infolist())>64*1024*1024:raise ValueError('XLSX descompactado muito grande')
        names=z.namelist(); sheets=[x for x in names if re.fullmatch(r'xl/worksheets/sheet\d+\.xml',x)]
        if not sheets:raise ValueError('Não encontrei aba de dados no XLSX')
        shared=[]
        if 'xl/sharedStrings.xml' in names:
            tree=ET.fromstring(z.read('xl/sharedStrings.xml'))
            for si in tree.findall(ns+'si'):
                shared.append(''.join(t.text or '' for t in si.iter(ns+'t')))
        root=ET.fromstring(z.read(sorted(sheets,key=lambda p:int(re.search(r'sheet(\d+)',p).group(1)))[0]))
        rows=[];allocated=0
        for row in root.iter(ns+'row'):
            cells={}
            for c in row.findall(ns+'c'):
                ref=c.attrib.get('r','');match=re.match('[A-Z]+',ref)
                if not match:continue
                col=0
                for ch in match.group():col=col*26+ord(ch)-64
                col-=1
                if col>=MAX_COLS:raise ValueError('Coluna XLSX acima do limite suportado.')
                if col in cells:raise ValueError('Célula repetida no XLSX.')
                if c.find(ns+'f') is not None:raise ValueError('XLSX contém fórmulas. Exporte ou cole somente valores antes de importar.')
                val=c.find(ns+'v');t=c.attrib.get('t','');inline=c.find(ns+'is')
                if t=='e':raise ValueError('XLSX contém erro de célula.')
                raw=val.text if val is not None else ''
                if t=='s' and raw: value=shared[int(raw)]
                elif t=='inlineStr' and inline is not None:value=''.join(v.text or '' for v in inline.iter(ns+'t'))
                elif raw and t not in ('str','e'):
                    try:value=float(raw)
                    except ValueError:value=raw
                else:value=raw
                cells[col]=value
            if cells:
                row_index=int(row.attrib.get('r',str(len(rows)+1)))-1
                if row_index< len(rows) or row_index>=MAX_ROWS or row_index>len(rows)+20000:raise ValueError('Número de linha inesperado ou acima do limite.')
                allocated+=max(cells)+1
                if allocated>MAX_CELLS:raise ValueError('Planilha excede limite de células.')
                while len(rows)<row_index:rows.append([])
                rows.append([cells.get(i,'') for i in range(max(cells)+1)])
        return rows

def read_csv(path):
    raw=Path(path).read_bytes()
    for enc in ('utf-8-sig','cp1252','latin1'):
        try:text=raw.decode(enc);break
        except UnicodeError:continue
    dialect=csv.Sniffer().sniff(text[:8192],delimiters=',;\t|') if text else csv.excel
    rows=[];cells=0
    for row in csv.reader(io.StringIO(text),dialect):
        cells+=len(row)
        if len(row)>MAX_COLS or len(rows)>=MAX_ROWS or cells>MAX_CELLS:raise ValueError('CSV excede limite de linhas ou colunas.')
        rows.append(row)
    return rows

def read_table(path):
    if not 0<Path(path).stat().st_size<=MAX_FILE_BYTES:raise ValueError('Arquivo vazio ou acima de 25 MB.')
    ext=Path(path).suffix.lower()
    try:
        if ext=='.xls':return read_xls(path)
        if ext=='.xlsx':return read_xlsx(path)
        if ext=='.csv':return read_csv(path)
    except (csv.Error,zipfile.BadZipFile,ET.ParseError,struct.error,IndexError,KeyError) as exc:
        raise ValueError('Arquivo inválido ou formato não suportado. Exporte novamente como CSV.') from exc
    raise ValueError('Aceitamos arquivos .xls, .xlsx ou .csv')

def _headers(values):
    seen={};out=[]
    for idx,v in enumerate(values):
        key=unicodedata.normalize('NFD',_clean_text(v).lower())
        key=''.join(ch for ch in key if not unicodedata.combining(ch)).replace(' ','_') or f'coluna_{idx+1}'
        key={'codigo_material':'cd_material','cod_material':'cd_material','codigo':'cd_material'}.get(key,key)
        seen[key]=seen.get(key,0)+1
        out.append(key if seen[key]==1 else f'{key}_{seen[key]}')
    return out

def parse_report(path):
    matrix=read_table(path)
    # Encontra cabeçalho com chave material e saldo/quantidade.
    header_idx=None
    for i,row in enumerate(matrix[:30]):
        keys=_headers(row)
        if 'cd_material' in keys:
            header_idx=i;break
    if header_idx is None:raise ValueError('Coluna cd_material não localizada; confira se é o relatório correto do SALUX')
    headers=_headers(matrix[header_idx]);entries=[]
    for raw in matrix[header_idx+1:]:
        if not any(_clean_text(x) for x in raw):continue
        entry={headers[j]:_num(raw[j] if j<len(raw) else '') for j in range(len(headers))}
        if not _clean_text(entry.get('cd_material','')):raise ValueError('Linha preenchida sem código de material. Corrija ou remova rodapés antes de importar.')
        entries.append(entry)
    if not entries:raise ValueError('Arquivo sem registros de materiais')
    h=set(headers)
    if {'qt_estoque','vl_custo_medio','qt_consumo_dia'} <= h: kind='estoque'
    elif {'mes','qt_consumida'}<=h or {'competencia','quantidade'}<=h:kind='consumo_mensal'
    else:raise ValueError('Colunas não reconhecidas. Esperado saldo/consumo diário ou consumo por competência')
    return kind,entries,headers
