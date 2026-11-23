"""Testes com dados fictícios e armazenamento temporário, sem rede externa.

Execute da raiz: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import csv
import http.client
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zipfile


REPO = Path(__file__).resolve().parents[1]
LOCAL = REPO / 'local'
EXAMPLE = REPO / 'examples' / 'estoque-ficticio.csv'
sys.path.insert(0, str(LOCAL))
from leitor_salux import parse_report, read_table


def write_xlsx(path, matrix):
    """Gera planilha mínima de teste, sem bibliotecas ou planilhas reais."""
    ns = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    worksheet = ET.Element('worksheet', xmlns=ns)
    rows = ET.SubElement(worksheet, 'sheetData')
    for row_number, values in enumerate(matrix, 1):
        row = ET.SubElement(rows, 'row', r=str(row_number))
        for col, value in enumerate(values):
            letters, number = '', col + 1
            while number:
                number, digit = divmod(number - 1, 26)
                letters = chr(65 + digit) + letters
            cell = ET.SubElement(row, 'c', r=f'{letters}{row_number}', t='inlineStr')
            ET.SubElement(ET.SubElement(cell, 'is'), 't').text = str(value)
    with zipfile.ZipFile(path, 'w') as book:
        book.writestr('xl/worksheets/sheet1.xml', ET.tostring(worksheet, encoding='utf-8'))


def write_biff5_xls(path, matrix, final_padding=True):
    """Gera OLE/BIFF5 simples com LABEL, formato suportado pelo importador."""
    def record(kind, payload):
        return struct.pack('<HH', kind, len(payload)) + payload
    stream = record(0x0809, struct.pack('<HH', 0x0500, 0x0010))
    for row, values in enumerate(matrix):
        for col, value in enumerate(values):
            text = str(value).encode('cp1252')
            stream += record(0x0204, struct.pack('<HHHH', row, col, 0, len(text)) + text)
    stream += record(0x000A, b'')
    size = max(4096, ((len(stream) + 511) // 512) * 512)
    count = size // 512
    header = bytearray(512)
    header[:8] = bytes.fromhex('d0cf11e0a1b11ae1')
    struct.pack_into('<H', header, 26, 3)
    struct.pack_into('<H', header, 28, 0xFFFE)
    struct.pack_into('<H', header, 30, 9)
    struct.pack_into('<H', header, 32, 6)
    struct.pack_into('<I', header, 44, 1)
    struct.pack_into('<I', header, 48, 1)
    struct.pack_into('<I', header, 56, 4096)
    struct.pack_into('<109I', header, 76, 0, *([0xFFFFFFFF] * 108))
    fat = [0xFFFFFFFF] * 128
    fat[0], fat[1] = 0xFFFFFFFD, 0xFFFFFFFE
    for index in range(2, count + 2):
        fat[index] = index + 1 if index < count + 1 else 0xFFFFFFFE
    directory = bytearray(512)
    name = 'Workbook\0'.encode('utf-16le')
    directory[:len(name)] = name
    struct.pack_into('<H', directory, 64, len(name))
    directory[66] = 2
    struct.pack_into('<I', directory, 116, 2)
    struct.pack_into('<Q', directory, 120, size if final_padding else len(stream))
    payload = stream.ljust(size, b'\0') if final_padding else stream
    path.write_bytes(bytes(header) + struct.pack('<128I', *fat) + bytes(directory) + payload)


class ParserTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='hmapn-parser-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_csv_ficticio_preserva_seis_materiais(self):
        kind, rows, headers = parse_report(EXAMPLE)
        self.assertEqual(kind, 'estoque')
        self.assertEqual(len(rows), 6)
        self.assertEqual(rows[0]['cd_material'], 'FICT-001')
        self.assertIn('qt_estoque', headers)

    def test_xlsx_ficticio_equivale_ao_csv(self):
        matrix = list(csv.reader(io.StringIO(EXAMPLE.read_text('utf-8')), delimiter=';'))
        file = self.root / 'estoque.xlsx'
        write_xlsx(file, matrix)
        self.assertEqual(parse_report(file), parse_report(EXAMPLE))

    def test_xls_biff5_ficticio_equivale_ao_csv(self):
        matrix = list(csv.reader(io.StringIO(EXAMPLE.read_text('utf-8')), delimiter=';'))
        file = self.root / 'estoque.xls'
        write_biff5_xls(file, matrix)
        self.assertEqual(parse_report(file), parse_report(EXAMPLE))

    def test_xls_aceita_setor_final_sem_padding_se_stream_esta_completa(self):
        matrix = list(csv.reader(io.StringIO(EXAMPLE.read_text('utf-8')), delimiter=';'))
        matrix = matrix[:1] + matrix[1:] * 4
        file = self.root / 'sem-padding.xls'
        write_biff5_xls(file, matrix, final_padding=False)
        data = file.read_bytes()
        declared = struct.unpack_from('<Q', data, 1024 + 120)[0]
        self.assertGreaterEqual(declared, 4096)
        self.assertNotEqual(len(data) % 512, 0)
        kind, rows, _ = parse_report(file)
        self.assertEqual(kind, 'estoque')
        self.assertEqual(len(rows), 24)
        self.assertEqual(rows[-1]['cd_material'], 'FICT-006')

    def test_xls_rejeita_ausencia_de_byte_da_stream_declarada(self):
        matrix = list(csv.reader(io.StringIO(EXAMPLE.read_text('utf-8')), delimiter=';'))
        file = self.root / 'truncado.xls'
        write_biff5_xls(file, matrix[:1] + matrix[1:] * 4, final_padding=False)
        file.write_bytes(file.read_bytes()[:-1])
        with self.assertRaises(ValueError):
            parse_report(file)

    def test_csv_decimal_portugues_e_aspas(self):
        file = self.root / 'decimal.csv'
        file.write_text('cd_material;ds_material;qt_estoque;vl_custo_medio;qt_consumo_dia\n'
                        'FICT-007;"Material; fictício";"10,5";"2,30";"0,5"\n', encoding='utf-8-sig')
        kind, rows, _ = parse_report(file)
        self.assertEqual(kind, 'estoque')
        self.assertEqual(rows[0]['ds_material'], 'Material; fictício')
        self.assertEqual(rows[0]['qt_estoque'], '10,5')

    def test_alias_de_codigo_material_preserva_registros(self):
        for alias in ('codigo_material', 'cód_material', 'codigo'):
            with self.subTest(alias=alias):
                file = self.root / 'alias.csv'
                file.write_text(EXAMPLE.read_text('utf-8').replace('cd_material;', alias + ';', 1), encoding='utf-8')
                kind, rows, _ = parse_report(file)
                self.assertEqual(kind, 'estoque')
                self.assertEqual(len(rows), 6)
                self.assertEqual(rows[0]['cd_material'], 'FICT-001')

    def test_xlsx_coluna_excessiva_rejeitada(self):
        file = self.root / 'coluna-excessiva.xlsx'
        with zipfile.ZipFile(file, 'w') as book:
            book.writestr('xl/worksheets/sheet1.xml',
                '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                '<sheetData><row r="1"><c r="XFD1" t="inlineStr"><is><t>teste</t></is></c></row>'
                '</sheetData></worksheet>')
        with self.assertRaises(ValueError):
            read_table(file)

    def test_consumo_mensal_reconhecido_sem_confundir_estoque(self):
        kind, rows, _ = parse_report(REPO / 'examples' / 'mensal-ficticio.csv')
        self.assertEqual(kind, 'consumo_mensal')
        self.assertEqual(len(rows), 2)

    def test_cabecalho_invalido_e_arquivo_vazio_falham(self):
        for contents in ('', 'coluna;outra\nvalor;exemplo\n'):
            with self.subTest(contents=contents):
                file = self.root / 'invalido.csv'
                file.write_text(contents, encoding='utf-8')
                with self.assertRaises((ValueError, csv.Error)):
                    parse_report(file)

    def test_xls_invalido_e_extensao_nao_suportada_falham(self):
        for name in ('falso.xls', 'falso.exe'):
            with self.subTest(name=name):
                file = self.root / name
                file.write_bytes(b'dados ficticios invalidos')
                with self.assertRaises(ValueError):
                    read_table(file)


class IsolatedBackend(unittest.TestCase):
    def setUp(self):
        # Falha antes do import se a implementação não garante isolamento.
        source = (LOCAL / 'servidor.py').read_text('utf-8')
        if 'HMAPN_DATA_DIR' not in source:
            self.fail('Backend ainda não oferece HMAPN_DATA_DIR; importação bloqueada por segurança.')
        self.directory = tempfile.TemporaryDirectory(prefix='hmapn-test-')
        self.root = Path(self.directory.name)
        self.addCleanup(self.directory.cleanup)
        self.env = patch.dict(os.environ, {
            'HMAPN_DATA_DIR': str(self.root),
            'HMAPN_INPUT_DIR': str(self.root / 'Entrada'),
            'HMAPN_HOST': '127.0.0.1',
            'HMAPN_PORT': '0',
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        spec = importlib.util.spec_from_file_location('hmapn_isolated_server', LOCAL / 'servidor.py')
        self.backend = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.backend)
        self.assertTrue(self.backend.DB.resolve().is_relative_to(self.root.resolve()))
        self.backend.initdb()
        self.backend.INPUT_DIR.mkdir(parents=True, exist_ok=True)

    def example(self, name='posicao-ficticia.csv', age=10):
        file = self.backend.INPUT_DIR / name
        shutil.copyfile(EXAMPLE, file)
        past = time.time() - age
        os.utime(file, (past, past))
        return file

    def import_count(self):
        with self.backend.dbopen() as connection:
            return connection.execute('SELECT COUNT(*) FROM imports').fetchone()[0]


class StorageTests(IsolatedBackend):
    def test_importacao_calcula_valores_e_seis_situacoes(self):
        status, _ = self.backend.import_file(self.example())
        self.assertEqual(status, 'importado')
        result = self.backend.build_summary()
        self.assertEqual(result['total_itens'], 6)
        self.assertEqual(result['valor_total'], 1252)
        self.assertEqual(result['consumo_estimado_reais_mes'], 450)
        self.assertEqual(result['status'], dict.fromkeys(
            ['zerado', 'critico', 'atencao', 'normal', 'excesso', 'sem_consumo'], 1))

    def test_numeros_invalidos_nao_viram_zero(self):
        for value in ('erro', '', 'NaN', 'inf', '-inf', float('nan'), float('inf'), None):
            with self.subTest(value=repr(value)), self.assertRaises(ValueError):
                self.backend.numeric(value)
        self.assertEqual(self.backend.numeric('1.234,50'), 1234.5)
        self.assertEqual(self.backend.numeric('0'), 0)

    def test_arquivo_com_valor_invalido_nao_deixa_importacao_parcial(self):
        for invalid in ('invalido', 'NaN', 'inf', '', '-2'):
            with self.subTest(value=invalid):
                file = self.example()
                text = file.read_text('utf-8').replace(';6;2;1;B;', f';6;{invalid};1;B;')
                file.write_text(text, encoding='utf-8')
                with self.assertRaises(ValueError):
                    self.backend.import_file(file)
                self.assertEqual(self.import_count(), 0)

    def test_copia_renomeada_nao_duplica_no_mesmo_dia(self):
        first = self.example()
        self.backend.import_file(first)
        copied = self.backend.INPUT_DIR / 'renomeado.csv'
        shutil.copy2(first, copied)
        status, _ = self.backend.import_file(copied)
        self.assertEqual(status, 'repetido')
        self.assertEqual(self.import_count(), 1)

    def test_linhas_duplicadas_rejeitadas_sem_importacao_parcial(self):
        file = self.example()
        lines = file.read_text('utf-8').splitlines()
        file.write_text('\n'.join(lines + [lines[1]]) + '\n', encoding='utf-8')
        with self.assertRaises((ValueError, self.backend.sqlite3.IntegrityError)):
            self.backend.import_file(file)
        self.assertEqual(self.import_count(), 0)

    def test_scan_persiste_e_nao_reimporta_arquivo_inalterado(self):
        file = self.example()
        original = file.read_bytes()
        self.backend.scan_once()
        self.assertEqual(self.import_count(), 1)
        self.backend.scan_once()
        self.assertEqual(self.import_count(), 1)
        self.backend.initdb()
        self.assertEqual(self.backend.build_summary()['total_itens'], 6)
        self.assertEqual(file.read_bytes(), original)
        self.assertIsNotNone(self.backend.STATE['last_scan'])

    def test_scan_registra_erro_e_preserva_base_anterior(self):
        self.backend.import_file(self.example())
        file = self.backend.INPUT_DIR / 'invalido.csv'
        file.write_text('errado;incorreto\n1;2\n', encoding='utf-8')
        os.utime(file, (time.time() - 10, time.time() - 10))
        self.backend.scan_once()
        result = self.backend.build_summary()
        self.assertEqual(result['total_itens'], 6)
        self.assertTrue(any(e['status'] == 'erro' for e in result['events']))

    def test_verificacao_forcada_repete_erros_sem_reprocessar_sucessos(self):
        self.example()
        with patch.object(self.backend, 'import_file', side_effect=ValueError('Falha temporária simulada')):
            self.backend.scan_once()
        self.assertEqual(self.import_count(), 0)
        self.backend.scan_once()
        self.assertEqual(self.import_count(), 0)
        self.backend.scan_once(force_errors=True)
        self.assertEqual(self.import_count(), 1)
        with patch.object(self.backend, 'import_file', wraps=self.backend.import_file) as imported:
            self.backend.scan_once(force_errors=True)
            imported.assert_not_called()

    def test_filtros_e_paginacao(self):
        self.backend.import_file(self.example())
        filtered = self.backend.build_items({'q': ['FICT-002'], 'status': ['critico']})
        self.assertEqual(filtered['total'], 1)
        self.assertEqual(filtered['rows'][0]['material'], 'FICT-002')
        page1 = self.backend.build_items({'limit': ['2'], 'offset': ['0']})
        page2 = self.backend.build_items({'limit': ['2'], 'offset': ['2']})
        self.assertEqual(page1['total'], 6)
        self.assertEqual(len(page1['rows']), 2)
        self.assertEqual(len(page2['rows']), 2)
        self.assertFalse({r['material'] for r in page1['rows']} & {r['material'] for r in page2['rows']})

    def test_consumo_mensal_nao_substitui_posicao(self):
        self.backend.import_file(self.example())
        file = self.backend.INPUT_DIR / 'mensal-ficticio.csv'
        shutil.copyfile(REPO / 'examples' / 'mensal-ficticio.csv', file)
        self.backend.import_file(file)
        summary = self.backend.build_summary()
        self.assertEqual(summary['total_itens'], 6)
        self.assertTrue(summary['consumo_mensal_disponivel'])

    def test_falha_ao_arquivar_original_nao_confirma_importacao(self):
        file = self.example()
        with patch.object(self.backend.os, 'replace', side_effect=OSError('Falha de escrita simulada')):
            with self.assertRaises(OSError):
                self.backend.import_file(file)
        self.assertEqual(self.import_count(), 0)
        self.assertEqual(list((self.backend.ROOT / 'ARQUIVOS_PROCESSADOS').iterdir()), [])
        self.assertEqual(file.read_bytes(), EXAMPLE.read_bytes())

    def test_falha_no_backup_preserva_importacao_e_registra_aviso(self):
        with patch.object(self.backend, 'backup_db', side_effect=OSError('Sem espaço simulado')):
            status, message = self.backend.import_file(self.example())
        self.assertEqual(status, 'importado')
        self.assertIn('backup', message.lower())
        self.assertEqual(self.import_count(), 1)
        self.assertEqual(self.backend.build_summary()['total_itens'], 6)
        self.assertTrue(any(e['status'] == 'aviso' for e in self.backend.build_summary()['events']))


class APITests(IsolatedBackend):
    def setUp(self):
        super().setUp()
        self.server = self.backend.ThreadingHTTPServer(('127.0.0.1', 0), self.backend.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .02}, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.port = self.server.server_address[1]
        self.origin = f'http://127.0.0.1:{self.port}'
        status, raw, _ = self.request('/api/health')
        self.assertEqual(status, 200)
        self.health = json.loads(raw)
        self.token = self.health['csrfToken']

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def request(self, path, method='GET', data=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        try:
            connection.request(method, path, body=data, headers=headers or {})
            response = connection.getresponse()
            return response.status, response.read(), dict(response.getheaders())
        finally:
            connection.close()

    def auth_headers(self, **extra):
        return {'X-HMAPN-Token': self.token, 'Origin': self.origin, **extra}

    def test_health_identifica_modo_local_e_banco(self):
        self.assertTrue(self.health['ok'])
        self.assertEqual(self.health['mode'], 'local')
        self.assertEqual(self.health['banco'], 'SQLite')
        self.assertGreaterEqual(len(self.token), 32)

    def test_shutdown_exige_token_e_encerra_servidor(self):
        status, _, _ = self.request('/api/shutdown', 'POST')
        self.assertEqual(status, 403)
        status, body, _ = self.request('/api/shutdown', 'POST', headers=self.auth_headers())
        self.assertEqual(status, 200, body)
        self.thread.join(timeout=3)
        self.assertFalse(self.thread.is_alive(), 'Servidor deve parar após encerramento autorizado.')

    def test_host_e_origem_externos_bloqueados(self):
        status, _, _ = self.request('/api/health', headers={'Host': 'host-malicioso.invalid'})
        self.assertEqual(status, 403)
        status, _, _ = self.request('/api/rescan', 'POST', headers=self.auth_headers(Origin='https://externo.invalid'))
        self.assertEqual(status, 403)

    def test_navegacao_de_outra_aplicacao_abre_html_em_modo_local(self):
        headers = {'Sec-Fetch-Site': 'cross-site', 'Sec-Fetch-Mode': 'navigate',
                   'Sec-Fetch-Dest': 'document', 'Origin': 'https://aplicacao.invalid'}
        for path in ('/', '/index.html'):
            with self.subTest(path=path):
                status, body, response_headers = self.request(path, headers=headers)
                self.assertEqual(status, 200, body)
                self.assertIn(b'<meta name="hmapn-mode" content="local">', body)
                self.assertEqual(response_headers['X-Frame-Options'], 'DENY')
        status, _, _ = self.request('/', headers={**headers, 'Host': 'malicioso.invalid'})
        self.assertEqual(status, 403)

    def test_api_cross_site_bloqueada_mesmo_com_cabecalhos_de_navegacao(self):
        for path in ('/api/health', '/api/summary', '/api/export'):
            for mode in ('cors', 'navigate'):
                with self.subTest(path=path, mode=mode):
                    headers = {'Sec-Fetch-Site': 'cross-site', 'Sec-Fetch-Mode': mode,
                               'Sec-Fetch-Dest': 'document'}
                    status, _, _ = self.request(path, headers=headers)
                    self.assertEqual(status, 403)

    def test_post_cross_site_bloqueado_mesmo_com_token_correto(self):
        headers = self.auth_headers(**{'Sec-Fetch-Site': 'cross-site',
                                      'Sec-Fetch-Mode': 'navigate', 'Sec-Fetch-Dest': 'document'})
        status, _, _ = self.request('/api/rescan', 'POST', headers=headers)
        self.assertEqual(status, 403)

    def test_iframe_cross_site_nao_recebe_excecao_de_navegacao(self):
        headers = {'Sec-Fetch-Site': 'cross-site', 'Sec-Fetch-Mode': 'navigate',
                   'Sec-Fetch-Dest': 'iframe', 'Origin': 'https://aplicacao.invalid'}
        status, _, _ = self.request('/', headers=headers)
        self.assertEqual(status, 403)

    def test_post_sem_token_ou_token_errado_bloqueado(self):
        for headers in ({}, {'X-HMAPN-Token': 'errado'}):
            with self.subTest(headers=headers):
                status, _, _ = self.request('/api/rescan', 'POST', headers=headers)
                self.assertEqual(status, 403)

    def test_rescan_atualiza_summary_items_e_persistencia(self):
        self.example()
        with patch.object(self.backend, 'import_file', side_effect=ValueError('Falha temporária simulada')):
            self.backend.scan_once()
        self.assertEqual(self.import_count(), 0)
        status, body, _ = self.request('/api/rescan', 'POST', headers=self.auth_headers())
        self.assertEqual(status, 200, body)
        status, body, _ = self.request('/api/summary')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['total_itens'], 6)
        status, body, _ = self.request('/api/items?q=FICT-002')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['total'], 1)
        self.assertEqual(self.import_count(), 1)

    def test_upload_csv_e_exportacao(self):
        status, body, _ = self.request('/api/import?name=exemplo.csv', 'POST', EXAMPLE.read_bytes(), self.auth_headers())
        self.assertIn(status, (200, 201), body)
        self.assertEqual(self.backend.build_summary()['total_itens'], 6)
        status, body, headers = self.request('/api/export')
        self.assertEqual(status, 200)
        self.assertIn('text/csv', headers['Content-Type'])
        self.assertIn('FICT-002', body.decode('utf-8-sig'))
        self.assertEqual(len(list(csv.reader(io.StringIO(body.decode('utf-8-sig')), delimiter=';'))), 7)

    def test_upload_xlsx(self):
        file = self.root / 'ficticio.xlsx'
        matrix = list(csv.reader(io.StringIO(EXAMPLE.read_text('utf-8')), delimiter=';'))
        write_xlsx(file, matrix)
        status, body, _ = self.request('/api/import?name=ficticio.xlsx', 'POST', file.read_bytes(), self.auth_headers())
        self.assertIn(status, (200, 201), body)
        self.assertEqual(self.backend.build_summary()['total_itens'], 6)

    def test_upload_invalido_nao_altera_base(self):
        for name, payload in (('malicioso.exe', b'nao-planilha'), ('vazio.csv', b''), ('invalido.csv', b'erro;teste\n1;2\n')):
            with self.subTest(name=name):
                status, body, _ = self.request('/api/import?name=' + name, 'POST', payload, self.auth_headers())
                self.assertIn(status, (400, 413, 415, 422), body)
                self.assertEqual(self.import_count(), 0)

    def test_upload_acima_do_limite_rejeitado_antes_de_ler_corpo(self):
        status, body, _ = self.request('/api/import?name=grande.csv', 'POST', b'',
                                      self.auth_headers(**{'Content-Length': str(26 * 1024 * 1024)}))
        self.assertIn(status, (400, 413), body)
        self.assertEqual(self.import_count(), 0)

    def test_exportacao_escapa_formulas_de_planilha(self):
        file = self.example()
        text = file.read_text('utf-8').replace('Material fictício crítico', '=1+1')
        file.write_text(text, encoding='utf-8')
        self.backend.import_file(file)
        status, body, _ = self.request('/api/export')
        self.assertEqual(status, 200)
        rows = list(csv.reader(io.StringIO(body.decode('utf-8-sig')), delimiter=';'))
        cells = [cell for row in rows for cell in row]
        self.assertIn("'=1+1", cells)
        self.assertNotIn('=1+1', cells)

    def test_rotas_privadas_nao_sao_publicadas(self):
        for path in ('/local/servidor.py', '/DADOS/hmapn.sqlite3', '/.git/config', '/../local/servidor.py'):
            with self.subTest(path=path):
                status, _, _ = self.request(path)
                self.assertIn(status, (400, 403, 404))


if __name__ == '__main__':
    unittest.main()
