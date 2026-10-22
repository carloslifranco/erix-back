# Erix Back-end

## Painel de risco para a operadora

Implementação inicial da proposta dos slides 11 e 12 do pitch: priorização de investigação, comparação entre pares e contexto do beneficiário. A aba **Painel da operadora** mostra valor solicitado em revisão prioritária, motivos, profissionais, beneficiários, dispersões de preço/frequência e evidências por pedido. A pontuação de 0 a 100 indica prioridade de revisão, não probabilidade de fraude. Não há execução ou bloqueio Pix; valores sinalizados não são economia auditada.

### Executar e demonstrar

Com o `.env` configurado e Docker Desktop aberto:

```powershell
docker compose up -d --build api streamlit
docker compose --profile tools run --rm --build trainer python -m scripts.demo_portfolio
```

Abra `http://localhost:8501`, vá a **Painel da operadora** e mantenha **Usar dados fictícios de demonstração** ligado. O segundo comando insere **150 pedidos históricos fictícios**, treina o modelo e cria **8 pedidos novos**, incluindo preço elevado, divergência documental e repetição de pedidos no mesmo dia. Não precisa de OCR nem de APIs externas para essa demonstração; os campos lidos são simulados e identificados como sintéticos. O comando usa o banco configurado, adiciona dados a cada execução e não apaga pedidos anteriores. Ele publica apenas o modelo de demonstração, separado do modelo operacional.

Para analisar seus próprios comprovantes de teste, use **Enviar solicitação** e preencha o contexto da operadora: referência estável do profissional, referência interna do beneficiário (opcional), especialidade, código do procedimento, região e quantidade de sessões/consultas. Use a mesma referência para a mesma pessoa e códigos padronizados de procedimento e região. Não use CPF como identificador do beneficiário. O valor por unidade é `requested_amount / quantity`; pacotes de sessões devem informar a quantidade correta.

Novos documentos com contexto recebem o relatório automaticamente após o OCR. Pedidos antigos sem esse contexto continuam disponíveis no fluxo anterior e ficam fora do painel. As tabelas antigas são preservadas; o startup cria uma nova tabela `claim_analytics`. Não há ALTER automático nas tabelas existentes.

### Sinais calculados

| Sinal | Referência atual |
|---|---|
| Preço fora dos pares | Mediana dos valores unitários dos 90 dias anteriores, para mesma especialidade, procedimento e região, excluindo o próprio prestador. Exige no mínimo 5 documentos e 3 outros prestadores. Preço ≥1,8x a mediana gera prioridade 85; ≤0,45x gera 65. |
| Frequência do profissional | Documentos apresentados nos 30 dias anteriores, incluindo o atual; alerta com pelo menos 20 documentos e volume superior a 3x a mediana dos prestadores pares. |
| Recorrência do beneficiário | Mais de 12 documentos do mesmo procedimento em 30 dias gera prioridade 80; pode ser tratamento recorrente legítimo. |
| Possível fracionamento | Ao menos 3 pedidos do mesmo beneficiário, profissional, procedimento e data de atendimento gera prioridade 85; exige conferência do contexto. |
| Valor documental divergente | Diferença superior a R$ 0,02 entre valor solicitado e valor lido com confiança de OCR suficiente gera prioridade 85. |
| Arquivo repetido | SHA-256 igual a um pedido anterior nos últimos 30 dias gera prioridade 90. |
| Contexto insuficiente | OCR baixo ou valor ausente é inconclusivo e gera prioridade 50, sem afirmar fraude. |

São regras iniciais ilustrativas, não limites clínicos/contratuais validados. O limiar padrão de revisão é 80. Quando vários motivos aparecem, usa-se a maior prioridade; cada pedido entra uma única vez no total financeiro por motivo principal. Pedidos já decididos saem do valor pendente, mas permanecem no histórico do período.

Frequência mede **documentos apresentados à Erix**, não todas as emissões do profissional. Preços vêm do histórico informado, não de uma tabela oficial de mercado. O gráfico de consumo soma valores solicitados conhecidos no ano do atendimento, dividido pelo limite anual informado: não é saldo contratual, nem total de pagamentos realizados. Recorrência e uso de limite, isoladamente, não comprovam fraude.

### Modelo de Machine Learning

O modelo adicional é um **Isolation Forest** do scikit-learn, independente do classificador documental antigo de `scripts/train_model.py`. Ele combina razão de preço em relação aos pares, disponibilidade dessa referência, frequência de prestador/beneficiário, repetição do par no mesmo dia, diferença de valor documental, duplicidade e confiança de OCR. Não exige rótulos de fraude para aprender padrões atípicos.

