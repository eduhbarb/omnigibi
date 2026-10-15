# 🗄️ Omnigibi

Omnigibi é uma banca virtual que faz **webscraping de quadrinhos** em várias lojas, usa um
**sistema inteligente de busca de preços** para descobrir o **menor preço já
registrado** e mantém um **histórico de variações** — gravando, a cada mudança,
o quanto o preço subiu/caiu (R$ e %) e o desconto vigente em %.

Backend em **Python (FastAPI)** + interface web própria (HTML/CSS/JS sem
dependências externas).

---

## ✨ O que ele faz

| Recurso | Como funciona |
|---|---|
| **Busca agregada** | Dispara todos os coletores em paralelo (`ThreadPoolExecutor`), tolerando lojas fora do ar. |
| **Busca inteligente** | Normaliza títulos (acentos, stopwords, ruído de anúncio), **identifica a edição/volume** e pontua relevância (Jaccard + SequenceMatcher + cobertura dos termos). Se a consulta literal não achar nada, tenta variações dela. |
| **Identidade do quadrinho** | Volume/edição fazem parte da identidade: **Vol. 1 e Vol. 2 nunca são o mesmo produto** (nem no agrupamento de preços, nem no histórico). |
| **Menor preço** | Ranqueia os grupos pelo menor preço e cruza com o histórico: marca 🏆 quando o preço atual bate o **menor valor já registrado**. |
| **Histórico de preços** | Uma linha em `price_history` **sempre que o preço muda**, com preço anterior, `delta` (R$), `delta_pct` (%), `discount_pct` (%) e o menor preço até aquele momento. |
| **Prateleira (banca)** | Monitoramento contínuo dos quadrinhos escolhidos, com sparkline, alerta de preço alvo e exportação CSV. |
| **Produto esgotado** | Item sem oferta disponível **não é descartado**: entra marcado como “em falta” (na busca e na prateleira), com o último preço conhecido quando existe. |
| **Capa variante** | A capa variante é um produto **distinto** da capa normal do mesmo número — nunca são fundidas no agrupamento nem no histórico. |
| **Pré-venda** | Produto ainda não lançado vem sinalizado como **📅 pré-venda** (continua comprável). |
| **Avisos de estoque** | Quando uma loja monitorada **volta a ter estoque**, um aviso é registrado e aparece no painel **🔔 Avisos** (com contador de não lidos). |
| **Atualização automática** | APScheduler reconsulta as lojas a cada `SCRAPE_INTERVAL_MINUTES`. |

### Exemplo de registro de histórico

| Data | Preço anterior | Novo preço | Variação | Desconto | Menor até aqui |
|---|---|---|---|---|---|
| 12/03 09:10 | R$ 129,90 | R$ 89,90 | ▼ R$ 40,00 (30,79%) | -30,79% | R$ 89,90 |
| 12/03 15:42 | R$ 89,90 | R$ 74,90 | ▼ R$ 15,00 (16,69%) | -42,34% | R$ 74,90 |
| 13/03 08:01 | R$ 74,90 | R$ 82,00 | ▲ R$ 7,10 (9,48%) | -36,87% | R$ 74,90 |

Repare que o **menor preço histórico é preservado** mesmo quando o valor sobe.

---

## 🚀 Como rodar

```powershell
cd banca-quadrinhos

python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

.\.venv\Scripts\python.exe run.py
```

- Interface: <http://127.0.0.1:8000/>
- Documentação da API (Swagger): <http://127.0.0.1:8000/docs>

Na primeira execução a banca é populada automaticamente com clássicos
(desative com `SEED_DEMO_DATA=false`).

```powershell
.\.venv\Scripts\python.exe run.py --reload        # desenvolvimento
.\.venv\Scripts\python.exe -m pytest              # testes
```

---

## 🧠 Como a "inteligência" de preços funciona

```
busca do usuário
      │
      ├─► normalização + identidade da edição (volume / nº / "Ano Um")
      ├─► coletores em paralelo  ──►  ofertas normalizadas (OfferResult)
      │        (se a consulta literal falhar, tenta variações dela)
      ├─► pontuação de relevância ──► resultados  +  relacionados
      ├─► agrupamento (mesmo produto entre lojas, nunca volumes diferentes)
      └─► ranking por preço + cruzamento com o histórico da banca
```

O agrupamento é o que permite comparar o **mesmo** quadrinho entre lojas com
títulos escritos de forma diferente
(`"Watchmen - Edição Definitiva"` ≈ `"Watchmen Edicao Definitiva Capa Dura"`).

### Por que "Vol. 01" e "Vol. 02" nunca se misturam

