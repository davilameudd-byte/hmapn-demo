# Validação da versão 2.0

Data: 23/11/2026.

## Resultado

- 38 testes automatizados aprovados, sem falhas ou casos ignorados. Tempo da suíte: 1,269 s no ambiente de desenvolvimento.
- Sintaxe validada em app.js, api.js e demo-data.js com Node.
- Sintaxe PowerShell validada nos scripts de início e encerramento.
- Verificação de publicação aprovada: arquivos operacionais, credenciais e caminhos pessoais excluídos do pacote público.
- Imagem do fundo comparada por SHA-256: idêntica ao fundo hospitalar anterior.
- Servidor iniciado e encerrado pelos novos atalhos; resposta HTTP e modo local conferidos.
- Compatibilidade conferida adicionalmente com as exportações XLS locais já usadas pelo projeto. Seus conteúdos e cópias não estão no repositório.

## O que os testes verificam

- CSV UTF-8 e separador brasileiro; XLSX sintético e XLS BIFF5/OLE sintético.
- XLS sem preenchimento no último setor, desde que todos os bytes declarados existam. Truncamento real é rejeitado.
- Quantidades, valores financeiros, classificação de cobertura, ausência de valores, NaN/infinito e custos negativos.
- Separação entre posição de estoque e consumo por competência; quantidades com unidades distintas não são somadas como total físico.
- Duplicidade, persistência, gravação atômica, falha de backup e nova tentativa de importação com erro.
- Busca, filtros, paginação e exportação CSV protegida contra fórmulas.
- Upload CSV/XLSX, limite de 25 MB, rejeição de nomes inválidos e proteção das rotas.
- Host, origem e token; navegação ao painel permitida, acesso externo à API bloqueado; encerramento autorizado.

## Verificação no navegador

A demonstração carregou com os indicadores, navegação e identificação de dados fictícios. A ferramenta de controle do navegador interrompeu a inspeção por não conseguir confirmar a URL da janela; a captura visual e os testes de interação em tela móvel ficaram pendentes. Não há afirmação de homologação visual completa nesta entrega.

## Limites

A API local foi testada com arquivos sintéticos; a conferência adicional dos relatórios locais não garante compatibilidade com todo layout SALUX. O painel integra arquivos exportados, não a API nem o banco do SALUX. A versão local é para um computador, sem autenticação multiusuário, acesso remoto ou homologação operacional hospitalar.

O consumo mensal exibido é estimado a partir do consumo médio diário. A data da posição usa a modificação do arquivo. Conferir essas premissas antes de usar os indicadores em decisões operacionais.

## Repetir os testes

```powershell
py -3 -m unittest discover -s tests -v
py -3 tests/check_publication.py
```

Os testes criam seus próprios arquivos e bases em diretórios temporários. Não acessam documentos reais nem o banco da operação.