Cada vetor usa apenas pedidos recebidos **antes** do pedido analisado. Histórico sintético e operacional não se misturam. O treinamento requer pelo menos **40 vetores com referência de pares suficiente**, com pelo menos 30 no bloco inicial de treino; aproximadamente 20% dos exemplos mais recentes ficam separados para avaliação temporal. Empates de timestamp não são divididos entre treino e avaliação. Isso é um mínimo técnico para a demonstração, não garantia de qualidade estatística.

O relatório de treino mostra taxa de anomalias no período posterior. Sem rótulos confirmados, ela não mede acurácia, precisão ou recall de fraude. A contaminação assumida pelo modelo é 5%, parâmetro de triagem que deve ser validado. Pedidos pontuados como anomalia recebem prioridade de ao menos 80; demais recebem prioridade inferior a 80, conforme percentil relativo ao treino. Essa transformação é uma política explícita de fila, não uma probabilidade calibrada. As regras podem elevar a prioridade independentemente do ML.

O modelo só pontua pedidos recebidos após sua publicação; pedidos anteriores ao treino não recebem métricas de ML sobre o próprio histórico. Não há retreinamento em cada upload nem aprendizado automático com decisões humanas. Para treinar com seu histórico contextualizado de testes:

```powershell
docker compose --profile tools run --rm trainer python -m scripts.train_portfolio --synthetic
```

Para o histórico marcado como não sintético, retire `--synthetic`. O acesso ao banco e a escrita dos artefatos são feitos pelo serviço `trainer`; a API só lê os modelos. O próximo pedido carrega o modelo publicado, sem reiniciar a API. Até haver histórico/modelo, o painel mostra as regras e o estado `not_trained`; não inventa pontuação de ML. Erro no artefato gera análise de ML inconclusiva e encaminhamento humano, preservando OCR e regras.

Configurações: `PORTFOLIO_REVIEW_THRESHOLD` (80), `PORTFOLIO_MIN_PEERS` (5 documentos, com mínimo fixo de 3 outros prestadores), `PORTFOLIO_PATIENT_MONTHLY_THRESHOLD` (12). Os arquivos padrão são `models/portfolio-anomaly.joblib` e `models/portfolio-anomaly-demo.joblib`. Não carregue artefatos joblib de fontes desconhecidas.

### API e testes do painel

- `POST /v1/claims` aceita o objeto opcional `analytics` com `provider_ref`, `beneficiary_ref`, `specialty`, `procedure_code`, `region`, `quantity`, `annual_limit`, `document_issue_date` e `is_synthetic`.
- `GET /v1/claims/{id}` inclui contexto e relatório em `analytics.report`.
- `GET /v1/risk/dashboard?synthetic=true&start=2026-01-01&end=2026-12-31` agrega relatórios por período de recebimento.
- `POST /v1/claims/{id}/portfolio-risk` recalcula o relatório de um pedido com contexto, OCR concluído e decisão ainda pendente.

Todas as rotas exigem `X-API-Key`. Testes: `python -m pytest -q`. Há cobertura para datas futuras fora das features, exclusão do próprio prestador dos pares, quantidades de sessões, separação sintético/operacional, treino temporal, falha do modelo, upload até painel e ausência de dupla contagem financeira. A aplicação ainda usa uma chave compartilhada e não oferece isolamento entre operadoras; esta versão continua destinada ao protótipo acadêmico com dados sintéticos.

Referência técnica: [Isolation Forest no scikit-learn](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.IsolationForest.html).

Proposta de arquitetura para uma plataforma que recebe pedidos de reembolso de consultas, verifica documentos e identifica solicitações que merecem análise adicional. O projeto está em fase inicial; este documento descreve uma arquitetura de referência para um MVP e os requisitos para evoluí-la.

## Princípio de segurança e produto

O sistema deve **priorizar e apoiar a análise**, não declarar automaticamente que uma pessoa cometeu fraude nem negar um reembolso apenas com base em um modelo. Um alerta significa “revisar”, não “fraude comprovada”. A elegibilidade do procedimento e o valor reembolsável são regras do benefício/contrato e devem ser avaliados separadamente da autenticidade dos documentos.

Dados de saúde são sensíveis. Antes de usar dados reais, a empresa deve definir finalidade, base legal, transparência, retenção e direitos dos titulares com apoio jurídico e de privacidade, considerando a LGPD e as regras aplicáveis ao produto e aos parceiros. Para desenvolvimento acadêmico, use dados sintéticos ou devidamente anonimizados.

