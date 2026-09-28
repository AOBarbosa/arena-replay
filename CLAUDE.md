# ArenaReplay - Sistema de Replay Instantâneo — Futevôlei / Vôlei de Areia / Beach Tenis

## Visão geral

Uma câmera grava a quadra continuamente. Quando um jogador aciona o gatilho, o sistema gera um clipe com os ~30 segundos anteriores (mais alguns segundos de pós-jogada). Os clipes são registrados no banco e expostos por uma API, que no futuro será consumida por uma plataforma web onde os alunos assistem e baixam as jogadas.

## Fase atual (MVP)

- Câmera: celular com app de câmera IP (ex: IP Webcam no Android), stream RTSP/HTTP na rede local.
- Gatilho: tecla do teclado (captura global).
- Tudo roda em um PC: Ubuntu 24.04 LTS, sessão Xorg, Intel i3, 8 GB RAM, sem GPU dedicada (Intel Quick Sync disponível via VAAPI).
- Banco de dados: PostgreSQL rodando em container Docker (docker compose).
- Serviços Python rodam diretamente no host (não em container), pois precisam de acesso ao teclado, à rede local e ao VAAPI.
- Armazenamento dos clipes em disco local.
- Interface: apenas uma API JSON e uma página HTML mínima de teste. NÃO construir frontend completo nesta fase.

## Evolução planejada (não implementar agora, mas a arquitetura deve permitir sem refatoração)

- **Plataforma web dos alunos em React/Next.js**, consumindo a API deste projeto. Será um projeto/pasta separado, criado futuramente. Por isso a API deve ser bem definida, versionada (`/api/v1`) e documentada via OpenAPI.
- Câmera definitiva: câmera IP outdoor com PoE, RTSP/ONVIF, à prova d'água e resistente a sol. A troca deve exigir apenas mudar a URL na configuração.
- Gatilho físico: botão ligado a Arduino/ESP32 via porta serial ou USB HID; possivelmente gatilho via HTTP.
- Múltiplas quadras, cada uma com sua câmera e seu gatilho, no mesmo PC.
- Armazenamento em nuvem (Cloudflare R2 ou S3) e acesso externo dos alunos.
- Possível câmera lenta (câmeras de 60 fps).
- Possível containerização dos demais serviços.

## Stack

- Python 3.12 (padrão do Ubuntu 24.04), ambiente virtual `.venv`
- ffmpeg (captura, segmentação, concatenação, thumbnails)
- PostgreSQL 16 em Docker, via `docker-compose.yml`
- SQLAlchemy 2.x + psycopg 3 para acesso ao banco; Alembic para migrations
- FastAPI + Uvicorn para a API
- Configuração em YAML (parâmetros do sistema) + `.env` (segredos e `DATABASE_URL`), validados na inicialização com pydantic
- pynput para a tecla (funciona na sessão Xorg)
- pytest para testes, ruff para lint e formatação
- Futuro (fora deste escopo): React/Next.js para o frontend dos alunos

## Arquitetura

Três processos Python no host + um container de banco:

1. **Capture** — um processo ffmpeg por quadra, supervisionado por Python.
2. **Trigger/Clipper** — escuta gatilhos e gera os clipes.
3. **API** — expõe clipes e metadados em JSON e serve os arquivos de vídeo.
4. **PostgreSQL** (Docker) — fonte única de verdade dos metadados.

Os processos se comunicam apenas pelo sistema de arquivos e pelo banco. Nenhum processo importa código interno de outro, exceto os módulos compartilhados (`config`, `db`, `storage`, `models`).

### Estrutura de pastas

```
replay/
  config.py          # carrega e valida YAML + .env
  models.py          # dataclasses de domínio (Court, Clip, Segment)
  capture/
    supervisor.py    # inicia o ffmpeg, monitora, reconecta com backoff
    cleanup.py       # remove segmentos fora da janela do buffer
  triggers/
    base.py          # interface Trigger: start(callback), stop()
    keyboard.py      # implementação com pynput
  clipper/
    segment_index.py # encontra os segmentos que cobrem uma janela de tempo
    builder.py       # concatena, ajusta duração, gera thumbnail
    worker.py        # fila de jobs; o gatilho nunca bloqueia esperando o corte
  storage/
    base.py          # interface ClipStorage: save, url_for, delete
    local.py         # implementação em disco
  db/
    engine.py        # engine e sessões SQLAlchemy
    tables.py        # modelos ORM
    repository.py    # única camada que executa queries
  api/
    app.py           # FastAPI, rotas em /api/v1
    schemas.py       # modelos pydantic de resposta (contrato do futuro frontend)
    dev_page.html    # página mínima de teste, descartável
migrations/          # Alembic
scripts/
  run_all.sh         # sobe o banco (se necessário) e os três serviços
  fake_camera.sh     # stream de teste gerado pelo ffmpeg (sem celular)
tests/
docker-compose.yml
config.example.yaml
.env.example
```

