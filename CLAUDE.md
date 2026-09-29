# ArenaReplay - Sistema de Replay Instantâneo — Futevôlei / Vôlei de Areia / Beach Tenis

## Visão geral

Uma câmera grava a quadra continuamente. Quando um jogador aciona o gatilho, o sistema gera um clipe com os ~30 segundos anteriores (mais alguns segundos de pós-jogada). Os clipes são registrados no banco e expostos por uma API, que no futuro será consumida por uma plataforma web onde os alunos assistem e baixam as jogadas.

## Fase atual (MVP)

- Câmera: celular com app de câmera IP (ex: IP Webcam no Android), stream RTSP H.264 na rede local. Enquanto o celular não é usado, a fonte é o `scripts/fake_camera.sh`.
- Gatilho: tecla do teclado (captura global); `--stdin` como alternativa de desenvolvimento.
- Tudo roda em um PC: Ubuntu 24.04 LTS, sessão Xorg, Intel i3, 8 GB RAM, sem GPU dedicada (Intel Quick Sync disponível via VAAPI).
- Desenvolvimento em macOS (Apple M1, Docker Desktop, Homebrew). O código deve rodar nos dois; o que depende de hardware (encoder, permissão de teclado) é configurável.
- Banco de dados: PostgreSQL rodando em container Docker (docker compose).
- Serviços Python rodam diretamente no host (não em container), pois precisam de acesso ao teclado, à rede local e ao VAAPI. Em produção, como serviços systemd de usuário.
- Armazenamento dos clipes em disco local.
- Interface: apenas uma API JSON e uma página HTML mínima de teste. NÃO construir frontend completo nesta fase.

Etapas concluídas: configuração e banco; captura; gatilho e clipper; API; monitoramento (logs em arquivo e heartbeats); `run_all.sh`, systemd e README.

## Evolução planejada (não implementar agora, mas a arquitetura deve permitir sem refatoração)

- **Plataforma web dos alunos em React/Next.js**, consumindo a API deste projeto. Será um projeto/pasta separado, criado futuramente. Por isso a API deve ser bem definida, versionada (`/api/v1`) e documentada via OpenAPI.
- Câmera definitiva: câmera IP outdoor com PoE, RTSP/ONVIF, à prova d'água e resistente a sol. A troca deve exigir apenas mudar a URL na configuração.
- Gatilho físico: botão ligado a Arduino/ESP32 via porta serial ou USB HID; possivelmente gatilho via HTTP (a API não chama ffmpeg: o caminho previsto é uma fila no banco com `SELECT … FOR UPDATE SKIP LOCKED` consumida pelo worker).
- Múltiplas quadras, cada uma com sua câmera e seu gatilho, no mesmo PC.
- Armazenamento em nuvem (Cloudflare R2 ou S3) e acesso externo dos alunos. `ClipStorage.local_path()` retorna `None` para storage remoto e a API redireciona para `url_for()`.
- Possível câmera lenta (câmeras de 60 fps).
- Possível containerização dos demais serviços.
- Alertas externos (Telegram/e-mail) quando `/api/v1/status` indicar problema.

## Stack

- Python 3.12 (padrão do Ubuntu 24.04), ambiente virtual `.venv` (no macOS, usar `python3.12` explicitamente)
- ffmpeg (captura, segmentação, concatenação, thumbnails); encoders `libx264`, `h264_vaapi` (Ubuntu) e `h264_videotoolbox` (macOS, só desenvolvimento)
- PostgreSQL 16 em Docker, via `docker-compose.yml`; `mediamtx` (perfil `fakecam`) como servidor RTSP da câmera falsa
- SQLAlchemy 2.x + psycopg 3 para acesso ao banco; Alembic para migrations
- FastAPI + Uvicorn para a API
- Configuração em YAML (parâmetros do sistema) + `.env` (segredos e `DATABASE_URL`), validados na inicialização com pydantic / pydantic-settings
- pynput para a tecla (funciona na sessão Xorg)
- systemd (unidades de usuário) para execução em produção com reinício automático
- pytest (+ httpx2 para o TestClient) para testes, ruff para lint e formatação (linha de 100 colunas)
- Futuro (fora deste escopo): React/Next.js para o frontend dos alunos

## Arquitetura

Três processos Python no host + um container de banco:

1. **Capture** — um processo ffmpeg por quadra, supervisionado por Python; limpa o buffer circular.
2. **Trigger/Clipper** — escuta gatilhos e gera os clipes (fila com um worker).
3. **API** — expõe clipes, metadados e o status do sistema em JSON e serve os arquivos de vídeo.
4. **PostgreSQL** (Docker) — fonte única de verdade dos metadados.