## Visão da arquitetura

```text
Aplicativo / Portal
        |
        v
API + autenticação e autorização
        |
        +--> Serviço de solicitações --------> PostgreSQL
        |          |
        |          +--> upload temporário --> armazenamento privado (quarentena)
        |                                      |
        |                                      v
        |                                 fila de eventos
        |                                      |
        |                                      v
        |                    varredura / OCR / extração / validações
        |                                      |
        |                                      v
        |                         regras + modelo de risco
        |                                      |
        |                                      v
        +<--------- resultado e fila de revisão humana
                                                   |
                                                   v
                                      painel de analistas
                                                   |
                                                   v
                                  decisão / integração de pagamento

Eventos auditáveis --> logs e métricas
Dados minimizados e pseudonimizados --> avaliação e treinamento offline
```

Os serviços podem começar como um **monólito modular** (uma aplicação e workers separados por processo), evitando microserviços prematuros. Mantenha fronteiras claras entre solicitações, documentos, análise, revisão e pagamentos para permitir extração futura.

### Componentes

| Componente | Responsabilidade |
|---|---|
| API de back-end | Autenticar usuários, validar pedidos, aplicar autorização por recurso e expor o estado da solicitação. |
| Serviço de solicitações | Criar e atualizar pedidos, controlar estados e garantir idempotência. |
| Armazenamento de documentos | Guardar arquivos em bucket privado, criptografado e sem acesso público; links temporários e limitados para upload/download. |
| Fila e workers | Processar documentos de forma assíncrona, com tentativas limitadas, fila de erros e proteção contra processamento duplicado. |
| Pipeline documental | Verificar formato/tamanho, detectar malware, extrair texto e campos por OCR e registrar a origem e confiança de cada campo. |
| Validador | Aplicar regras determinísticas: campos obrigatórios, datas plausíveis, consistência entre recibo e pedido, duplicidade e, quando disponível, validação com prestador/emissor. |
| Serviço de risco | Combinar regras e sinais do modelo em uma recomendação explicável; não aprova nem nega reembolso sozinho. |
| Console de analista | Exibir evidências, campos extraídos, alertas e histórico; permitir revisão, correção de OCR e decisão humana justificada. |
| Persistência | PostgreSQL para dados transacionais; armazenamento de objetos para documentos; fila gerenciada ou equivalente para trabalhos. |
| Operação de ML | Versionar dados, features, código e modelos; avaliar, aprovar, publicar, monitorar e reverter versões. |
| Observabilidade e auditoria | Métricas, logs estruturados sem conteúdo clínico, trilha de auditoria protegida e alertas operacionais. |

### Stack inicial sugerida

- **API e workers:** Python com FastAPI e Pydantic, se a equipe já tiver familiaridade com Python.
- **Banco transacional:** PostgreSQL.
- **Documentos:** armazenamento de objetos compatível com S3, com criptografia e políticas privadas.
- **Processamento assíncrono:** serviço de filas gerenciado ou RabbitMQ; escolher conforme infraestrutura e custo.
- **OCR:** componente substituível, escolhido após teste com recibos reais autorizados. Não presumir que OCR é prova de autenticidade.
- **Modelo tabular:** começar com regras transparentes e uma baseline simples (por exemplo, regressão logística ou gradient boosting) quando houver dados rotulados suficientes.
- **MLOps:** versionamento de datasets/modelos e registro de experimentos; MLflow é uma opção, não requisito.
- **Deploy:** containers em ambiente gerenciado, segredos em cofre e infraestrutura separada por desenvolvimento, homologação e produção.

São sugestões, não dependências obrigatórias. Para um protótipo de disciplina, dá para implementar API, PostgreSQL, armazenamento local não sensível e um worker simples, sem processar dados reais de saúde.

## Fluxo de uma solicitação

