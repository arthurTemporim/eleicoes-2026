"""Processa log + RDV de cada seção do 2º turno, lendo os zips de urna remotamente.

Para cada UF:
  1. lê só o índice (diretório central) de bu_imgbu_logjez_rdv_vscmr_2026_2t_<UF>.zip no CDN do TSE;
  2. busca via HTTP Range apenas o log (log.jez) e o RDV (rdv.dat) de cada seção, sem gravar o zip;
  3. grava agregados em dataset/processed/.

Saídas (t2_<UF>):
  log_hora_*.parquet    votos computados e habilitações (total e biométricas) por seção e hora LOCAL da urna
                        (votos_biometria = votos cujo eleitor foi habilitado por biometria)
  log_secoes_*.parquet  uma linha por seção: hora em que a urna ficou pronta, nº de urnas no log, erros
  rdv_tipos_*.parquet   votos para Presidente por seção e tipo (nominal, branco, nulo...)
  rdv_nulos_*.parquet   números digitados nos votos nulos para Presidente, por seção

Mensagens do log usadas (conferidas nos logs de 2026):
  "O voto do eleitor foi computado"                -> um voto
  "Eleitor foi habilitado"                         -> um eleitor liberado para votar
  "Tipo de habilitação do eleitor [biométrica]"    -> a habilitação seguinte foi por biometria
  "Urna pronta para receber votos"                 -> abertura (8h de Brasília; usada para inferir o fuso)

Uso:
    python scripts/process_urna.py --ufs AC
    python scripts/process_urna.py                    # todas as UFs
"""
import argparse
import io
import os
import re
import sys
import time
import zipfile
from collections import Counter, defaultdict
from multiprocessing import Pool

import asn1tools
import pandas as pd
import py7zr

from datasets import DATA_TURNO, PACOTES, PROCESSED_DIR, TURNO, UFS, info_arquivo
from download import listar_recursos
from remote_zip import ler_membros, listar, nova_sessao

SPEC_RDV = os.path.join(os.path.dirname(__file__), "spec", "rdv.asn1")

# o<pleito:5><uf:2><municipio:5><zona:4><secao:4>-<tipo>.<ext>
RE_NOME = re.compile(r"^o\d{5}([a-z]{2})(\d{5})(\d{4})(\d{4})-(\w+)\.(\w+)$")

MSG_VOTO = "O voto do eleitor foi computado"
MSG_HABILITADO = "Eleitor foi habilitado"
MSG_BIOMETRICA = "Tipo de habilitação do eleitor [biométrica]"
MSG_PRONTA = "Urna pronta para receber votos"

MAGIC_ZIP = b"PK\x03\x04"
MAGIC_7Z = b"7z\xbc\xaf\x27\x1c"

CARGO_PRESIDENTE = ("cargoConstitucional", "presidente")
TIPOS_NULO_DIGITADO = {"nulo", "nuloPorRepeticao"}

SAIDAS = ("log_hora", "log_secoes", "rdv_tipos", "rdv_nulos")
SECOES_POR_TAREFA = 8

# Estado por processo do pool
_spec = None
_sessao = None
_url = None
_tamanho = None
_data = DATA_TURNO


def _init_worker(url, tamanho):
    global _spec, _sessao, _url, _tamanho
    _spec = asn1tools.compile_files(SPEC_RDV, codec="ber")
    _sessao = nova_sessao()
    _url, _tamanho = url, tamanho


def linhas_log(blob):
    """Linhas de todos os logd.dat do pacote (.jez pode ser zip ou 7z e conter pacotes arquivados)."""
    if blob[:4] == MAGIC_ZIP:
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            arquivos = [(n, z.read(n)) for n in z.namelist()]
    elif blob[:6] == MAGIC_7Z:
        with py7zr.SevenZipFile(io.BytesIO(blob)) as z:
            arquivos = [(n, f.read()) for n, f in z.readall().items()]
    else:
        return blob.decode("latin-1").splitlines()
    linhas = []
    for nome, conteudo in arquivos:
        if conteudo[:4] == MAGIC_ZIP or conteudo[:6] == MAGIC_7Z:
            linhas.extend(linhas_log(conteudo))
        elif nome.endswith(".dat"):
            linhas.extend(conteudo.decode("latin-1").splitlines())
    return linhas