Os processos se comunicam apenas pelo sistema de arquivos e pelo banco. Nenhum processo importa código interno de outro, exceto os módulos compartilhados (`config`, `models`, `buffer`, `status`, `logs`, `db`, `storage`). Os contratos em arquivo são:

- **Segmentos** (`buffer.py`): `data/segments/<court_id>/<court_id>_YYYYmmdd_HHMMSS.ts`, horário UTC de abertura do segmento.
- **Leases** (`buffer.py`): `data/segments/<court_id>/.leases/<clip_id>.json` com a janela do clipe; a limpeza não apaga segmentos que cruzam um lease (folga de 10 s); leases com mais de 10 min são descartados.
- **Heartbeats** (`status.py`): `data/status/<service>.json`, reescrito a cada 5 s por capture e clipper; `state: stopped` no encerramento limpo; sem atualização por 15 s = serviço `down`.

### Estrutura de pastas

```
replay/
  config.py          # carrega e valida YAML + .env
  models.py          # dataclasses de domínio (Court, Clip, Segment, ClipStatus)
  buffer.py          # nomes de segmentos e leases (contrato capture ↔ clipper)
  status.py          # heartbeats dos serviços (contrato capture/clipper ↔ api)
  logs.py            # logging no console + arquivo por serviço com rotação diária
  capture/
    __main__.py      # python -m replay.capture
    supervisor.py    # inicia o ffmpeg, monitora, reconecta com backoff, expõe o estado
    cleanup.py       # remove segmentos fora da janela do buffer, respeitando leases
  triggers/
    base.py          # interface Trigger: start(callback), stop(); Debouncer
    keyboard.py      # implementação com pynput
    stdin.py         # gatilho pelo terminal (desenvolvimento, SSH)
  clipper/
    __main__.py      # python -m replay.clipper [--stdin]
    segment_index.py # encontra os segmentos que cobrem uma janela de tempo e os buracos
    builder.py       # concatena, ajusta duração, gera thumbnail
    worker.py        # fila de jobs; o gatilho nunca bloqueia esperando o corte
  storage/
    base.py          # interface ClipStorage: save, url_for, delete, local_path
    local.py         # implementação em disco
  db/
    engine.py        # engine e sessões SQLAlchemy
    tables.py        # modelos ORM
    repository.py    # única camada que executa queries
  api/
    __main__.py      # python -m replay.api [--export-openapi FILE]
    app.py           # FastAPI, rotas em /api/v1, página /dev
    schemas.py       # modelos pydantic de resposta (contrato do futuro frontend)
    system_status.py # monta /api/v1/status a partir dos heartbeats
    dev_page.html    # página mínima de teste, descartável
  devtools/
    preview.py       # página estática com todos os clipes, inclusive falhos
migrations/          # Alembic
deploy/systemd/      # unidades systemd de usuário (template com @REPO_DIR@)
docs/openapi.json    # contrato da API para o frontend (versionado e testado)
docker/postgres-init/# cria o banco de testes na primeira inicialização do volume
scripts/
  run_all.sh         # sobe o banco, aplica migrations e os três serviços (--fake-camera, --stdin)
  install_services.sh# instala/remove as unidades systemd de usuário
  fake_camera.sh     # stream de teste gerado pelo ffmpeg (sem celular)
tests/
docker-compose.yml
config.example.yaml
.env.example
```

### Responsabilidades e limites

- **capture** só grava segmentos e limpa o buffer. Não sabe que clipes existem e não acessa o banco. Publica o estado por quadra (`connecting`/`recording`/`reconnecting`/`stopped`) no heartbeat.
- **triggers** só detectam o evento e chamam um callback com `court_id` e horário. Não sabem nada de vídeo. Aplicam debounce.
- **clipper** só lê a pasta de segmentos, produz clipes via `ClipStorage` e registra no banco via `repository`. Não sabe como o gatilho foi acionado. Publica fila, contadores e última falha no heartbeat.
- **api** só lê o banco via `repository`, os heartbeats via `status` e serve arquivos via `ClipStorage`. Nunca chama ffmpeg. É o único ponto de contato do futuro frontend Next.js.
- **storage** é a única camada que conhece caminhos físicos dos clipes finais.
- **repository** é a única camada que executa SQL; o resto do código não importa SQLAlchemy diretamente.

## Banco de dados (Docker)

