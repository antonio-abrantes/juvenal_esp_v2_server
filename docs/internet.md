# Consultas à internet: clima, câmbio e notícias

O módulo roda no Python e reutiliza a conversa existente. O ESP continua enviando PCM e recebendo a resposta por TTS, sem precisar conhecer essas APIs. Funciona também no modo de microfone/alto-falantes do computador.

São ferramentas de leitura independentes do Home Assistant: desligar `HASS_ENABLED` não desliga a internet. O controle geral `TOOLS_ENABLED` e a compatibilidade de tool calling do provedor/modelo continuam sendo os já configurados. A busca usa DuckDuckGo pela biblioteca Python open source **DDGS**, no processo do Juvenal, sem API paga ou chave de busca. Clima usa Open-Meteo para uso pessoal não comercial e câmbio usa dados oficiais BCB. Chamadas ao LLM continuam conforme o provedor usado.

**Orange Pi: execução nativa/systemd. Nunca instalar, subir ou recriar containers do Juvenal ou de serviços auxiliares nesse host.** Nenhum SearXNG, daemon de busca, porta adicional ou Docker é necessário para este módulo. Os exemplos Docker gerais do repositório destinam-se a outros ambientes autorizados, como a VPS.

## Ativar na instalação nativa do Orange Pi

Depois de commitar/publicar a develop, no Orange Pi:

```bash
git -C /opt/juvenal_server status --short --branch
git -C /opt/juvenal_server pull --ff-only origin develop
```

Se houver alterações locais, interromper a atualização e conferir antes de continuar. Instale somente a dependência opcional de busca na venv existente:

```bash
/opt/juvenal_server/.venv/bin/python -m pip install --only-binary=:all: -r /opt/juvenal_server/requirements-internet.txt
```

O comando não instala outro ASR nem recria a venv. O backend Parakeet selecionado permanece o mesmo. Instalações novas pelos scripts `install.sh`/`install.ps1` incluem esse manifesto quando `INTERNET_ENABLED=true` e `WEB_SEARCH_ENABLED=true` já estiverem configurados. Se a biblioteca faltar, somente a busca fica indisponível (`search_dependency_missing`); clima/câmbio e o servidor continuam disponíveis.

Edite `/opt/juvenal_server/.env`, preservando as configurações atuais:

```dotenv
INTERNET_ENABLED=true
WEATHER_ENABLED=true
CURRENCY_ENABLED=true
WEB_SEARCH_ENABLED=true
WEB_SEARCH_PROVIDER=duckduckgo
WEATHER_DEFAULT_LOCATION=Sousa, Paraíba
INTERNET_TIMEOUT_SECONDS=8
INTERNET_MAX_RESULTS=3
INTERNET_CACHE_TTL_SECONDS=300
INTERNET_LOG_CONTENT=false
```

A localização acima é um exemplo; pode ser cidade/estado ou cidade/país. Se ficar vazia, Juvenal deve pedir a localização quando você não a informar. Homônimos sem contexto geram pedido de esclarecimento. A ferramenta pode consultar agora/hoje/amanhã ou uma data dos próximos 16 dias. Datas da previsão e seu fuso vêm do provedor; datas de consulta/câmbio usam o relógio/fuso do servidor, que deve estar correto.

Todas as consultas são chamadas pelo próprio servidor Python. **Não há endpoint auxiliar de busca para configurar.** Se copiou a orientação anterior, troque `WEB_SEARCH_PROVIDER=searxng` por `duckduckgo` e remova `SEARXNG_URL`, que não é mais utilizada. `WEB_SEARCH_ENABLED=false` desliga somente pesquisa/notícias.

Depois da configuração, reinicie e confira os logs:

```bash
systemctl restart juvenal-server.service
journalctl -u juvenal-server.service --since "2 minutes ago" -f
```

O log `Consultas internet` mostra disponibilidade por ferramenta. `curl -sS http://127.0.0.1:8000/health` mostra `capabilities.tools.internet.features`; disponibilidade significa configuração/capacidade, não uma sondagem contínua da fonte. `Ctrl+C` sai do journal sem parar o serviço.

## Busca Python nativa

`requirements-internet.txt` fixa `ddgs==9.16.1`, sem os extras API/MCP. A biblioteca suporta Python 3.10+, usa chamadas HTTPS e não executa um servidor separado. O adaptador seleciona explicitamente `backend=duckduckgo`, região `br-pt`, no máximo três resultados e timeout limitado pelo prazo restante da consulta. Não usa seleção automática de vários motores.

Busca geral usa `text()`; categoria notícias usa `news()`. Para filtro anual, usa busca textual com `timelimit=y`, pois o contrato documentado de notícias cobre dia/semana/mês. Datas ausentes permanecem desconhecidas. O filtro de período do buscador não substitui conferir a data retornada pela fonte.

DDGS e dependências têm distribuições para os ambientes documentados, incluindo wheels ARM64 de `primp`; a instalação e consulta no Orange Pi ainda dependem da validação manual. O comando acima exige wheels, evitando compilar dependências no host. Em caso de erro de instalação, conferir a saída antes de mudar o ambiente existente.

