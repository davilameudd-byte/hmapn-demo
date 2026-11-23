# HMAPN · Controle logístico

Painel com importação de relatórios, histórico em SQLite e o fundo hospitalar da interface anterior. Sem serviços pagos, bibliotecas externas ou envio dos relatórios a serviços de terceiros.

[Abrir demonstração pública](https://davilameudd-byte.github.io/hmapn-demo/)

## Duas formas de abrir

| Forma | Dados | Recursos |
| --- | --- | --- |
| GitHub Pages ou index.html | Exemplos fictícios | Painel, filtros, busca, histórico demonstrativo e exportação CSV |
| Servidor local | Relatórios escolhidos no computador | CSV/XLSX/XLS, monitoramento de pasta, banco SQLite, histórico e CSV |

O GitHub Pages serve HTML, CSS e JavaScript; não executa Python. O servidor está neste repositório, mas o banco hospitalar não fica publicado. A página pública não tenta se conectar ao computador do visitante.

## Iniciar no Windows

1. Baixe em **Code → Download ZIP** e extraia a pasta inteira.
2. Abra `local/INICIAR_PAINEL.bat`.
3. Acesse o endereço informado, por padrão `http://127.0.0.1:8765`.
4. Em **Importações**, selecione um relatório ou salve-o na pasta indicada no painel.

O **Python 3.10 ou superior** executa a importação e o servidor. O **SQLite** já vem com o Python e preserva as posições importadas. Se necessário, use o [instalador oficial do Python](https://www.python.org/downloads/windows/). Não é necessário instalar Node, PostgreSQL ou pacotes com pip.

Também pode iniciar pelo terminal:

```powershell
py -3 local/servidor.py
```

Para escolher pastas, defina as variáveis antes de iniciar:

```powershell
$env:HMAPN_DATA_DIR = 'C:\HMAPN-local'
$env:HMAPN_INPUT_DIR = 'C:\Relatorios-HMAPN'
py -3 local/servidor.py
```

São caminhos de exemplo. A primeira pasta guarda banco, logs e cópias; a segunda contém as exportações. Não use a pasta do banco como entrada.

## Conexões

- Relatórios exportados → validação → SQLite → indicadores e série histórica.
- Importação pela tela e verificação automática da pasta local.
- Busca e filtros → tabela de materiais → exportação CSV.
- Diagnóstico do navegador, serviço local, banco e pasta de entrada.

Não há conexão direta à API ou ao banco SALUX. Ela depende do contrato técnico e do acesso fornecidos pela equipe responsável pelo sistema.

## Indicadores e limites

- Valor em estoque: saldos positivos × custo médio, somados em reais. Saldos negativos são classificados como zerados e não reduzem o valor exibido.
- Cobertura: saldo ÷ consumo médio diário, quando o consumo é positivo.
- Zerado: saldo menor ou igual a zero. Crítico: saldo positivo até 7 dias. Atenção: acima de 7 e até 30 dias. Excesso: acima de 120 dias.
- Consumo mensal estimado: consumo médio diário × 30 × custo médio. Não é consumo realizado por competência.
- Quantidades de unidades diferentes não representam um total físico somável.
- A data da posição vem da modificação do relatório. Confira ao copiar arquivos antigos.

Os layouts estão documentados em `examples/`. Relatórios diferentes precisam de adaptação e conferência antes de uso operacional. XLS antigo é limitado aos registros reconhecidos pelo leitor; formato não suportado deve retornar erro.

## Segurança e backup

O servidor atende apenas este computador, em `127.0.0.1`. Não oferece acesso pela rede hospitalar nem contas de usuários. Valida origem, token para alterações e arquivos públicos permitidos. Isso não substitui o controle de acesso ao Windows.

Banco, documentos, logs, configurações e backups são excluídos do Git. Encerre o serviço antes de copiar a pasta de dados para um local protegido. O histórico não recupera relatórios que nunca foram importados.

## Verificação

```powershell
py -3 -m unittest discover -s tests -v
```

Os testes usam arquivos fictícios e diretórios temporários. Consulte `VALIDACAO.md` para os resultados e limites desta entrega. O GitHub repete os testes e a verificação de sintaxe a cada alteração.

## Estrutura

- `index.html`, `app.css`, `app.js`: interface compartilhada.
- `api.js`, `demo-data.js`: modo local e demonstração.
- `assets/`: fundo hospitalar.
- `local/`: servidor e importador Python.
- `examples/`: relatórios fictícios.
- `tests/`: testes isolados.