def processar_log(blob):
    """Retorna (contagens por hora, hora_pronta, n_urnas).

    A sequência no log é: [Tipo de habilitação [biométrica]] -> Eleitor foi habilitado -> ... ->
    O voto do eleitor foi computado. Cada voto computado herda o tipo da última habilitação.
    """
    por_hora = defaultdict(Counter)
    pronta = None
    urnas = set()
    tipo_bio_pendente = False  # viu "Tipo de habilitação [biométrica]" e aguarda "Eleitor foi habilitado"
    habilitado_bio = False     # tipo da habilitação do eleitor que está votando
    # pacotes arquivados podem repetir linhas do log atual: cada linha tem hash próprio, então
    # dict.fromkeys deduplica preservando a ordem
    for linha in dict.fromkeys(linhas_log(blob)):
        campos = linha.split("\t")
        if len(campos) < 5 or not campos[0].startswith(_data):
            continue
        msg = campos[4]
        hora = int(campos[0][11:13])
        if msg == MSG_VOTO:
            por_hora[hora]["votos"] += 1
            por_hora[hora]["votos_biometria"] += habilitado_bio
            habilitado_bio = False
            urnas.add(campos[2])
        elif msg == MSG_HABILITADO:
            por_hora[hora]["habilitados"] += 1
            por_hora[hora]["habilitados_biometria"] += tipo_bio_pendente
            habilitado_bio, tipo_bio_pendente = tipo_bio_pendente, False
        elif msg == MSG_BIOMETRICA:
            tipo_bio_pendente = True
        elif msg.startswith("Habilitação cancelada"):
            tipo_bio_pendente = False
        elif msg == MSG_PRONTA:
            hhmmss = campos[0][11:19]
            pronta = min(pronta, hhmmss) if pronta else hhmmss
    return por_hora, pronta, len(urnas)


def processar_rdv(blob):
    """Votos para Presidente: (Counter[tipo], Counter[(tipo, digitacao)])."""
    entidade = _spec.decode("EntidadeResultadoRDV", blob)
    _, eleicoes = entidade["rdv"]["eleicoes"]
    tipos, nulos = Counter(), Counter()
    for eleicao in eleicoes:
        for vc in eleicao["votosCargos"]:
            if vc["idCargo"] != CARGO_PRESIDENTE:
                continue
            for voto in vc["votos"]:
                tipo = voto["tipoVoto"]
                tipos[tipo] += 1
                if tipo in TIPOS_NULO_DIGITADO and voto.get("digitacao"):
                    nulos[(tipo, voto["digitacao"])] += 1
    return tipos, nulos


def processar_secao(chave, arquivos, conteudos):
    secao = {"cd_municipio": chave[0], "nr_zona": chave[1], "nr_secao": chave[2],
             "hora_pronta": None, "n_urnas_log": 0, "tem_log": "log" in arquivos,
             "tem_rdv": "rdv" in arquivos, "erro": None}
    horas, tipos, nulos = [], [], []
    erros = []
    if "log" in arquivos:
        try:
            por_hora, secao["hora_pronta"], secao["n_urnas_log"] = processar_log(conteudos[arquivos["log"].filename])
            horas = [(h, c["votos"], c["votos_biometria"], c["habilitados"], c["habilitados_biometria"])
                     for h, c in por_hora.items()]
        except Exception as e:
            erros.append(f"log: {e!r}")
    if "rdv" in arquivos:
        try:
            t, n = processar_rdv(conteudos[arquivos["rdv"].filename])
            tipos = list(t.items())
            nulos = [(*k, q) for k, q in n.items()]
        except Exception as e:
            erros.append(f"rdv: {e!r}")
    secao["erro"] = "; ".join(erros) or None
    return chave, secao, horas, tipos, nulos


def processar_lote(lote):
    """Busca log + RDV de um lote de seções numa leva de requisições e processa cada seção."""
    infos = [info for _, arquivos in lote for info in arquivos.values()]
    try:
        conteudos, transferido = ler_membros(_sessao, _url, _tamanho, infos)
    except Exception as e:
        erro = f"download: {e!r}"
        return [(chave, {"cd_municipio": chave[0], "nr_zona": chave[1], "nr_secao": chave[2],
                         "hora_pronta": None, "n_urnas_log": 0, "tem_log": "log" in arq,
                         "tem_rdv": "rdv" in arq, "erro": erro}, [], [], [])
                for chave, arq in lote], 0
    return [processar_secao(chave, arquivos, conteudos) for chave, arquivos in lote], transferido