O número da edição é extraído do título (`Vol. 2`, `#12`, `nº 7`, `Ano Um`,
`Volume Dois`, `Vol. II`) e entra na chave do produto. Duas ofertas só são
agrupadas quando:

1. os volumes são compatíveis (`same_product`), e
2. os títulos são equivalentes — igualdade após normalização, um título sendo o
   outro + sufixo de loja (`"... - Editora Independente"`) ou Jaccard alto.

Assim, `"Robin: Ano Um"` **não** é agrupado com `"Batman & Robin: Ano Um Vol. 01"`,
e `"Batman & Robin: Ano Um Vol. 02"` aparece na seção **Outros volumes e títulos
relacionados** — fora do histórico de preço do Vol. 01.

### Por que a "Capa Variante" não se mistura com a capa normal

Além do volume, a **capa variante** também faz parte da identidade. `"Absolute
Batman 01"` e `"Absolute Batman 01 - Capa Variante"` são **dois produtos
distintos**: um não é sufixo do outro. Grafias diferentes da variante
(`"capa variante"`, `"variant cover"`) são canonicalizadas para o mesmo marcador,
então duas variantes entre lojas continuam se agrupando.

Isso evita o pior cenário: comparar preços de volumes diferentes como se fossem
o mesmo produto e registrar um histórico que "pula" entre edições.

Regras de negócio em `app/services/price_service.py`:

- **Desconto** = maior preço de referência disponível (`preço "de"` da loja →
  `preço de capa` cadastrado) menos o preço atual.
- **Registro** só é criado se o preço realmente mudou (`abs(anterior - novo) >= 0,001`).
- Além do histórico por loja, existe a linha **consolidada** (`source = null`),
  que representa o **melhor preço entre todas as lojas** naquele instante.

### Produto esgotado e avisos de estoque

Um produto sem oferta disponível **não é removido**:

- o coletor **não descarta** itens sem preço quando a loja os sinaliza como
  indisponíveis — a oferta entra com `in_stock = false` e o preço pode ser nulo;
- a busca mostra o produto marcado como **“em falta”** e, se só algumas lojas
  ficaram sem estoque, um selo **“N loja(s) em falta”**;
- o “melhor preço” considera apenas ofertas **em estoque**; quando não há
  nenhuma, a prateleira exibe **“em falta”** em vez de um preço;
- ao **voltar ao estoque** uma loja monitorada, é gravado um registro em
  `alerts` (`kind = "volta_estoque"`) mostrado no painel **🔔 Avisos** — sem
  duplicar quando o estado não muda. Oferta esgotada sem preço **não gera
  histórico de preço** (não há valor a comparar).

---

## 🔌 Fontes de preço (scrapers)

| Fonte | Tipo | Observação |
|---|---|---|
| `amazon` | HTML + BeautifulSoup | `amazon.com.br/s?k=...&i=stripbooks`. Extrai título, preço, preço "de", ASIN e capa; detecta captcha e itens **em falta**; devolve erro tratado quando bloqueado. |
| `mercadolivre` | API pública JSON | `api.mercadolibre.com` — não exige chave (pode responder 403 dependendo da região). |
| `panini` | HTML (Magento 2) + `curl_cffi` | A loja fica atrás de um CDN que bloqueia clientes HTTP comuns por *fingerprint* de TLS (HTTP 403 até na home). O coletor usa `curl_cffi` imitando o Chrome para passar e lê o preço exato de `data-price-amount`. |
| `comix` | HTML + BeautifulSoup | Seletores declarativos em `app/scrapers/sites.json` (desabilitado por padrão). |

> A antiga fonte `simulador` (offline) foi **removida das categorias de busca**. O módulo continua no código apenas para os testes automatizados.

### Adicionar uma loja nova sem escrever código

Edite `app/scrapers/sites.json`:

```json
{
  "minha_loja": {
    "label": "Minha Loja de HQs",
    "homepage": "https://minhaloja.com.br",
    "search_url": "https://minhaloja.com.br/busca?q={query}",
    "enabled": true,
    "selectors": {
      "item": ".produto",
      "title": ".produto__titulo",
      "price": ".produto__preco",
      "link": "a",
      "image": "img"
    }
  }
}
```

Depois inclua `minha_loja` em `ENABLED_SOURCES`. Para lógicas específicas,
crie uma classe em `app/scrapers/` herdando de `BaseScraper` e registre-a em
`app/scrapers/__init__.py` (`_make`).

> ⚠️ Sites mudam de layout e usam anti-bot. Quando uma loja falha, a busca
> continua e a interface mostra quais fontes ficaram indisponíveis.