- `docker-compose.yml` com PostgreSQL 16, volume nomeado para persistência, healthcheck, `restart: unless-stopped` e porta publicada apenas em `127.0.0.1` (`POSTGRES_PORT`, padrão 5432; no Mac de desenvolvimento a 5434, pois há outros Postgres).
- Credenciais vindas do `.env` (nunca commitado; manter `.env.example`).
- Banco de testes `<POSTGRES_DB>_test` no mesmo container, criado por `docker/postgres-init/` só na primeira inicialização do volume.
- Toda alteração de schema via migration do Alembic.
- Datas sempre em `timestamptz` (UTC no banco; conversão para America/Fortaleza apenas na exibição).

## Modelo de dados

Tabela `courts`: `id` (texto, o mesmo id do `config.yaml`, ex: `court1`), `name`, `created_at`. As quadras do config são sincronizadas pelo clipper ao iniciar (insere/atualiza, nunca apaga).
Tabela `clips`: `id` (uuid, gerado no gatilho e usado também no lease), `court_id` (FK), `triggered_at`, `start_at`, `end_at`, `duration_s`, `file_key`, `thumb_key`, `status` (`processing` | `ready` | `failed`), `error`, `created_at`.
Índice em (`court_id`, `triggered_at`).

- `triggered_at`, `start_at`, `end_at` são horários reais (já descontado o `latency_offset_s`). `end_at` é o fim real da janela coberta, não `start_at + duration_s` (com buracos o conteúdo é mais curto que a janela).
- O registro é criado pelo worker ao pegar o job (não no gatilho). Ao iniciar, o worker marca como `failed` os clipes presos em `processing`.
- `file_key`: `<court_id>/<YYYY>/<MM>/<DD>/<clip_id>.mp4` (data em UTC); `thumb_key` igual com `.jpg`.

## API (v1, contrato para o futuro frontend)

- `GET /api/v1/health` (banco ok?)
- `GET /api/v1/status` (estado dos serviços, das câmeras por quadra e do clipper; nível `ok`/`warning`/`error` com motivos)
- `GET /api/v1/courts`
- `GET /api/v1/clips?court_id=&date=&limit=&cursor=` (paginação por cursor opaco, mais recentes primeiro, apenas `ready`; `date` é o dia local no `timezone` do config; `limit` 1–100)
- `GET /api/v1/clips/{id}` (qualquer status, para o cliente acompanhar até `ready`; não expõe a mensagem de erro)
- `GET /api/v1/clips/{id}/video` (com suporte a HTTP Range, para permitir avançar no player)
- `GET /api/v1/clips/{id}/thumbnail`
- `GET /api/v1/clips/{id}/download` (com `Content-Disposition: attachment`, nome no horário local)
- CORS configurável, já preparado para a origem do futuro app Next.js.
- Documentação em `/api/v1/docs`; `docs/openapi.json` é versionado e um teste falha se a API mudar sem regenerá-lo (`python -m replay.api --export-openapi docs/openapi.json`).
- `/dev` serve a página de teste (fora do contrato).

## Regras técnicas do vídeo

- Segmentos curtos (padrão 2 s) com `-c:v copy`, usando `-f segment -strftime 1 -reset_timestamps 1`, com o horário de início no nome do arquivo (ex: `court1_20260928_153012.ts`). O ffmpeg roda com `TZ=UTC`. Preferir `.ts` para os segmentos, pois é robusto a interrupções.
- Usar o stream RTSP H.264 (`/h264_ulaw.sdp` no IP Webcam, `-rtsp_transport tcp`), não o MJPEG. O áudio é transcodificado para AAC 48 kHz (MPEG-TS não aceita μ-law; 8 kHz é ruim para navegadores); `capture.audio: false` descarta.
- Os segmentos são cortados em keyframes: a duração real segue o GOP da câmera. O `segment_index` não supõe duração fixa: o fim de cada segmento é limitado pela duração real (ffprobe), senão o último segmento antes de uma queda "esconde" o buraco.
- O segmento em gravação no momento do gatilho está incompleto: o worker espera o pós-jogada e até existir um segmento que começa depois do fim da janela (ou um prazo), antes de concatenar.
- `capture.latency_offset_s` compensa o atraso do stream: a janela do clipe é deslocada por ele.
- Com `-c copy`, os cortes só são exatos em keyframes. Abordagem padrão (`clip.encoder: copy`): concatenar sem recodificar (input seek) e ajustar a duração; recodificação opcional via `clip.encoder` (`libx264`, `h264_vaapi`, `h264_videotoolbox`). Trade-off documentado no README.
- O clipe final deve ser MP4 com `-movflags +faststart`, para começar a tocar no navegador antes do download completo.
- O buffer mantém N minutos por quadra; a limpeza nunca apaga segmentos que um job em andamento está usando (leases).
- Queda de stream (celular bloqueou, Wi-Fi caiu) é esperada: reconectar com backoff exponencial com jitter e registrar em log; stream sem segmento novo por `stall_timeout_s` também reinicia o ffmpeg. Buracos no buffer não podem quebrar o clipper, que deve gerar o clipe com o que existir e registrar o aviso.