### Responsabilidades e limites

- **capture** só grava segmentos. Não sabe que clipes existem e não acessa o banco.
- **triggers** só detectam o evento e chamam um callback com `court_id` e horário. Não sabem nada de vídeo. Aplicam debounce.
- **clipper** só lê a pasta de segmentos, produz clipes via `ClipStorage` e registra no banco via `repository`. Não sabe como o gatilho foi acionado.
- **api** só lê o banco via `repository` e serve arquivos via `ClipStorage`. Nunca chama ffmpeg. É o único ponto de contato do futuro frontend Next.js.
- **storage** é a única camada que conhece caminhos físicos dos clipes finais.
- **repository** é a única camada que executa SQL; o resto do código não importa SQLAlchemy diretamente.

## Banco de dados (Docker)

- `docker-compose.yml` com PostgreSQL 16, volume nomeado para persistência, healthcheck e porta publicada apenas em `127.0.0.1`.
- Credenciais vindas do `.env` (nunca commitado; manter `.env.example`).
- Toda alteração de schema via migration do Alembic.
- Datas sempre em `timestamptz` (UTC no banco; conversão para America/Fortaleza apenas na exibição).

## Modelo de dados

Tabela `courts`: `id`, `name`, `created_at`.
Tabela `clips`: `id` (uuid), `court_id` (FK), `triggered_at`, `start_at`, `end_at`, `duration_s`, `file_key`, `thumb_key`, `status` (`processing` | `ready` | `failed`), `error`, `created_at`.
Índice em (`court_id`, `triggered_at`).

## API (v1, contrato para o futuro frontend)

- `GET /api/v1/courts`
- `GET /api/v1/clips?court_id=&date=&limit=&cursor=` (paginação por cursor, mais recentes primeiro, apenas `ready`)
- `GET /api/v1/clips/{id}`
- `GET /api/v1/clips/{id}/video` (com suporte a HTTP Range, para permitir avançar no player)
- `GET /api/v1/clips/{id}/thumbnail`
- `GET /api/v1/clips/{id}/download` (com `Content-Disposition: attachment`)
- CORS configurável, já preparado para a origem do futuro app Next.js.

## Regras técnicas do vídeo

- Segmentos curtos (padrão 2 s) com `-c copy`, usando `-f segment -strftime 1 -reset_timestamps 1`, com o horário de início no nome do arquivo (ex: `court1_20260928_153012.ts`). Preferir `.ts` para os segmentos, pois é robusto a interrupções.
- O segmento em gravação no momento do gatilho está incompleto: o worker deve esperar o pós-jogada e o fechamento do segmento seguinte antes de concatenar.
- Com `-c copy`, os cortes só são exatos em keyframes. Abordagem padrão: concatenar sem recodificar e depois ajustar a duração; recodificação (com VAAPI quando disponível) deve ser opcional via configuração. Documentar o trade-off.
- O clipe final deve ser MP4 com `-movflags +faststart`, para começar a tocar no navegador antes do download completo.
- O buffer mantém N minutos por quadra; a limpeza nunca apaga segmentos que um job em andamento está usando.
- Queda de stream (celular bloqueou, Wi-Fi caiu) é esperada: reconectar com backoff e registrar em log. Buracos no buffer não podem quebrar o clipper, que deve gerar o clipe com o que existir e registrar o aviso.

## Configuração

`config.example.yaml`: lista de quadras (id, nome, URL do stream, tecla do gatilho) e parâmetros globais: duração do clipe (30 s), pós-jogada (3 s), duração do segmento, tamanho do buffer em minutos, debounce, pastas de dados, porta da API, origens CORS permitidas, recodificação ligada/desligada.
`.env.example`: `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `DATABASE_URL`.

## Convenções

- Type hints em tudo, funções pequenas, logging com o módulo `logging` (nunca `print`), incluindo `court_id` nas mensagens.
- Todo comando ffmpeg é montado em uma função testável que retorna a lista de argumentos.
- Testes para `segment_index`, montagem de comandos ffmpeg, repositório (contra um Postgres de teste em Docker) e debounce. Testes de integração podem usar o `fake_camera.sh`.
- Comentários e mensagens de log em português.

## Forma de trabalho

- Antes de mudanças grandes, apresente o plano e aguarde confirmação.
- Implemente em etapas pequenas e diga como testar cada uma.
- Não crie nada de React/Next.js nesta fase.
- Mantenha o README atualizado: instalação (incluindo Docker), configuração do app no celular, como rodar e como testar.