1. **Autenticar e autorizar:** o usuário entra por identidade segura. A API verifica que ele pode criar ou consultar solicitações vinculadas à sua conta.
2. **Criar pedido:** registrar beneficiário, consulta, prestador, data, valor e dados mínimos do contrato necessários à análise. Gerar um identificador e uma chave de idempotência.
3. **Enviar documento:** emitir URL de upload de curta duração para uma área de quarentena. Aplicar limite de tamanho e tipos permitidos; não confiar no nome ou no `Content-Type` enviado pelo cliente.
4. **Processar arquivo:** validar formato, verificar malware e calcular hash para identificar o mesmo arquivo. Arquivos inválidos ficam bloqueados e geram erro explícito; nunca são tratados como aprovados.
5. **Extrair e confirmar:** OCR extrai campos como prestador, data, valor e identificador fiscal, preservando valor bruto, valor normalizado e confiança. A pessoa pode corrigir erros antes da análise.
6. **Validar consistência:** verificar campos obrigatórios, datas, valores, duplicidades e correspondência entre formulário e documento. Consultas externas só são feitas com autorização e integração legítima.
7. **Analisar risco:** regras determinísticas e modelo produzem sinais, score calibrado quando possível, versão do modelo e explicações legíveis. Baixa qualidade de OCR ou ausência de dados suficientes deve encaminhar para revisão, não ser interpretada como fraude.
8. **Encaminhar:** solicitação de baixo risco e com critérios objetivos satisfeitos pode seguir para validação de elegibilidade/pagamento; risco elevado, conflito ou incerteza vai para revisão humana. Os limiares são configurados e validados com dados reais representativos antes de produção.
9. **Decidir e informar:** analista registra decisão, motivo padronizado e observação. O sistema informa o status e permite contestação/correção conforme processo definido.
10. **Pagar separadamente:** só um serviço autorizado de elegibilidade/pagamento pode liberar o reembolso, com controles de limite, reconciliação e idempotência. O score de risco não é autorização de pagamento.
11. **Aprender com revisão:** resultados confirmados e auditados podem compor dados de avaliação/treinamento, respeitando finalidade, retenção e acesso. Uma suspeita ou alerta, por si só, não é rótulo de fraude.

### Estados sugeridos

`rascunho` → `aguardando_documentos` → `recebida` → `processando` → `aguardando_revisao` ou `em_analise` → `aprovada` / `negada` / `necessita_correcao` → `pagamento_pendente` → `paga`.

Também deve haver estados explícitos de erro/reprocessamento (`falha_processamento`, `documento_invalido`) e cancelamento. Transições devem ser validadas no servidor e registradas. Uma solicitação em falha não pode adquirir estado de aprovação por fallback.

## Requisitos funcionais

1. Criar, consultar e acompanhar solicitação com controle de acesso por usuário/beneficiário.
2. Receber documentos com limites configuráveis, validação de arquivo, quarentena e exclusão segura.
3. Extrair campos, apresentar confiança e permitir confirmação/correção pelo usuário ou analista.
4. Validar consistência de dados e detectar duplicidade sem afirmar fraude somente por coincidência.
5. Gerar recomendação de risco com evidências, versão de regras/modelo e motivo de encaminhamento.
6. Encaminhar casos incertos ou de risco a uma fila de análise humana.
7. Registrar decisões, alterações, reprocessamentos e acessos em trilha de auditoria.
8. Separar análise de fraude, elegibilidade contratual, decisão final e execução de pagamento.
9. Comunicar status, solicitação de correção e resultado ao usuário sem expor sinais internos que facilitem contornar controles.
10. Permitir contestação e reavaliação por um processo humano definido.
11. Permitir exportação/eliminação de dados e cumprimento de prazos conforme política aprovada.
12. Administrar acesso de analistas, regras, versões de modelo e limiares com segregação de funções.

## Requisitos não funcionais

- **Privacidade e minimização:** coletar apenas dados necessários; separar identificadores de dados analíticos; não enviar conteúdo clínico a serviços externos sem avaliação contratual, de privacidade e segurança.
- **Segurança:** TLS em trânsito; criptografia em repouso; gestão e rotação de segredos; MFA para operadores; menor privilégio; proteção contra abuso de API; controle de sessão e revisão de permissões.
- **Isolamento de documentos:** sem bucket público; acesso temporário; varredura antes da leitura; limites de tipo/tamanho; proteção contra arquivos maliciosos e conteúdo ativo.
- **Auditoria:** trilha append-only ou com controles equivalentes, com ator, ação, horário, recurso e motivo; acesso à trilha restrito. Evitar incluir documento, OCR integral, CPF ou dados clínicos em logs.
- **Confiabilidade:** processamento assíncrono, idempotência, tentativas limitadas, dead-letter queue, recuperação de falhas e backup/restauração testados.
- **Desempenho:** definir metas após medir a infraestrutura; processamento de documento pode ser assíncrono e o usuário deve receber estado, não aguardar OCR na chamada HTTP.
- **Disponibilidade e escala:** escalar workers independentemente da API; limitar taxa e tamanho de fila; alertar sobre acúmulo e falhas.
- **Acessibilidade e clareza:** mensagens compreensíveis, status rastreável e instruções de correção; não expor uma conclusão de fraude sem apuração.
- **Manutenibilidade:** contratos de API versionados, migrações revisadas, configuração por ambiente, testes automatizados e runbooks.