## Configuração

`config.example.yaml`: `timezone`; lista de quadras (id, nome, URL do stream, tecla do gatilho, padrão `f9`); `capture` (duração do segmento, buffer em minutos, `latency_offset_s`, áudio, transporte RTSP, backoff, `stall_timeout_s`); `clip` (duração 30 s, pós-jogada 3 s, `encoder`, `vaapi_device`); `trigger.debounce_s`; `paths` (segmentos, clipes, logs, status); `api` (host, porta, origens CORS).
`.env.example`: `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `POSTGRES_PORT`, `DATABASE_URL`, `TEST_DATABASE_URL`.
`REPLAY_CONFIG` aponta para outro arquivo de config (usado em testes manuais).

## Monitoramento

- Logs no console e em `data/logs/<service>.log`, rotação à meia-noite, 14 dias. Access log do uvicorn só para respostas de erro (a página `/dev` faz polling). Linhas ruidosas do ffmpeg (`Non-monotonic DTS`, `Last message repeated`) vão para DEBUG.
- Heartbeats em `data/status/` lidos por `GET /api/v1/status`; a página `/dev` mostra uma barra de status atualizada a cada 3 s.
- Serviço: `running`, `stopped` (encerramento limpo), `down` (heartbeat parado há mais de 15 s), `unknown` (nunca rodou). Nível `error` se banco ou serviço fora; `warning` se câmera não está gravando ou clipe falhou nos últimos 10 min.

## Execução

- `scripts/run_all.sh [--fake-camera] [--stdin]`: tudo em um terminal; se um serviço morre, encerra todos; recusa rodar se as unidades systemd estiverem ativas.
- `scripts/install_services.sh [--uninstall]`: unidades systemd **de usuário** (`arena-replay-capture`, `-clipper`, `-api`) com `Restart=on-failure`; o clipper fica ligado a `graphical-session.target` porque o pynput precisa da sessão Xorg. Para rodar sem ninguém, login automático no Ubuntu.
- Serviços individuais: `python -m replay.capture`, `python -m replay.clipper [--stdin]`, `python -m replay.api`, `python -m replay.devtools.preview [--watch --open]`.

## Convenções

- Type hints em tudo, funções pequenas, logging com o módulo `logging` (nunca `print`), incluindo `court_id` nas mensagens (formato `[court1] ...`).
- Todo comando ffmpeg é montado em uma função testável que retorna a lista de argumentos.
- Operações que dependem de tempo ou processos externos aceitam injeção (`now`, `sleep`, `probe`, clock do debounce) para serem testáveis.
- Escritas de arquivos compartilhados entre processos (leases, heartbeats, páginas geradas) são atômicas: arquivo temporário + rename.
- Testes para `segment_index`, montagem de comandos ffmpeg, repositório (contra o Postgres de teste em Docker), debounce, builder e worker com ffmpeg real (segmentos gerados em `tests/conftest.py`), supervisor com um ffmpeg falso (`tests/fake_ffmpeg.py`), API e heartbeats. Testes de banco são pulados se o Postgres não estiver no ar; testes com ffmpeg, se ele não estiver instalado.
- Rodar `ruff check .` e `ruff format` (100 colunas) antes de commitar.
- Tudo em inglês: código, comentários, docstrings, mensagens de log e de erro, README, configs de exemplo, scripts, testes e mensagens de commit (Conventional Commits: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`).
- Commits breves, um por arquivo ou pequeno grupo (arquivo + seu teste).

## Forma de trabalho

- Antes de mudanças grandes, apresente o plano e aguarde confirmação.
- Implemente em etapas pequenas e diga como testar cada uma.
- Valide de ponta a ponta com a câmera falsa, não só com testes unitários, e diga o que não pôde ser testado no macOS (systemd, pynput no Xorg, VAAPI).
- Não crie nada de React/Next.js nesta fase.
- Mantenha o README atualizado: instalação (incluindo Docker), configuração do app no celular, como rodar e como testar.
- Mantenha este arquivo atualizado quando a arquitetura, os contratos ou as decisões mudarem.
