"""Configuração das fontes de dados (Portal de Dados Abertos do TSE, API CKAN).

Escopo: 2º turno (25/10/2026), cargo Presidente.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATASET_DIR = ROOT / "dataset"
RAW_DIR = DATASET_DIR / "raw"
PROCESSED_DIR = DATASET_DIR / "processed"

CKAN_API = "https://dadosabertos.tse.jus.br/api/3/action/package_show"

TURNO = 2
DATA_TURNO = "25/10/2026"  # os logs da urna também têm registros de dias de preparação e do 1º turno

# apelido -> id do pacote no CKAN
PACOTES = {
    # CSVs de resultado: só os membros listados em MEMBROS_RESULTADOS são extraídos (leitura remota)
    "resultados": "resultados-2026",
    # Boletim de Urna em CSV (contém QT_ELEI_BIOM_SEM_HABILITACAO)
    "bu": "resultados-2026-boletim-de-urna",
    # Arquivos da urna por seção (BU, RDV, log): lidos remotamente pelo process_urna.py
    "urna": "resultados-2026-arquivos-transmitidos-para-totalizacao",
}
PACOTES_PADRAO = ["resultados", "bu"]

# zip -> membros necessários (os arquivos _BR têm só o cargo Presidente)
MEMBROS_RESULTADOS = {
    "detalhe_votacao_munzona_2026.zip": ["detalhe_votacao_munzona_2026_BR.csv"],
    "votacao_candidato_munzona_2026.zip": ["votacao_candidato_munzona_2026_BR.csv"],
}

UFS = [
    "AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA", "MG", "MS", "MT", "PA",
    "PB", "PE", "PI", "PR", "RJ", "RN", "RO", "RR", "RS", "SC", "SE", "SP", "TO", "ZZ",
]

# ex.: bweb_2t_AC_261020260742.zip, bu_imgbu_logjez_rdv_vscmr_2026_2t_SP.zip
_RE_TURNO_UF = re.compile(r"_([12])t_([A-Z]{2})[_.]")


def info_arquivo(url):
    """Extrai (turno, uf) do nome do arquivo; (None, None) quando não é particionado."""
    m = _RE_TURNO_UF.search(url.rsplit("/", 1)[-1])
    return (int(m.group(1)), m.group(2)) if m else (None, None)