## Dados e modelo

### Dados mínimos recomendados

- `claim`: identificador, usuário/beneficiário, contrato ou plano referenciado, prestador, data da consulta, valor solicitado, moeda, status e timestamps.
- `document`: identificador, solicitação, chave privada do objeto, hash, tipo detectado, tamanho, status da varredura, status de OCR e prazo de retenção.
- `extracted_field`: documento, nome do campo, valor normalizado, confiança, página/posição de origem e confirmação/correção.
- `risk_assessment`: solicitação, score/faixa, sinais/regras acionados, versão do modelo e das regras, timestamp e estado de revisão.
- `review`: analista, decisão, código de motivo, justificativa, timestamps e eventuais alterações.
- `audit_event`: ator, ação, recurso, horário, correlação da requisição e metadados estritamente necessários.
- `payment`: referência externa, valor autorizado, estado, chave de idempotência e reconciliação; sem dados de cartão armazenados pelo serviço.

Não guardar dados de cartão. Evitar copiar dados pessoais para features, logs ou relatórios. Definir separação lógica/criptográfica e permissões para documentos, dados transacionais e conjuntos analíticos.

### Sinais possíveis (não conclusivos isoladamente)

- divergência entre valor/data/prestador informados e extraídos;
- repetição de hash ou documento/solicitação semelhante;
- inconsistências de campos, formato ou sequência temporal;
- padrões estatísticos de volume ou frequência, considerados no contexto;
- falha em validação com fonte legítima, quando essa fonte existir e a consulta for permitida.

Uma imagem ruim, ausência de QR code, prestador pouco frequente ou dado faltante não provam fraude. Esses sinais podem decorrer de erro, acessibilidade, variação legítima do documento ou falta de integração.

### Treinamento e avaliação

- Definir os rótulos com cuidado: por exemplo, `regular_confirmado`, `fraude_confirmada`, `inconclusivo` e `erro_operacional`. “Mandado para revisão” não equivale a fraude.
- Treinar somente com dados obtidos e usados legitimamente, com acesso restrito, minimização e governança documentada.
- Separar treino/teste por pessoa e tempo para evitar que documentos ou usuários correlacionados vazem entre conjuntos.
- Comparar com baseline de regras e medir precisão, recall, taxa de falsos positivos, calibração e volume de casos encaminhados. Revisar métricas por grupos relevantes, quando legal e estatisticamente viável, para identificar disparidades.
- Escolher limiares de acordo com o custo de falsos positivos e a capacidade de revisão; não otimizar apenas acurácia global.
- Registrar dataset, features, código, métricas, aprovação e versão de cada modelo. Publicar em modo sombra antes de influenciar o fluxo.
- Monitorar mudança de distribuição, qualidade do OCR, desempenho e taxas de revisão. Suspender ou reverter modelo se houver degradação.
- Não permitir que o modelo aprenda automaticamente com decisões de analista sem validação da qualidade dos rótulos.

## API inicial sugerida

Usar JSON para metadados e upload direto temporário para o armazenamento de objetos. Exigir autenticação, autorização por recurso, validação de schema, limites de taxa e correlação de requisição.

| Método e rota | Uso |
|---|---|
| `POST /v1/claims` | Criar solicitação e devolver identificador. Aceitar `Idempotency-Key`. |
| `POST /v1/claims/{id}/documents/upload-url` | Obter autorização temporária para enviar arquivo permitido. |
| `POST /v1/claims/{id}/documents/complete` | Confirmar upload e iniciar validação/processamento. |
| `GET /v1/claims/{id}` | Consultar status e dados visíveis ao solicitante. |
| `POST /v1/claims/{id}/confirm-extracted-data` | Confirmar ou corrigir campos extraídos antes da avaliação. |
| `GET /v1/claims/{id}/events` | Consultar histórico permitido ao solicitante. |
| `GET /v1/reviews` | Listar casos da fila; somente perfil autorizado. |
| `POST /v1/reviews/{id}/decision` | Registrar decisão humana, código de motivo e justificativa. |

Os endpoints de analista devem ser separados por escopo e perfil. Não expor score bruto, features sensíveis ou detalhes antifraude no endpoint público. Erros devem ser explícitos e não transformar falha de OCR, fila ou modelo em “aprovado”.

## Controles de segurança e privacidade

