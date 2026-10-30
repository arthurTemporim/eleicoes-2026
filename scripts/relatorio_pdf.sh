#!/usr/bin/env bash
# Gera o PDF do relatório (relatorio.md -> relatorio.pdf), com os gráficos Mermaid renderizados.
#
# Usa a imagem oficial do mermaid-cli (Chromium + mermaid + marked); só requer Docker.
# O PDF não é versionado (ver .gitignore).
#
# Uso:
#   scripts/relatorio_pdf.sh                      # relatorio.md -> relatorio.pdf
#   scripts/relatorio_pdf.sh entrada.md saida.pdf
set -euo pipefail

IMAGEM="minlag/mermaid-cli:11.4.2"
RAIZ="$(cd "$(dirname "$0")/.." && pwd)"
ENTRADA="${1:-docs/relatorio.md}"
SAIDA="${2:-relatorio.pdf}"

docker run --rm \
  -u "$(id -u):$(id -g)" \
  -v "$RAIZ":/data -w /data \
  -e NODE_PATH=/home/mermaidcli/node_modules \
  --entrypoint node \
  "$IMAGEM" scripts/relatorio_pdf.cjs "$ENTRADA" "$SAIDA"