DuckDuckGo pode limitar ou bloquear consultas e o formato dos resultados pode mudar. Nessas situações, Juvenal informa indisponibilidade; não inventa resultados, não contorna CAPTCHA nem inicia outro serviço. A biblioteca retorna resultados de busca, e o cliente limitado do Juvenal faz a leitura opcional de até duas páginas públicas. Não utiliza a API Instant Answer como se fosse busca completa.

## Comportamento e fontes

| Ferramenta | Dados | Como interpretar |
|---|---|---|
| `consultar_clima` | Open-Meteo: temperatura, umidade, sensação, vento, códigos de condição, previsão e unidades | Estimativa de modelos meteorológicos; não mede um sensor da sua casa |
| `consultar_cambio` | BCB/PTAX: último fechamento disponível nos 14 dias anteriores à data pedida, moeda estrangeira/BRL, compra/venda; pode converter um valor | Referência oficial datada, sem spread de varejo; não é preço de negociação ao vivo. Datas futuras são recusadas |
| `pesquisar_internet` | DuckDuckGo via DDGS: busca geral/notícias, período opcional, até três fontes e até duas leituras públicas limitadas | Distingue snippet de página lida; publicação desconhecida permanece desconhecida. Resultados antigos não viram notícias atuais por terem sido consultados agora |

Exemplos para avaliação manual: “Qual a temperatura agora?”, “Vai chover amanhã em Sousa, Paraíba?”, “Qual a última PTAX do dólar?”, “Quanto são cem euros em reais pela PTAX?”, “Pesquise as notícias de hoje sobre [tema]” e “O que aconteceu recentemente em [evento/local]?”. Para dados de outros países, indique a localização de forma explícita.

O LLM sintetiza os dados em uma a três frases, citando fonte e data/hora relevantes. O painel Python apresenta links das fontes em eventos próprios. O resultado estruturado respeita o limite existente de 4.096 bytes. Conteúdo de páginas é tratado como dado externo, sem autoridade para alterar persona, revelar segredos ou comandar o HA. Após consulta externa neste turno, ações HA ficam indisponíveis até a próxima fala do usuário; peça uma ação residencial em um novo turno, separado da pesquisa.

Há cache em memória de até 64 consultas, com TTL configurável (zero desativa o reaproveitamento); o retorno preserva o horário original e informa `cached`. APIs de clima/câmbio e leitura de páginas usam HTTP com prazo, limite de bytes, TLS verificado e DNS fixado por conexão. Essa leitura bloqueia IPs privados/loopback/link-local e redirecionamentos para eles, sem exceção para serviços LAN. A pesquisa usa o cliente HTTPS próprio do DDGS, com TLS verificado e timeout, dentro do mesmo worker/orçamento. Não compartilha cookies/autenticação do navegador ou headers do HA/LLM. Ao cancelar/expirar, o worker continua ocupado até a operação nativa terminar; não acumula consultas concorrentes.

O log registra ferramenta, duração, status e uso de cache. `INTERNET_LOG_CONTENT=true` acrescenta consulta/resultado sanitizados para diagnóstico; textos podem ser privados. Nenhum WAV é enviado às APIs. Se fontes falharem, a resposta deve informar indisponibilidade ou falta de evidência, em vez de inventar dados. Páginas extensas, paywall, JavaScript, CAPTCHA ou motores bloqueados podem limitar a evidência disponível. Não há navegador, scraping ilimitado, alerta proativo ou assinatura de notícias.

## Windows e retorno

As mesmas variáveis ficam no `.env` privado do Windows. Na raiz do repositório, instale `& .\.venv\Scripts\python.exe -m pip install --only-binary=:all: -r .\requirements-internet.txt` e inicie com `& .\scripts\start.ps1`. Para desligar o módulo inteiro, defina `INTERNET_ENABLED=false` e reinicie; para desligar só busca, `WEB_SEARCH_ENABLED=false`. Nenhuma troca de ASR ou firmware é necessária para ativar/desativar as ferramentas.

## Estado de validação

Implementação e preparação documental entregues. Apenas análise estática de sintaxe/código; nenhum teste automatizado, startup, consulta funcional, container ou deploy executado pelo agente. Pendentes: obter dados reais de cada provedor, confirmação de cidade, feriados/moedas, notícia antiga/sem data, erros/timeouts, cancelamento, HA desligado com internet ligada, retorno à escuta e avaliação no Orange Pi. As evidências anteriores de Parakeet/HA/playback continuam separadas.

Referências: [Open-Meteo](https://open-meteo.com/en/docs), [geocoding](https://open-meteo.com/en/docs/geocoding-api), [termos/atribuição](https://open-meteo.com/en/terms), [PTAX/BCB](https://dadosabertos.bcb.gov.br/dataset/dolar-americano-usd-todos-os-boletins-diarios), [DDGS 9.16.1: API Python/licença MIT](https://pypi.org/project/ddgs/9.16.1/), [código DDGS](https://github.com/deedy5/ddgs).