- Autenticação OIDC/OAuth2 ou provedor confiável; autorização por papel e por recurso (RBAC + verificação de propriedade).
- MFA para analistas e administradores; contas individuais; remoção rápida de acesso quando necessário.
- Criptografia e gestão de chaves por ambiente; segredos fora do código-fonte.
- URLs assinadas de curta duração, escopo restrito a um objeto e validação do upload no servidor.
- Varredura antimalware, limites contra abuso, validação do conteúdo real do arquivo e acesso isolado ao parser/OCR.
- Logs sem conteúdo dos documentos, tokens, dados clínicos ou identificadores diretos; mascaramento e retenção curta.
- Backup criptografado, teste de restauração e plano para incidentes envolvendo dados pessoais.
- Política de retenção e descarte verificável para originais, extrações, cópias, backups e dados de treinamento.
- Avaliação de fornecedores/suboperadores, localização, retenção e uso secundário antes de enviar dados a serviços de OCR/IA.
- Avaliação de impacto e revisão jurídica/privacidade antes do uso em produção; documentar finalidade e explicação fornecida às pessoas afetadas.

## Observabilidade e operação

Medir: pedidos recebidos e processados, latência por etapa, falhas de upload/OCR, tempo em fila, reprocessamentos, tamanho de fila, disponibilidade da API, casos encaminhados, taxa de correção de OCR, decisões revertidas e métricas do modelo. Usar métricas agregadas; não criar painéis com dados pessoais desnecessários.

Alertar para falhas de processamento, fila parada, aumento anormal de erros, indisponibilidade de integrações e degradação do modelo. Incluir `request_id`/`claim_id` pseudonimizado para rastrear uma operação sem registrar o documento.

## Testes e critérios de aceite do MVP

1. Testes unitários de transições de estado, regras de consistência, idempotência e autorização.
2. Testes de integração para upload, fila, armazenamento, falha/retry, duplicidade e processamento de documento inválido.
3. Testes de segurança para acesso cruzado entre usuários, permissões de analista, expiração de URL e exposição acidental em logs.
4. Testes de contrato da API e migrações de banco.
5. Testes do pipeline com documentos sintéticos/de teste: OCR imperfeito deve gerar baixa confiança/revisão, não aprovação automática.
6. Avaliação do modelo em conjunto temporal separado, com métricas e limiar aprovados antes de qualquer uso operacional.
7. Simulação de indisponibilidade de fila, OCR e modelo; nenhuma dessas falhas pode produzir aprovação ou negação silenciosa.
8. Teste documentado de backup/restauração e exclusão conforme política definida.
9. Para lançamento: revisão de segurança, privacidade/jurídica, plano de incidentes, monitoramento e processo humano de contestação aprovados.

## Fases de implementação

1. **Protótipo acadêmico:** API e estados de solicitação, dados sintéticos, regras simples, OCR opcional e painel de revisão simulado. Sem decisões ou pagamentos reais.
2. **MVP controlado:** upload seguro, fila, OCR, validações, revisão humana, auditoria e métricas. Modelo em modo sombra, sem afetar aprovação/negação.
3. **Piloto autorizado:** dados e integrações legítimos, validação de privacidade e segurança, avaliação de desempenho e revisão de limiares.
4. **Produção gradual:** expansão monitorada, reavaliações periódicas, resposta a incidentes, governança do modelo e rollback.

Não começar pelo treinamento de uma IA: sem rótulos confiáveis, cobertura representativa e fluxo humano, um modelo não consegue distinguir fraude real de erro documental de forma segura. Primeiro construa o fluxo confiável de dados, regras verificáveis e revisão; depois avalie se o ML agrega valor.

## Implementação inicial

O repositório agora contém um MVP executável em FastAPI e Streamlit, com PostgreSQL, OCR PaddleOCR e uma camada de pontuação por regras. O classificador scikit-learn é opcional e só é carregado quando existe um artefato treinado. **Nenhuma pontuação decide reembolso:** os estados só organizam a análise e toda decisão é registrada por uma pessoa.

### Estrutura do projeto

```text
erix-back/
├── app/
│   ├── api.py                 # Endpoints, upload, fila de revisão
│   ├── main.py                # Inicialização da API e banco
│   ├── models.py              # Tabelas SQLAlchemy (pedidos e revisões)
│   ├── schemas.py             # Contratos Pydantic
│   ├── core/                  # Configuração e chave da API
│   ├── db/                    # Base e sessão SQLAlchemy
│   └── services/              # OCR, análise e pontuação de risco
├── scripts/train_model.py     # Treino/avaliação da baseline scikit-learn
├── tests/                     # Testes de validação, API e regras
├── streamlit_app.py           # Interface de envio e revisão humana
├── Dockerfile
├── docker-compose.yml
├── requirements.txt           # Dependências de aplicação
├── requirements-ocr.txt       # Dependências de aplicação + PaddleOCR
└── requirements-dev.txt       # Dependências de aplicação + testes
```

