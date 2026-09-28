#!/usr/bin/env bash
# Câmera falsa para desenvolvimento: gera vídeo de teste com o ffmpeg e publica
# em RTSP (H.264 + áudio μ-law, como o IP Webcam) através do container mediamtx.
#
# Uso: scripts/fake_camera.sh [court_id] [gop_segundos]
#   court_id      caminho do stream (padrão: court1) -> rtsp://127.0.0.1:8554/court1
#   gop_segundos  intervalo entre keyframes (padrão: 2). Use 4-5 para simular um celular.
#
# Variáveis: FAKECAM_RTSP_PORT (padrão 8554), FAKECAM_SIZE (1280x720), FAKECAM_FPS (30)
set -euo pipefail

cd "$(dirname "$0")/.."

COURT_ID="${1:-court1}"
GOP_S="${2:-2}"
PORT="${FAKECAM_RTSP_PORT:-8554}"
SIZE="${FAKECAM_SIZE:-1280x720}"
FPS="${FAKECAM_FPS:-30}"
URL="rtsp://127.0.0.1:${PORT}/${COURT_ID}"

command -v ffmpeg >/dev/null || { echo "ffmpeg não encontrado" >&2; exit 1; }

echo "Subindo o servidor RTSP (mediamtx)..."
docker compose --profile fakecam up -d mediamtx >/dev/null

# Espera a porta RTSP aceitar conexões
for _ in $(seq 1 20); do
    if (exec 3<>"/dev/tcp/127.0.0.1/${PORT}") 2>/dev/null; then
        break
    fi
    sleep 0.5
done

# Relógio sobreposto (só se o ffmpeg tiver o filtro drawtext, que depende do freetype)
VF="format=yuv420p"
if ffmpeg -hide_banner -filters 2>/dev/null | grep -q " drawtext "; then
    VF="drawtext=text='%{localtime}':fontsize=48:fontcolor=white:box=1:boxcolor=black@0.6:x=20:y=20,${VF}"
else
    echo "Aviso: ffmpeg sem drawtext; o vídeo não terá o relógio sobreposto." >&2
fi

GOP_FRAMES=$(( GOP_S * FPS ))
echo "Publicando ${URL} (${SIZE}@${FPS}fps, keyframe a cada ${GOP_S}s). Ctrl+C para parar."

exec ffmpeg -hide_banner -loglevel warning \
    -re -f lavfi -i "testsrc2=size=${SIZE}:rate=${FPS}" \
    -re -f lavfi -i "sine=frequency=440:beep_factor=4:sample_rate=8000" \
    -vf "${VF}" \
    -c:v libx264 -preset veryfast -tune zerolatency -profile:v main \
    -g "${GOP_FRAMES}" -keyint_min "${GOP_FRAMES}" -sc_threshold 0 -b:v 2M \
    -c:a pcm_mulaw -ar 8000 -ac 1 \
    -f rtsp -rtsp_transport tcp "${URL}"