---

## ⚙️ Configuração (`.env`)

Copie `.env.example` para `.env`:

| Variável | Padrão | Descrição |
|---|---|---|
| `ENABLED_SOURCES` | `mercadolivre,amazon,panini` | Fontes consultadas. |
| `HTTP_TIMEOUT` | `12` | Timeout de cada requisição (s). |
| `MAX_RESULTS_PER_SOURCE` | `12` | Ofertas por loja em cada busca. |
| `USER_AGENT` | Chrome desktop | Agente enviado pelos coletores. A Amazon só entrega o HTML de busca para UA de navegador. |
| `SCHEDULER_ENABLED` | `true` | Atualização automática. |
| `SCRAPE_INTERVAL_MINUTES` | `30` | Intervalo da atualização automática. |
| `SEED_DEMO_DATA` | `true` | Popula a banca na primeira execução. |
| `DATABASE_URL` | SQLite em `data/banca.db` | Também aceita PostgreSQL (`postgresql+psycopg://...`). |
| `SIM_PRICE_BUCKET_SECONDS` | `120` | Granularidade da variação do simulador. |

---

## 🗺️ Endpoints principais

| Método | Rota | Descrição |
|---|---|---|
| `POST` | `/api/search` | Busca inteligente agregada (não persiste). Resposta: `results` (produtos), `related` (outros volumes/parecidos), `query_volume`, `query_variants` e o relatório de cada fonte. |
| `GET` | `/api/comics` | Prateleira com preço atual, menor histórico e desconto. |
| `POST` | `/api/comics` | Adiciona um quadrinho e já coleta os preços. |
| `GET` | `/api/comics/{id}` | Detalhe: ofertas, histórico consolidado e por loja. |
| `PATCH` | `/api/comics/{id}` | Edita dados e define o **preço alvo**. |
| `DELETE` | `/api/comics/{id}` | Remove da banca (apaga o histórico). |
| `POST` | `/api/comics/{id}/refresh` | Reconsulta as lojas e registra as variações. |
| `GET` | `/api/comics/{id}/history` | Histórico com delta, delta % e desconto %. |
| `GET` | `/api/comics/{id}/history.csv` | Exporta o histórico em CSV. |
| `POST` | `/api/refresh` | Atualiza toda a banca. |
| `GET` | `/api/changes` | Últimas variações registradas. |
| `GET` | `/api/alerts` | Avisos de monitoração (ex.: produto que voltou ao estoque). Inclui `unseen`. |
| `POST` | `/api/alerts/ack` | Marca um aviso (`{"alert_id": 1}`) ou todos como lidos. |
| `GET` | `/api/export` | Exporta a prateleira inteira (quadrinhos + histórico + avisos) em JSON. |
| `POST` | `/api/import` | Importa um arquivo exportado fazendo **merge** (não apaga nada). |
| `GET` | `/api/stats` · `/api/sources` | Indicadores e fontes disponíveis. |

---

## 💾 Backup (exportar/importar JSON)