### Executar com Docker Compose

1. Instale Docker Desktop com Docker Compose. 
2. Na pasta `erix-back`, copie `.env.example` para `.env` e troque `POSTGRES_PASSWORD` e `API_KEY` por valores locais fortes.
3. Execute `docker compose up --build`.
4. Abra a interface em `http://localhost:8501`, a documentação da API em `http://localhost:8001/docs` e verifique o health check com a chave: `curl.exe -H "X-API-Key: change-this-development-key" http://localhost:8001/v1/health`.
5. Para parar, execute `docker compose down`. Os dados persistem nos volumes; `docker compose down -v` apaga os dados e deve ser usado somente se essa exclusão for desejada.

O MVP aceita imagens JPEG e PNG de até 10 MB. O arquivo é validado, salvo em volume privado e enviado ao PaddleOCR em processamento de fundo. O endpoint responde com estado `processing`; a interface deve ser atualizada para consultar o resultado. Uploads não são aceitos como prova de autenticidade. Para PDF, documentos multipágina ou antivírus, será necessário adicionar componentes específicos.

Os limiares `RISK_REVIEW_THRESHOLD` (padrão `0.35`) e `LOW_OCR_CONFIDENCE_THRESHOLD` (padrão `0.65`) podem ser configurados no `.env`; calibrá-los com dados representativos e capacidade de revisão antes de qualquer piloto.

### Executar localmente sem Docker

