# ArenaReplay

Replay instantâneo para quadras de areia: uma câmera grava continuamente e, ao
acionar o gatilho, o sistema gera um clipe com os ~30 s anteriores.
Arquitetura e regras em [CLAUDE.md](CLAUDE.md).

> Em construção. Etapa atual: **1 — configuração, banco e câmera falsa.**
> O README completo (celular, execução dos serviços) vem na etapa 5.

## Requisitos

- Ubuntu 24.04 (produção) ou macOS (apenas desenvolvimento)
- Python 3.12, ffmpeg, Docker com o plugin `docker compose`

```bash
# Ubuntu
sudo apt install python3.12-venv ffmpeg
# macOS
brew install python@3.12 ffmpeg
```

## Instalação

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev]"

cp .env.example .env              # troque a senha (em DATABASE_URL e TEST_DATABASE_URL também)
cp config.example.yaml config.yaml

docker compose up -d --wait db    # PostgreSQL 16 em 127.0.0.1:${POSTGRES_PORT}
.venv/bin/alembic upgrade head    # cria as tabelas
```

Se a porta 5432 já estiver em uso (outro Postgres na máquina), mude
`POSTGRES_PORT` e a porta nas duas URLs do `.env`.

O banco de testes (`<POSTGRES_DB>_test`) é criado automaticamente, mas **só na
primeira inicialização do volume**. Se o volume já existia, crie-o à mão:

```bash
docker compose exec db sh -c 'createdb -U "$POSTGRES_USER" "${POSTGRES_DB}_test"'
```

## Câmera falsa (sem celular)

```bash
scripts/fake_camera.sh court1      # publica rtsp://127.0.0.1:8554/court1
ffplay -rtsp_transport tcp rtsp://127.0.0.1:8554/court1
```

O script sobe o container `mediamtx` (servidor RTSP, perfil `fakecam`) e publica
um padrão de teste com bipe de áudio, em H.264 + μ-law como o IP Webcam.
O segundo argumento muda o intervalo de keyframes (ex: `scripts/fake_camera.sh court1 5`
para simular um celular com GOP longo).

## Testes e lint

```bash
.venv/bin/pytest            # testes de banco são pulados se o Postgres não estiver no ar
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```