O rodapé da página tem **Exportar dados (JSON)** e **Importar dados (JSON)**. O
arquivo leva os quadrinhos, o histórico (melhor preço + por loja) e as
**preferências** de interface (fontes escolhidas, filtro da prateleira, "só no
menor preço"). A importação faz **merge**: casa pelo título normalizado e
descarta observações de histórico já existentes — importar o mesmo arquivo duas
vezes não duplica nada. As preferências do arquivo só substituem as suas se a
opção "aplicar preferências do arquivo" estiver marcada.

---

## 🧱 Estrutura do projeto

```
banca-quadrinhos/
├── run.py                     # ponto de entrada (uvicorn)
├── requirements.txt
├── .env.example
├── app/
│   ├── main.py                # app FastAPI + ciclo de vida (init, seed, scheduler)
│   ├── config.py              # configuração via .env
│   ├── database.py            # engine/sessão SQLAlchemy + migração leve de schema
│   ├── models.py              # Comic, Offer, PriceHistory, Alert
│   ├── schemas.py             # contratos de entrada/saída
│   ├── api/routes.py          # rotas REST
│   ├── scrapers/
│   │   ├── base.py            # contrato + OfferResult + ScraperError (+ retry em 429/503)
│   │   ├── amazon.py          # fonte Amazon (seletores + captcha + itens em falta)
│   │   ├── panini.py          # fonte Panini (Magento 2 via curl_cffi)
│   │   ├── mercadolivre.py    # fonte via API JSON
│   │   ├── generic_html.py    # fonte HTML dirigida por CSS selectors
│   │   ├── sites.json         # lojas declarativas
│   │   └── simulator.py       # fonte offline — mantida só para testes
│   └── services/
│       ├── text.py            # normalização, volume/edição, preços, similaridade
│       ├── price_service.py   # histórico, delta, desconto, snapshots e avisos
│       ├── search_service.py  # busca agregada + clustering + ranking
│       ├── banca_service.py   # adicionar/atualizar/seed/estatísticas
│       └── scheduler.py       # atualização periódica
├── web/                       # interface (index.html, styles.css, app.js)
└── tests/                     # 50 testes (unidade + integração)
```

---

## ☁️ Deploy na Vercel

O projeto já vem com os arquivos de deploy:

- `api/index.py` — função serverless que expõe a app FastAPI (objeto `app`).
- `vercel.json` — builds (`@vercel/python` + `@vercel/static`) e rotas
  (`/static/*` → `web/*`, `/` → `web/index.html`, resto → a API).
- `.python-version` — `3.12`.

### Opção A — painel da Vercel (mais simples)

1. Suba o repositório no GitHub (veja abaixo).
2. Em <https://vercel.com/new>, importe o repositório.
3. A Vercel lê o `vercel.json` sozinha. Confirme e faça o deploy.

### Opção B — CLI

```bash
npm i -g vercel
vercel login
vercel --prod
```

### Variáveis de ambiente

Já há padrões em `vercel.json` (`SCHEDULER_ENABLED=false`,
`SEED_DEMO_DATA=false`, `ENABLED_SOURCES=mercadolivre,amazon,panini`,
`DATABASE_URL=sqlite:////tmp/omnigibi.db`). Para **persistir os dados**, troque
`DATABASE_URL` por um Postgres gerenciado (Vercel Postgres / Neon):

```bash
# requirements.txt precisa de: psycopg[binary]
DATABASE_URL=postgresql+psycopg://usuario:senha@host/banco
```

### Limites da Vercel (importante)

- **Sem disco persistente.** O SQLite em `/tmp` é apagado a cada *cold start*.
  Sem um Postgres, use **Exportar/Importar JSON** para não perder a prateleira.
- **Sem cron/APScheduler.** Use o botão **Atualizar tudo** (ou um cron externo
  chamando `POST /api/refresh`).
- **Timeout de função.** Buscas em muitas lojas podem estourar o limite do plano
  (Hobby é curto). Reduza `ENABLED_SOURCES` se der timeout.
- `curl_cffi` (usado na Panini) é um binário grande; o bundle ainda cabe, mas
  fique atento ao tamanho.

### Subir para o GitHub (local)

```bash
git init
git add .
git commit -m "chore: Omnigibi + deploy Vercel"
git branch -M main
git remote add origin https://github.com/<usuario>/<repo>.git
git push -u origin main
```

---

## 🧪 Testes

```powershell
.\.venv\Scripts\python.exe -m pytest -v
```

Cobrem os pontos críticos: parsing de preço pt-BR, normalização de títulos,
identificação de volume/edição (`Vol. 01` ≠ `Vol. 02`), separação da **capa
variante**, parsing do HTML da Amazon (incluindo captcha, **itens em falta** e
**pré-venda**), parsing do HTML da Panini (Magento 2, com pré-venda), agrupamento
de ofertas equivalentes, separação de livros diferentes, descarte de
irrelevantes, tolerância a falha de uma loja, **persistência de oferta esgotada
sem preço**, **aviso quando o produto volta ao estoque**, registro de queda/alta
com delta e desconto, preservação do menor preço histórico e o fluxo completo da
API (incluindo `/api/alerts`) e o **export/import** da prateleira (merge
idempotente, formato inválido, versão de schema).

---

## 📌 Limitações conhecidas

- Lojas com proteção anti-bot (Cloudflare, captcha, WAF por *fingerprint* de TLS)
  podem responder 403/503 de forma intermitente — a busca continua com as demais
  fontes e a interface mostra quais ficaram indisponíveis. A Panini cai nesse
  caso e é atendida com `curl_cffi` (imitação de TLS de navegador); para cenários
  ainda mais agressivos, troque o `BaseScraper` por uma implementação com
  navegador headless (Playwright).
- A coleta é feita apenas em páginas públicas de busca, com **uma requisição por
  consulta** e sem paralelismo agressivo — verifique os termos de uso e o
  `robots.txt` de cada loja antes de usar em produção.
- O `simulador` **não aparece nas categorias de busca**; ele existe apenas para
  os testes automatizados rodarem sem internet.
- Preço total com frete é exibido, mas o ranking principal usa o preço do produto.