Use Python 3.11 e um PostgreSQL local. Crie ambiente virtual, instale `requirements-ocr.txt` (inclui o PaddleOCR, que é pesado), configure as variáveis de `.env.example` com host/credenciais locais e execute:

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements-ocr.txt
.\venv\Scripts\python.exe -m uvicorn app.main:app --reload
.\venv\Scripts\python.exe -m streamlit run streamlit_app.py
```

Para executar apenas os testes/regras sem instalar os pacotes grandes de OCR, instale `requirements-dev.txt`; em outro terminal rode:

```powershell
.\venv\Scripts\python.exe -m pytest
```

### Rotas iniciais

- `POST /v1/claims`: cria pedido com prestador, data e valor.
- `POST /v1/claims/{id}/document`: envia JPEG/PNG e agenda extração/OCR.
- `GET /v1/claims` e `GET /v1/claims/{id}`: consulta pedidos e processamento.
- `GET /v1/reviews`: lista pedidos para análise humana.
- `POST /v1/claims/{id}/reviews`: registra decisão humana e justificativa.
- `GET /v1/health`: health check.

As rotas de dados exigem `X-API-Key`. O valor padrão é apenas para testes locais; configure um segredo próprio. Essa chave compartilhada não substitui autenticação por pessoa, autorização por beneficiário, MFA ou papéis de analista, portanto o sistema **não deve ser publicado na internet nem usado com dados reais** nesta etapa.

### Classificador opcional

Para treinar a baseline, prepare um CSV contendo as colunas `amount_mismatch`, `date_mismatch`, `duplicate_document`, `ocr_confidence` e `label`. Os rótulos são `0` para caso regular confirmado e `1` para fraude confirmada; não use “suspeito” ou “enviado para revisão” como rótulo confirmado. São exigidas pelo menos quatro amostras de cada classe para criar divisão treino/teste:

```powershell
.\venv\Scripts\python.exe .\scripts\train_model.py .\dados_rotulados.csv --output .\models\reimbursement-risk.joblib
```

O CSV deve ter uso legítimo, rótulos revisados e ser separado por pessoa e tempo para evitar vazamento. Não inclua dados reais de saúde em datasets sem governança e autorização adequadas. O artefato joblib só deve vir de fonte confiável, pois carregar pickle/joblib não confiável pode executar código. Com o arquivo colocado em `models`, a API inclui a avaliação do modelo como mais um sinal de triagem; ela continua sem aprovar ou negar automaticamente.

### Verificação gratuita de consistência e consulta oficial

A análise de novos documentos inclui `extracted_fields.verification`, também exibido na fila de revisão do Streamlit. Sem credenciais e sem chamadas externas, a aplicação verifica dígitos do CPF, identifica referências a CRM/CRP e separa confiança do OCR de autenticidade. CPF com dígitos corretos não comprova existência, situação cadastral ou titularidade. Um CRM/CRP extraído não comprova inscrição ativa ou autoria do recibo. A autenticidade visual permanece `not_verified`: não há detector externo de adulteração integrado.

Na fila de revisão, use **Verificar consistência sem custo** para comparar dados do paciente informados pelo analista com o texto do OCR. O resultado `matches_input` significa apenas coincidência com o dado informado, nunca confirmação em fonte oficial. Os dados de referência fornecidos não são persistidos; o relatório guarda o resultado e CPFs mascarados. O texto original do OCR continua armazenado pelo pipeline existente e pode conter dados pessoais.

Para não confundir paciente e prestador, a extração exige rótulos explícitos, por exemplo:

```text
Paciente: Pessoa Exemplo
CPF do paciente: 000.000.000-00
Nascimento do paciente: 01/02/1990
CRM-SP 12345
Data da consulta: 20/10/2026
Valor: R$ 180,00
```

Exemplo fictício: o CPF acima é propositalmente inválido. Também são aceitos `Cliente:`, `Beneficiário:`, `CPF do cliente:` e `Data de nascimento do paciente:`; CRM no formato `CRM 12345/SP` e CRP no formato `CRP 06/12345`. A extração é uma baseline: campos não rotulados, ambíguos ou com baixa confiança vão para conferência. Não se presume que o CPF genérico de um documento seja do paciente. Outros conselhos ainda não são extraídos. Dados ausentes não são prova de fraude.

API: `POST /v1/claims/{id}/verification`, autenticada por `X-API-Key`, aceita os campos opcionais `patient_name`, `patient_cpf` e `patient_birth_date` (ISO `YYYY-MM-DD`). Exige OCR concluído e pedido pendente de revisão. O relatório é salvo no JSON existente, sem novas tabelas. Inconsistências encaminham para `awaiting_review`, sem transformar os resultados em uma probabilidade de fraude ou alterar o modelo de quatro features. Novas comparações não removem alertas anteriores nem reabrem decisões encerradas.

Consultas gratuitas para o analista, disponíveis como links no painel:

- [Busca de médicos do CFM](https://portal.cfm.org.br/busca-medicos): conferir nome, CRM, UF e situação.
- [Cadastro de profissionais do CFP](https://cadastro.cfp.org.br/): conferir nome, CRP, região e situação.
- [Consulta de situação cadastral do CPF](https://servicos.receita.fazenda.gov.br/Servicos/CPF/ConsultaSituacao/ConsultaPublica.asp): conferir os dados e o resultado disponibilizados pelo serviço oficial.
- [Consulta de NFS-e](https://www.gov.br/pt-br/servicos/consultar-nota-fiscal-de-servico-eletronica): para documentos fiscais compatíveis com o sistema emissor.

Abrir os links não executa nem comprova uma consulta. Registre fonte, data e resultado na justificativa da revisão. A aplicação não automatiza CAPTCHA, não envia dados pessoais na URL e não transmite documentos a terceiros. [O webservice do CFM exige contratação e chave](https://crmvirtual.cfm.org.br/BR/servico/web-service---listagem-de-medicos); consulta pública em site não implica API aberta para empresas. Nenhuma API gratuita que comprove todos esses elementos foi integrada. Mesmo um cadastro confirmado não comprova que a consulta ocorreu ou que o comprovante foi emitido pelo profissional.

Para aplicar as alterações no Docker:

```powershell
docker compose up -d --build api streamlit
```

Novos uploads recebem o relatório automaticamente. Para pedidos antigos com OCR concluído e ainda pendentes, use o botão de comparação no painel. O protótipo continua destinado a dados sintéticos.

### Pendências operacionais

- O processamento `BackgroundTasks` é local à API e não persiste trabalhos após reinício; substitua por fila durável antes de operar em produção.
- `create_all` cria tabelas para o MVP; adote migrações versionadas (por exemplo, Alembic) para evolução do banco.
- A autenticação por API key é compartilhada e não identifica usuário/analista. Adote OIDC, autorização por recurso e trilha de auditoria protegida antes de uso real.
- O armazenamento é um volume local Docker, sem política de retenção/expurgo implementada.
- O OCR/extração de campos é uma baseline; valide precisão com documentos autorizados e adicione revisão/correção do texto antes de decisões.
- Ainda faltam antivírus, criptografia/gestão de chaves operacional, controles de abuso, monitoramento, backups testados, processo de contestação e revisão jurídica/privacidade.
