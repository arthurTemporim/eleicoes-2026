"""Baixa os dados de resultado e do Boletim de Urna do 2º turno.

- resultados: extrai só os CSVs listados em datasets.MEMBROS_RESULTADOS direto do zip remoto
  (HTTP Range), sem baixar o zip inteiro -> dataset/<nome do zip>/
- bu: baixa os zips bweb_2t_* para dataset/raw/bu/ (retomável, confere o .sha512 do TSE);
  depois rode scripts/extract.py

As URLs são lidas da API CKAN no momento da execução.

Uso:
    python scripts/download.py
    python scripts/download.py --pacotes bu --ufs AC SP
"""
import argparse
import hashlib
import sys
import time

import requests
from tqdm import tqdm

from datasets import CKAN_API, DATASET_DIR, MEMBROS_RESULTADOS, PACOTES, PACOTES_PADRAO, RAW_DIR, TURNO, info_arquivo
from remote_zip import extrair_membro

CHUNK = 1 << 20


def listar_recursos(pacote_id):
    """URLs dos recursos do pacote.

    A API do TSE fica atrás de um CDN que guarda as respostas em cache por até 1 h
    (cache-control: max-age=3600) e pode devolver uma versão antiga do pacote, sem arquivos
    publicados depois. O parâmetro "_" (ignorado pela API) força uma resposta atualizada.
    """
    resp = requests.get(CKAN_API, params={"id": pacote_id, "_": int(time.time())}, timeout=60)
    resp.raise_for_status()
    return [r["url"] for r in resp.json()["result"]["resources"]]


def selecionar_bu(urls, ufs=None):
    """Retorna [(url_zip, url_sha512 ou None)] do BU do 2º turno, filtrados por UF."""
    todas = set(urls)
    selecionadas = []
    for url in urls:
        turno, uf = info_arquivo(url)
        if not url.endswith(".zip") or turno != TURNO or (ufs and uf not in ufs):
            continue
        sha = url + ".sha512"
        selecionadas.append((url, sha if sha in todas else None))
    return selecionadas


def sha512_arquivo(caminho):
    h = hashlib.sha512()
    with open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(CHUNK), b""):
            h.update(bloco)
    return h.hexdigest()


def baixar(url, destino_dir, url_sha512=None):
    """Baixa url para destino_dir (retomável). Retorna o caminho final."""
    destino_dir.mkdir(parents=True, exist_ok=True)
    destino = destino_dir / url.rsplit("/", 1)[-1]
    if destino.exists():
        return destino

    parcial = destino.with_name(destino.name + ".part")
    inicio = parcial.stat().st_size if parcial.exists() else 0
    headers = {"Range": f"bytes={inicio}-"} if inicio else {}
    with requests.get(url, headers=headers, stream=True, timeout=120) as resp:
        if resp.status_code == 416:  # .part já completo
            pass
        else:
            resp.raise_for_status()
            if inicio and resp.status_code != 206:  # servidor ignorou o Range
                inicio = 0
            total = int(resp.headers.get("content-length", 0)) + inicio
            with open(parcial, "ab" if inicio else "wb") as f, tqdm(
                total=total, initial=inicio, unit="B", unit_scale=True, desc=destino.name
            ) as barra:
                for bloco in resp.iter_content(CHUNK):
                    f.write(bloco)
                    barra.update(len(bloco))

    if url_sha512:
        esperado = requests.get(url_sha512, timeout=60).text.split()[0].lower()
        obtido = sha512_arquivo(parcial)
        if obtido != esperado:
            parcial.unlink()
            raise RuntimeError(f"SHA-512 não confere para {destino.name}; arquivo descartado")
    parcial.rename(destino)
    return destino


def baixar_resultados():
    urls = {u.rsplit("/", 1)[-1]: u for u in listar_recursos(PACOTES["resultados"])}
    for zip_nome, membros in MEMBROS_RESULTADOS.items():
        destino_dir = DATASET_DIR / zip_nome.removesuffix(".zip")
        destino_dir.mkdir(parents=True, exist_ok=True)
        for membro in membros:
            destino = destino_dir / membro
            if destino.exists():
                continue
            parcial = destino.with_name(destino.name + ".part")
            extrair_membro(urls[zip_nome], membro, parcial)
            parcial.rename(destino)
            print(f"{membro}: {destino.stat().st_size / 1e6:.1f} MB")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--pacotes", nargs="+", choices=PACOTES_PADRAO, default=PACOTES_PADRAO)
    p.add_argument("--ufs", nargs="+", help="filtra o BU por UF (ex.: AC SP ZZ)")
    args = p.parse_args(argv)

    if "resultados" in args.pacotes:
        baixar_resultados()
    if "bu" in args.pacotes:
        falhas = []
        for url, sha in selecionar_bu(listar_recursos(PACOTES["bu"]), args.ufs):
            try:
                baixar(url, RAW_DIR / "bu", sha)
            except Exception as e:  # segue com os demais e reporta no fim
                print(f"ERRO {url}: {e}", file=sys.stderr)
                falhas.append(url)
        if falhas:
            sys.exit(f"{len(falhas)} arquivo(s) falharam")


if __name__ == "__main__":
    main()
