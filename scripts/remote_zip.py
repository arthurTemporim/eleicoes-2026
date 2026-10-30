"""Leitura seletiva de zips remotos via HTTP Range.

O CDN do TSE aceita requisições parciais (Range), então dá para ler o índice de um zip
(diretório central, no fim do arquivo) e buscar só os membros de interesse, sem baixar
o zip inteiro. Nos arquivos de urna isso evita ~28% dos bytes (assinaturas e imagens do BU)
e não ocupa disco.
"""
import struct
import zipfile
import zlib

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

LOCAL_HEADER = struct.Struct("<4s5H3L2H")  # assinatura, versão, flags, método, hora, data, crc, csize, usize, len(nome), len(extra)
MARGEM_EXTRA = 1024  # bytes a mais pedidos para cobrir o campo "extra" do local header


def nova_sessao(conexoes=4):
    retry = Retry(total=8, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504],
                  allowed_methods=["GET", "HEAD"])
    s = requests.Session()
    s.mount("https://", HTTPAdapter(max_retries=retry, pool_connections=conexoes, pool_maxsize=conexoes))
    return s


def ler_intervalo(sessao, url, inicio, fim):
    """Bytes [inicio, fim) do arquivo remoto."""
    resp = sessao.get(url, headers={"Range": f"bytes={inicio}-{fim - 1}"}, timeout=120)
    resp.raise_for_status()
    if resp.status_code != 206 or len(resp.content) != fim - inicio:
        raise IOError(f"resposta inesperada para Range {inicio}-{fim}: {resp.status_code}, {len(resp.content)} bytes")
    return resp.content


class HttpFile:
    """Arquivo somente leitura e seekable sobre HTTP Range (o suficiente para zipfile.ZipFile)."""

    def __init__(self, url, sessao=None):
        self.url = url
        self.sessao = sessao or nova_sessao()
        resp = self.sessao.head(url, timeout=60)
        resp.raise_for_status()
        self.tamanho = int(resp.headers["Content-Length"])
        self.pos = 0

    def seekable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, offset, whence=0):
        self.pos = {0: offset, 1: self.pos + offset, 2: self.tamanho + offset}[whence]
        return self.pos

    def read(self, n=-1):
        fim = self.tamanho if n is None or n < 0 else min(self.pos + n, self.tamanho)
        if fim <= self.pos:
            return b""
        dados = ler_intervalo(self.sessao, self.url, self.pos, fim)
        self.pos = fim
        return dados


def listar(url, sessao=None):
    """(infolist(), tamanho em bytes) do zip remoto, lendo só o diretório central."""
    f = HttpFile(url, sessao)
    with zipfile.ZipFile(f) as z:
        return z.infolist(), f.tamanho


def _descomprimir(info, bloco, offset_bloco, sessao, url):
    """Extrai o membro `info` de `bloco`, que começa em `offset_bloco` do arquivo remoto."""
    ini = info.header_offset - offset_bloco
    cab = LOCAL_HEADER.unpack_from(bloco, ini)
    if cab[0] != b"PK\x03\x04":
        raise IOError(f"local header inválido em {info.filename}")
    dados_ini = ini + LOCAL_HEADER.size + cab[9] + cab[10]
    dados = bloco[dados_ini:dados_ini + info.compress_size]
    if len(dados) < info.compress_size:  # campo extra maior que a margem: busca o membro de novo
        a = info.header_offset + dados_ini - ini
        dados = ler_intervalo(sessao, url, a, a + info.compress_size)
    if info.compress_type == zipfile.ZIP_DEFLATED:
        conteudo = zlib.decompress(dados, -15)
    elif info.compress_type == zipfile.ZIP_STORED:
        conteudo = dados
    else:
        raise IOError(f"método de compressão {info.compress_type} não suportado ({info.filename})")
    if zlib.crc32(conteudo) != info.CRC:
        raise IOError(f"CRC não confere em {info.filename}")
    return conteudo


def ler_membros(sessao, url, tamanho, infos, gap_max=16 * 1024):
    """Lê vários membros de um zip remoto (de `tamanho` bytes), juntando membros próximos numa mesma requisição.

    Retorna ({nome: bytes}, bytes_transferidos).
    """
    infos = sorted(infos, key=lambda i: i.header_offset)
    grupos, atual = [], []
    for info in infos:
        ini = info.header_offset
        fim = min(tamanho, ini + LOCAL_HEADER.size + len(info.orig_filename.encode()) + MARGEM_EXTRA + info.compress_size)
        if atual and ini - atual[-1][2] <= gap_max:
            atual.append((info, ini, max(fim, atual[-1][2])))
        else:
            if atual:
                grupos.append(atual)
            atual = [(info, ini, fim)]
    if atual:
        grupos.append(atual)

    saida, transferido = {}, 0
    for grupo in grupos:
        inicio, fim = grupo[0][1], grupo[-1][2]
        bloco = ler_intervalo(sessao, url, inicio, fim)
        transferido += len(bloco)
        for info, _, _ in grupo:
            saida[info.filename] = _descomprimir(info, bloco, inicio, sessao, url)
    return saida, transferido


def extrair_membro(url, nome, destino):
    """Grava em `destino` um único membro do zip remoto, em streaming."""
    with zipfile.ZipFile(HttpFile(url)) as z, z.open(nome) as src, open(destino, "wb") as dst:
        while bloco := src.read(8 << 20):
            dst.write(bloco)