def agrupar_secoes(infos):
    """{(municipio, zona, secao): {"log": ZipInfo, "rdv": ZipInfo}} na ordem do zip."""
    secoes = defaultdict(dict)
    for info in sorted(infos, key=lambda i: i.header_offset):
        m = RE_NOME.match(info.filename)
        if not m:
            continue
        _, mun, zona, secao, tipo, ext = m.groups()
        chave = (int(mun), int(zona), int(secao))
        if tipo == "log" and ext == "jez":
            secoes[chave]["log"] = info
        elif tipo == "rdv" and ext == "dat":
            secoes[chave]["rdv"] = info
    return secoes


def processar_uf(url, uf, workers):
    infos, tamanho = listar(url)
    secoes = list(agrupar_secoes(infos).items())
    lotes = [secoes[i:i + SECOES_POR_TAREFA] for i in range(0, len(secoes), SECOES_POR_TAREFA)]
    reg_secoes, reg_horas, reg_tipos, reg_nulos = [], [], [], []
    transferido, inicio = 0, time.time()
    with Pool(workers, initializer=_init_worker, initargs=(url, tamanho)) as pool:
        for i, (resultados, bytes_lote) in enumerate(pool.imap_unordered(processar_lote, lotes), 1):
            transferido += bytes_lote
            for chave, secao, horas, tipos, nulos in resultados:
                reg_secoes.append(secao)
                reg_horas += [(*chave, *h) for h in horas]
                reg_tipos += [(*chave, *t) for t in tipos]
                reg_nulos += [(*chave, *n) for n in nulos]
            if i % 500 == 0 or i == len(lotes):
                dt = time.time() - inicio
                print(f"  {uf}: {len(reg_secoes)}/{len(secoes)} seções, {transferido / 1e9:.2f} GB, "
                      f"{len(reg_secoes) / dt:.0f} seções/s, {transferido / dt / 1e6:.0f} MB/s", flush=True)

    chave_cols = ["cd_municipio", "nr_zona", "nr_secao"]
    dfs = {
        "log_secoes": pd.DataFrame(reg_secoes),
        "log_hora": pd.DataFrame(reg_horas, columns=chave_cols + [
            "hora_local", "votos", "votos_biometria", "habilitados", "habilitados_biometria"]),
        "rdv_tipos": pd.DataFrame(reg_tipos, columns=chave_cols + ["tipo_voto", "qt_votos"]),
        "rdv_nulos": pd.DataFrame(reg_nulos, columns=chave_cols + ["tipo_voto", "digitacao", "qt_votos"]),
    }
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    for nome, df in dfs.items():
        df.insert(0, "sg_uf", uf)
        df.to_parquet(saida(nome, uf), index=False)
    n_erros = int(dfs["log_secoes"]["erro"].notna().sum()) if reg_secoes else 0
    print(f"{uf}: {len(secoes)} seções, {transferido / 1e9:.2f} GB lidos em {time.time() - inicio:.0f} s, "
          f"{n_erros} seções com erro", flush=True)


def saida(nome, uf):
    return PROCESSED_DIR / f"{nome}_t{TURNO}_{uf}.parquet"


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ufs", nargs="+", default=UFS)
    p.add_argument("--workers", type=int, default=32, help="processos em paralelo (o gargalo é a rede)")
    args = p.parse_args(argv)

    urls = {}
    for url in listar_recursos(PACOTES["urna"]):
        turno, uf = info_arquivo(url)
        if url.endswith(".zip") and turno == TURNO:
            urls[uf] = url
    for uf in args.ufs:
        if uf not in urls:
            print(f"{uf}: arquivo do {TURNO}º turno não encontrado no CKAN", file=sys.stderr)
            continue
        if all(saida(n, uf).exists() for n in SAIDAS):
            print(f"{uf}: já processado")
            continue
        processar_uf(urls[uf], uf, args.workers)


if __name__ == "__main__":
    sys.exit(main())
